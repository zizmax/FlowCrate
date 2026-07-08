# Flow Crate

Local browser UI for playing, queueing, or creating Spotify playlists from cached Flow State posts.

## Quick Start

```bash
cd ~/Desktop/FlowState2
./flowcrate
```

The launcher creates a local `.venv`, installs requirements when needed, sets `PYTHONPATH=src`, and starts the app at `http://127.0.0.1:8765`. The legacy `./flowstate` launcher remains as a compatibility wrapper.

## What The UI Does

- Save Spotify settings locally and keep Substack SID as an optional fallback.
- Show a cached dashboard at `/` with the latest Flow State post, a Play Latest button, selected latest entries, and an archive table.
- Refresh the local cache from Flow State explicitly when you want the current latest post plus 10 previous posts.
- Play selected rows now, add selected rows to queue, or create a playlist from selected rows.
- Prefer an active Spotify device, use a single available device, ask when multiple devices are available, and on macOS open Spotify Desktop when no device is available.
- Keep the older Create/Preview flow at `/create` for compatibility.
- Start fresh from Settings by clearing local config and Spotify auth without deleting logs or previews.
- Browse structured JSON run logs plus CSV compatibility logs.

## Local Data

- App config: `~/.flowcrate/config.env`
- Spotify token cache: `~/.flowcrate/spotify_token.cache`
- Dashboard cache: `~/.flowcrate/flowcrate.db`
- Bundled cache seed: `~/Desktop/FlowState2/data/seeds/flowstate_recent_seed.json`
- Legacy reset also removes `~/.flowstate/config.env` and `~/.flowstate/spotify_token.cache` if present.
- Run logs: `~/Desktop/FlowState2/logs/`
- Preview cache: `~/Desktop/FlowState2/data/previews/`

## CLI

```bash
PYTHONPATH=src .venv/bin/python -m flowcrate.cli samples/dp_test.json
PYTHONPATH=src .venv/bin/python -m flowcrate.cli "https://www.flowstate.fm/p/..." -n "Playlist Name"
PYTHONPATH=src .venv/bin/python -m flowcrate.cli "https://www.flowstate.fm/p/..." --output-mode queue
```

JSON job files can include `playlist_name`, `playlist_description`, `playlist_public`, `output_mode`, and `urls`.

## Logs

New runs write a structured JSON log as the canonical record and a flat CSV file for compatibility. JSON logs preserve album parent rows and the expanded child tracks that were added to Spotify.
