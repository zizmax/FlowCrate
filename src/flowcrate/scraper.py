import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
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


def _browser_cookie_session():
    """Build a session authenticated with browser_cookie3 cookies, or None.

    This reads the local browser cookie stores (and may prompt for keychain
    access on macOS), so it is only ever called as a paywall fallback.
    """
    if not browser_cookie3:
        return None
    session = _plain_session()
    loaders = [
        ("Chrome", getattr(browser_cookie3, "chrome", None)),
        ("Firefox", getattr(browser_cookie3, "firefox", None)),
        ("Safari", getattr(browser_cookie3, "safari", None)),
        ("Brave", getattr(browser_cookie3, "brave", None)),
        ("Edge", getattr(browser_cookie3, "edge", None)),
        ("Chromium", getattr(browser_cookie3, "chromium", None)),
    ]
    loaded = False
    for browser_name, loader in loaders:
        if not loader:
            continue
        try:
            logging.info("Attempting to load Substack cookies from %s.", browser_name)
            session.cookies.update(loader(domain_name="substack.com"))
            session.cookies.update(loader(domain_name="flowstate.fm"))
            loaded = True
            logging.info("Loaded Substack cookies from %s.", browser_name)
        except Exception as exc:
            logging.debug("Could not load %s cookies: %s", browser_name, exc)
    return session if loaded else None


def _sid_session():
    """Build a session authenticated with the configured SUBSTACK_SID, or None."""
    cfg = load_config()
    sid = cfg.substack_sid or os.getenv("SUBSTACK_SID")
    if not sid:
        return None
    logging.info("Building Flow State session with SUBSTACK_SID fallback.")
    session = _plain_session()
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

    Substack renders the gate in a container with a ``paywall`` class or testid and a
    "Subscribe to keep reading"-style call to action, so we look for both patterns.
    """
    if soup is None:
        return False
    if soup.find(class_=re.compile(r"paywall", re.I)):
        return True
    for attr in ("data-testid", "data-component-name"):
        if soup.find(attrs={attr: re.compile(r"paywall", re.I)}):
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
