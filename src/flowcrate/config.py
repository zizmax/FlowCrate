import os
from dataclasses import dataclass

from dotenv import dotenv_values, load_dotenv

from .paths import CONFIG_FILE, LEGACY_CONFIG_FILE, LEGACY_TOKEN_CACHE, PROJECT_ROOT, TOKEN_CACHE, ensure_dirs


KNOWN_KEYS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI",
    "SUBSTACK_SID",
    "SONOS_IP",
    "SONOS_ROOM",
    "API_TOKEN",
]
LEGACY_ENV_KEYS = [
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
]


@dataclass
class AppConfig:
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    substack_sid: str = ""
    sonos_ip: str = ""
    sonos_room: str = ""
    api_token: str = ""

    @property
    def spotify_ready(self):
        return bool(self.spotify_client_id and self.spotify_client_secret)

    @property
    def values(self):
        return {
            "SPOTIFY_CLIENT_ID": self.spotify_client_id,
            "SPOTIFY_CLIENT_SECRET": self.spotify_client_secret,
            "SPOTIFY_REDIRECT_URI": self.spotify_redirect_uri,
            "SUBSTACK_SID": self.substack_sid,
            "SONOS_IP": self.sonos_ip,
            "SONOS_ROOM": self.sonos_room,
            "API_TOKEN": self.api_token,
        }


def load_config():
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(CONFIG_FILE, override=True)
    return AppConfig(
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID", "") or os.getenv("SPOTIPY_CLIENT_ID", ""),
        spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET", "") or os.getenv("SPOTIPY_CLIENT_SECRET", ""),
        spotify_redirect_uri=(
            os.getenv("SPOTIFY_REDIRECT_URI", "")
            or os.getenv("SPOTIPY_REDIRECT_URI", "")
            or "http://127.0.0.1:8888/callback"
        ),
        substack_sid=os.getenv("SUBSTACK_SID", ""),
        sonos_ip=os.getenv("SONOS_IP", ""),
        sonos_room=os.getenv("SONOS_ROOM", ""),
        api_token=os.getenv("API_TOKEN", ""),
    )


def read_saved_values():
    if not CONFIG_FILE.exists():
        return {}
    return {k: v or "" for k, v in dotenv_values(CONFIG_FILE).items()}


def save_config(form_values):
    ensure_dirs()
    existing = read_saved_values()
    merged = dict(existing)
    for key in KNOWN_KEYS:
        val = form_values.get(key, "")
        if val is None:
            val = ""
        merged[key] = val.strip()
    if not merged.get("SPOTIFY_REDIRECT_URI"):
        merged["SPOTIFY_REDIRECT_URI"] = "http://127.0.0.1:8888/callback"

    lines = []
    for key in KNOWN_KEYS:
        val = merged.get(key, "")
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_config()


def reset_local_config():
    removed = []
    for path in (CONFIG_FILE, TOKEN_CACHE, LEGACY_CONFIG_FILE, LEGACY_TOKEN_CACHE):
        if path.exists():
            path.unlink()
            removed.append(path)
    for key in KNOWN_KEYS + LEGACY_ENV_KEYS:
        os.environ.pop(key, None)
    return removed


def masked(value):
    if not value:
        return "Missing"
    if len(value) <= 8:
        return "Set"
    return f"{value[:4]}...{value[-4:]}"
