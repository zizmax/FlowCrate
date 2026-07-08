"""Spike: play the latest Flow State post on a Sonos speaker via SoCo ShareLink.

Run from the project root while on the same Wi-Fi as the speakers:

    PYTHONPATH=src .venv/bin/python scripts/sonos_spike.py            # list speakers
    PYTHONPATH=src .venv/bin/python scripts/sonos_spike.py --room "Living Room"
    PYTHONPATH=src .venv/bin/python scripts/sonos_spike.py --room "Living Room" --no-play

If discovery finds nothing at home, check System Settings -> Privacy &
Security -> Local Network and enable it for your terminal app, then retry.
"""
import argparse
import logging
import sys

import soco
from soco.discovery import scan_network
from soco.plugins.sharelink import ShareLinkPlugin

from flowcrate.scraper import extract_source_post, get_recent_posts
from flowcrate.spotify import parse_spotify_url


def discover_speakers():
    zones = soco.discover(timeout=10) or scan_network(scan_timeout=2.0) or set()
    return sorted(zones, key=lambda z: z.player_name)


def resolve_uris(items):
    """Direct links become URIs for free; unlinked items use Spotify search if configured."""
    uris, unresolved = [], []
    searcher = None
    for item in items:
        parsed = parse_spotify_url(item.get("spotify_link"))
        if parsed:
            uris.append((item, parsed["uri"]))
            continue
        if searcher is None:
            try:
                from flowcrate.spotify import SpotifyManager

                searcher = SpotifyManager()
            except Exception as exc:
                print(f"Spotify search unavailable ({exc}); skipping unlinked items.")
                searcher = False
        if searcher:
            found = searcher.search_item(item["artist"], item["name"], item.get("type", "track"))
            if found["status"] == "FOUND":
                uris.append((item, found["uri"]))
                continue
        unresolved.append(item)
    return uris, unresolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", help="Sonos room/speaker name to play on")
    parser.add_argument("--no-play", action="store_true", help="Queue only, don't start playback")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    speakers = discover_speakers()
    if not speakers:
        sys.exit("No Sonos speakers found. Are you on the home Wi-Fi? Check Local Network permission.")

    print("Speakers found:")
    for z in speakers:
        print(f"  - {z.player_name} ({z.ip_address}), coordinator={z.group.coordinator.player_name}")
    if not args.room:
        print("\nRe-run with --room <name> to queue and play the latest post.")
        return

    matches = [z for z in speakers if z.player_name.lower() == args.room.lower()]
    if not matches:
        sys.exit(f"No speaker named {args.room!r}.")
    speaker = matches[0].group.coordinator
    print(f"\nTargeting {speaker.player_name} (group coordinator).")

    latest = get_recent_posts(limit=1)[0]
    print(f"Latest post: {latest['title']} ({latest['date']})")
    parsed = extract_source_post(latest["url"])
    uris, unresolved = resolve_uris(parsed["items"])
    if not uris:
        sys.exit("No playable Spotify URIs resolved from the post.")
    print(f"Resolved {len(uris)} items; {len(unresolved)} unresolved.")

    sharelink = ShareLinkPlugin(speaker)
    first_position = None
    for item, uri in uris:
        position = sharelink.add_share_link_to_queue(uri)
        if first_position is None:
            first_position = position
        print(f"  queued #{position}: {item['artist']} — {item['name']} ({uri})")

    if args.no_play:
        print("Queued only (--no-play). Done.")
        return
    speaker.play_from_queue(first_position - 1)  # play_from_queue is 0-based
    print(f"Playing from queue position {first_position} on {speaker.player_name}. SPIKE SUCCESS.")


if __name__ == "__main__":
    main()
