# Flow Crate

Flow Crate is a small personal companion app for the
[Flow State](https://flowstate.fm) ambient-music newsletter. It shows you the
latest posts and plays the day's picks on your Spotify — and, if you like, on
your Sonos speakers with a simple "Hey Siri, play Flow Crate" shortcut.

It's an unofficial personal tool. Flow Crate fetches the newsletter for you the
same way your browser does when you open it, and it never redistributes any of
its content. Please **subscribe to [Flow State](https://flowstate.fm) and
support the artists** — this app is just a nicer remote control for music you
already have access to.

## Install

You'll need **Python 3.10 or newer**. Then:

```bash
pipx install git+https://github.com/zizmax/FlowCrate.git
flowcrate
```

Running `flowcrate` opens the dashboard in your browser. From there, the
**Settings** page walks you through connecting Spotify (and, optionally, Sonos
and Siri) right inside the app — there's nothing else to configure by hand.

## Keep it running

If you want Flow Crate always available (so the Siri shortcut just works), keep
it running in the background.

### macOS

```bash
flowcrate --install-service     # start at login, restart automatically
flowcrate --uninstall-service   # remove it again
```

If Sonos discovery stops working afterward, macOS may need you to re-grant
**Local Network** permission to Python under System Settings → Privacy &
Security → Local Network.

### Raspberry Pi / Linux (systemd)

Create `~/.config/systemd/user/flowcrate.service`:

```ini
[Unit]
Description=Flow Crate
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=%h/.local/bin/flowcrate --no-browser
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

(Adjust `ExecStart` to wherever pipx/venv installed the `flowcrate` command.)
Then enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now flowcrate
```

Set the Pi's hostname (e.g. `flowcrate.local` via avahi/mDNS) so the Siri
shortcut URL stays stable across reboots and IP changes.

One-tap Siri shortcut generation requires macOS. On Linux, use the in-app
manual recipe in Settings, or download the shortcut once from a Mac — it keeps
working as long as the hostname and API token don't change.

## Development

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
```

## Disclaimer

Flow Crate is an unaffiliated personal project. It is not endorsed by or
connected to Flow State, Spotify, Sonos, or Apple.
