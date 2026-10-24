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

## Open

- `src/flowcrate/static/rewind-flow-crate-universal.shortcut` is not built yet.
  `scripts/build_universal_shortcut.py` needs a Mac signed into iCloud. Until
  then, Settings on non-macOS hosts hides that button.
- Unsigned `.shortcut` import on current iOS is UNVERIFIED.
- An uncommitted change that moves the Spotify redirect to `http://127.0.0.1` exists
  in the main checkout. Its origin is unknown and it is not committed. Do not
  ship it to a headless Pi without deciding: it breaks Connect Spotify from
  other LAN computers.
