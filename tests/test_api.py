import unittest
from unittest.mock import MagicMock, patch

from flowcrate.app import create_app
from flowcrate.config import AppConfig


def _cfg(**overrides):
    values = {"api_token": "secret", "sonos_ip": "192.168.0.34", "sonos_room": "Office"}
    values.update(overrides)
    return AppConfig(**values)


def _entry(spotify_uri="spotify:track:abc", **overrides):
    entry = {
        "row_id": "entry",
        "parsed_artist": "Noémi Büchi",
        "spotify_artist": "Noémi Büchi",
        "parsed_name": "Matter",
        "spotify_uri": spotify_uri,
    }
    entry.update(overrides)
    return entry


class ApiAuthTests(unittest.TestCase):
    def test_403_when_no_api_token_configured(self):
        app = create_app()
        app.config.update(TESTING=True)
        with patch("flowcrate.app.load_config", return_value=_cfg(api_token="")):
            response = app.test_client().post("/api/play-latest")
        self.assertEqual(response.status_code, 403)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("speak", data)
        self.assertIn("Settings", data["speak"])

    def test_401_on_wrong_token(self):
        app = create_app()
        app.config.update(TESTING=True)
        with patch("flowcrate.app.load_config", return_value=_cfg()):
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "nope"}
            )
        self.assertEqual(response.status_code, 401)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("speak", data)


class ApiPlayLatestTests(unittest.TestCase):
    def test_success_path_reads_from_cache(self):
        app = create_app()
        app.config.update(TESTING=True)

        post = {
            "title": "Flow State Ep. 333: Noémi Büchi Guest Mix",
            "date": "2026-07-07",
            "url": "https://www.flowstate.fm/p/ep-333",
        }
        entries = [
            _entry("spotify:track:one", spotify_artist="A"),
            _entry("spotify:album:two", spotify_artist="B", row_id="entry2"),
        ]
        queue_result = {"queued": 23, "first_position": 1, "room": "Office"}

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.cache_is_stale", return_value=False), \
             patch("flowcrate.app.get_recent_posts", return_value=[post]), \
             patch("flowcrate.app.has_cached_post", return_value=True), \
             patch("flowcrate.app.refresh_from_flowstate") as refresh, \
             patch("flowcrate.app.latest_cached_post", return_value=(post, entries)), \
             patch("flowcrate.app.sonos.get_speaker", return_value=MagicMock()) as get_speaker, \
             patch("flowcrate.app.sonos.queue_and_play", return_value=queue_result) as queue_and_play:
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "secret"}, json={}
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["queued"], 23)
        self.assertEqual(data["room"], "Office")
        self.assertEqual(data["unresolved"], 0)
        self.assertEqual(data["artists"], ["A", "B"])
        self.assertEqual(
            data["speak"],
            "Playing Flow State Ep. 333: Noémi Büchi Guest Mix from 2026-07-07. 23 tracks on Office.",
        )
        # Album URIs stay whole; the queued URIs are the stored ones verbatim.
        queued_uris = queue_and_play.call_args[0][1]
        self.assertEqual(queued_uris, ["spotify:track:one", "spotify:album:two"])
        # Cache was fresh and the latest post already cached: no refresh needed.
        refresh.assert_not_called()
        get_speaker.assert_called_once()
        queue_and_play.assert_called_once()

    def test_served_from_cache_without_spotify_manager(self):
        app = create_app()
        app.config.update(TESTING=True)
        post = {"title": "Cached", "date": "2026-07-07", "url": "https://x"}
        entries = [_entry("spotify:track:one"), _entry("spotify:album:two", row_id="entry2")]
        queue_result = {"queued": 2, "first_position": 1, "room": "Office"}

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.cache_is_stale", return_value=False), \
             patch("flowcrate.app.get_recent_posts", return_value=[post]), \
             patch("flowcrate.app.has_cached_post", return_value=True), \
             patch("flowcrate.app.refresh_from_flowstate") as refresh, \
             patch("flowcrate.app.SpotifyManager", side_effect=AssertionError("must not build")), \
             patch("flowcrate.app.latest_cached_post", return_value=(post, entries)), \
             patch("flowcrate.app.sonos.get_speaker", return_value=MagicMock()), \
             patch("flowcrate.app.sonos.queue_and_play", return_value=queue_result):
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "secret"}, json={}
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        refresh.assert_not_called()

    def test_refresh_runs_when_cache_stale(self):
        app = create_app()
        app.config.update(TESTING=True)
        post = {"title": "Cached", "date": "2026-07-07", "url": "https://x"}
        entries = [_entry("spotify:track:one")]
        queue_result = {"queued": 1, "first_position": 1, "room": "Office"}

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.cache_is_stale", return_value=True), \
             patch("flowcrate.app.refresh_from_flowstate") as refresh, \
             patch("flowcrate.app.latest_cached_post", return_value=(post, entries)), \
             patch("flowcrate.app.sonos.get_speaker", return_value=MagicMock()), \
             patch("flowcrate.app.sonos.queue_and_play", return_value=queue_result):
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "secret"}, json={}
            )

        self.assertEqual(response.status_code, 200)
        refresh.assert_called_once_with(limit=2)

    def test_refresh_runs_when_latest_post_not_cached(self):
        app = create_app()
        app.config.update(TESTING=True)
        post = {"title": "Cached", "date": "2026-07-07", "url": "https://new"}
        entries = [_entry("spotify:track:one")]
        queue_result = {"queued": 1, "first_position": 1, "room": "Office"}

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.cache_is_stale", return_value=False), \
             patch("flowcrate.app.get_recent_posts", return_value=[post]), \
             patch("flowcrate.app.has_cached_post", return_value=False), \
             patch("flowcrate.app.refresh_from_flowstate") as refresh, \
             patch("flowcrate.app.latest_cached_post", return_value=(post, entries)), \
             patch("flowcrate.app.sonos.get_speaker", return_value=MagicMock()), \
             patch("flowcrate.app.sonos.queue_and_play", return_value=queue_result):
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "secret"}, json={}
            )

        self.assertEqual(response.status_code, 200)
        refresh.assert_called_once_with(limit=2)

    def test_no_playable_items_returns_400(self):
        app = create_app()
        app.config.update(TESTING=True)
        post = {"title": "Empty", "date": "2026-07-07", "url": "https://x"}
        entries = [_entry(None)]

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.cache_is_stale", return_value=False), \
             patch("flowcrate.app.get_recent_posts", return_value=[post]), \
             patch("flowcrate.app.has_cached_post", return_value=True), \
             patch("flowcrate.app.refresh_from_flowstate"), \
             patch("flowcrate.app.latest_cached_post", return_value=(post, entries)):
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "secret"}, json={}
            )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("speak", data)


class BuildSpeakTests(unittest.TestCase):
    def test_skipped_clause_is_a_proper_sentence(self):
        from flowcrate.app import _build_speak

        post = {"title": "Mix", "date": "2026-07-07"}
        result = {"queued": 20, "room": "Office"}
        self.assertEqual(
            _build_speak(post, result, 3, False),
            "Playing Mix from 2026-07-07. 20 tracks on Office. 3 tracks were skipped.",
        )
        self.assertEqual(
            _build_speak(post, result, 1, True),
            "Playing Mix from 2026-07-07. 20 tracks on Office. 1 track was skipped."
            " Connect Spotify to resolve unlinked tracks.",
        )


if __name__ == "__main__":
    unittest.main()
