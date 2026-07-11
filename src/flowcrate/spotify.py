import logging
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .config import load_config
from .paths import LOCAL_STATE_DIR, TOKEN_CACHE, ensure_dirs

SPOTIFY_STATE_FILE = LOCAL_STATE_DIR / "spotify_state.json"


class SpotifyScopeError(RuntimeError):
    pass


class SpotifyRateLimitError(RuntimeError):
    def __init__(self, retry_after=None, retry_until=None):
        self.retry_after = retry_after
        self.retry_until = retry_until
        message = "Spotify is rate-limited"
        if retry_until:
            message = f"{message} until {retry_until}"
        super().__init__(message)


class SpotifyManager:
    def __init__(self):
        ensure_dirs()
        cfg = load_config()
        if not cfg.spotify_client_id or not cfg.spotify_client_secret:
            raise ValueError("Missing Spotify Client ID or Client Secret in Settings.")

        self.scope = (
            "playlist-modify-private playlist-modify-public playlist-read-private "
            "user-modify-playback-state user-read-playback-state"
        )
        self.auth_manager = SpotifyOAuth(
            client_id=cfg.spotify_client_id,
            client_secret=cfg.spotify_client_secret,
            redirect_uri=cfg.spotify_redirect_uri,
            scope=self.scope,
            cache_path=str(TOKEN_CACHE),
            show_dialog=True,
        )
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)
        self.user_info = self.sp.current_user()
        self.user_id = self.user_info["id"]
        self.display_name = self.user_info.get("display_name", "Unknown")
        logging.info("Authenticated as %s (%s)", self.display_name, self.user_id)

    def create_playlist(self, name, description="Created by Flow Crate", public=False):
        payload = {
            "name": name,
            "public": public,
            "description": description or "",
        }
        playlist = self._post("me/playlists", payload=payload)
        return {
            "id": playlist["id"],
            "url": playlist.get("external_urls", {}).get("spotify", ""),
            "name": playlist.get("name", name),
        }

    @staticmethod
    def _normalize(text):
        if not text:
            return ""
        text = re.sub(r"\(.*?\)|\[.*?\]", "", text)
        return re.sub(r"[^\w\s]", "", text).lower().strip()

    def _is_match(self, search_artist, search_name, found_item):
        found_artists = [a["name"].lower() for a in found_item.get("artists", [])]
        found_name = found_item.get("name", "").lower()

        norm_search_artist = self._normalize(search_artist)
        norm_search_name = self._normalize(search_name)
        norm_found_name = self._normalize(found_name)

        artist_match = False
        search_artist_parts = set(norm_search_artist.split())
        for found_artist in found_artists:
            found_artist_norm = self._normalize(found_artist)
            if norm_search_artist in found_artist_norm or found_artist_norm in norm_search_artist:
                artist_match = True
                break
            if search_artist_parts & set(found_artist_norm.split()):
                artist_match = True
                break

        if not artist_match:
            return False, f"Artist mismatch: expected '{search_artist}', found '{', '.join(found_artists)}'"

        search_name_parts = set(norm_search_name.split())
        found_name_parts = set(norm_found_name.split())
        if norm_search_name in norm_found_name or norm_found_name in norm_search_name:
            return True, "Match"
        if search_name_parts and len(search_name_parts & found_name_parts) >= (len(search_name_parts) / 2):
            return True, "Match"
        return False, f"Name mismatch: expected '{search_name}', found '{found_name}'"

    def search_item(self, artist, name, item_type="track"):
        result = {
            "uri": None,
            "spotify_name": None,
            "spotify_artist": None,
            "spotify_link": None,
            "status": "NOT_FOUND",
            "match_type": "NONE",
            "failure_reason": None,
        }

        for query, match_type in [
            (f"artist:{artist} {item_type}:{name}", "SEARCH_STRICT"),
            (f"{artist} {name}", "SEARCH_BROAD"),
        ]:
            logging.info("Searching Spotify: %s", query)
            results = self._with_retry(lambda: self.sp.search(q=query, limit=1, type=item_type))
            items = results.get(f"{item_type}s", {}).get("items", [])
            if not items:
                continue
            item = items[0]
            is_match, reason = self._is_match(artist, name, item)
            if is_match:
                result.update(
                    {
                        "uri": item["uri"],
                        "spotify_name": item["name"],
                        "spotify_artist": ", ".join(a["name"] for a in item.get("artists", [])),
                        "spotify_link": item.get("external_urls", {}).get("spotify"),
                        "status": "FOUND",
                        "match_type": match_type,
                    }
                )
                return result
            result.update(
                {
                    "status": "MISMATCH_REJECTED",
                    "match_type": match_type,
                    "failure_reason": reason,
                    "spotify_name": item.get("name"),
                    "spotify_artist": ", ".join(a["name"] for a in item.get("artists", [])),
                    "spotify_link": item.get("external_urls", {}).get("spotify"),
                }
            )
            logging.warning("Spotify match rejected: %s", reason)

        return result

    def get_album_tracks(self, album_uri):
        return [track["uri"] for track in self.get_album_track_rows(album_uri)]

    def get_album_track_rows(self, album_uri):
        tracks = []
        results = self._with_retry(lambda: self.sp.album_tracks(album_uri))
        tracks.extend(self._album_track_row(t) for t in results.get("items", []))
        while results.get("next"):
            results = self._with_retry(lambda: self.sp.next(results))
            tracks.extend(self._album_track_row(t) for t in results.get("items", []))
        return tracks

    @staticmethod
    def _album_track_row(track):
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        link = track.get("external_urls", {}).get("spotify")
        return {
            "uri": track.get("uri"),
            "name": track.get("name"),
            "artist": artists,
            "link": link,
            "track_number": track.get("track_number"),
            "disc_number": track.get("disc_number"),
            "duration_ms": track.get("duration_ms"),
        }

    def add_tracks_to_playlist(self, playlist_id, track_uris):
        for i in range(0, len(track_uris), 100):
            self._post(f"playlists/{playlist_id}/items", payload={"uris": track_uris[i : i + 100]})

    def add_tracks_to_queue(self, track_uris, device_id=None):
        added = 0
        for uri in track_uris:
            args = {"uri": uri}
            if device_id:
                args["device_id"] = device_id
            self._post("me/player/queue", args=args)
            added += 1
        return {"added": added, "device_id": device_id}

    def get_available_devices(self):
        try:
            return self._with_retry(lambda: self.sp.devices()).get("devices", [])
        except SpotifyException as exc:
            self._raise_scope_error(exc)
            raise

    def get_playback_state(self):
        try:
            return self._with_retry(lambda: self.sp.current_playback())
        except SpotifyException as exc:
            self._raise_scope_error(exc)
            raise

    def start_playback(self, track_uris, device_id=None):
        if not track_uris:
            return {"started": 0, "device_id": device_id}
        payload = {"uris": track_uris}
        args = {"device_id": device_id} if device_id else None
        self._put("me/player/play", args=args, payload=payload)
        return {"started": len(track_uris), "device_id": device_id}

    def transfer_playback(self, device_id, play=False):
        self._put("me/player", payload={"device_ids": [device_id], "play": bool(play)})
        return {"device_id": device_id, "play": bool(play)}

    def _post(self, url, args=None, payload=None):
        try:
            return self._with_retry(lambda: self.sp._post(url, args=args, payload=payload))
        except SpotifyException as exc:
            self._raise_scope_error(exc)
            raise

    def _put(self, url, args=None, payload=None):
        try:
            return self._with_retry(lambda: self.sp._put(url, args=args, payload=payload))
        except SpotifyException as exc:
            self._raise_scope_error(exc)
            raise

    def _with_retry(self, call, attempts=4):
        try:
            return call()
        except SpotifyException as exc:
            if getattr(exc, "http_status", None) == 429:
                delay = _retry_after_seconds(exc) or 60
                retry_until = set_spotify_rate_limit(delay)
                logging.warning("Spotify rate limit active until %s.", retry_until)
                raise SpotifyRateLimitError(retry_after=delay, retry_until=retry_until) from exc
            raise

    @staticmethod
    def _raise_scope_error(exc):
        text = f"{exc}".lower()
        if getattr(exc, "http_status", None) == 403 and ("scope" in text or "permission" in text):
            raise SpotifyScopeError(
                "Spotify rejected the request because the saved auth token is missing required scopes. "
                "Run `flowcrate --reset`, then reconnect Spotify."
            ) from exc

    def get_uri_from_link(self, spotify_link):
        parsed = parse_spotify_url(spotify_link)
        if not parsed:
            return None
        item_type = parsed["item_type"]
        item_id = parsed["item_id"]
        uri = parsed["uri"]

        try:
            info = self._with_retry(lambda: self.sp.track(item_id) if item_type == "track" else self.sp.album(item_id))
            return {
                "uri": uri,
                "spotify_name": info["name"],
                "spotify_artist": ", ".join(a["name"] for a in info.get("artists", [])),
                "spotify_link": info.get("external_urls", {}).get("spotify"),
                "status": "FOUND",
                "match_type": "DIRECT_LINK",
                "failure_reason": None,
            }
        except Exception as exc:
            logging.warning("Could not inspect direct Spotify link %s: %s", spotify_link, exc)
            return {"uri": uri, "status": "FOUND", "match_type": "DIRECT_LINK", "failure_reason": None}


def parse_spotify_url(spotify_link):
    """Parse a Spotify open URL into a local URI without calling Spotify."""
    if not spotify_link or "open.spotify.com" not in spotify_link:
        return None
    parsed = urlparse(spotify_link)
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts and path_parts[0].startswith("intl-"):
        path_parts = path_parts[1:]
    if len(path_parts) < 2 or path_parts[0] not in {"album", "track"}:
        return None
    item_type, item_id = path_parts[0], path_parts[1]
    return {
        "item_type": item_type,
        "item_id": item_id,
        "uri": f"spotify:{item_type}:{item_id}",
        "spotify_link": f"https://open.spotify.com/{item_type}/{item_id}",
    }


def set_spotify_rate_limit(retry_after_seconds):
    ensure_dirs()
    retry_until_dt = datetime.now() + timedelta(seconds=max(float(retry_after_seconds or 0), 0))
    retry_until = retry_until_dt.isoformat(timespec="seconds")
    SPOTIFY_STATE_FILE.write_text(
        f'{{"state":"rate_limited","retry_until":"{retry_until}"}}',
        encoding="utf-8",
    )
    return retry_until


def spotify_service_state():
    try:
        raw = SPOTIFY_STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"state": "ok", "retry_until": "", "active": False, "message": ""}
    except Exception:
        return {"state": "unknown", "retry_until": "", "active": False, "message": ""}

    import json

    try:
        data = json.loads(raw)
    except Exception:
        return {"state": "unknown", "retry_until": "", "active": False, "message": ""}
    retry_until = data.get("retry_until") or ""
    active = False
    if retry_until:
        try:
            active = datetime.fromisoformat(retry_until) > datetime.now()
        except ValueError:
            active = False
    message = ""
    if active and data.get("state") == "rate_limited":
        message = f"Spotify is rate-limited until {retry_until}. Flow State cache rows remain available."
    return {
        "state": data.get("state") or "ok",
        "retry_until": retry_until,
        "active": active,
        "message": message,
    }


def _retry_after_seconds(exc):
    headers = getattr(exc, "headers", None) or {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        return max(float(value), 0)
    except (TypeError, ValueError):
        return None
