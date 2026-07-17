"""Background-service management for Flow Crate.

macOS uses a launchd LaunchAgent; Linux (e.g. Raspberry Pi) uses a systemd
user service. The unit/plist generators are kept as pure functions
(:func:`build_launchd_plist`, :func:`build_systemd_unit`) so they can be
unit-tested without touching the filesystem, ``launchctl``, or ``systemctl``.
"""

import getpass
import os
import platform
import plistlib
import shutil
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
    """Install and start Flow Crate as a background service for this OS."""
    system = platform.system()
    if system == "Darwin":
        return _install_launchd(url)
    if system == "Linux":
        return _install_systemd(url)
    print(f"--install-service isn't supported on {system}.")
    return False


def uninstall_service():
    """Remove the Flow Crate background service for this OS. Idempotent."""
    system = platform.system()
    if system == "Darwin":
        return _uninstall_launchd()
    if system == "Linux":
        return _uninstall_systemd()
    print(f"--uninstall-service isn't supported on {system}.")
    return False


def _install_launchd(url=None):
    """Write and load the launchd agent (macOS). Idempotent."""
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


def _uninstall_launchd():
    """Unload and delete the launchd agent. Idempotent (fine if not installed)."""
    path = plist_path()
    _unload(path)
    if path.exists():
        path.unlink()
        print(f"Removed Flow Crate launchd agent at {path}")
    else:
        print("No Flow Crate launchd agent was installed.")
    return True


# ---------------------------------------------------------------------------
# Linux / systemd (Raspberry Pi)
#
# We install a *user* service (no root needed to write the unit or start it),
# then enable lingering so it also starts at boot without an interactive login
# — which is what makes a headless Pi behave like an appliance. Ports 8765/8443
# are >1024, so no privileged binding is required.
# ---------------------------------------------------------------------------

SYSTEMD_UNIT_NAME = "flowcrate.service"


def systemd_user_unit_path():
    """Absolute path to the per-user systemd unit."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user" / SYSTEMD_UNIT_NAME


def build_systemd_unit(executable=None, working_dir=None):
    """Return the systemd user-unit file contents (unit-testable, no I/O).

    Uses ``python -m flowcrate.app`` so it runs against the interpreter Flow
    Crate is installed into (defaults to the current one).
    """
    executable = executable or sys.executable
    working_dir = str(working_dir or Path.home())
    return "\n".join(
        [
            "[Unit]",
            "Description=Flow Crate",
            "Wants=network-online.target",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={executable} -m flowcrate.app --no-browser",
            f"WorkingDirectory={working_dir}",
            "Restart=always",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _systemctl_user(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _install_systemd(url=None):
    """Write, enable, and start the systemd user service (Linux). Idempotent."""
    if shutil.which("systemctl") is None:
        print("systemd (systemctl) was not found; cannot install a service automatically.")
        return False

    ensure_dirs()
    path = systemd_user_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_systemd_unit(), encoding="utf-8")

    _systemctl_user("daemon-reload")
    enabled = _systemctl_user("enable", "--now", SYSTEMD_UNIT_NAME)
    if enabled.returncode != 0:
        detail = (enabled.stderr or enabled.stdout or "").strip()
        print(f"Wrote the unit to {path}, but `systemctl --user` could not start it:")
        if detail:
            print(f"  {detail}")
        print("This usually means the user session bus isn't available (common over")
        print("plain SSH). Enable lingering, then re-run install:")
        print(f"  sudo loginctl enable-linger {getpass.getuser()}")
        print("  flowcrate --install-service")
        return False

    print(f"Installed Flow Crate systemd user service at {path}")
    print("Enabled and started via systemctl --user; it restarts if it exits.")

    # Lingering makes the user service start at boot without an interactive login.
    linger = subprocess.run(
        ["loginctl", "enable-linger", getpass.getuser()], capture_output=True, text=True
    )
    if linger.returncode == 0:
        print("Enabled lingering so Flow Crate starts at boot without logging in.")
    else:
        print("To also start it at boot without logging in, run:")
        print(f"  sudo loginctl enable-linger {getpass.getuser()}")

    if url:
        print(f"Flow Crate will be reachable at {url}")
    print(f"Manage it with: systemctl --user status|restart|stop {SYSTEMD_UNIT_NAME}")
    return True


def _uninstall_systemd():
    """Stop, disable, and remove the systemd user service. Idempotent."""
    if shutil.which("systemctl") is None:
        print("systemd (systemctl) was not found.")
        return False

    path = systemd_user_unit_path()
    _systemctl_user("disable", "--now", SYSTEMD_UNIT_NAME)
    removed = path.exists()
    if removed:
        path.unlink()
    _systemctl_user("daemon-reload")
    if removed:
        print(f"Removed Flow Crate systemd user service at {path}")
    else:
        print("No Flow Crate systemd user service was installed.")
    return True
