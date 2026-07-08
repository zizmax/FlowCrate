import argparse
import logging
import platform
import socket
import subprocess
import threading
import time
import webbrowser
from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from . import __version__, sonos
from .cache import (
    cache_is_stale,
    dashboard_data,
    has_cached_post,
    last_refreshed_at,
    latest_cached_post,
    playlist_name_for_selection,
    refresh_from_flowstate,
    selected_track_uris,
)
from .config import load_config, masked, reset_local_config, save_config
from .logs import list_logs, read_log
from .paths import CONFIG_FILE, LOGS_DIR, PROJECT_ROOT, TOKEN_CACHE, ensure_dirs
from .playback import DevicePickerRequired, resolve_playback_target
from .scraper import get_recent_posts, reset_session_cache, test_flowstate_fetch
from .spotify import SpotifyManager, SpotifyRateLimitError

# Single-flight background refresh: page load, tab focus, and the API may all trigger
# at once; only one refresh ever runs at a time.
_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE = {"running": False}


def _start_background_refresh(limit=11):
    """Start a daemon refresh thread unless one is already running. Returns started?"""
    with _REFRESH_LOCK:
        if _REFRESH_STATE["running"]:
            return False
        _REFRESH_STATE["running"] = True
    thread = threading.Thread(target=_background_refresh_worker, args=(limit,), daemon=True)
    thread.start()
    return True


def _background_refresh_worker(limit):
    try:
        refresh_from_flowstate(limit=limit)
    except Exception:
        logging.exception("Background Flow State refresh failed")
    finally:
        with _REFRESH_LOCK:
            _REFRESH_STATE["running"] = False


def _refresh_status():
    with _REFRESH_LOCK:
        running = _REFRESH_STATE["running"]
    return {"refreshing": running, "refreshed_at": last_refreshed_at(), "stale": cache_is_stale()}


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
        if cache_is_stale():
            _start_background_refresh(limit=11)
        return _render_dashboard()

    @app.route("/api/refresh-status")
    def api_refresh_status():
        return jsonify(_refresh_status())

    @app.route("/api/refresh", methods=["POST"])
    def api_refresh():
        _start_background_refresh(limit=11)
        return jsonify(_refresh_status())

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
            local_hostname=_local_hostname(),
            local_port=request.host.rsplit(":", 1)[1] if ":" in request.host else "80",
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

    @app.route("/api/sonos-devices")
    def api_sonos_devices():
        try:
            devices = sonos.list_speakers(timeout=5)
            return jsonify({"ok": True, "devices": devices})
        except sonos.SonosError as exc:
            return jsonify({"ok": False, "error": str(exc), "devices": []})
        except Exception as exc:
            logging.exception("api/sonos-devices failed")
            error = str(exc)
            if sonos._is_host_unreachable(exc):
                error = sonos._LOCAL_NETWORK_HINT
            return jsonify({"ok": False, "error": error, "devices": []})

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
            _ensure_latest_cached()
            post, entries = latest_cached_post()
            if not post:
                raise RuntimeError("No Flow State posts were found.")
        except Exception as exc:
            logging.exception("api/play-latest cache read failed")
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

        resolved = [(entry, entry["spotify_uri"]) for entry in entries if entry.get("spotify_uri")]
        unresolved = [entry for entry in entries if not entry.get("spotify_uri")]
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
        speak = _build_speak(post, result, len(unresolved), False)
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


def _ensure_latest_cached():
    """Refresh the cache when it is stale or the newest live post is not cached yet.

    Keeps api/play-latest cheap: a full refresh only runs when needed, and the
    latest-post probe is a single lightweight request.
    """
    if cache_is_stale():
        refresh_from_flowstate(limit=2)
        return
    try:
        recent = get_recent_posts(limit=1)
    except Exception as exc:
        logging.warning("api/play-latest latest-post check failed: %s", exc)
        return
    if recent and not has_cached_post(recent[0].get("url")):
        refresh_from_flowstate(limit=2)


def _unique_artists(resolved, limit=8):
    artists = []
    for item, _ in resolved:
        artist = (item.get("spotify_artist") or item.get("artist") or item.get("parsed_artist") or "").strip()
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
    status = _refresh_status()
    return render_template(
        "dashboard.html",
        dashboard=data,
        device_picker=device_picker,
        refreshing=status["refreshing"],
        cache_stale=cache_is_stale(),
    )


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


def _local_hostname():
    """Return the local mDNS hostname, e.g. ``mymac.local``.

    On macOS, gethostname() often returns the DHCP-derived name (like
    ``192-168-0-31.lan``), which other devices cannot resolve; the Bonjour
    name from ``scutil --get LocalHostName`` is what .local resolution uses.
    """
    name = ""
    if platform.system() == "Darwin":
        try:
            name = subprocess.run(
                ["scutil", "--get", "LocalHostName"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip().lower()
        except Exception:
            name = ""
    if not name:
        name = socket.gethostname().lower()
        name = name.removesuffix(".lan").removesuffix(".local")
    return name + ".local"


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
