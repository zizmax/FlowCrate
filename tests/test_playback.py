import unittest
from unittest.mock import patch

from flowcrate.playback import DevicePickerRequired, PlaybackTargetError, resolve_playback_target


class PlaybackTargetTests(unittest.TestCase):
    def test_prefers_active_unrestricted_device(self):
        spotify = FakeSpotify(
            [
                {"id": "restricted", "name": "Locked", "is_active": True, "is_restricted": True},
                {"id": "active", "name": "Mac", "is_active": True, "is_restricted": False},
            ]
        )

        target = resolve_playback_target(spotify, open_desktop=False)

        self.assertEqual(target.device_id, "active")
        self.assertEqual(target.source, "active")

    def test_uses_single_available_device(self):
        spotify = FakeSpotify([{"id": "only", "name": "Speaker", "is_active": False, "is_restricted": False}])

        target = resolve_playback_target(spotify, open_desktop=False)

        self.assertEqual(target.device_id, "only")

    def test_multiple_devices_require_picker(self):
        spotify = FakeSpotify(
            [
                {"id": "one", "name": "One", "is_active": False, "is_restricted": False},
                {"id": "two", "name": "Two", "is_active": False, "is_restricted": False},
            ]
        )

        with self.assertRaises(DevicePickerRequired):
            resolve_playback_target(spotify, open_desktop=False)

    def test_no_device_fails_off_mac(self):
        spotify = FakeSpotify([])

        with patch("flowcrate.playback.platform.system", return_value="Linux"):
            with self.assertRaises(PlaybackTargetError):
                resolve_playback_target(spotify, timeout=0)


class FakeSpotify:
    def __init__(self, devices):
        self.devices = devices

    def get_available_devices(self):
        return self.devices


if __name__ == "__main__":
    unittest.main()
