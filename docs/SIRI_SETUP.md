# "Hey Siri, Play Flow Crate" Setup

One-time setup, about five minutes. Requires the Flow Crate server running on a
Mac that is awake and on the same network as your Sonos speakers.

## 1. Configure the server

In Flow Crate → Settings → "Sonos & Siri":

- **SONOS_IP** — your speaker's IP address (Sonos app → Settings → System →
  About My System). More reliable than room-name discovery on networks that
  filter multicast.
- **SONOS_ROOM** — the room name to play in (e.g. `Office`). Used when
  SONOS_IP is unset, and shown in responses.
- **API_TOKEN** — any long random string; the Shortcut must send the same
  value. Generate one with: `openssl rand -hex 16`

Save, then verify from a terminal:

```bash
curl -s -X POST http://127.0.0.1:8765/api/play-latest \
  -H "X-FlowCrate-Token: YOUR_TOKEN" \
  -H "Content-Type: application/json" -d '{"play": false}'
```

You should get JSON with `"ok": true` and a `speak` sentence (with
`"play": false` it queues without starting playback).

## 2. Build the Shortcut

On your iPhone, open Shortcuts → new shortcut, name it exactly **Play Flow
Crate** (the name is the Siri phrase). Add three actions:

1. **Get Contents of URL**
   - URL: `http://YOUR-MAC-NAME.local:8765/api/play-latest`
     (find the name in macOS System Settings → General → Sharing → Local hostname)
   - Method: POST
   - Headers: `X-FlowCrate-Token` = your token
2. **Get Dictionary Value** — key `speak` from the previous action's output
3. **Speak Text** — the Dictionary Value

Say "Hey Siri, Play Flow Crate." Music should start on the configured Sonos
room and Siri reads the post title, date, and track count.

## Notes

- Every response includes `speak`, including failures, so Siri will tell you
  what went wrong (e.g. Spotify not connected, speaker unreachable).
- Guest-mix posts have no direct Spotify links; those tracks resolve via
  Spotify search, which requires your Spotify credentials to be connected in
  Settings. Regular posts play without them.
- If the speaker is grouped, playback targets the whole group.
- macOS Local Network permission must be granted to "Python" (System
  Settings → Privacy & Security → Local Network), or the server cannot reach
  the speakers. If the toggle is missing, run the server once from a plain
  Terminal window (not tmux) and allow the prompt.
