import json
import logging
import os
import plistlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    import browser_cookie3
except Exception:  # pragma: no cover - import availability is environment-specific
    browser_cookie3 = None

from .config import load_config

_session_cache = None
_preferred_browser = None


def set_preferred_browser(name):
    """Set a module-level preferred-browser hint (call from request context)."""
    global _preferred_browser
    _preferred_browser = name


def browser_from_user_agent(ua):
    """Map a browser User-Agent string to a cookie-loader name.

    Order matters: Edge must be checked before Chrome because Edge UAs contain
    both "Edg/" and "Chrome/". Brave is indistinguishable from Chrome in UA.

    Returns one of "Edge", "Firefox", "Chrome", "Safari", or None.
    """
    if not ua:
        return None
    if "Edg/" in ua:
        return "Edge"
    if "Firefox/" in ua:
        return "Firefox"
    if "Chrome/" in ua:
        return "Chrome"
    if "Safari/" in ua:
        return "Safari"
    return None


def get_session():
    """Return the cached requests session, building a plain unauthenticated one first.

    Browser cookies and SUBSTACK_SID are never loaded eagerly: reading browser
    cookies triggers scary macOS keychain prompts. Authenticated sessions are only
    built on demand in ``get_post_snapshot`` when a fetch fails or the page looks
    paywalled, and the session that works is cached for reuse.
    """
    global _session_cache
    if _session_cache is None:
        load_dotenv()
        _session_cache = _plain_session()
        logging.info("Using unauthenticated Substack session.")
    return _session_cache


def _plain_session():
    session = requests.Session()
    _apply_default_headers(session)
    return session


@lru_cache(maxsize=1)
def _default_browser_name():
    """Detect the macOS default browser from the LaunchServices plist.

    Returns a browser name matching the loader names used in
    ``_browser_cookie_session()`` (Chrome, Firefox, Safari, Brave, Edge,
    Chromium), or None if detection fails or is unsupported.
    """
    try:
        plist_path = (
            Path.home()
            / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
        )
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
        bundle_id = None
        for handler in data.get("LSHandlers", []):
            if handler.get("LSHandlerURLScheme", "").lower() == "http":
                bundle_id = handler.get("LSHandlerRoleAll", "")
                break
        if not bundle_id:
            return None
        bid = bundle_id.lower()
        # Order matters: chromium must be tested before chrome.
        if "chromium" in bid:
            return "Chromium"
        if "chrome" in bid:
            return "Chrome"
        if "firefox" in bid:
            return "Firefox"
        if "brave" in bid:
            return "Brave"
        if "edge" in bid:
            return "Edge"
        if "safari" in bid:
            return "Safari"
        return None
    except Exception as exc:
        logging.debug("Could not detect default browser: %s", exc)
        return None


# Cookies that indicate a real logged-in Substack session. ``substack.sid`` is the
# session cookie Substack sets on ``.substack.com`` after login (the same one the
# manual SID fallback uses).
_SUBSTACK_AUTH_COOKIES = ("substack.sid",)


def _has_substack_auth(session):
    """True when the session carries a Substack login cookie."""
    names = {c.name for c in session.cookies}
    return any(name in names for name in _SUBSTACK_AUTH_COOKIES)


def _browser_cookie_session():
    """Build a session authenticated with browser_cookie3 cookies, or None.

    Reading a browser's cookie store triggers a macOS keychain prompt for every
    Chromium-family browser (Chrome, Brave, Edge, Chromium each have a separate
    "Safe Storage" keychain item, so granting one does not cover the others). To
    keep prompts to a minimum we:

      * try browsers in preference order (the browser the user is viewing the
        dashboard in first, then the system default, then the rest);
      * read each browser's cookies at most once, into a fresh session so a
        browser without a login can't clobber the cookies of one that has it;
      * stop at the *first* browser that actually carries a Substack login
        cookie, so browsers the user isn't logged into are never touched.

    This is only ever called as a paywall fallback.
    """
    if not browser_cookie3:
        return None
    _FIXED_ORDER = [
        ("Chrome", getattr(browser_cookie3, "chrome", None)),
        ("Firefox", getattr(browser_cookie3, "firefox", None)),
        ("Safari", getattr(browser_cookie3, "safari", None)),
        ("Brave", getattr(browser_cookie3, "brave", None)),
        ("Edge", getattr(browser_cookie3, "edge", None)),
        ("Chromium", getattr(browser_cookie3, "chromium", None)),
    ]
    default_name = _default_browser_name()
    # Build ordered loader list: UA hint first, then system default, then rest.
    seen = set()
    ordered = []
    for name in (_preferred_browser, default_name):
        if name and name not in seen:
            match = [e for e in _FIXED_ORDER if e[0] == name]
            if match:
                ordered.extend(match)
                seen.add(name)
                if name == _preferred_browser and _preferred_browser:
                    logging.info("UA-hinted browser is %s; trying it first for cookies.", name)
                elif name == default_name:
                    logging.info("Default browser detected as %s; trying it first for cookies.", name)
    for e in _FIXED_ORDER:
        if e[0] not in seen:
            ordered.append(e)
            seen.add(e[0])

    for browser_name, loader in ordered:
        if not loader:
            continue
        candidate = _plain_session()
        try:
            logging.info("Attempting to load Substack cookies from %s.", browser_name)
            candidate.cookies.update(loader(domain_name="substack.com"))
        except Exception as exc:
            logging.debug("Could not load %s cookies: %s", browser_name, exc)
            continue
        if not _has_substack_auth(candidate):
            logging.debug("%s has no Substack login cookie; trying next browser.", browser_name)
            continue
        # Winning browser: also pull custom-domain cookies (same keychain item,
        # so no extra prompt), then stop before touching any other browser.
        try:
            candidate.cookies.update(loader(domain_name="flowstate.fm"))
        except Exception as exc:
            logging.debug("Could not load %s flowstate.fm cookies: %s", browser_name, exc)
        logging.info("Loaded Substack session from %s.", browser_name)
        return candidate
    logging.info("No browser had a Substack login cookie.")
    return None


def _sid_session():
    """Build a session from manually-supplied/synced session cookies, or None.

    Flow State is a Substack *custom domain*, so the cookie that actually unlocks
    paid posts is ``connect.sid`` on ``www.flowstate.fm`` — ``substack.sid`` on
    ``.substack.com`` alone does not authorize custom-domain fetches. We set the
    flowstate ``connect.sid`` when available (the primary unlocker) and also the
    ``substack.sid`` if present, for completeness.
    """
    cfg = load_config()
    connect_sid = cfg.flowstate_connect_sid or os.getenv("FLOWSTATE_CONNECT_SID")
    sid = cfg.substack_sid or os.getenv("SUBSTACK_SID")
    if not connect_sid and not sid:
        return None
    logging.info("Building Flow State session from saved session cookies.")
    session = _plain_session()
    if connect_sid:
        session.cookies.set("connect.sid", connect_sid, domain="www.flowstate.fm")
    if sid:
        session.cookies.set("substack.sid", sid, domain=".substack.com")
    return session


def reset_session_cache():
    global _session_cache
    _session_cache = None


def get_soup(url):
    snapshot = get_post_snapshot(url)
    if snapshot:
        return BeautifulSoup(snapshot["raw_html"], "html.parser")
    return None


def get_post_snapshot(url):
    """Fetch one Flow State URL and return raw HTML plus parsed metadata.

    Starts with the plain unauthenticated session. Only if that fetch fails or the
    page looks paywalled do we retry with browser cookies, then SUBSTACK_SID; the
    session that works is cached for subsequent requests.
    """
    raw_html = _fetch_snapshot_html(url)
    if not raw_html:
        return None

    soup = BeautifulSoup(raw_html, "html.parser")
    title_tag = soup.find("h1", class_="post-title") or soup.find("h1")
    post_title = title_tag.get_text().strip() if title_tag else ""
    return {
        "url": url,
        "raw_html": raw_html,
        "title": post_title,
        "source_date": _extract_source_date(soup),
        "soup": soup,
    }


def _fetch_snapshot_html(url):
    """Fetch HTML for url, escalating auth only when needed. Caches the working session."""
    global _session_cache
    html = _get_html(get_session(), url)
    if html is not None and not _looks_paywalled_html(html):
        return html
    if html is None:
        logging.warning("Unauthenticated Flow State fetch failed for %s; trying authenticated fallbacks.", url)
    else:
        logging.info("Flow State page for %s looks paywalled; trying authenticated fallbacks.", url)

    for builder in (_browser_cookie_session, _sid_session):
        candidate = builder()
        if candidate is None:
            continue
        retry = _get_html(candidate, url)
        if retry is None:
            continue
        if not _looks_paywalled_html(retry):
            _session_cache = candidate
            return retry
        if html is None:
            html = retry
    return html


def _get_html(session, url):
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except Exception as exc:
        logging.warning("Flow State fetch failed for %s: %s", url, exc)
        return None


def _looks_paywalled_html(raw_html):
    return _looks_paywalled(BeautifulSoup(raw_html, "html.parser")) if raw_html else False


def _looks_paywalled(soup):
    """Return True when the page shows a Substack paywall gate instead of full content.

    The real gate is ``<div class="paywall" data-testid="paywall"
    data-component-name="Paywall">`` plus a "Subscribe to keep reading"-style call
    to action. We match those markers *exactly* rather than as substrings, because
    Substack ships client-side paywall scaffolding — ``class="paywall-jump"`` and
    ``data-component-name="PaywallToDOM"`` — to authenticated subscribers too. A
    substring match on "paywall" flags those and would wrongly report an unlocked
    post as gated (which broke automatic session detection).
    """
    if soup is None:
        return False
    # Exact gate markers: class token "paywall", or the paywall testid/component.
    if soup.find(class_="paywall"):
        return True
    if soup.find(attrs={"data-testid": "paywall"}):
        return True
    if soup.find(attrs={"data-component-name": "Paywall"}):
        return True
    text = soup.get_text(" ", strip=True).lower()
    gate_phrases = (
        "subscribe to keep reading",
        "this post is for paid subscribers",
        "this post is for paying subscribers",
        "keep reading with a 7-day free trial",
    )
    return any(phrase in text for phrase in gate_phrases)


def _apply_default_headers(session):
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) "
                "Gecko/20100101 Firefox/133.0"
            )
        }
    )


def test_flowstate_fetch():
    """Fetch Flow State without requiring a SID and return displayable proof."""
    soup = get_soup("https://www.flowstate.fm/")
    if not soup:
        raise RuntimeError("Flow State could not be fetched.")
    title = ""
    title_tag = soup.find("title") or soup.find("h1")
    if title_tag:
        title = title_tag.get_text().strip()
    if not title:
        meta_title = soup.find("meta", property="og:title")
        title = meta_title.get("content", "").strip() if meta_title else ""
    return {
        "ok": True,
        "title": title or "Flow State",
        "mode": "Public access (browser cookies or SID used only if paywalled)",
    }


def check_flowstate_access():
    """Probe Flow State and return an access-level dict.

    Returns a dict with keys:
      ``"status"``: ``"full"`` | ``"free"`` | ``"none"``
      ``"message"``: human-readable description suitable for display in the UI.
      ``"scanned"``: number of posts actually probed (0 on network failure).

    Logic:
    1. Fetch recent posts (limit 30). On failure → status ``"none"``.
    2. For each post, try a plain (no-auth) fetch and check for a paywall gate.
    3. If no post is paywalled → ``"full"`` (all readable without auth).
    4. If a paywalled post is found, try browser-cookie session then SID session.
       The first that un-paywalls the post → ``"full"`` (with the source noted).
       If neither works → ``"free"``.
    """
    try:
        posts = get_recent_posts(limit=30)
    except Exception as exc:
        return {"status": "none", "message": f"No access — Flow State unreachable: {exc}", "scanned": 0}

    plain = _plain_session()
    paywalled_url = None
    scanned = 0
    for post in posts:
        url = post.get("url")
        if not url:
            continue
        html = _get_html(plain, url)
        scanned += 1
        if html and _looks_paywalled_html(html):
            paywalled_url = url
            break

    if paywalled_url is None:
        return {
            "status": "full",
            "message": f"Full access — no paywalled posts found to test (checked {scanned})",
            "scanned": scanned,
        }

    for builder, label in (
        (_browser_cookie_session, "via your browser session"),
        (_sid_session, "via your Substack SID"),
    ):
        candidate = builder()
        if candidate is None:
            continue
        retry = _get_html(candidate, paywalled_url)
        if retry and not _looks_paywalled_html(retry):
            return {
                "status": "full",
                "message": f"Full access — paid posts unlock {label}",
                "scanned": scanned,
            }

    return {
        "status": "free",
        "message": "Free posts only — automatic session detection failed; paste your SID below",
        "scanned": scanned,
    }


def get_recent_posts(limit=5):
    """Return recent Flow State posts as dicts with title/url/date."""
    posts = _recent_posts_from_archive_api(limit)
    if not posts:
        posts = _recent_posts_from_feed(limit)
    if not posts:
        posts = _recent_posts_from_home(limit)
    if not posts:
        raise RuntimeError("Could not discover recent Flow State posts.")
    return posts[:limit]


def _recent_posts_from_archive_api(limit):
    url = f"https://www.flowstate.fm/api/v1/archive?sort=new&search=&offset=0&limit={limit}"
    try:
        response = get_session().get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logging.debug("Flow State archive API lookup failed: %s", exc)
        return []

    posts = []
    for item in data:
        post_url = item.get("canonical_url") or item.get("url") or item.get("web_url")
        title = item.get("title") or item.get("subtitle") or "Flow State post"
        if not post_url:
            slug = item.get("slug")
            post_url = f"https://www.flowstate.fm/p/{slug}" if slug else ""
        if post_url:
            post_url = urljoin("https://www.flowstate.fm/", post_url)
            posts.append(
                {
                    "title": title,
                    "url": post_url,
                    "date": (item.get("post_date") or item.get("published_at") or "").split("T")[0],
                }
            )
    return posts


def _recent_posts_from_feed(limit):
    try:
        response = get_session().get("https://www.flowstate.fm/feed", timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception as exc:
        logging.debug("Flow State RSS lookup failed: %s", exc)
        return []

    posts = []
    for item in root.findall(".//channel/item"):
        title = item.findtext("title") or "Flow State post"
        link = item.findtext("link") or ""
        pub_date = item.findtext("pubDate") or ""
        date = ""
        if pub_date:
            try:
                date = parsedate_to_datetime(pub_date).date().isoformat()
            except Exception:
                date = pub_date
        if link:
            posts.append({"title": title.strip(), "url": link.strip(), "date": date})
        if len(posts) >= limit:
            break
    return posts


def _recent_posts_from_home(limit):
    soup = get_soup("https://www.flowstate.fm/")
    if not soup:
        return []
    posts = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if "/p/" not in href:
            continue
        post_url = urljoin("https://www.flowstate.fm/", href)
        if post_url in seen:
            continue
        seen.add(post_url)
        title = link.get_text(" ", strip=True) or "Flow State post"
        posts.append({"title": title, "url": post_url, "date": ""})
        if len(posts) >= limit:
            break
    return posts


def parse_element(element):
    spotify_link_tag = element.find("a", href=re.compile(r"open\.spotify\.com"))
    if not spotify_link_tag:
        return None

    spotify_link = spotify_link_tag.get("href")
    em_tag = element.find("em")

    name = em_tag.get_text().strip() if em_tag else ""
    text_content = element.get_text(separator=" ").strip()

    if not name:
        link_text = spotify_link_tag.get_text().strip()
        if link_text and link_text in text_content:
            pre_text = text_content.split(link_text)[0]
            name = re.sub(r"[–-]\s*$", "", pre_text).strip()

    if not name:
        name = spotify_link_tag.get_text().strip()

    remainder = text_content.replace(name, "", 1).strip()
    remainder = re.sub(r"^[–-]\s*", "", remainder)

    metadata_match = re.search(r"\((.*?)\)", remainder)
    metadata = metadata_match.group(1) if metadata_match else ""

    if metadata_match:
        artist_name = remainder.split("(")[0].strip()
    else:
        split_point = remainder.find("Spotify")
        artist_name = remainder[:split_point].strip() if split_point != -1 else remainder.strip()

    artist_name = re.sub(r"\s+[–-]$", "", artist_name).strip()
    if len(artist_name) > 80:
        return None
    if artist_name.lower().startswith("today we're listening to"):
        return None

    item_type = "track" if "/track/" in spotify_link else "album"
    if not artist_name and " - " in name:
        artist_name, name = [p.strip() for p in name.split(" - ", 1)]

    return {
        "artist": artist_name,
        "name": name,
        "type": item_type,
        "spotify_link": spotify_link,
        "metadata": metadata,
        "raw_text": text_content,
    }


def _extract_source_date(soup):
    date_tag = soup.find("time", attrs={"datetime": True})
    if date_tag:
        return date_tag.get("datetime", "").split("T")[0]

    ld_json = soup.find("script", type="application/ld+json")
    if ld_json:
        try:
            data = json.loads(ld_json.get_text())
            if "datePublished" in data:
                return data["datePublished"].split("T")[0]
            for item in data.get("@graph", []):
                if "datePublished" in item:
                    return item["datePublished"].split("T")[0]
        except Exception:
            pass

    meta_date = soup.find("meta", property="article:published_time")
    if meta_date and meta_date.get("content"):
        return meta_date.get("content").split("T")[0]
    return ""


def extract_from_snapshot(snapshot):
    """Parse Flow State listening rows from an already fetched snapshot."""
    url = snapshot["url"]
    soup = snapshot["soup"]
    post_title = snapshot.get("title", "")
    source_date = snapshot.get("source_date", "")

    extracted_items = []
    seen_keys = set()

    def add_item(item):
        if not item or not item.get("name") or not item.get("artist"):
            return
        item["artist"] = item["artist"].strip()[:100]
        item["name"] = item["name"].strip()[:100]
        if not item["artist"] or not item["name"]:
            return
        key = (item["artist"].lower(), item["name"].lower())
        if key in seen_keys:
            return
        seen_keys.add(key)
        item["source_url"] = url
        item["source_date"] = source_date
        extracted_items.append(item)

    for li in soup.find_all("li"):
        add_item(parse_element(li))

    for p in soup.find_all("p"):
        if not p.find_parent("li"):
            add_item(parse_element(p))

    text = soup.get_text(separator="\n")
    timestamp_regex = r"(\d{1,2}:\d{2}(?::\d{2})?)\s*[–-]\s*(.*?)\s*[–-]\s*(.*)"
    for line in text.split("\n"):
        line = line.strip()
        match = re.search(timestamp_regex, line)
        if match:
            add_item(
                {
                    "artist": match.group(2).strip(),
                    "name": match.group(3).strip(),
                    "type": "track",
                    "spotify_link": None,
                    "metadata": "",
                    "raw_text": line,
                }
            )

    logging.info("Found %s items in post.", len(extracted_items))
    return extracted_items, post_title


def extract_source_post(url):
    """Fetch and parse one Flow State post without touching Spotify APIs."""
    logging.info("Scraping post: %s", url)
    snapshot = get_post_snapshot(url)
    if not snapshot:
        return {"items": [], "title": "", "source_date": "", "raw_html": ""}
    items, post_title = extract_from_snapshot(snapshot)
    return {
        "items": items,
        "title": post_title,
        "source_date": snapshot.get("source_date", ""),
        "raw_html": snapshot.get("raw_html", ""),
    }


def extract_from_post(url):
    """Scrape one Flow State post and return (items, post_title)."""
    parsed = extract_source_post(url)
    return parsed["items"], parsed["title"]
