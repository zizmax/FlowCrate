import argparse
import logging
import sys

from .workflow import OUTPUT_MODES, run_cli


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Create Spotify playlists or queue tracks from Flow State posts.")
    parser.add_argument("input", help="Substack URL or JSON job file path.")
    parser.add_argument("-n", "--name", help="Optional playlist name.")
    parser.add_argument("-d", "--description", help="Optional playlist description.")
    parser.add_argument("--public", action="store_true", help="Create a public playlist. Defaults to private.")
    parser.add_argument(
        "--output-mode",
        choices=sorted(OUTPUT_MODES),
        default="playlist",
        help="Spotify output action. Defaults to playlist.",
    )
    args = parser.parse_args(argv)

    preview, result, _ = run_cli(args.input, args.name, args.description, args.public, output_mode=args.output_mode)
    if result["playlist"]:
        print(f"Playlist: {result['playlist'].get('url') or result['playlist']['id']}")
    if result["queue"].get("added"):
        print(f"Queued: {result['queue']['added']}")
    print(f"Tracks added: {result['track_count']}")
    print(f"Log: {result['log_path']}")
    print(f"Previewed items: {len(preview['results'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
