# Flow Crate

Local browser UI for playing, queueing, or creating Spotify playlists from cached Flow State posts.

## Quick Start

```bash
cd ~/Desktop/FlowState2
./flowcrate
```

The launcher creates a local `.venv`, installs requirements when needed, sets `PYTHONPATH=src`, and starts the app at `http://127.0.0.1:8765`.

## What The UI Does

- **Dashboard (`/`)** — shows the latest Flow State post plus a recent-posts archive from the local cache. It auto-refreshes: on load, on tab focus, and via a Refresh button it checks Flow State for the current latest post plus 10 previous posts.
- **Play / Queue / Playlist** — select rows and play them now, add them to the queue, or create a Spotify playlist. Playback prefers an active Spotify device, uses a single available device automatically, asks when several are available, and on macOS opens Spotify Desktop when nothing is available.
- **Settings** — save Spotify credentials locally (with an optional Substack SID fallback) and configure the optional Sonos & Siri integration. Flow State is read over public access by default; browser cookies or the SID are only used if a post appears paywalled.
- **Sonos & Siri** — set a Sonos speaker and an API token to enable `POST /api/play-latest`, which queues and plays the latest post on Sonos. Wire it to an Apple Shortcut ("Hey Siri, play Flow Crate") — see [`docs/SIRI_SETUP.md`](docs/SIRI_SETUP.md).

## Local Data

- App config: `~/.flowcrate/config.env`
- Spotify token cache: `~/.flowcrate/spotify_token.cache`
- Dashboard cache: `~/.flowcrate/flowcrate.db`
- Run logs: `~/Desktop/FlowState2/logs/`

## Logs

Runs write a structured JSON log as the canonical record and a flat CSV file for compatibility. Browse them from the Run logs link in Settings.
