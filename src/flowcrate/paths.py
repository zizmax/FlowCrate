import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_STATE_DIR = Path(os.getenv("FLOWCRATE_STATE_DIR", Path.home() / ".flowcrate")).expanduser()
LEGACY_LOCAL_STATE_DIR = Path.home() / ".flowstate"
CONFIG_FILE = LOCAL_STATE_DIR / "config.env"
TOKEN_CACHE = LOCAL_STATE_DIR / "spotify_token.cache"
LEGACY_CONFIG_FILE = LEGACY_LOCAL_STATE_DIR / "config.env"
LEGACY_TOKEN_CACHE = LEGACY_LOCAL_STATE_DIR / "spotify_token.cache"
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
PREVIEWS_DIR = DATA_DIR / "previews"
SEEDS_DIR = DATA_DIR / "seeds"
FLOWCRATE_DB = LOCAL_STATE_DIR / "flowcrate.db"


def ensure_dirs():
    LOCAL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
