import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flowcrate.app as app_module
import flowcrate.config as config_module
from flowcrate.app import _start_background_refresh, create_app


class AppTests(unittest.TestCase):
    def test_dashboard_renders_latest_and_archive(self):
        app = create_app()
        app.config.update(TESTING=True)

        with patch("flowcrate.app.dashboard_data", return_value=_dashboard_payload()), \
             patch("flowcrate.app.cache_is_stale", return_value=False):
            response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Latest Flow State", html)
        self.assertIn("Play Latest", html)
        self.assertNotIn("Latest Entries", html)
        self.assertNotIn("Play Now", html)
        self.assertIn('class="play-button"', html)
        self.assertIn("Refresh explicitly", html)
        self.assertIn("select-all", html)
        self.assertIn("post-group-row", html)
        self.assertIn("Ready", html)
        self.assertIn("title=\"Track URI(s) are available", html)
        self.assertIn("child-track-row", html)
        self.assertNotIn("artist-continuation", html)
        self.assertIn("Archive", html)
        # Change 1: playable bubble replaces ready/linked
        self.assertIn("playable", html)
        self.assertNotIn("ready entries", html)
        self.assertNotIn("linked albums", html)
        # Change 2: action button tooltips
        self.assertIn("replaces whatever is currently playing", html)
        self.assertIn("end of your current Spotify queue", html)
        self.assertIn("named after the post", html)
        # Change 3: Refresh button is in cache-card, not bulk-actions
        self.assertIn("refresh-in-card", html)
        # Change 4: child-dash
        self.assertIn("child-dash", html)


class BackgroundRefreshLockTests(unittest.TestCase):
    def test_only_one_refresh_runs_at_a_time(self):
        release = threading.Event()
        started = threading.Event()
        calls = []

        def slow_refresh(limit=11):
            calls.append(limit)
            started.set()
            release.wait(timeout=5)

        with patch("flowcrate.app.refresh_from_flowstate", side_effect=slow_refresh):
            self.assertTrue(_start_background_refresh(limit=2))
            self.assertTrue(started.wait(timeout=5))
            # A second trigger while the first is running is refused (single-flight).
            self.assertFalse(_start_background_refresh(limit=2))
            release.set()
            # Let the worker finish and clear the running flag.
            for _ in range(250):
                if not app_module._REFRESH_STATE["running"]:
                    break
                threading.Event().wait(0.02)

        self.assertEqual(calls, [2])
        self.assertFalse(app_module._REFRESH_STATE["running"])


def _dashboard_payload():
    return {
        "latest": {"title": "Karim", "date": "2026-05-22", "url": "https://www.flowstate.fm/p/karim"},
        "latest_post": {
            "title": "Karim",
            "date": "2026-05-22",
            "url": "https://www.flowstate.fm/p/karim",
            "entries": [
                {
                    "row_id": "entry",
                    "playable": True,
                    "parsed_artist": "Karim",
                    "parsed_name": "Lila",
                    "parsed_type": "album",
                    "source_url": "https://www.flowstate.fm/p/karim",
                    "source_date": "2026-05-22",
                    "spotify_link": "https://open.spotify.com/album/album",
                    "spotify_artist": "Karim",
                    "spotify_name": "Lila",
                    "duration_display": "61m",
                    "notes": "no vocals",
                    "track_count": 9,
                    "match_status": "FOUND",
                    "readiness_status": "Ready",
                    "readiness_label": "Ready",
                    "readiness_key": "status-ready",
                    "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
                    "children": [
                        {
                            "spotify_uri": "spotify:track:one",
                            "spotify_artist": "Karim",
                            "spotify_name": "One",
                            "spotify_link": "https://open.spotify.com/track/one",
                            "track_number": 1,
                            "duration_ms": 120000,
                        }
                    ],
                }
            ],
            "summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "playable_count": 1, "track_count": 9, "duration": "61m", "notes": "no vocals"},
            "playable_entry_count": 1,
            "linked_entry_count": 0,
            "playable_count": 1,
            "track_count": 9,
        },
        "archive_posts": [
            {
                "title": "SUSS",
                "date": "2026-05-18",
                "url": "https://www.flowstate.fm/p/suss",
                "entries": [
                    {
                        "row_id": "archive-entry",
                        "playable": True,
                        "parsed_artist": "SUSS",
                        "parsed_name": "Night Suite",
                        "parsed_type": "album",
                        "source_url": "https://www.flowstate.fm/p/suss",
                        "source_date": "2026-05-18",
                        "spotify_link": "https://open.spotify.com/album/archive",
                        "spotify_artist": "SUSS",
                        "spotify_name": "Night Suite",
                        "duration_display": "42m",
                        "notes": "",
                        "track_count": 8,
                        "match_status": "FOUND",
                        "readiness_status": "Ready",
                        "readiness_label": "Ready",
                        "readiness_key": "status-ready",
                        "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
                        "children": [],
                    }
                ],
                "summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "playable_count": 1, "track_count": 8, "duration": "42m", "notes": ""},
                "playable_entry_count": 1,
                "linked_entry_count": 0,
                "playable_count": 1,
                "track_count": 8,
            }
        ],
        "latest_entries": [
            {
                "row_id": "entry",
                "playable": True,
                "parsed_artist": "Karim",
                "parsed_name": "Lila",
                "parsed_type": "album",
                "source_url": "https://www.flowstate.fm/p/karim",
                "source_date": "2026-05-22",
                "spotify_link": "https://open.spotify.com/album/album",
                "spotify_artist": "Karim",
                "spotify_name": "Lila",
                "duration_display": "61m",
                "notes": "no vocals",
                "track_count": 9,
                "match_status": "FOUND",
                "readiness_status": "Ready",
                "readiness_label": "Ready",
                "readiness_key": "status-ready",
                "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
                "children": [],
            }
        ],
        "archive_entries": [
            {
                "row_id": "archive-entry",
                "playable": True,
                "parsed_artist": "SUSS",
                "parsed_name": "Night Suite",
                "parsed_type": "album",
                "source_url": "https://www.flowstate.fm/p/suss",
                "source_date": "2026-05-18",
                "spotify_link": "https://open.spotify.com/album/archive",
                "spotify_artist": "SUSS",
                "spotify_name": "Night Suite",
                "duration_display": "42m",
                "notes": "",
                "track_count": 8,
                "match_status": "FOUND",
                "readiness_status": "Ready",
                "readiness_label": "Ready",
                "readiness_key": "status-ready",
                "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
                "children": [],
            }
        ],
        "cache_path": "/tmp/flowcrate.db",
        "cache_status": {
            "state": "stale",
            "label": "Needs refresh",
            "value": "2026-05-22T09:00:00",
            "needs_refresh": True,
            "is_seeded": False,
        },
        "spotify_state": {"active": False, "message": ""},
        "latest_summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "playable_count": 1, "track_count": 9, "duration": "61m", "notes": "no vocals"},
        "archive_summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "playable_count": 1, "track_count": 8, "duration": "42m", "notes": ""},
    }


class ApiSessionTests(unittest.TestCase):
    """The /api/session endpoint used to sync cookies to a headless install."""

    def _client(self):
        app = create_app()
        app.config.update(TESTING=True)
        return app.test_client()

    def test_get_reports_session_state(self):
        cfg = SimpleNamespace(api_token="x", flowstate_connect_sid="c9", substack_sid="")
        with patch("flowcrate.app.load_config", return_value=cfg):
            r = self._client().get("/api/session")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["has_session"])
        self.assertEqual(data["connect_sid"], "c9")

    def test_400_when_no_api_token_configured(self):
        with patch("flowcrate.app.load_config", return_value=SimpleNamespace(api_token="")):
            r = self._client().post("/api/session", json={"connect_sid": "x"})
        self.assertEqual(r.status_code, 400)

    def test_401_on_bad_token(self):
        with patch("flowcrate.app.load_config", return_value=SimpleNamespace(api_token="good")):
            r = self._client().post(
                "/api/session", headers={"X-FlowCrate-Token": "bad"}, json={"connect_sid": "x"}
            )
        self.assertEqual(r.status_code, 401)

    def test_400_on_empty_cookies(self):
        with patch("flowcrate.app.load_config", return_value=SimpleNamespace(api_token="good")):
            r = self._client().post(
                "/api/session", headers={"X-FlowCrate-Token": "good"}, json={}
            )
        self.assertEqual(r.status_code, 400)

    def test_saves_cookies_and_resets_session(self):
        saved = {}
        with patch("flowcrate.app.load_config", return_value=SimpleNamespace(api_token="good")), \
             patch("flowcrate.app.save_config_values", side_effect=saved.update), \
             patch("flowcrate.app.reset_session_cache") as reset:
            r = self._client().post(
                "/api/session",
                headers={"X-FlowCrate-Token": "good"},
                json={"connect_sid": "c1", "substack_sid": "s1"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(saved, {"FLOWSTATE_CONNECT_SID": "c1", "SUBSTACK_SID": "s1"})
        reset.assert_called_once()


class SaveConfigValuesTests(unittest.TestCase):
    """save_config_values() must patch individual keys without wiping the rest."""

    def test_partial_update_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_file = Path(d) / "config.env"
            with patch.object(config_module, "CONFIG_FILE", cfg_file), \
                 patch.object(config_module, "ensure_dirs", lambda: None), \
                 patch.object(config_module, "load_config", lambda: None):
                config_module.save_config(
                    {"SPOTIFY_CLIENT_ID": "abc", "API_TOKEN": "tok"}
                )
                config_module.save_config_values({"FLOWSTATE_CONNECT_SID": "c1"})
                saved = config_module.read_saved_values()
        self.assertEqual(saved["SPOTIFY_CLIENT_ID"], "abc")
        self.assertEqual(saved["API_TOKEN"], "tok")
        self.assertEqual(saved["FLOWSTATE_CONNECT_SID"], "c1")


if __name__ == "__main__":
    unittest.main()
