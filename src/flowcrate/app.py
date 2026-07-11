import argparse
import logging
import logging.handlers
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

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
from .config import load_config, masked, reset_local_config, save_config, save_config_values
from .logs import list_logs, read_log
from .paths import CONFIG_FILE, LOGS_DIR, TOKEN_CACHE, ensure_dirs
from .playback import DevicePickerRequired, resolve_playback_target
from .scraper import (
    browser_from_user_agent,
    check_flowstate_access,
    get_recent_posts,
    reset_session_cache,
    set_preferred_browser,
    test_flowstate_fetch,
)
from .shortcut import (
    UNIVERSAL_HOST_PLACEHOLDER,
    UNIVERSAL_TOKEN,
    ShortcutError,
    signed_shortcut,
    unsigned_shortcut,
)
from .spotify import SpotifyManager, SpotifyRateLimitError

# Single-flight background refresh: page load, tab focus, and the API may all trigger
# at once; only one refresh ever runs at a time.
_REFRESH_LOCK = threading.Lock()
_REFRESH_STATE = {"running": False}

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _configure_file_logging():
    """Attach a rotating file handler to the root logger writing server.log.

    Werkzeug's request-line logger propagates to the root logger, so its lines
    land in the file too. Idempotent: repeated calls (e.g. from tests that build
    the app many times) do not stack duplicate handlers.
    """
    ensure_dirs()
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_flowcrate_file_handler", False):
            return
    handler = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler._flowcrate_file_handler = True
    root.addHandler(handler)
    # Ensure records actually reach the handler even if basicConfig ran later.
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)


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
    _configure_file_logging()
    app = Flask(__name__)
    app.secret_key = "flowcrate-local-only"

    @app.before_request
    def _detect_browser():
        ua = request.headers.get("User-Agent", "")
        name = browser_from_user_agent(ua)
        if name:
            set_preferred_browser(name)

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
            local_port=_local_port(),
            is_macos=platform.system() == "Darwin",
            universal_host_placeholder=UNIVERSAL_HOST_PLACEHOLDER,
            universal_token=UNIVERSAL_TOKEN,
        )

    @app.route("/api/test-substack", methods=["POST"])
    def api_test_substack():
        try:
            proof = test_flowstate_fetch()
            return jsonify(
                {
                    "ok": True,
                    "category": "success",
                    "message": f"Flow State fetch succeeded: {proof['title']}.",
                }
            )
        except Exception as exc:
            return jsonify(
                {
                    "ok": False,
                    "category": "error",
                    "message": f"Substack test failed: {exc}",
                }
            )

    @app.route("/api/flowstate-access")
    def api_flowstate_access():
        try:
            result = check_flowstate_access()
            if result.get("scanned", 0) > 11:
                _start_background_refresh(limit=result["scanned"])
            return jsonify(result)
        except Exception as exc:
            logging.exception("api/flowstate-access failed")
            return jsonify({"status": "none", "message": f"No access — unexpected error: {exc}"})

    @app.route("/api/session", methods=["GET", "POST"])
    def api_session():
        """Receive (POST) or report (GET) Flow State session cookies.

        Lets a headless install (e.g. a Raspberry Pi with no browser to read
        cookies from) get an authenticated session: you run a small command on the
        machine where you're logged in, and it POSTs the cookies here (authorized
        with the same API token as ``/api/play-latest``). GET lets the open
        Settings page poll for a session that was just synced from another device.
        """
        cfg = load_config()
        if request.method == "GET":
            return jsonify(
                {
                    "has_session": bool(cfg.flowstate_connect_sid or cfg.substack_sid),
                    "connect_sid": cfg.flowstate_connect_sid,
                    "substack_sid": cfg.substack_sid,
                }
            )
        if not cfg.api_token:
            return jsonify({"ok": False, "error": "Set an API token in Settings first."}), 400
        if request.headers.get("X-FlowCrate-Token") != cfg.api_token:
            return jsonify({"ok": False, "error": "Invalid or missing API token."}), 401
        data = request.get_json(silent=True) or {}
        connect_sid = (data.get("connect_sid") or "").strip()
        substack_sid = (data.get("substack_sid") or "").strip()
        if not connect_sid and not substack_sid:
            return jsonify({"ok": False, "error": "No session cookies provided."}), 400
        updates = {}
        if connect_sid:
            updates["FLOWSTATE_CONNECT_SID"] = connect_sid
        if substack_sid:
            updates["SUBSTACK_SID"] = substack_sid
        save_config_values(updates)
        reset_session_cache()
        logging.info("Session cookies synced via /api/session: %s", ", ".join(sorted(updates)))
        return jsonify({"ok": True, "message": "Session updated.", "received": sorted(updates)})

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
        text_log = filename.lower().endswith((".log", ".txt"))
        return render_template(
            "log_detail.html", filename=filename, rows=rows, status=status, text_log=text_log
        )

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

    @app.route("/api/siri-shortcut")
    def api_siri_shortcut():
        """Personalized shortcut with this install's real URL + token baked in.

        On macOS we sign it (imports with no fuss). On any other host there's no
        signer, so we return it unsigned — ready to use, but the device needs
        "Allow Untrusted Shortcuts" enabled to import it.
        """
        cfg = load_config()
        if not cfg.api_token:
            return jsonify({"ok": False, "error": "Set an API token in Settings first."}), 400
        url = f"http://{_local_hostname()}:{_local_port()}/api/play-latest"
        if platform.system() == "Darwin":
            try:
                data = signed_shortcut(url, cfg.api_token)
            except ShortcutError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 502
        else:
            data = unsigned_shortcut(url, cfg.api_token)
        return Response(
            data,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="Play Flow Crate.shortcut"'},
        )

    @app.route("/api/siri-shortcut/universal")
    def api_siri_shortcut_universal():
        """Pre-signed universal shortcut (placeholder URL/token, edited on-device).

        Lets a non-macOS host still offer a one-tap, iOS-Safari-importable signed
        shortcut; the user swaps in their host + token once after importing.
        """
        path = os.path.join(app.static_folder, "play-flow-crate-universal.shortcut")
        if not os.path.exists(path):
            return jsonify({"ok": False, "error": "Universal shortcut is not bundled in this build."}), 404
        return send_file(
            path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name="Play Flow Crate.shortcut",
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
        ("Config file", str(CONFIG_FILE), CONFIG_FILE.exists()),
        ("Logs folder", str(LOGS_DIR), LOGS_DIR.exists()),
        ("Spotify Client ID", masked(cfg.spotify_client_id), bool(cfg.spotify_client_id)),
        ("Spotify Client Secret", masked(cfg.spotify_client_secret), bool(cfg.spotify_client_secret)),
        ("Spotify redirect URI", cfg.spotify_redirect_uri, bool(cfg.spotify_redirect_uri)),
        (
            "Flow State session cookie",
            masked(cfg.flowstate_connect_sid or cfg.substack_sid)
            if (cfg.flowstate_connect_sid or cfg.substack_sid)
            else "Optional",
            True,
        ),
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


def _local_port():
    """Return the port from the current request's host, defaulting to 80."""
    return request.host.rsplit(":", 1)[1] if ":" in request.host else "80"


def _local_ip():
    """Best-effort primary LAN IPv4 address (the one used for outbound traffic).

    Uses a UDP socket to a public address to discover which interface/IP the OS
    would route through; no packets are actually sent. Returns None on failure.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


def _reset_local_state():
    """Delete local config and Spotify auth token after an interactive confirmation."""
    print("This will delete the following local files (logs and previews are kept):")
    print(f"  Config file:         {CONFIG_FILE}")
    print(f"  Spotify token cache: {TOKEN_CACHE}")
    if input("Type RESET to confirm: ").strip() != "RESET":
        print("Reset cancelled.")
        return
    removed = reset_local_config()
    print(f"Started fresh. Removed {len(removed)} local config/auth file(s).")


def _ansi_enabled():
    """True when it's safe to emit ANSI escapes to stdout.

    Disabled when stdout is not a terminal (e.g. captured to a launchd log file),
    when NO_COLOR is set, or for dumb terminals — so log files stay clean text.
    """
    return (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )


def _style(text, *codes):
    """Wrap text in the given SGR codes, or return it unchanged if ANSI is off."""
    if not codes or not _ansi_enabled():
        return text
    return "\033[" + ";".join(codes) + "m" + text + "\033[0m"


def _hyperlink(url, label=None):
    """Render an OSC 8 clickable hyperlink, falling back to plain text."""
    label = label or url
    if not _ansi_enabled():
        return label
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


def _linkify(url):
    """Styled (underlined cyan) clickable link where the terminal supports it."""
    return _hyperlink(url, _style(url, "4", "36"))


def _print_startup_banner(url, extra_urls=()):
    ctrl_c = _style("Ctrl+C", "1", "33")
    install = _style("flowcrate --install-service", "1", "32")
    uninstall = _style("flowcrate --uninstall-service", "1", "32")
    print(f"{_style('Flow Crate', '1')} {__version__} — {_linkify(url)}")
    for extra in extra_urls:
        print(f"     also reachable at {_linkify(extra)}")
    print(f"Running in the foreground; press {ctrl_c} to stop.")
    print(f"Tip: run it permanently in the background with {install}")
    print(f"     (it starts at login and restarts itself; remove with {uninstall}).")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Flow Crate local web UI.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete local config and Spotify auth token, then exit (asks for confirmation).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show per-request and debug logs.",
    )
    service_group = parser.add_mutually_exclusive_group()
    service_group.add_argument(
        "--install-service",
        action="store_true",
        help="Install and load a macOS launchd agent so Flow Crate runs at login.",
    )
    service_group.add_argument(
        "--uninstall-service",
        action="store_true",
        help="Unload and remove the macOS launchd agent.",
    )
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=_LOG_FORMAT)
    else:
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if args.reset:
        _reset_local_state()
        return

    if args.install_service:
        from . import service

        service.install_service(url=f"http://{_local_hostname()}:{args.port}")
        return
    if args.uninstall_service:
        from . import service

        service.uninstall_service()
        return

    app = create_app()
    if args.host == "0.0.0.0":
        # Bound to all interfaces: lead with the network address (so a headless
        # host prints a URL other devices can actually use), then the IP and
        # localhost as fallbacks.
        primary = f"http://{_local_hostname()}:{args.port}"
        extras = []
        ip = _local_ip()
        if ip:
            extras.append(f"http://{ip}:{args.port}")
        extras.append(f"http://localhost:{args.port}")
        open_url = f"http://localhost:{args.port}"
    else:
        primary = f"http://{args.host}:{args.port}"
        extras = []
        open_url = primary
    _print_startup_banner(primary, extras)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(open_url)).start()
    # Never enable Flask debug mode: the Werkzeug debugger allows code execution
    # and the server binds to the LAN. --verbose only raises log verbosity.
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
