# Flow Crate Handoff

Last updated: 2026-05-18

## Current State

The local app runs at `http://127.0.0.1:8765` via:

```bash
cd ~/Desktop/FlowState2
./flowcrate
```

`./flowstate` remains as a compatibility wrapper. The Python package now lives at `src/flowcrate`, so direct module commands should set `PYTHONPATH=src`.

## Implemented

- User-facing name changed to Flow Crate.
- Local config moved to `~/.flowcrate`; Settings reset also removes legacy `~/.flowstate` config/token files.
- Settings includes a Start Fresh danger-zone action guarded by typing `RESET`.
- Preview includes a final output selector: playlist, queue, or playlist plus queue.
- Spotify scopes now include queue and playback-state permissions.
- Queue output expands album rows into child tracks using the same preview-resolved data as playlist creation.
- Flow State remains the source publication name for `flowstate.fm`.

## Code Pointers

- Flask routes and Settings/Create/Preview flow: `src/flowcrate/app.py`
- Preview, output modes, album expansion, JSON/CSV logging, CLI workflow: `src/flowcrate/workflow.py`
- Spotify API wrapper, scopes, queue support: `src/flowcrate/spotify.py`
- Local config and reset helpers: `src/flowcrate/config.py`
- Styling: `src/flowcrate/static/styles.css`
- User docs: `README.md`

## Verification Notes

Use:

```bash
PYTHONPATH=src .venv/bin/python -m compileall src/flowcrate
PYTHONPATH=src .venv/bin/python -m flowcrate.cli --help
```

Spotify users with older cached tokens should use Settings -> Start Fresh and reconnect before queue actions.
