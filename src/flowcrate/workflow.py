import csv
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import LOGS_DIR, PREVIEWS_DIR, ensure_dirs
from .scraper import extract_from_post, get_recent_posts
from .spotify import SpotifyManager

OUTPUT_MODES = {
    "playlist": "Create playlist",
    "queue": "Add to queue",
    "playlist_and_queue": "Create playlist and add to queue",
}

RUN_FIELDNAMES = [
    "row_kind",
    "row_id",
    "parent_id",
    "raw_scraped_text",
    "parsed_artist",
    "parsed_name",
    "parsed_type",
    "source_url",
    "source_date",
    "match_status",
    "match_type",
    "spotify_uri",
    "spotify_artist",
    "spotify_name",
    "spotify_link",
    "failure_reason",
    "is_album_expanded",
    "child_count",
    "track_number",
    "disc_number",
]


@dataclass
class JobStatus:
    id: str
    title: str
    state: str = "queued"
    phase: str = "Queued"
    messages: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def log(self, message):
        self.phase = message
        self.messages.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")


def parse_urls(text):
    urls = []
    for line in re.split(r"[\n,]+", text or ""):
        line = line.strip()
        if line:
            urls.append(line)
    return urls


def source_preset_options():
    return [
        ("paste", "Paste URLs"),
        ("latest", "Latest post"),
        ("last5", "Last 5 posts"),
    ]


def resolve_source_preset(preset, urls_text):
    preset = preset or "paste"
    if preset == "latest":
        posts = get_recent_posts(limit=1)
    elif preset == "last5":
        posts = get_recent_posts(limit=5)
    else:
        urls = parse_urls(urls_text)
        posts = [{"title": "", "url": url, "date": ""} for url in urls]
        preset = "paste"

    return {
        "kind": preset,
        "label": dict(source_preset_options()).get(preset, "Paste URLs"),
        "posts": posts,
        "urls": [post["url"] for post in posts],
    }


def load_job_input(input_val):
    if input_val.startswith("http"):
        return {
            "playlist_name": None,
            "playlist_description": None,
            "playlist_public": False,
            "urls": [input_val],
        }
    path = Path(input_val)
    if not path.exists():
        raise FileNotFoundError(f"Input is not a URL or existing job file: {input_val}")
    job = json.loads(path.read_text(encoding="utf-8"))
    return {
        "playlist_name": job.get("playlist_name") or job.get("name"),
        "playlist_description": job.get("playlist_description") or job.get("description"),
        "playlist_public": bool(job.get("playlist_public") or job.get("public", False)),
        "output_mode": job.get("output_mode") or "playlist",
        "urls": job.get("urls", []),
    }


def scrape_urls(urls, progress=None):
    all_items = []
    first_post_title = ""
    for idx, url in enumerate(urls, start=1):
        if progress:
            progress(f"Scraping {idx}/{len(urls)}")
        items, post_title = extract_from_post(url)
        all_items.extend(items)
        if not first_post_title:
            first_post_title = post_title
        time.sleep(0.5)
    return all_items, first_post_title


def resolve_items(items, spotify=None, progress=None, expand_albums=True):
    spotify = spotify or SpotifyManager()
    processed = []
    for idx, item in enumerate(items, start=1):
        if progress:
            progress(f"Resolving {idx}/{len(items)}: {item.get('artist')} - {item.get('name')}")
        record = {
            "row_kind": "source",
            "row_id": uuid.uuid4().hex,
            "parent_id": None,
            "raw_scraped_text": item.get("raw_text", ""),
            "parsed_artist": item["artist"],
            "parsed_name": item["name"],
            "parsed_type": item["type"],
            "raw_metadata": item.get("metadata", ""),
            "source_url": item["source_url"],
            "source_date": item.get("source_date", ""),
            "match_status": "NOT_FOUND",
            "match_type": "NONE",
            "spotify_uri": None,
            "spotify_artist": None,
            "spotify_name": None,
            "spotify_link": None,
            "failure_reason": None,
            "is_album_expanded": False,
            "children": [],
        }

        search_result = None
        if item.get("spotify_link"):
            search_result = spotify.get_uri_from_link(item["spotify_link"])
        if not search_result or not search_result.get("uri"):
            search_result = spotify.search_item(item["artist"], item["name"], item["type"])
        if search_result:
            record.update(
                {
                    "match_status": search_result.get("status", "NOT_FOUND"),
                    "match_type": search_result.get("match_type", "NONE"),
                    "spotify_uri": search_result.get("uri"),
                    "spotify_artist": search_result.get("spotify_artist"),
                    "spotify_name": search_result.get("spotify_name"),
                    "spotify_link": search_result.get("spotify_link"),
                    "failure_reason": search_result.get("failure_reason"),
                }
            )
        if expand_albums and record.get("match_status") == "FOUND" and _is_album_uri(record.get("spotify_uri")):
            if progress:
                progress(f"Expanding album: {record.get('spotify_artist')} - {record.get('spotify_name')}")
            record["children"] = _album_child_records(record, spotify.get_album_track_rows(record["spotify_uri"]))
            record["is_album_expanded"] = bool(record["children"])
        processed.append(record)
    return processed


def preview_urls(
    urls,
    playlist_name=None,
    playlist_description=None,
    public=False,
    source_preset=None,
    progress=None,
):
    items, first_title = scrape_urls(urls, progress=progress)
    resolved = resolve_items(items, progress=progress)
    source_preset = source_preset or {"kind": "paste", "label": "Paste URLs", "posts": [], "urls": urls}
    resolved_name = playlist_name or _default_playlist_name(source_preset, first_title, urls)
    resolved_description = playlist_description or _default_playlist_description(source_preset, first_title, urls)
    preview = {
        "id": uuid.uuid4().hex,
        "playlist_name": resolved_name,
        "playlist_description": resolved_description,
        "playlist_public": bool(public),
        "output_mode": "playlist",
        "playlist": {
            "name": resolved_name,
            "description": resolved_description,
            "public": bool(public),
        },
        "source_preset": source_preset,
        "urls": urls,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "results": resolved,
    }
    save_preview(preview)
    return preview


def save_preview(preview):
    ensure_dirs()
    path = PREVIEWS_DIR / f"{preview['id']}.json"
    path.write_text(json.dumps(preview, indent=2), encoding="utf-8")
    return path


def load_preview(preview_id):
    path = PREVIEWS_DIR / f"{preview_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Preview not found: {preview_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def create_csv_run_report(results, playlist_name, job_name="web"):
    ensure_dirs()
    safe_name = "".join(c if c.isalnum() else "_" for c in playlist_name).strip("_") or "Flow_State"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"run_{safe_name}_{job_name}_{timestamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_FIELDNAMES)
        writer.writeheader()
        writer.writerows(_flatten_for_csv(results))
    return path


def create_json_run_report(preview, playlist, track_uris, csv_path, job_name="web", output_mode=None, queue_result=None):
    ensure_dirs()
    playlist_name = _preview_playlist_name(preview)
    safe_name = "".join(c if c.isalnum() else "_" for c in playlist_name).strip("_") or "Flow_State"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"run_{safe_name}_{job_name}_{timestamp}.json"
    payload = {
        "schema_version": 2,
        "job_name": job_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_mode": normalize_output_mode(output_mode or preview.get("output_mode")),
        "playlist": {
            "name": playlist_name,
            "description": _preview_playlist_description(preview),
            "public": _preview_playlist_public(preview),
            "spotify": playlist,
            "track_count": len(track_uris),
        },
        "queue": queue_result or {"added": 0},
        "source_preset": preview.get("source_preset", {}),
        "urls": preview.get("urls", []),
        "results": preview.get("results", []),
        "csv_log": csv_path.name,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def run_output_from_preview(preview, progress=None, job_name="web", output_mode=None, spotify=None):
    spotify = spotify or SpotifyManager()
    output_mode = normalize_output_mode(output_mode or preview.get("output_mode"))
    preview["output_mode"] = output_mode
    playlist_name = _preview_playlist_name(preview)
    playlist_description = _preview_playlist_description(preview)
    playlist_public = _preview_playlist_public(preview)

    track_uris = _resolved_track_uris(preview, spotify, progress=progress)
    playlist = {}
    queue_result = {"added": 0}

    if output_mode in {"playlist", "playlist_and_queue"}:
        if progress:
            progress(f"Creating playlist: {playlist_name}")
        playlist = spotify.create_playlist(playlist_name, description=playlist_description, public=playlist_public)
        if track_uris:
            if progress:
                progress(f"Adding {len(track_uris)} tracks to playlist")
            spotify.add_tracks_to_playlist(playlist["id"], track_uris)

    if output_mode in {"queue", "playlist_and_queue"}:
        if progress:
            progress(f"Adding {len(track_uris)} tracks to queue")
        queue_result = spotify.add_tracks_to_queue(track_uris)

    if progress:
        progress("Writing run log")
    csv_path = create_csv_run_report(preview["results"], playlist_name, job_name)
    json_path = create_json_run_report(
        preview,
        playlist,
        track_uris,
        csv_path,
        job_name,
        output_mode=output_mode,
        queue_result=queue_result,
    )
    return {
        "output_mode": output_mode,
        "playlist": playlist,
        "queue": queue_result,
        "track_count": len(track_uris),
        "log_path": str(json_path),
        "log_filename": json_path.name,
        "csv_log_path": str(csv_path),
        "csv_log_filename": csv_path.name,
    }


def create_playlist_from_preview(preview, progress=None, job_name="web"):
    return run_output_from_preview(preview, progress=progress, job_name=job_name, output_mode="playlist")


def run_cli(input_val, playlist_name=None, playlist_description=None, public=False, output_mode=None):
    job_name = "direct_url"
    job = load_job_input(input_val)
    urls = job["urls"]
    if not input_val.startswith("http"):
        job_name = Path(input_val).name
    name = playlist_name or job.get("playlist_name")
    description = playlist_description or job.get("playlist_description")
    is_public = bool(public or job.get("playlist_public", False))
    preview = preview_urls(urls, playlist_name=name, playlist_description=description, public=is_public)
    preview["output_mode"] = normalize_output_mode(output_mode or job.get("output_mode"))
    result = run_output_from_preview(preview, job_name=job_name)
    return preview, result, job_name


def normalize_output_mode(value):
    value = value or "playlist"
    if value not in OUTPUT_MODES:
        raise ValueError(f"Unknown output mode: {value}")
    return value


def _resolved_track_uris(preview, spotify, progress=None):
    track_uris = []
    results = preview["results"]
    for idx, record in enumerate(results, start=1):
        if progress:
            progress(f"Preparing tracks {idx}/{len(results)}")
        if record.get("match_status") != "FOUND" or not record.get("spotify_uri"):
            continue
        uri = record["spotify_uri"]
        if _is_album_uri(uri):
            record["is_album_expanded"] = True
            if not record.get("children"):
                record["children"] = _album_child_records(record, spotify.get_album_track_rows(uri))
            album_tracks = [child["spotify_uri"] for child in record.get("children", []) if child.get("spotify_uri")]
            track_uris.extend(album_tracks)
            logging.info("Expanded %s into %s tracks.", uri, len(album_tracks))
        else:
            track_uris.append(uri)
    return track_uris


def _is_album_uri(uri):
    return bool(uri and ":album:" in uri)


def _album_child_records(parent, tracks):
    children = []
    for track in tracks:
        if not track.get("uri"):
            continue
        children.append(
            {
                "row_kind": "album_track",
                "row_id": uuid.uuid4().hex,
                "parent_id": parent["row_id"],
                "raw_scraped_text": parent.get("raw_scraped_text", ""),
                "parsed_artist": track.get("artist") or parent.get("spotify_artist") or parent.get("parsed_artist"),
                "parsed_name": track.get("name") or "",
                "parsed_type": "track",
                "source_url": parent.get("source_url", ""),
                "source_date": parent.get("source_date", ""),
                "match_status": "FOUND",
                "match_type": "ALBUM_TRACK",
                "spotify_uri": track.get("uri"),
                "spotify_artist": track.get("artist") or parent.get("spotify_artist"),
                "spotify_name": track.get("name"),
                "spotify_link": track.get("link"),
                "duration_ms": track.get("duration_ms"),
                "failure_reason": None,
                "is_album_expanded": False,
                "track_number": track.get("track_number"),
                "disc_number": track.get("disc_number"),
                "children": [],
            }
        )
    return children


def _flatten_for_csv(results):
    rows = []
    for record in results:
        row = _csv_safe_row(record)
        row["child_count"] = len(record.get("children", []))
        rows.append(row)
        for child in record.get("children", []):
            rows.append(_csv_safe_row(child))
    return rows


def _csv_safe_row(record):
    return {field: _csv_value(record.get(field)) for field in RUN_FIELDNAMES}


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _default_playlist_name(source_preset, first_title, urls):
    posts = source_preset.get("posts", [])
    if len(posts) == 1 and posts[0].get("title"):
        return posts[0]["title"]
    if first_title and len(urls) == 1:
        return first_title
    if source_preset.get("kind") == "last5":
        return f"Flow State: Last {len(urls)} posts"
    return f"Flow State Archive {datetime.now().strftime('%Y-%m-%d')}"


def _default_playlist_description(source_preset, first_title, urls):
    count = len(urls)
    if count == 1:
        title = first_title or (source_preset.get("posts") or [{}])[0].get("title") or "a Flow State post"
        return f"Created by Flow Crate from Flow State post: {title}."
    return f"Created by Flow Crate from {count} Flow State posts."


def _preview_playlist_name(preview):
    return preview.get("playlist", {}).get("name") or preview.get("playlist_name") or "Flow Crate playlist"


def _preview_playlist_description(preview):
    return preview.get("playlist", {}).get("description") or preview.get("playlist_description") or "Created by Flow Crate."


def _preview_playlist_public(preview):
    if "playlist" in preview and "public" in preview["playlist"]:
        return bool(preview["playlist"]["public"])
    return bool(preview.get("playlist_public", False))
