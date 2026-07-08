#!/usr/bin/env python3
"""Print setup values from the original FlowState .env for manual copy/paste.

This intentionally prints sensitive values to your terminal. Run it only on your
own machine, and avoid pasting the output into chats, logs, or screenshots.
"""

from pathlib import Path

OLD_ENV = Path.home() / "Desktop" / "FlowState" / ".env"

SETUP_KEYS = [
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_REDIRECT_URI",
    "SUBSTACK_SID",
]

EXTRA_KEYS = [
    "SPOTIPY_CLIENT_ID",
    "SPOTIPY_CLIENT_SECRET",
    "SPOTIPY_REDIRECT_URI",
    "SPOTIFY_SP_DC",
]


def parse_env(path):
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def print_value(label, value):
    if value:
        print(f"{label}: {value}")
    else:
        print(f"{label}: <missing>")


def main():
    if not OLD_ENV.exists():
        raise SystemExit(f"Could not find old env file: {OLD_ENV}")

    values = parse_env(OLD_ENV)

    print(f"Source: {OLD_ENV}")
    print()
    print("Flow Crate setup fields")
    print("-----------------------")
    print_value("Spotify Client ID", values.get("SPOTIFY_CLIENT_ID") or values.get("SPOTIPY_CLIENT_ID"))
    print_value(
        "Spotify Client Secret",
        values.get("SPOTIFY_CLIENT_SECRET") or values.get("SPOTIPY_CLIENT_SECRET"),
    )
    print_value(
        "Spotify Redirect URI",
        values.get("SPOTIFY_REDIRECT_URI") or values.get("SPOTIPY_REDIRECT_URI"),
    )
    print_value("Substack SID", values.get("SUBSTACK_SID"))

    present_extra = [key for key in EXTRA_KEYS if values.get(key)]
    if present_extra:
        print()
        print("Other possibly relevant old values")
        print("----------------------------------")
        for key in present_extra:
            print_value(key, values.get(key))

    print()
    print("Config block")
    print("------------")
    for key in SETUP_KEYS:
        value = values.get(key, "")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        print(f'{key}="{escaped}"')


if __name__ == "__main__":
    main()
