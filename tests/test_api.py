import unittest
from unittest.mock import MagicMock, patch

from flowcrate.app import create_app, resolve_post_items
from flowcrate.config import AppConfig


def _cfg(**overrides):
    values = {"api_token": "secret", "sonos_ip": "192.168.0.34", "sonos_room": "Office"}
    values.update(overrides)
    return AppConfig(**values)


def _post(spotify_link="https://open.spotify.com/track/abc"):
    return {
        "artist": "Noémi Büchi",
        "name": "Matter",
        "type": "track",
        "spotify_link": spotify_link,
    }


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
    def test_success_path(self):
        app = create_app()
        app.config.update(TESTING=True)

        post = {
            "title": "Flow State Ep. 333: Noémi Büchi Guest Mix",
            "date": "2026-07-07",
            "url": "https://www.flowstate.fm/p/ep-333",
        }
        parsed = {
            "items": [
                {"artist": "A", "name": "One", "type": "track", "spotify_link": "https://open.spotify.com/track/one"},
                {"artist": "B", "name": "Two", "type": "album", "spotify_link": "https://open.spotify.com/album/two"},
            ]
        }
        queue_result = {"queued": 23, "first_position": 1, "room": "Office"}

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.get_recent_posts", return_value=[post]), \
             patch("flowcrate.app.extract_source_post", return_value=parsed), \
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
        get_speaker.assert_called_once()
        queue_and_play.assert_called_once()

    def test_no_playable_items_returns_400(self):
        app = create_app()
        app.config.update(TESTING=True)
        post = {"title": "Empty", "date": "2026-07-07", "url": "https://x"}
        parsed = {"items": [{"artist": "A", "name": "One", "type": "track", "spotify_link": None}]}

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.get_recent_posts", return_value=[post]), \
             patch("flowcrate.app.extract_source_post", return_value=parsed), \
             patch("flowcrate.app.SpotifyManager", side_effect=ValueError("no creds")):
            response = app.test_client().post(
                "/api/play-latest", headers={"X-FlowCrate-Token": "secret"}, json={}
            )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("speak", data)


class ResolvePostItemsTests(unittest.TestCase):
    def test_direct_links_resolve_without_spotify_manager(self):
        items = [_post(), _post("https://open.spotify.com/album/xyz")]
        with patch("flowcrate.app.SpotifyManager") as manager:
            resolved, unresolved, unavailable = resolve_post_items(items)
        self.assertEqual(len(resolved), 2)
        self.assertEqual(unresolved, [])
        self.assertFalse(unavailable)
        manager.assert_not_called()

    def test_unlinked_items_fall_back_to_search(self):
        items = [{"artist": "A", "name": "One", "type": "track", "spotify_link": None}]
        searcher = MagicMock()
        searcher.search_item.return_value = {"status": "FOUND", "uri": "spotify:track:found"}
        with patch("flowcrate.app.SpotifyManager", return_value=searcher):
            resolved, unresolved, unavailable = resolve_post_items(items)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0][1], "spotify:track:found")
        self.assertEqual(unresolved, [])
        self.assertFalse(unavailable)
        searcher.search_item.assert_called_once_with("A", "One", "track")

    def test_search_unavailable_marks_unresolved(self):
        items = [{"artist": "A", "name": "One", "type": "track", "spotify_link": None}]
        with patch("flowcrate.app.SpotifyManager", side_effect=ValueError("no creds")):
            resolved, unresolved, unavailable = resolve_post_items(items)
        self.assertEqual(resolved, [])
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(unavailable)


if __name__ == "__main__":
    unittest.main()


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
