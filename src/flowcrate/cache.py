import logging
import re
import sqlite3
from hashlib import sha256
from datetime import datetime, timedelta

from .paths import FLOWCRATE_DB, ensure_dirs
from .scraper import extract_source_post, get_recent_posts
from .spotify import SpotifyManager, SpotifyRateLimitError, parse_spotify_url, spotify_service_state


def connect(db_path=None):
    ensure_dirs()
    conn = sqlite3.connect(db_path or FLOWCRATE_DB)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def dashboard_data(db_path=None):
    with connect(db_path) as conn:
        posts = _read_posts(conn)
        latest = posts[0] if posts else None
        archive = posts[1:11] if len(posts) > 1 else []
        latest_entries = _read_entries(conn, latest["url"]) if latest else []
        latest_post = _post_group(latest, latest_entries) if latest else None
        archive_posts = []
        archive_entries = []
        for post in archive:
            entries = _read_entries(conn, post["url"])
            archive_entries.extend(entries)
            archive_posts.append(_post_group(post, entries))
        meta = _read_cache_meta(conn)
        return {
            "latest": latest,
            "latest_post": latest_post,
            "archive_posts": archive_posts,
            "latest_entries": latest_entries,
            "archive_entries": archive_entries,
            "cache_path": db_path or FLOWCRATE_DB,
            "updated_at": posts[0]["fetched_at"] if posts else "",
            "cache_status": _cache_status(meta, posts),
            "spotify_state": spotify_service_state(),
            "latest_summary": summarize_entries(latest_entries),
            "archive_summary": summarize_entries(archive_entries),
        }


def refresh_from_flowstate(limit=11, progress=None, db_path=None):
    if progress:
        progress(f"Discovering latest {limit} Flow State posts")
    posts = get_recent_posts(limit=limit)
    now = datetime.now().isoformat(timespec="seconds")
    fetched_count = 0
    # searcher: None = not yet built, False = unavailable, otherwise a SpotifyManager.
    searcher_state = {"searcher": None, "rate_limited": False}
    with connect(db_path) as conn:
        known = _known_posts(conn)
        _set_cache_meta(conn, "refresh_started_at", now)
        conn.commit()

        for idx, post in enumerate(posts, start=1):
            url = post.get("url") or ""
            if not url:
                continue
            existing = known.get(url)
            if existing and _post_metadata_unchanged(existing, post):
                if progress:
                    progress(f"Stopping at cached post {idx}/{len(posts)}: {post.get('title') or url}")
                break

            if progress:
                progress(f"Fetching Flow State post {idx}/{len(posts)}: {post.get('title') or url}")
            source = extract_source_post(url)
            content_hash = _content_hash(source.get("raw_html", ""))
            if existing and existing.get("content_hash") == content_hash:
                _upsert_post_metadata(conn, post, idx - 1, now, content_hash=content_hash)
            else:
                upsert_source_post(conn, post, source, idx - 1, now, content_hash)
                fetched_count += 1
                _resolve_unlinked_entries(conn, url, searcher_state, progress=progress)
            _set_cache_meta(conn, "last_checkpoint_url", url)
            _set_cache_meta(conn, "last_checkpoint_at", datetime.now().isoformat(timespec="seconds"))
            conn.commit()

        _reposition_cached_posts(conn, posts)
        _set_cache_meta(conn, "cache_source", "flowstate")
        _set_cache_meta(conn, "updated_at", now)
        _set_cache_meta(conn, "refreshed_at", now)
        conn.commit()
    return fetched_count


# Terminal match states are never searched again: each item is searched at most once, ever.
_TERMINAL_MATCH_STATUS = ("FOUND", "NOT_FOUND", "MISMATCH_REJECTED")


def _resolve_unlinked_entries(conn, post_url, searcher_state, progress=None):
    """Search Spotify for entries in a freshly fetched post that have no URI.

    Only entries never searched before (no terminal match_status) are resolved,
    so each item costs at most one Spotify search across all refreshes. A rate
    limit stops searching entirely so remaining items wait for a future refresh.
    """
    if searcher_state.get("rate_limited"):
        return
    rows = conn.execute(
        """
        SELECT row_id, parsed_artist, parsed_name, parsed_type
        FROM entries
        WHERE post_url = ?
          AND (spotify_uri IS NULL OR spotify_uri = '')
          AND COALESCE(match_status, '') NOT IN ('FOUND', 'NOT_FOUND', 'MISMATCH_REJECTED')
        ORDER BY position
        """,
        (post_url,),
    ).fetchall()
    if not rows:
        return
    searcher = _refresh_searcher(searcher_state)
    if not searcher:
        return
    for row in rows:
        if progress:
            progress(f"Searching Spotify for {row['parsed_artist']} - {row['parsed_name']}")
        try:
            found = searcher.search_item(row["parsed_artist"], row["parsed_name"], row["parsed_type"] or "track")
        except SpotifyRateLimitError:
            searcher_state["rate_limited"] = True
            logging.warning("Spotify rate limit reached during refresh; leaving remaining items unsearched.")
            return
        except Exception as exc:
            logging.warning("Spotify search failed for %s: %s", row["parsed_name"], exc)
            continue
        _apply_search_result(conn, row["row_id"], post_url, found)
        conn.commit()


def _refresh_searcher(searcher_state):
    if searcher_state["searcher"] is None:
        try:
            searcher_state["searcher"] = SpotifyManager()
        except Exception as exc:
            logging.warning("Spotify search unavailable during refresh: %s", exc)
            searcher_state["searcher"] = False
    return searcher_state["searcher"]


def _apply_search_result(conn, row_id, post_url, found):
    status = found.get("status") or "NOT_FOUND"
    uri = found.get("uri")
    conn.execute(
        """
        UPDATE entries
        SET spotify_uri = ?, spotify_artist = ?, spotify_name = ?, spotify_link = ?,
            match_status = ?, match_type = ?, failure_reason = ?
        WHERE row_id = ?
        """,
        (
            uri,
            found.get("spotify_artist"),
            found.get("spotify_name"),
            found.get("spotify_link"),
            status,
            found.get("match_type"),
            found.get("failure_reason"),
            row_id,
        ),
    )
    conn.execute("DELETE FROM tracks WHERE entry_id = ?", (row_id,))
    if _is_track_uri(uri):
        conn.execute(
            """
            INSERT INTO tracks (
                track_id, entry_id, post_url, position, spotify_uri, spotify_artist,
                spotify_name, spotify_link, duration_ms, track_number, disc_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{row_id}:0",
                row_id,
                post_url,
                0,
                uri,
                found.get("spotify_artist"),
                found.get("spotify_name"),
                found.get("spotify_link"),
                None,
                None,
                None,
            ),
        )


def cache_is_stale(hours=8, db_path=None):
    """Return True when the cache has never been refreshed or is older than hours."""
    with connect(db_path) as conn:
        meta = _read_cache_meta(conn)
    refreshed_at = meta.get("refreshed_at") or ""
    if not refreshed_at:
        return True
    try:
        refreshed = datetime.fromisoformat(refreshed_at)
    except ValueError:
        return True
    return datetime.now() - refreshed >= timedelta(hours=hours)


def last_refreshed_at(db_path=None):
    with connect(db_path) as conn:
        meta = _read_cache_meta(conn)
    return meta.get("refreshed_at") or ""


def has_cached_post(url, db_path=None):
    if not url:
        return False
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM posts WHERE url = ? LIMIT 1", (url,)).fetchone()
    return row is not None


def latest_cached_post(db_path=None):
    """Return (post, entries) for the newest cached post, or (None, []) if empty."""
    with connect(db_path) as conn:
        posts = _read_posts(conn)
        if not posts:
            return None, []
        latest = posts[0]
        entries = _read_entries(conn, latest["url"])
    return latest, entries


def replace_cache(conn, payload, cache_source=None):
    _init_schema(conn)
    conn.execute("DELETE FROM tracks")
    conn.execute("DELETE FROM entries")
    conn.execute("DELETE FROM posts")
    conn.execute("DELETE FROM cache_meta")
    now = datetime.now().isoformat(timespec="seconds")
    for post_position, post in enumerate(payload.get("posts", [])):
        url = post.get("url") or ""
        if not url:
            continue
        conn.execute(
            """
            INSERT INTO posts (
                url, title, source_date, position, fetched_at, discovered_at,
                parsed_at, content_hash, raw_source_html
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,
                source_date=excluded.source_date,
                position=excluded.position,
                fetched_at=excluded.fetched_at,
                discovered_at=excluded.discovered_at,
                parsed_at=excluded.parsed_at,
                content_hash=excluded.content_hash,
                raw_source_html=excluded.raw_source_html
            """,
            (
                url,
                post.get("title") or "Flow State post",
                post.get("date") or "",
                post_position,
                now,
                post.get("discovered_at") or now,
                post.get("parsed_at") or now,
                post.get("content_hash") or _content_hash(post.get("raw_source_html", "")),
                post.get("raw_source_html", ""),
            ),
        )
        for entry_position, entry in enumerate(post.get("entries", [])):
            upsert_entry(conn, url, entry, entry_position)
    source = cache_source or payload.get("cache_source") or "unknown"
    _set_cache_meta(conn, "cache_source", source)
    _set_cache_meta(conn, "updated_at", now)
    if source == "flowstate":
        _set_cache_meta(conn, "refreshed_at", now)
    conn.commit()


def upsert_entry(conn, post_url, entry, position=0):
    row_id = entry.get("row_id") or f"{post_url}#{position}"
    meta = parse_metadata(entry.get("raw_metadata") or entry.get("metadata") or _metadata_from_raw(entry.get("raw_scraped_text", "")))
    children = entry.get("children") or []
    conn.execute(
        """
        INSERT OR REPLACE INTO entries (
            row_id, post_url, position, raw_scraped_text, raw_metadata,
            parsed_artist, parsed_name, parsed_type, source_url, source_date,
            match_status, match_type, spotify_uri, spotify_artist, spotify_name,
            spotify_link, failure_reason, is_album_expanded, child_count,
            duration_text, duration_minutes, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            post_url,
            position,
            entry.get("raw_scraped_text", ""),
            entry.get("raw_metadata") or entry.get("metadata") or meta["raw_metadata"],
            entry.get("parsed_artist", ""),
            entry.get("parsed_name", ""),
            entry.get("parsed_type", ""),
            entry.get("source_url", post_url),
            entry.get("source_date", ""),
            entry.get("match_status", ""),
            entry.get("match_type", ""),
            entry.get("spotify_uri"),
            entry.get("spotify_artist"),
            entry.get("spotify_name"),
            entry.get("spotify_link"),
            entry.get("failure_reason"),
            1 if entry.get("is_album_expanded") else 0,
            len(children),
            meta["duration_text"],
            meta["duration_minutes"],
            meta["notes"],
        ),
    )
    conn.execute("DELETE FROM tracks WHERE entry_id = ?", (row_id,))
    track_rows = children if children else ([entry] if _is_track_uri(entry.get("spotify_uri")) else [])
    for track_position, track in enumerate(track_rows):
        uri = track.get("spotify_uri")
        if not uri:
            continue
        conn.execute(
            """
            INSERT INTO tracks (
                track_id, entry_id, post_url, position, spotify_uri, spotify_artist,
                spotify_name, spotify_link, duration_ms, track_number, disc_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track.get("row_id") or f"{row_id}:{track_position}",
                row_id,
                post_url,
                track_position,
                uri,
                track.get("spotify_artist") or track.get("parsed_artist"),
                track.get("spotify_name") or track.get("parsed_name"),
                track.get("spotify_link"),
                track.get("duration_ms"),
                track.get("track_number"),
                track.get("disc_number"),
            ),
        )


def parse_metadata(raw_metadata):
    raw = (raw_metadata or "").strip()
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    duration_text = ""
    duration_minutes = None
    notes = []
    for part in parts:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minutes)", part, flags=re.I)
        if match and duration_minutes is None:
            duration_minutes = float(match.group(1))
            duration_text = f"{match.group(1)}m"
        else:
            notes.append(part)
    return {
        "raw_metadata": raw,
        "duration_text": duration_text,
        "duration_minutes": duration_minutes,
        "notes": ", ".join(notes),
    }


def selected_track_uris(entry_ids, db_path=None, spotify=None, expand_albums=False):
    if not entry_ids:
        return []
    placeholders = ",".join("?" for _ in entry_ids)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT e.*
            FROM entries e
            JOIN posts p ON p.url = e.post_url
            WHERE e.row_id IN ({placeholders})
            ORDER BY p.position, e.position
            """,
            list(entry_ids),
        ).fetchall()
        uris = []
        for row in rows:
            entry = dict(row)
            child_uris = _track_uris_for_entry(conn, entry["row_id"])
            if child_uris:
                uris.extend(child_uris)
            elif _is_track_uri(entry.get("spotify_uri")):
                uris.append(entry["spotify_uri"])
            elif expand_albums and _is_album_uri(entry.get("spotify_uri")):
                if not spotify:
                    continue
                try:
                    tracks = spotify.get_album_track_rows(entry["spotify_uri"])
                except SpotifyRateLimitError:
                    raise
                except Exception as exc:
                    conn.execute(
                        "UPDATE entries SET match_status = ?, failure_reason = ? WHERE row_id = ?",
                        ("FAILED", f"Album expansion failed: {exc}", entry["row_id"]),
                    )
                    conn.commit()
                    raise
                _replace_album_tracks(conn, entry, tracks)
                child_uris = [track.get("uri") for track in tracks if track.get("uri")]
                uris.extend(child_uris)
        conn.commit()
    return uris


def playlist_name_for_selection(entry_ids, db_path=None):
    if not entry_ids:
        return "Flow Crate playlist"
    placeholders = ",".join("?" for _ in entry_ids)
    with connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT p.title
            FROM entries e
            JOIN posts p ON p.url = e.post_url
            WHERE e.row_id IN ({placeholders})
            ORDER BY p.position, e.position
            LIMIT 1
            """,
            list(entry_ids),
        ).fetchone()
    return f"Flow Crate: {row['title']}" if row else "Flow Crate playlist"


def summarize_entries(entries):
    ready = [entry for entry in entries if entry.get("readiness_status") == "Ready"]
    linked = [entry for entry in entries if entry.get("readiness_status") == "Linked"]
    selected = ready + linked
    track_count = sum(entry.get("track_count", 0) for entry in ready)
    duration_ms = sum(entry.get("duration_ms") or 0 for entry in ready)
    notes = sorted({entry.get("notes") for entry in selected if entry.get("notes")})
    return {
        "entry_count": len(ready),
        "linked_count": len(linked),
        "actionable_count": len(selected),
        "playable_count": len(selected),
        "track_count": track_count,
        "duration": format_duration_ms(duration_ms) if duration_ms else _duration_from_entry_minutes(selected),
        "notes": ", ".join(notes[:4]),
    }


def format_duration_ms(duration_ms):
    minutes = round((duration_ms or 0) / 60000)
    hours, mins = divmod(minutes, 60)
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _post_group(post, entries):
    summary = summarize_entries(entries)
    group = dict(post)
    group["entries"] = entries
    group["summary"] = summary
    group["playable_entry_count"] = summary["entry_count"]
    group["linked_entry_count"] = summary["linked_count"]
    group["playable_count"] = summary["playable_count"]
    group["track_count"] = summary["track_count"]
    return group


def _read_posts(conn):
    rows = conn.execute(
        """
        SELECT url, title, source_date AS date, position, fetched_at, discovered_at,
               parsed_at, content_hash
        FROM posts
        ORDER BY position, source_date DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _read_entries(conn, post_url):
    rows = conn.execute(
        """
        SELECT
            e.*,
            p.title AS post_title,
            p.source_date AS post_date,
            COUNT(t.spotify_uri) AS track_count,
            SUM(COALESCE(t.duration_ms, 0)) AS duration_ms
        FROM entries e
        JOIN posts p ON p.url = e.post_url
        LEFT JOIN tracks t ON t.entry_id = e.row_id AND t.spotify_uri LIKE 'spotify:track:%'
        WHERE e.post_url = ?
        GROUP BY e.row_id
        ORDER BY e.position
        """,
        (post_url,),
    ).fetchall()
    entries = []
    children_by_entry = _read_child_tracks(conn, [row["row_id"] for row in rows])
    for row in rows:
        entry = dict(row)
        entry["children"] = children_by_entry.get(entry["row_id"], [])
        readiness = row_readiness(entry)
        entry.update(readiness)
        entry["playable"] = readiness["selectable"]
        entry["duration_display"] = format_duration_ms(entry["duration_ms"]) if entry.get("duration_ms") else entry.get("duration_text")
        entries.append(entry)
    return entries


def row_readiness(entry):
    status = (entry.get("match_status") or "").upper()
    uri = entry.get("spotify_uri") or ""
    track_count = entry.get("track_count") or 0
    failure_reason = entry.get("failure_reason") or ""

    if status == "FAILED":
        return {
            "readiness_status": "Failed",
            "readiness_label": "Failed",
            "readiness_key": "status-failed",
            "readiness_tooltip": failure_reason or "Spotify matching or album expansion failed.",
            "selectable": False,
        }
    if status in {"NOT_FOUND", "MISMATCH_REJECTED"}:
        return {
            "readiness_status": "Not Found",
            "readiness_label": "Not Found",
            "readiness_key": "status-not-found",
            "readiness_tooltip": failure_reason or "Spotify search ran and did not find an acceptable match.",
            "selectable": False,
        }
    if _is_track_uri(uri) or track_count > 0:
        return {
            "readiness_status": "Ready",
            "readiness_label": "Ready",
            "readiness_key": "status-ready",
            "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
            "selectable": True,
        }
    if _is_album_uri(uri):
        return {
            "readiness_status": "Linked",
            "readiness_label": "Ready",
            "readiness_key": "status-ready",
            "readiness_tooltip": "Album link from Flow State — Flow Crate loads the album's tracks when you press play.",
            "selectable": True,
        }
    return {
        "readiness_status": "Needs Match",
        "readiness_label": "Needs Match",
        "readiness_key": "status-needs-match",
        "readiness_tooltip": "Flow State did not provide a Spotify URI. Spotify search is needed before this row can be used.",
        "selectable": False,
    }


def upsert_source_post(conn, post, source, position, now, content_hash=None):
    url = post.get("url") or source.get("url") or ""
    if not url:
        return
    source_date = post.get("date") or source.get("source_date") or ""
    title = post.get("title") or source.get("title") or "Flow State post"
    raw_html = source.get("raw_html", "")
    content_hash = content_hash or _content_hash(raw_html)
    conn.execute(
        """
        INSERT INTO posts (
            url, title, source_date, position, fetched_at, discovered_at,
            parsed_at, content_hash, raw_source_html
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            source_date=excluded.source_date,
            position=excluded.position,
            fetched_at=excluded.fetched_at,
            discovered_at=excluded.discovered_at,
            parsed_at=excluded.parsed_at,
            content_hash=excluded.content_hash,
            raw_source_html=excluded.raw_source_html
        """,
        (url, title, source_date, position, now, now, now, content_hash, raw_html),
    )
    conn.execute("DELETE FROM tracks WHERE post_url = ?", (url,))
    conn.execute("DELETE FROM entries WHERE post_url = ?", (url,))
    for entry_position, item in enumerate(source.get("items", [])):
        upsert_entry(conn, url, _source_item_entry(url, item, entry_position), entry_position)


def _source_item_entry(post_url, item, position):
    link_info = parse_spotify_url(item.get("spotify_link"))
    uri = link_info["uri"] if link_info else None
    match_status = "FOUND" if uri else "NEEDS_MATCH"
    spotify_link = link_info["spotify_link"] if link_info else item.get("spotify_link")
    return {
        "row_id": f"{post_url}#{position}",
        "raw_scraped_text": item.get("raw_text", ""),
        "raw_metadata": item.get("metadata", ""),
        "parsed_artist": item.get("artist", ""),
        "parsed_name": item.get("name", ""),
        "parsed_type": item.get("type", ""),
        "source_url": item.get("source_url") or post_url,
        "source_date": item.get("source_date", ""),
        "match_status": match_status,
        "match_type": "DIRECT_LINK" if uri else "NONE",
        "spotify_uri": uri,
        "spotify_artist": item.get("artist") if uri else None,
        "spotify_name": item.get("name") if uri else None,
        "spotify_link": spotify_link,
        "failure_reason": None,
        "is_album_expanded": False,
        "children": [],
    }


def _init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS posts (
            url TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_date TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT,
            discovered_at TEXT,
            parsed_at TEXT,
            content_hash TEXT,
            raw_source_html TEXT
        );

        CREATE TABLE IF NOT EXISTS entries (
            row_id TEXT PRIMARY KEY,
            post_url TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            raw_scraped_text TEXT,
            raw_metadata TEXT,
            parsed_artist TEXT,
            parsed_name TEXT,
            parsed_type TEXT,
            source_url TEXT,
            source_date TEXT,
            match_status TEXT,
            match_type TEXT,
            spotify_uri TEXT,
            spotify_artist TEXT,
            spotify_name TEXT,
            spotify_link TEXT,
            failure_reason TEXT,
            is_album_expanded INTEGER NOT NULL DEFAULT 0,
            child_count INTEGER NOT NULL DEFAULT 0,
            duration_text TEXT,
            duration_minutes REAL,
            notes TEXT,
            FOREIGN KEY(post_url) REFERENCES posts(url)
        );

        CREATE TABLE IF NOT EXISTS tracks (
            track_id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL,
            post_url TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            spotify_uri TEXT NOT NULL,
            spotify_artist TEXT,
            spotify_name TEXT,
            spotify_link TEXT,
            duration_ms INTEGER,
            track_number INTEGER,
            disc_number INTEGER,
            FOREIGN KEY(entry_id) REFERENCES entries(row_id),
            FOREIGN KEY(post_url) REFERENCES posts(url)
        );

        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    _ensure_column(conn, "posts", "discovered_at", "TEXT")
    _ensure_column(conn, "posts", "parsed_at", "TEXT")
    _ensure_column(conn, "posts", "content_hash", "TEXT")
    _ensure_column(conn, "posts", "raw_source_html", "TEXT")


def _ensure_column(conn, table, column, definition):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _known_posts(conn):
    rows = conn.execute(
        """
        SELECT url, title, source_date, content_hash, fetched_at
        FROM posts
        ORDER BY position, source_date DESC
        """
    ).fetchall()
    return {row["url"]: dict(row) for row in rows}


def _post_metadata_unchanged(existing, post):
    post_title = (post.get("title") or existing.get("title") or "").strip()
    post_date = (post.get("date") or existing.get("source_date") or "").strip()
    return (
        existing
        and (existing.get("title") or "").strip() == post_title
        and (existing.get("source_date") or "").strip() == post_date
    )


def _upsert_post_metadata(conn, post, position, now, content_hash=None):
    conn.execute(
        """
        UPDATE posts
        SET title = ?, source_date = ?, position = ?, fetched_at = ?,
            discovered_at = COALESCE(discovered_at, ?), content_hash = COALESCE(?, content_hash)
        WHERE url = ?
        """,
        (
            post.get("title") or "Flow State post",
            post.get("date") or "",
            position,
            now,
            now,
            content_hash,
            post.get("url"),
        ),
    )


def _reposition_cached_posts(conn, discovered_posts):
    seen = set()
    for position, post in enumerate(discovered_posts):
        url = post.get("url") or ""
        if not url:
            continue
        seen.add(url)
        conn.execute("UPDATE posts SET position = ? WHERE url = ?", (position, url))
    rows = conn.execute(
        "SELECT url FROM posts ORDER BY source_date DESC, fetched_at DESC, title"
    ).fetchall()
    position = len(discovered_posts)
    for row in rows:
        if row["url"] in seen:
            continue
        conn.execute("UPDATE posts SET position = ? WHERE url = ?", (position, row["url"]))
        position += 1


def _read_child_tracks(conn, entry_ids):
    if not entry_ids:
        return {}
    placeholders = ",".join("?" for _ in entry_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM tracks
        WHERE entry_id IN ({placeholders}) AND spotify_uri LIKE 'spotify:track:%'
        ORDER BY entry_id, position
        """,
        entry_ids,
    ).fetchall()
    children = {}
    for row in rows:
        child = dict(row)
        children.setdefault(child["entry_id"], []).append(child)
    return children


def _track_uris_for_entry(conn, entry_id):
    rows = conn.execute(
        "SELECT spotify_uri FROM tracks WHERE entry_id = ? ORDER BY position",
        (entry_id,),
    ).fetchall()
    return [row["spotify_uri"] for row in rows if _is_track_uri(row["spotify_uri"])]


def _replace_album_tracks(conn, entry, tracks):
    conn.execute("DELETE FROM tracks WHERE entry_id = ?", (entry["row_id"],))
    for position, track in enumerate(tracks):
        uri = track.get("uri")
        if not _is_track_uri(uri):
            continue
        conn.execute(
            """
            INSERT INTO tracks (
                track_id, entry_id, post_url, position, spotify_uri, spotify_artist,
                spotify_name, spotify_link, duration_ms, track_number, disc_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{entry['row_id']}:{position}",
                entry["row_id"],
                entry["post_url"],
                position,
                uri,
                track.get("artist") or entry.get("spotify_artist"),
                track.get("name"),
                track.get("link"),
                track.get("duration_ms"),
                track.get("track_number"),
                track.get("disc_number"),
            ),
        )
    conn.execute(
        "UPDATE entries SET is_album_expanded = 1, child_count = ? WHERE row_id = ?",
        (len([track for track in tracks if _is_track_uri(track.get("uri"))]), entry["row_id"]),
    )


def _is_track_uri(uri):
    return bool(uri and ":track:" in uri)


def _is_album_uri(uri):
    return bool(uri and ":album:" in uri)


def _content_hash(raw_html):
    return sha256((raw_html or "").encode("utf-8")).hexdigest() if raw_html else ""


def _set_cache_meta(conn, key, value):
    conn.execute(
        """
        INSERT INTO cache_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def _read_cache_meta(conn):
    rows = conn.execute("SELECT key, value FROM cache_meta").fetchall()
    return {row["key"]: row["value"] for row in rows}


def _cache_status(meta, posts):
    source = meta.get("cache_source") or ""
    refreshed_at = meta.get("refreshed_at") or ""
    fallback_updated_at = posts[0]["fetched_at"] if posts else ""
    if source == "flowstate" and refreshed_at:
        return {
            "state": "fresh",
            "label": "Last refreshed",
            "value": refreshed_at,
            "needs_refresh": False,
            "is_seeded": False,
        }
    return {
        "state": "stale",
        "label": "Needs refresh",
        "value": fallback_updated_at,
        "needs_refresh": True,
        "is_seeded": False,
    }


def _metadata_from_raw(raw_text):
    match = re.search(r"\((.*?)\)", raw_text or "")
    return match.group(1) if match else ""


def _duration_from_entry_minutes(entries):
    minutes = sum(entry.get("duration_minutes") or 0 for entry in entries)
    return f"{int(minutes)}m" if minutes else ""
