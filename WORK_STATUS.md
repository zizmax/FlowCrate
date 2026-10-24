# Work status

_Updated 2026-10-23._

## Done (v1.9.6)

- **Missing-posts bug fixed.** A refresh used to stop at the first post it already
  had. The Siri path refreshed only the newest 2 posts when the cache was stale,
  so any posts published while nobody used Flow Crate were never cached. One
  install was missing 18 posts since June. Refreshes now skip cached posts instead
  of stopping. After a long absence they also page back until they reach a cached
  post. The quick Siri refresh then starts a full background refresh.
- **Rewind Flow Crate.** `/api/play-latest` accepts an optional `{"day": "..."}`
  ("yesterday", "last Wednesday", "October 15", "3 days ago", "10/15"). It looks
  in the cache first, then the live archive, which fetches older posts on demand.
  With no post that day, it plays the closest earlier post and says so. The
  existing "Play Flow Crate" shortcut is unchanged (empty body = latest).
  Settings offers the Rewind shortcut and how to build it from the existing one.
- Verbatim request: `docs/feedback/2026-10-23-siri-dates.md`.
- Deployed 2026-10-23: the Pi runs eb5d275 (pipx from GitHub). Live test of
  `{"day": "last Wednesday"}` from the LAN played Julia Gjertsen (Oct 21) on Sonos.
  A one-time repair fetched the 26 older posts missing from the Pi's cache;
  0 posts since Jun 26 are missing now. Backup: `~/.flowcrate/flowcrate.db.bak-2026-10-23`.
- Landing page deployed with `npx wrangler pages deploy site --project-name flowcrate --branch main`
  (direct upload; pushing to GitHub does not redeploy it).

- v1.9.7: Settings → "When Flow Crate plays on Sonos" (`SONOS_QUEUE_MODE`):
  `replace` (default) clears the Sonos queue before playing; `append` keeps the
  old behavior. Queue-only requests (`"play": false`) always append. History
  showed no deliberate reason for appending (it came from the Sonos spike).
  Deployed to the Pi 2026-10-23 20:32 with `SONOS_QUEUE_MODE="replace"` (config
  backup `~/.flowcrate/config.env.bak-2026-10-23`). Live test: replaying Jun 18
  queued at position 1 and the queue stayed at 12 items (append would have used 13).

## Open

- `src/flowcrate/static/rewind-flow-crate-universal.shortcut` is not built yet.
  `scripts/build_universal_shortcut.py` needs a Mac signed into iCloud. Until
  then, Settings on non-macOS hosts hides that button.
- Unsigned `.shortcut` import on current iOS is UNVERIFIED.
- An uncommitted change that moves the Spotify redirect to `http://127.0.0.1` exists
  in the main checkout. Its origin is unknown and it is not committed (copy:
  `~/code/FlowCrate-spotify-loopback-wip.patch`). Do not
  ship it to a headless Pi without deciding: it breaks Connect Spotify from
  other LAN computers.
