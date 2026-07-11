"""launchd (macOS) service management for Flow Crate.

The plist generation is kept as a pure function (:func:`build_launchd_plist`)
so it can be unit-tested without touching the filesystem or ``launchctl``.
"""

import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

from .paths import LOGS_DIR, ensure_dirs

LABEL = "com.flowcrate.server"
PLIST_NAME = f"{LABEL}.plist"

_LOCAL_NETWORK_NOTE = (
    "Note: macOS may ask you to re-grant Local Network permission to Python. "
    "If Sonos discovery stops working under launchd, open System Settings > "
    "Privacy & Security > Local Network and enable Python."
)


def plist_path():
    """Absolute path to the user's LaunchAgent plist."""
    return Path.home() / "Library" / "LaunchAgents" / PLIST_NAME


def build_launchd_plist(executable=None, working_dir=None, logs_dir=None):
    """Return the launchd plist as a plain dict (unit-testable, no I/O).

    Uses ``python -m flowcrate.app`` so it works once flowcrate is pip-installed
    into the interpreter at ``executable`` (defaults to the current one).
    """
    executable = executable or sys.executable
    working_dir = str(working_dir or Path.home())
    logs_dir = Path(logs_dir or LOGS_DIR)
    log_file = str(logs_dir / "launchd.log")
    return {
        "Label": LABEL,
        "ProgramArguments": [executable, "-m", "flowcrate.app", "--no-browser"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": working_dir,
        "StandardOutPath": log_file,
        "StandardErrorPath": log_file,
    }


def _uid():
    return os.getuid()


def _load(path):
    """Load the agent, preferring modern ``bootstrap`` with a ``load`` fallback."""
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "bootstrap"
    subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
    return "load"


def _unload(path):
    """Unload the agent, preferring modern ``bootout`` with an ``unload`` fallback."""
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "bootout"
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    return "unload"


def install_service(url=None):
    """Write and load the launchd agent (macOS only). Idempotent."""
    if platform.system() != "Darwin":
        print("--install-service is only available on macOS.")
        print("On Linux/Raspberry Pi, use a systemd unit instead (see the README).")
        return False

    ensure_dirs()
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Idempotent: if a previous copy is loaded, unload it before re-installing.
    if path.exists():
        _unload(path)

    plist = build_launchd_plist()
    with path.open("wb") as handle:
        plistlib.dump(plist, handle)

    how = _load(path)
    print(f"Installed Flow Crate launchd agent at {path}")
    print(f"Loaded via launchctl {how}. It will start at login and restart if it exits.")
    if url:
        print(f"Flow Crate will be reachable at {url}")
    print(_LOCAL_NETWORK_NOTE)
    return True


def uninstall_service():
    """Unload and delete the launchd agent. Idempotent (fine if not installed)."""
    if platform.system() != "Darwin":
        print("--uninstall-service is only available on macOS.")
        return False

    path = plist_path()
    _unload(path)
    if path.exists():
        path.unlink()
        print(f"Removed Flow Crate launchd agent at {path}")
    else:
        print("No Flow Crate launchd agent was installed.")
    return True
