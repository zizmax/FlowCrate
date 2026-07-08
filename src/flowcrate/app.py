import argparse
import logging
import threading
import time
import webbrowser
from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from . import __version__, sonos
from .cache import dashboard_data, playlist_name_for_selection, refresh_from_flowstate, selected_track_uris
from .config import load_config, masked, reset_local_config, save_config
from .logs import list_logs, read_log
from .paths import CONFIG_FILE, LOGS_DIR, PROJECT_ROOT, TOKEN_CACHE, ensure_dirs
from .playback import DevicePickerRequired, resolve_playback_target
from .scraper import extract_source_post, get_recent_posts, reset_session_cache, test_flowstate_fetch
from .spotify import SpotifyManager, SpotifyRateLimitError, parse_spotify_url
from .workflow import (
    JobStatus,
    load_preview,
    normalize_output_mode,
    preview_urls,
    resolve_source_preset,
    run_output_from_preview,
    save_preview,
    source_preset_options,
)

JOBS = {}
JOBS_LOCK = threading.Lock()


def create_app():
    ensure_dirs()
    app = Flask(__name__)
    app.secret_key = "flowcrate-local-only"

    @app.template_filter("fmt_time")
    def fmt_time(value):
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    @app.context_processor
    def inject_globals():
        cfg = load_config()
        return {
            "config": cfg,
            "masked": masked,
            "version": __version__,
        }

    @app.route("/")
    def index():
        return _render_dashboard()

    @app.route("/refresh", methods=["POST"])
    def refresh_dashboard():
        try:
            count = refresh_from_flowstate(limit=11)
            flash(f"Refreshed Flow State cache with {count} post(s).", "success")
        except Exception as exc:
            logging.exception("Flow State refresh failed")
            flash(f"Refresh failed: {exc}", "error")
        return redirect(url_for("index"))

    @app.route("/dashboard/action", methods=["POST"])
    def dashboard_action():
        action = request.form.get("action", "")
        entry_ids = request.form.getlist("entry_id")
        device_id = request.form.get("device_id") or None
        if not entry_ids:
            flash("Select at least one playable row.", "error")
            return redirect(url_for("index"))

        try:
            spotify = SpotifyManager()
            track_uris = selected_track_uris(entry_ids, spotify=spotify, expand_albums=True)
            if not track_uris:
                flash("The selected rows do not have Spotify tracks.", "error")
                return redirect(url_for("index"))
            if action == "play":
                target = resolve_playback_target(spotify, requested_device_id=device_id)
                spotify.start_playback(track_uris, device_id=target.device_id)
                flash(f"Playing {len(track_uris)} track(s) on {target.name}.", "success")
            elif action == "queue":
                target = resolve_playback_target(spotify, requested_device_id=device_id)
                result = spotify.add_tracks_to_queue(track_uris, device_id=target.device_id)
                flash(f"Queued {result['added']} track(s) on {target.name}.", "success")
            elif action == "playlist":
                name = request.form.get("playlist_name", "").strip() or playlist_name_for_selection(entry_ids)
                playlist = spotify.create_playlist(
                    name,
                    description="Created by Flow Crate from selected Flow State rows.",
                    public=False,
                )
                spotify.add_tracks_to_playlist(playlist["id"], track_uris)
                flash(f"Created playlist: {playlist.get('name') or name}.", "success")
            else:
                flash("Unknown dashboard action.", "error")
        except DevicePickerRequired as exc:
            return _render_dashboard(
                device_picker={
                    "devices": exc.devices,
                    "action": action,
                    "entry_ids": entry_ids,
                    "track_count": len(track_uris),
                }
            )
        except SpotifyRateLimitError as exc:
            flash(str(exc), "warning")
        except Exception as exc:
            logging.exception("Dashboard action failed")
            flash(f"Action failed: {exc}", "error")
        return redirect(url_for("index"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        cfg = load_config()
        test_results = {}
        if request.method == "POST":
            cfg = save_config(request.form)
            reset_session_cache()
            action = request.form.get("action", "save")
            if action == "test_spotify":
                try:
                    spotify = SpotifyManager()
                    test_results["spotify"] = {
                        "category": "success",
                        "message": f"Connected as {spotify.display_name}.",
                    }
                except Exception as exc:
                    test_results["spotify"] = {"category": "error", "message": f"Spotify test failed: {exc}"}
            elif action == "test_substack":
                try:
                    proof = test_flowstate_fetch()
                    test_results["substack"] = {
                        "category": "success",
                        "message": f"Flow State fetch succeeded: {proof['title']}.",
                    }
                except Exception as exc:
                    test_results["substack"] = {"category": "error", "message": f"Substack test failed: {exc}"}
            else:
                flash("Settings saved.", "success")
            cfg = load_config()
        return render_template(
            "settings.html",
            cfg=cfg,
            config_file=CONFIG_FILE,
            checks=_status_checks(),
            test_results=test_results,
        )

    @app.route("/settings/reset", methods=["POST"])
    def reset_settings():
        confirmation = request.form.get("confirmation", "").strip()
        if confirmation != "RESET":
            flash("Type RESET to start fresh.", "error")
            return redirect(url_for("settings"))
        removed = reset_local_config()
        reset_session_cache()
        flash(f"Started fresh. Removed {len(removed)} local config/auth file(s); logs and previews were kept.", "success")
        return redirect(url_for("settings"))

    @app.route("/setup")
    def setup():
        return redirect(url_for("settings"))

    @app.route("/create", methods=["GET"])
    def create_playlist():
        return render_template("create.html", source_presets=source_preset_options())

    @app.route("/preview", methods=["POST"])
    def preview():
        try:
            source_preset = resolve_source_preset(request.form.get("source_preset", "paste"), request.form.get("urls", ""))
            urls = source_preset["urls"]
            playlist_name = request.form.get("playlist_name", "").strip() or None
            playlist_description = request.form.get("playlist_description", "").strip() or None
            playlist_public = request.form.get("playlist_public") == "public"
            if not urls:
                flash("Paste at least one Flow State URL.", "error")
                return redirect(url_for("create_playlist"))
            messages = []

            def progress(message):
                messages.append(message)

            preview_data = preview_urls(
                urls,
                playlist_name=playlist_name,
                playlist_description=playlist_description,
                public=playlist_public,
                source_preset=source_preset,
                progress=progress,
            )
            flash(f"Preview complete: {len(preview_data['results'])} items found.", "success")
            return redirect(url_for("preview_detail", preview_id=preview_data["id"]))
        except Exception as exc:
            logging.exception("Preview failed")
            flash(f"Preview failed: {exc}", "error")
            return redirect(url_for("create_playlist"))

    @app.route("/preview/<preview_id>")
    def preview_detail(preview_id):
        try:
            preview_data = load_preview(preview_id)
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("create_playlist"))
        return render_template(
            "preview.html",
            preview=preview_data,
            counts=_status_counts(preview_data["results"]),
            track_count=_track_count(preview_data["results"]),
        )

    @app.route("/preview/<preview_id>/create", methods=["POST"])
    def create_from_preview(preview_id):
        try:
            preview_data = load_preview(preview_id)
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("create_playlist"))

        try:
            output_mode = normalize_output_mode(request.form.get("output_mode", "playlist"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("preview_detail", preview_id=preview_id))
        preview_data["output_mode"] = output_mode
        save_preview(preview_data)
        playlist_title = preview_data.get("playlist", {}).get("name") or preview_data.get("playlist_name", "Flow Crate playlist")
        job = JobStatus(id=preview_id, title=playlist_title)
        with JOBS_LOCK:
            JOBS[job.id] = job

        thread = threading.Thread(target=_run_create_job, args=(job.id, preview_data), daemon=True)
        thread.start()
        return redirect(url_for("job_detail", job_id=job.id))

    @app.route("/jobs/<job_id>")
    def job_detail(job_id):
        job = _get_job(job_id)
        if not job:
            flash("Job not found.", "error")
            return redirect(url_for("create_playlist"))
        return render_template("job.html", job=job)

    @app.route("/jobs/<job_id>/status")
    def job_status(job_id):
        job = _get_job(job_id)
        if not job:
            return "Job not found.", 404
        return render_template("_job_status.html", job=job)

    @app.route("/logs")
    def logs():
        return render_template("logs.html", logs=list_logs(), logs_dir=LOGS_DIR)

    @app.route("/logs/<filename>")
    def log_detail(filename):
        status = request.args.get("status", "")
        try:
            rows = read_log(filename, status_filter=status)
        except Exception as exc:
            flash(str(exc), "error")
            return redirect(url_for("logs"))
        return render_template("log_detail.html", filename=filename, rows=rows, status=status)

    @app.route("/status")
    def status():
        return redirect(url_for("settings"))

    @app.route("/api/play-latest", methods=["POST"])
    def api_play_latest():
        cfg = load_config()
        if not cfg.api_token:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "API access is not configured.",
                        "speak": "Flow Crate is not set up for Siri yet. Set an API token in Settings.",
                    }
                ),
                403,
            )
        if request.headers.get("X-FlowCrate-Token") != cfg.api_token:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Invalid or missing API token.",
                        "speak": "Sorry, Flow Crate could not verify that request.",
                    }
                ),
                401,
            )

        body = request.get_json(silent=True) or {}
        play = body.get("play", True)
        room = (body.get("room") or "").strip() or cfg.sonos_room or None

        try:
            posts = get_recent_posts(limit=1)
            if not posts:
                raise RuntimeError("No Flow State posts were found.")
            post = posts[0]
            parsed = extract_source_post(post["url"])
            items = parsed.get("items", [])
        except Exception as exc:
            logging.exception("api/play-latest scrape failed")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Could not read the latest Flow State post: {exc}",
                        "speak": "Sorry, Flow Crate could not read the latest Flow State post.",
                    }
                ),
                502,
            )

        resolved, unresolved, search_unavailable = resolve_post_items(items)
        if not resolved:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "No playable Spotify tracks were found in the latest post.",
                        "speak": "Sorry, Flow Crate found no playable tracks in the latest post.",
                    }
                ),
                400,
            )

        try:
            speaker = sonos.get_speaker(ip=cfg.sonos_ip or None, room=room)
            result = sonos.queue_and_play(speaker, [uri for _, uri in resolved], play=play)
        except sonos.SonosError as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(exc),
                        "speak": f"Sorry, Flow Crate could not reach your Sonos. {exc}",
                    }
                ),
                502,
            )
        except Exception as exc:
            logging.exception("api/play-latest Sonos playback failed")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Sonos playback failed: {exc}",
                        "speak": "Sorry, Flow Crate could not play on your Sonos.",
                    }
                ),
                502,
            )

        artists = _unique_artists(resolved)
        speak = _build_speak(post, result, len(unresolved), search_unavailable)
        return jsonify(
            {
                "ok": True,
                "post": {"title": post.get("title", ""), "date": post.get("date", ""), "url": post.get("url", "")},
                "queued": result["queued"],
                "unresolved": len(unresolved),
                "room": result["room"],
                "artists": artists,
                "speak": speak,
            }
        )

    return app


def resolve_post_items(items):
    """Resolve post items to Spotify URIs without failing on missing credentials.

    Direct spotify_link values become URIs for free. Items without a direct link
    fall back to a Spotify search, constructing SpotifyManager lazily and only once.
    If SpotifyManager cannot be built (no credentials), unlinked items go unresolved.

    Returns (resolved, unresolved, search_unavailable) where resolved is a list of
    (item, uri) pairs and search_unavailable is True when a search was needed but
    Spotify was not configured.
    """
    resolved = []
    unresolved = []
    searcher = None  # None = not yet built, False = unavailable
    search_unavailable = False
    for item in items:
        parsed = parse_spotify_url(item.get("spotify_link"))
        if parsed:
            resolved.append((item, parsed["uri"]))
            continue
        if searcher is None:
            try:
                searcher = SpotifyManager()
            except Exception as exc:
                logging.warning("Spotify search unavailable for api/play-latest: %s", exc)
                searcher = False
                search_unavailable = True
        if searcher:
            try:
                found = searcher.search_item(item.get("artist"), item.get("name"), item.get("type", "track"))
            except Exception as exc:
                logging.warning("Spotify search failed for %s: %s", item.get("name"), exc)
                found = None
            if found and found.get("status") == "FOUND":
                resolved.append((item, found["uri"]))
                continue
        unresolved.append(item)
    return resolved, unresolved, search_unavailable


def _unique_artists(resolved, limit=8):
    artists = []
    for item, _ in resolved:
        artist = (item.get("artist") or "").strip()
        if artist and artist not in artists:
            artists.append(artist)
        if len(artists) >= limit:
            break
    return artists


def _build_speak(post, result, unresolved_count, search_unavailable):
    title = post.get("title") or "the latest Flow State post"
    date = post.get("date") or ""
    queued = result["queued"]
    room = result["room"]
    track_word = "track" if queued == 1 else "tracks"
    if date:
        speak = f"Playing {title} from {date}. {queued} {track_word} on {room}."
    else:
        speak = f"Playing {title}. {queued} {track_word} on {room}."
    if unresolved_count > 0:
        skipped_word = "track was" if unresolved_count == 1 else "tracks were"
        speak += f" {unresolved_count} {skipped_word} skipped."
        if search_unavailable:
            speak += " Connect Spotify to resolve unlinked tracks."
    return speak


def _render_dashboard(device_picker=None):
    data = dashboard_data()
    return render_template(
        "dashboard.html",
        dashboard=data,
        device_picker=device_picker,
    )


def _run_create_job(job_id, preview_data):
    job = _get_job(job_id)
    if not job:
        return
    try:
        job.state = "running"

        def progress(message):
            job.log(message)

        result = run_output_from_preview(preview_data, progress=progress)
        job.result = result
        job.state = "completed"
        job.log("Complete")
    except Exception as exc:
        logging.exception("Create job failed")
        job.error = str(exc)
        job.state = "failed"
        job.log("Failed")


def _get_job(job_id):
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _status_counts(rows):
    counts = {}
    for row in rows:
        key = row.get("match_status") or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _track_count(rows):
    count = 0
    for row in rows:
        if row.get("match_status") != "FOUND" or not row.get("spotify_uri"):
            continue
        children = row.get("children", [])
        if children:
            count += len([child for child in children if child.get("spotify_uri")])
        else:
            count += 1
    return count


def _status_checks():
    cfg = load_config()
    checks = [
        ("Project folder", str(PROJECT_ROOT), PROJECT_ROOT.exists()),
        ("Config file", str(CONFIG_FILE), CONFIG_FILE.exists()),
        ("Logs folder", str(LOGS_DIR), LOGS_DIR.exists()),
        ("Spotify Client ID", masked(cfg.spotify_client_id), bool(cfg.spotify_client_id)),
        ("Spotify Client Secret", masked(cfg.spotify_client_secret), bool(cfg.spotify_client_secret)),
        ("Spotify redirect URI", cfg.spotify_redirect_uri, bool(cfg.spotify_redirect_uri)),
        ("Substack SID fallback", masked(cfg.substack_sid) if cfg.substack_sid else "Optional", True),
        ("Spotify token cache", str(TOKEN_CACHE), TOKEN_CACHE.exists()),
    ]
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Flow Crate local web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    app = create_app()
    url = f"http://{args.host}:{args.port}"
    print(f"Flow Crate is running at {url}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
