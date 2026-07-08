# API Improvement Suggestions

## 1. Split Spotify auth from higher-level app actions

Create a small module whose only job is loading config, building `SpotifyOAuth`, and returning an authenticated `spotipy.Spotify` client or lightweight `SpotifySession`.

Reasoning:
- `src/flowcrate/spotify.py` currently combines auth, search, playback, queueing, playlist creation, and retry behavior.
- One-off scripts should not need to instantiate a broad app service just to get an authenticated client.
- Separating auth reduces coupling and makes ad hoc tasks easier for an AI to compose correctly.

Concrete target:
- Add something like `flowcrate.spotify_auth.get_client()` or `flowcrate.spotify_auth.create_session()`.
- Keep token cache, scopes, and config paths centralized there.

## 2. Add a reusable album-resolution primitive

Add one public helper that takes `artist` and `album_name`, resolves the best Spotify album match, and returns both album metadata and ordered track URIs.

Reasoning:
- This playlist task mostly needed one operation: "find this album and give me its tracks."
- Current code exposes `search_item(...)` and `get_album_track_rows(...)`, but callers must orchestrate matching, failure handling, and expansion themselves.
- AI agents work better with one task-shaped primitive than several lower-level methods that must be stitched together repeatedly.

Concrete target:
- Add something like `resolve_album(artist, album_name) -> { album_uri, spotify_artist, spotify_name, track_uris, track_rows }`.
- Make it raise a clear structured error when matching fails.

## 3. Improve retry handling for transient Spotify failures

Refine `_with_retry()` so transient `429` and `5xx` failures use bounded backoff with jitter and preserve clear failure context.

Reasoning:
- This one-off run failed once due to a `429` mixed with repeated upstream `502` responses during album search, then succeeded on retry after cooldown.
- Current behavior is serviceable for the web app, but brittle for automation that needs to finish unattended.
- Better retry semantics reduce wasted reruns and make agent-driven scripts more reliable.

Concrete target:
- Retry a small number of times on `429`, `500`, `502`, `503`, and `504`.
- Respect `Retry-After` when present.
- Surface a final exception that includes operation name, query/endpoint, attempt count, and retry-until timestamp if rate-limited.
