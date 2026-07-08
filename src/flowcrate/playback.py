import platform
import subprocess
import time
from dataclasses import dataclass


class PlaybackTargetError(RuntimeError):
    pass


class DevicePickerRequired(PlaybackTargetError):
    def __init__(self, devices):
        self.devices = devices
        super().__init__("Choose a Spotify device.")


@dataclass
class PlaybackTarget:
    device_id: str
    name: str
    source: str


def usable_devices(spotify):
    return [device for device in spotify.get_available_devices() if not device.get("is_restricted")]


def resolve_playback_target(spotify, requested_device_id=None, allow_picker=True, open_desktop=True, timeout=12):
    devices = usable_devices(spotify)
    if requested_device_id:
        for device in devices:
            if device.get("id") == requested_device_id:
                return _target(device, "selected")
        raise PlaybackTargetError("Selected Spotify device is no longer available.")

    active = [device for device in devices if device.get("is_active")]
    if active:
        return _target(active[0], "active")

    if len(devices) == 1:
        return _target(devices[0], "available")

    if len(devices) > 1 and allow_picker:
        raise DevicePickerRequired(devices)

    if not devices and open_desktop and platform.system() == "Darwin":
        _open_spotify_desktop()
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1)
            devices = usable_devices(spotify)
            mac_devices = [device for device in devices if _looks_like_desktop(device)]
            if mac_devices:
                return _target(mac_devices[0], "mac_desktop")
            if len(devices) == 1:
                return _target(devices[0], "available_after_open")

    raise PlaybackTargetError(
        "No usable Spotify device is available. Open Spotify on this Mac or start playback on another Spotify Connect device, then try again."
    )


def _target(device, source):
    return PlaybackTarget(device_id=device.get("id"), name=device.get("name") or "Spotify device", source=source)


def _open_spotify_desktop():
    subprocess.Popen(["open", "-a", "Spotify"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _looks_like_desktop(device):
    dtype = (device.get("type") or "").lower()
    return dtype in {"computer", "desktop"} or "mac" in (device.get("name") or "").lower()
