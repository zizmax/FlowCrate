import unittest
from unittest.mock import patch

from flowcrate.app import create_app


class AppTests(unittest.TestCase):
    def test_dashboard_renders_latest_and_archive(self):
        app = create_app()
        app.config.update(TESTING=True)

        with patch("flowcrate.app.dashboard_data", return_value=_dashboard_payload()):
            response = app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Latest Flow State", html)
        self.assertIn("Play Latest", html)
        self.assertNotIn("Latest Entries", html)
        self.assertNotIn("Play Now", html)
        self.assertIn('class="play-button"', html)
        self.assertIn("Seeded sample data", html)
        self.assertIn("Refresh explicitly", html)
        self.assertIn("select-all", html)
        self.assertIn("post-group-row", html)
        self.assertIn("Ready", html)
        self.assertIn("title=\"Track URI(s) are available", html)
        self.assertIn("child-track-row", html)
        self.assertNotIn("artist-continuation", html)
        self.assertIn("Archive", html)


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
            "summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "track_count": 9, "duration": "61m", "notes": "no vocals"},
            "playable_entry_count": 1,
            "linked_entry_count": 0,
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
                        "readiness_key": "status-ready",
                        "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
                        "children": [],
                    }
                ],
                "summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "track_count": 8, "duration": "42m", "notes": ""},
                "playable_entry_count": 1,
                "linked_entry_count": 0,
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
                "readiness_key": "status-ready",
                "readiness_tooltip": "Track URI(s) are available for playback, queue, and playlist actions.",
                "children": [],
            }
        ],
        "cache_path": "/tmp/flowcrate.db",
        "cache_status": {
            "state": "seed",
            "label": "Seeded sample data",
            "value": "2026-05-22T09:00:00",
            "needs_refresh": True,
            "is_seeded": True,
        },
        "spotify_state": {"active": False, "message": ""},
        "latest_summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "track_count": 9, "duration": "61m", "notes": "no vocals"},
        "archive_summary": {"entry_count": 1, "linked_count": 0, "actionable_count": 1, "track_count": 8, "duration": "42m", "notes": ""},
    }


if __name__ == "__main__":
    unittest.main()
