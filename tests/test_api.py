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


class SonosDevicesEndpointTests(unittest.TestCase):
    def _app(self):
        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_success_returns_devices(self):
        devices = [
            {"room": "Kitchen", "ip": "192.168.1.10", "coordinator": True},
            {"room": "Office", "ip": "192.168.1.11", "coordinator": False},
        ]
        with patch("flowcrate.app.sonos.list_speakers", return_value=devices):
            response = self._app().test_client().get("/api/sonos-devices")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["devices"], devices)

    def test_exception_returns_ok_false_with_error(self):
        import errno as _errno
        exc = OSError(_errno.EHOSTUNREACH, "No route to host")
        exc.errno = _errno.EHOSTUNREACH
        with patch("flowcrate.app.sonos.list_speakers", side_effect=exc):
            response = self._app().test_client().get("/api/sonos-devices")

        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["devices"], [])
        # The Local Network hint should appear in the error message
        self.assertIn("Local Network", data["error"])

    def test_sonos_error_returns_ok_false(self):
        from flowcrate import sonos as sonos_module
        with patch("flowcrate.app.sonos.list_speakers",
                   side_effect=sonos_module.SonosError("boom")):
            response = self._app().test_client().get("/api/sonos-devices")

        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "boom")
        self.assertEqual(data["devices"], [])

    def test_no_auth_required(self):
        """Endpoint is accessible without any token header."""
        with patch("flowcrate.app.sonos.list_speakers", return_value=[]):
            response = self._app().test_client().get("/api/sonos-devices")
        self.assertEqual(response.status_code, 200)


class SonosListSpeakersTests(unittest.TestCase):
    def test_returns_sorted_speaker_list(self):
        from flowcrate import sonos as sonos_module

        zone_a = MagicMock()
        zone_a.player_name = "Office"
        zone_a.ip_address = "192.168.1.11"
        zone_a.group.coordinator.ip_address = "192.168.1.11"

        zone_b = MagicMock()
        zone_b.player_name = "Kitchen"
        zone_b.ip_address = "192.168.1.10"
        zone_b.group.coordinator.ip_address = "192.168.1.99"  # different coordinator

        with patch("flowcrate.sonos.soco.discover", return_value={zone_a, zone_b}):
            result = sonos_module.list_speakers(timeout=1)

        self.assertEqual(len(result), 2)
        # Sorted by room name: Kitchen < Office
        self.assertEqual(result[0]["room"], "Kitchen")
        self.assertEqual(result[0]["ip"], "192.168.1.10")
        self.assertFalse(result[0]["coordinator"])
        self.assertEqual(result[1]["room"], "Office")
        self.assertTrue(result[1]["coordinator"])

    def test_empty_when_no_speakers_found(self):
        from flowcrate import sonos as sonos_module

        with patch("flowcrate.sonos.soco.discover", return_value=None), \
             patch("flowcrate.sonos.scan_network", return_value=None):
            result = sonos_module.list_speakers(timeout=1)

        self.assertEqual(result, [])

    def test_raises_sonos_error_on_host_unreachable(self):
        import errno as _errno
        from flowcrate import sonos as sonos_module

        exc = OSError(_errno.EHOSTUNREACH, "No route to host")
        exc.errno = _errno.EHOSTUNREACH
        with patch("flowcrate.sonos.soco.discover", side_effect=exc):
            with self.assertRaises(sonos_module.SonosError) as ctx:
                sonos_module.list_speakers(timeout=1)
        self.assertIn("Local Network", str(ctx.exception))


class SettingsPageTests(unittest.TestCase):
    def _get_settings(self):
        from flowcrate.config import AppConfig
        app = create_app()
        app.config.update(TESTING=True)
        empty_cfg = AppConfig()
        with patch("flowcrate.app.load_config", return_value=empty_cfg):
            return app.test_client().get("/settings")

    def test_settings_renders_scan_button(self):
        response = self._get_settings()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Scan for Speakers", html)
        self.assertIn("sonos-scan-btn", html)

    def test_settings_renders_generate_token_button(self):
        html = self._get_settings().get_data(as_text=True)
        self.assertIn("Generate Token", html)
        self.assertIn("generate-token-btn", html)

    def test_settings_renders_shortcut_url(self):
        html = self._get_settings().get_data(as_text=True)
        self.assertIn("/api/play-latest", html)
        self.assertIn(".local", html)
        self.assertNotIn(".lan.local", html)

    def test_settings_renders_download_shortcut_and_no_md_reference(self):
        html = self._get_settings().get_data(as_text=True)
        self.assertIn("Download Siri Shortcut", html)
        self.assertIn("/api/siri-shortcut", html)
        self.assertIn("toggle-token-btn", html)
        self.assertIn("copy-token-btn", html)
        # The docs/SIRI_SETUP.md reference must be gone.
        self.assertNotIn("SIRI_SETUP.md", html)


class ShortcutWorkflowTests(unittest.TestCase):
    def test_url_and_token_land_in_workflow(self):
        from flowcrate.shortcut import build_workflow

        wf = build_workflow("http://mymac.local:8765/api/play-latest", "tok123")
        actions = wf["WFWorkflowActions"]
        download = actions[0]["WFWorkflowActionParameters"]
        self.assertEqual(download["WFURL"], "http://mymac.local:8765/api/play-latest")
        header_item = download["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"][0]
        self.assertEqual(header_item["WFKey"]["Value"]["string"], "X-FlowCrate-Token")
        self.assertEqual(header_item["WFValue"]["Value"]["string"], "tok123")

    def test_actions_in_expected_order(self):
        from flowcrate.shortcut import build_workflow

        wf = build_workflow("http://x/api/play-latest", "tok")
        identifiers = [a["WFWorkflowActionIdentifier"] for a in wf["WFWorkflowActions"]]
        self.assertEqual(
            identifiers,
            [
                "is.workflow.actions.downloadurl",
                "is.workflow.actions.getvalueforkey",
                "is.workflow.actions.speaktext",
                "is.workflow.actions.showresult",
                "is.workflow.actions.output",
            ],
        )

    def test_actions_explicitly_wired(self):
        from flowcrate.shortcut import build_workflow

        wf = build_workflow("http://x/api/play-latest", "tok")
        actions = wf["WFWorkflowActions"]
        download_uuid = actions[0]["WFWorkflowActionParameters"]["UUID"]
        dict_params = actions[1]["WFWorkflowActionParameters"]
        dict_uuid = dict_params["UUID"]
        self.assertTrue(download_uuid)
        self.assertTrue(dict_uuid)
        # Get Dictionary Value must consume the URL response explicitly.
        self.assertEqual(dict_params["WFInput"]["Value"]["OutputUUID"], download_uuid)
        self.assertEqual(dict_params["WFInput"]["Value"]["Type"], "ActionOutput")
        # Speak Text, Show Result, and Stop and Output must reference the
        # dictionary value.
        for idx, key in ((2, "WFText"), (3, "Text"), (4, "WFOutput")):
            text = actions[idx]["WFWorkflowActionParameters"][key]["Value"]
            attachment = text["attachmentsByRange"]["{0, 1}"]
            self.assertEqual(attachment["OutputUUID"], dict_uuid)
            self.assertEqual(attachment["Type"], "ActionOutput")
            self.assertEqual(text["string"], "￼")


class SiriShortcutRouteTests(unittest.TestCase):
    def _app(self):
        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_400_when_no_token_configured(self):
        with patch("flowcrate.app.load_config", return_value=_cfg(api_token="")):
            response = self._app().test_client().get("/api/siri-shortcut")
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("API token", data["error"])

    def test_success_returns_signed_attachment(self):
        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.signed_shortcut", return_value=b"SIGNED") as sign:
            response = self._app().test_client().get("/api/siri-shortcut")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"SIGNED")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("Play Flow Crate.shortcut", response.headers["Content-Disposition"])
        sign.assert_called_once()
        url_arg = sign.call_args[0][0]
        self.assertIn("/api/play-latest", url_arg)

    def test_502_on_shortcut_error(self):
        from flowcrate.shortcut import ShortcutError

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.signed_shortcut", side_effect=ShortcutError("boom")):
            response = self._app().test_client().get("/api/siri-shortcut")
        self.assertEqual(response.status_code, 502)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "boom")


if __name__ == "__main__":
    unittest.main()
