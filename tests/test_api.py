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
        # Pin the OS so the Siri section renders deterministically regardless of the
        # host running the tests (macOS shows the single signed-download button).
        # The non-macOS branch is covered by SiriShortcutRouteTests.
        with patch("flowcrate.app.load_config", return_value=empty_cfg), \
             patch("flowcrate.app.platform.system", return_value="Darwin"):
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

    def test_settings_non_macos_offers_both_shortcut_options(self):
        from flowcrate.config import AppConfig
        app = create_app()
        app.config.update(TESTING=True)
        with patch("flowcrate.app.load_config", return_value=AppConfig(api_token="tok")), \
             patch("flowcrate.app.platform.system", return_value="Linux"):
            html = app.test_client().get("/settings").get_data(as_text=True)
        self.assertIn("Download universal shortcut", html)
        self.assertIn("Download ready-to-use (unsigned)", html)
        self.assertIn("/api/siri-shortcut/universal", html)

    def test_settings_renders_spotify_onboarding(self):
        html = self._get_settings().get_data(as_text=True)
        self.assertIn("First time? Create your Spotify app (2 minutes)", html)
        self.assertIn("developer.spotify.com/dashboard", html)
        # The https callback URI suggestion must be surfaced for the Spotify app setup.
        self.assertIn("/callback", html)
        self.assertIn("Connect Spotify", html)

    def test_settings_drops_5_user_note_and_reset_form(self):
        html = self._get_settings().get_data(as_text=True)
        self.assertNotIn("limited to 5 users", html)
        self.assertNotIn("Start Fresh", html)
        # Flow State section heading and new copy phrase are present.
        self.assertIn("Flow State", html)
        self.assertIn("Public Flow State posts work by default", html)
        # Re-check access button replaces old test button.
        self.assertIn("flowstate-access-btn", html)
        self.assertNotIn("test-substack-btn", html)

    def test_settings_marks_active_nav_page(self):
        html = self._get_settings().get_data(as_text=True)
        self.assertIn('href="/settings" class="active" aria-current="page"', html)


class ApiTestSubstackTests(unittest.TestCase):
    def _app(self):
        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_ok_case(self):
        with patch("flowcrate.app.test_flowstate_fetch", return_value={"title": "Flow State"}):
            response = self._app().test_client().post("/api/test-substack")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["category"], "success")
        self.assertIn("Flow State", data["message"])

    def test_error_case(self):
        with patch("flowcrate.app.test_flowstate_fetch", side_effect=RuntimeError("blocked")):
            response = self._app().test_client().post("/api/test-substack")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["category"], "error")
        self.assertIn("blocked", data["message"])

    def test_reset_route_removed(self):
        response = self._app().test_client().post("/settings/reset")
        self.assertEqual(response.status_code, 404)


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
        # Speak Text and Show Result must reference the dictionary value.
        for idx, key in ((2, "WFText"), (3, "Text")):
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

    def test_macos_returns_signed_attachment(self):
        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.platform.system", return_value="Darwin"), \
             patch("flowcrate.app.signed_shortcut", return_value=b"SIGNED") as sign:
            response = self._app().test_client().get("/api/siri-shortcut")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"SIGNED")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("Play Flow Crate.shortcut", response.headers["Content-Disposition"])
        sign.assert_called_once()
        url_arg = sign.call_args[0][0]
        self.assertIn("/api/play-latest", url_arg)

    def test_non_macos_returns_unsigned_personalized(self):
        import plistlib

        with patch("flowcrate.app.load_config", return_value=_cfg(api_token="tok42")), \
             patch("flowcrate.app.platform.system", return_value="Linux"):
            response = self._app().test_client().get("/api/siri-shortcut")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Play Flow Crate.shortcut", response.headers["Content-Disposition"])
        workflow = plistlib.loads(response.data)
        params = workflow["WFWorkflowActions"][0]["WFWorkflowActionParameters"]
        self.assertIn("/api/play-latest", params["WFURL"])
        header = params["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"][0]
        self.assertEqual(header["WFValue"]["Value"]["string"], "tok42")

    def test_universal_route_returns_bundled_attachment(self):
        response = self._app().test_client().get("/api/siri-shortcut/universal")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Play Flow Crate.shortcut", response.headers["Content-Disposition"])
        # The bundled file is a real signed shortcut (~22 KB), not a stub.
        self.assertGreater(len(response.data), 1000)

    def test_502_on_shortcut_error(self):
        from flowcrate.shortcut import ShortcutError

        with patch("flowcrate.app.load_config", return_value=_cfg()), \
             patch("flowcrate.app.platform.system", return_value="Darwin"), \
             patch("flowcrate.app.signed_shortcut", side_effect=ShortcutError("boom")):
            response = self._app().test_client().get("/api/siri-shortcut")
        self.assertEqual(response.status_code, 502)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "boom")


class ApiFlowstateAccessTests(unittest.TestCase):
    """Tests for the GET /api/flowstate-access endpoint."""

    def _app(self):
        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_full_status_returned(self):
        result = {
            "status": "full",
            "message": "Full access — paid posts unlock via your browser session",
            "scanned": 5,
        }
        with patch("flowcrate.app.check_flowstate_access", return_value=result):
            response = self._app().test_client().get("/api/flowstate-access")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "full")
        self.assertIn("Full access", data["message"])

    def test_free_status_returned(self):
        result = {
            "status": "free",
            "message": "Free posts only — automatic session detection failed; paste your SID below",
            "scanned": 3,
        }
        with patch("flowcrate.app.check_flowstate_access", return_value=result):
            response = self._app().test_client().get("/api/flowstate-access")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "free")
        self.assertIn("Free posts only", data["message"])

    def test_none_status_returned(self):
        result = {
            "status": "none",
            "message": "No access — Flow State unreachable: timeout",
            "scanned": 0,
        }
        with patch("flowcrate.app.check_flowstate_access", return_value=result):
            response = self._app().test_client().get("/api/flowstate-access")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "none")
        self.assertIn("No access", data["message"])

    def test_unexpected_exception_returns_none_status(self):
        with patch(
            "flowcrate.app.check_flowstate_access",
            side_effect=RuntimeError("kaboom"),
        ):
            response = self._app().test_client().get("/api/flowstate-access")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "none")
        self.assertIn("kaboom", data["message"])

    def test_background_refresh_triggered_when_scanned_gt_11(self):
        result = {
            "status": "full",
            "message": "Full access — no paywalled posts found to test (checked 20)",
            "scanned": 20,
        }
        with patch("flowcrate.app.check_flowstate_access", return_value=result), \
             patch("flowcrate.app._start_background_refresh") as mock_refresh:
            self._app().test_client().get("/api/flowstate-access")
        mock_refresh.assert_called_once_with(limit=20)

    def test_background_refresh_not_triggered_when_scanned_lte_11(self):
        result = {
            "status": "full",
            "message": "Full access — paid posts unlock via your browser session",
            "scanned": 5,
        }
        with patch("flowcrate.app.check_flowstate_access", return_value=result), \
             patch("flowcrate.app._start_background_refresh") as mock_refresh:
            self._app().test_client().get("/api/flowstate-access")
        mock_refresh.assert_not_called()

    def test_background_refresh_not_triggered_when_scanned_exactly_11(self):
        result = {
            "status": "full",
            "message": "Full access — no paywalled posts found to test (checked 11)",
            "scanned": 11,
        }
        with patch("flowcrate.app.check_flowstate_access", return_value=result), \
             patch("flowcrate.app._start_background_refresh") as mock_refresh:
            self._app().test_client().get("/api/flowstate-access")
        mock_refresh.assert_not_called()


class CheckFlowstateAccessTests(unittest.TestCase):
    """Unit tests for check_flowstate_access() in scraper.py."""

    def test_returns_scanned_key_on_none_status(self):
        from flowcrate.scraper import check_flowstate_access
        with patch("flowcrate.scraper.get_recent_posts", side_effect=RuntimeError("down")):
            result = check_flowstate_access()
        self.assertEqual(result["status"], "none")
        self.assertIn("scanned", result)
        self.assertEqual(result["scanned"], 0)

    def test_returns_scanned_key_when_no_paywalled_posts(self):
        from flowcrate.scraper import check_flowstate_access
        posts = [{"url": f"https://flowstate.fm/p/post{i}"} for i in range(3)]
        # _get_html returns content that is NOT paywalled
        with patch("flowcrate.scraper.get_recent_posts", return_value=posts), \
             patch("flowcrate.scraper._plain_session", return_value=object()), \
             patch("flowcrate.scraper._get_html", return_value="<html>open content</html>"), \
             patch("flowcrate.scraper._looks_paywalled_html", return_value=False):
            result = check_flowstate_access()
        self.assertEqual(result["status"], "full")
        self.assertIn("scanned", result)
        self.assertEqual(result["scanned"], 3)

    def test_returns_scanned_key_stops_at_first_paywalled(self):
        from flowcrate.scraper import check_flowstate_access
        posts = [{"url": f"https://flowstate.fm/p/post{i}"} for i in range(5)]
        # All posts look paywalled; probe stops at first one
        with patch("flowcrate.scraper.get_recent_posts", return_value=posts), \
             patch("flowcrate.scraper._plain_session", return_value=object()), \
             patch("flowcrate.scraper._get_html", return_value="<html>paywall</html>"), \
             patch("flowcrate.scraper._looks_paywalled_html", return_value=True), \
             patch("flowcrate.scraper._browser_cookie_session", return_value=None), \
             patch("flowcrate.scraper._sid_session", return_value=None):
            result = check_flowstate_access()
        self.assertIn("scanned", result)
        self.assertEqual(result["scanned"], 1)


class LooksPaywalledTests(unittest.TestCase):
    """Unit tests for the _looks_paywalled_html() gate detector in scraper.py."""

    def _check(self, html):
        from flowcrate.scraper import _looks_paywalled_html
        return _looks_paywalled_html(html)

    def test_real_gate_by_testid_is_paywalled(self):
        self.assertTrue(self._check('<div class="paywall" data-testid="paywall">x</div>'))

    def test_real_gate_by_component_name_is_paywalled(self):
        self.assertTrue(self._check('<div data-component-name="Paywall">x</div>'))

    def test_gate_phrase_is_paywalled(self):
        self.assertTrue(self._check("<p>This post is for paid subscribers</p>"))

    def test_subscriber_scaffolding_is_not_paywalled(self):
        # Substack ships these to authenticated subscribers on *unlocked* posts;
        # a substring match on "paywall" used to flag them and break detection.
        html = (
            '<div class="paywall-jump"></div>'
            '<div data-component-name="PaywallToDOM"></div>'
            "<article>full unlocked content</article>"
        )
        self.assertFalse(self._check(html))

    def test_open_post_is_not_paywalled(self):
        self.assertFalse(self._check("<article>open content</article>"))


class SidSessionTests(unittest.TestCase):
    """_sid_session() builds a session from synced/manual Flow State cookies."""

    def _cfg(self, connect="", substack=""):
        from types import SimpleNamespace
        return SimpleNamespace(flowstate_connect_sid=connect, substack_sid=substack)

    def test_returns_none_without_any_cookie(self):
        from flowcrate import scraper
        with patch("flowcrate.scraper.load_config", return_value=self._cfg()), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("FLOWSTATE_CONNECT_SID", None)
            os.environ.pop("SUBSTACK_SID", None)
            self.assertIsNone(scraper._sid_session())

    def test_sets_connect_sid_on_flowstate_domain(self):
        from flowcrate import scraper
        with patch("flowcrate.scraper.load_config", return_value=self._cfg(connect="c1", substack="s1")):
            session = scraper._sid_session()
        jar = {(c.name, c.domain): c.value for c in session.cookies}
        self.assertEqual(jar[("connect.sid", "www.flowstate.fm")], "c1")
        self.assertEqual(jar[("substack.sid", ".substack.com")], "s1")


class DefaultBrowserNameTests(unittest.TestCase):
    """Unit tests for the _default_browser_name() scraper helper."""

    def setUp(self):
        # Clear the lru_cache between tests so each test is independent.
        from flowcrate.scraper import _default_browser_name
        _default_browser_name.cache_clear()

    def tearDown(self):
        from flowcrate.scraper import _default_browser_name
        _default_browser_name.cache_clear()

    def test_returns_none_when_plist_missing(self):
        from flowcrate.scraper import _default_browser_name
        with patch("flowcrate.scraper.Path.home", side_effect=RuntimeError("no home")):
            result = _default_browser_name()
        self.assertIsNone(result)

    def test_returns_none_when_open_raises(self):
        from flowcrate.scraper import _default_browser_name
        import builtins
        real_open = builtins.open

        def fake_open(path, *a, **kw):
            if "launchservices" in str(path).lower():
                raise FileNotFoundError("no plist")
            return real_open(path, *a, **kw)

        with patch("builtins.open", side_effect=fake_open):
            result = _default_browser_name()
        self.assertIsNone(result)

    def test_maps_chrome_bundle_id(self):
        from flowcrate.scraper import _default_browser_name
        fake_data = {
            "LSHandlers": [
                {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "com.google.Chrome"},
            ]
        }
        with patch("flowcrate.scraper.plistlib.load", return_value=fake_data), \
             patch("builtins.open", unittest.mock.mock_open()):
            result = _default_browser_name()
        self.assertEqual(result, "Chrome")

    def test_maps_firefox_bundle_id(self):
        from flowcrate.scraper import _default_browser_name
        fake_data = {
            "LSHandlers": [
                {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "org.mozilla.firefox"},
            ]
        }
        with patch("flowcrate.scraper.plistlib.load", return_value=fake_data), \
             patch("builtins.open", unittest.mock.mock_open()):
            result = _default_browser_name()
        self.assertEqual(result, "Firefox")

    def test_chromium_takes_priority_over_chrome(self):
        """Bundle IDs containing 'chromium' must map to Chromium, not Chrome."""
        from flowcrate.scraper import _default_browser_name
        fake_data = {
            "LSHandlers": [
                {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "org.chromium.Chromium"},
            ]
        }
        with patch("flowcrate.scraper.plistlib.load", return_value=fake_data), \
             patch("builtins.open", unittest.mock.mock_open()):
            result = _default_browser_name()
        self.assertEqual(result, "Chromium")

    def test_returns_none_when_no_http_handler(self):
        from flowcrate.scraper import _default_browser_name
        fake_data = {
            "LSHandlers": [
                {"LSHandlerURLScheme": "https", "LSHandlerRoleAll": "com.apple.safari"},
            ]
        }
        with patch("flowcrate.scraper.plistlib.load", return_value=fake_data), \
             patch("builtins.open", unittest.mock.mock_open()):
            result = _default_browser_name()
        self.assertIsNone(result)


class BrowserFromUserAgentTests(unittest.TestCase):
    """Unit tests for browser_from_user_agent() in scraper."""

    def _fn(self, ua):
        from flowcrate.scraper import browser_from_user_agent
        return browser_from_user_agent(ua)

    def test_firefox_ua(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0"
        self.assertEqual(self._fn(ua), "Firefox")

    def test_edge_ua(self):
        # Edge UA contains both "Edg/" and "Chrome/" — must resolve to Edge.
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"
        self.assertEqual(self._fn(ua), "Edge")

    def test_chrome_ua(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        self.assertEqual(self._fn(ua), "Chrome")

    def test_safari_ua(self):
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        self.assertEqual(self._fn(ua), "Safari")

    def test_unknown_ua_returns_none(self):
        self.assertIsNone(self._fn("curl/7.88.1"))

    def test_empty_ua_returns_none(self):
        self.assertIsNone(self._fn(""))

    def test_none_ua_returns_none(self):
        self.assertIsNone(self._fn(None))


class DashboardEntryRowClassTests(unittest.TestCase):
    """Verify that grouped entry table rows carry class 'entry-row'."""

    def test_dashboard_html_contains_entry_row_class(self):
        from flowcrate.app import create_app
        from tests.test_app import _dashboard_payload

        app = create_app()
        app.config.update(TESTING=True)
        with patch("flowcrate.app.dashboard_data", return_value=_dashboard_payload()), \
             patch("flowcrate.app.cache_is_stale", return_value=False):
            response = app.test_client().get("/")
        html = response.get_data(as_text=True)
        self.assertIn('class="entry-row"', html)


if __name__ == "__main__":
    unittest.main()
