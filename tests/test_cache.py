import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spotipy.exceptions import SpotifyException

from flowcrate.cache import (
    connect,
    dashboard_data,
    import_seed,
    parse_metadata,
    refresh_from_flowstate,
    replace_cache,
    row_readiness,
    selected_track_uris,
)
from flowcrate.spotify import SpotifyManager, SpotifyRateLimitError, parse_spotify_url, set_spotify_rate_limit, spotify_service_state


class CacheTests(unittest.TestCase):
    def test_parse_metadata_splits_duration_and_notes(self):
        parsed = parse_metadata("39m, no vocals, ambient")

        self.assertEqual(parsed["duration_text"], "39m")
        self.assertEqual(parsed["duration_minutes"], 39)
        self.assertEqual(parsed["notes"], "no vocals, ambient")
        self.assertEqual(parsed["raw_metadata"], "39m, no vocals, ambient")

    def test_seed_import_is_idempotent_and_reads_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            payload = _seed_payload()

            with connect(db_path) as conn:
                import_seed(conn, payload)
                import_seed(conn, payload)

            data = dashboard_data(db_path)
            self.assertEqual(data["latest"]["title"], "Latest")
            self.assertEqual(data["cache_status"]["state"], "seed")
            self.assertTrue(data["cache_status"]["needs_refresh"])
            self.assertEqual(len(data["latest_entries"]), 1)
            self.assertEqual(data["latest_summary"]["track_count"], 2)
            self.assertEqual(data["latest_entries"][0]["notes"], "no vocals")

    def test_refreshed_cache_reports_last_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            with connect(db_path) as conn:
                replace_cache(conn, _seed_payload(), cache_source="flowstate")

            data = dashboard_data(db_path)

        self.assertEqual(data["cache_status"]["state"], "fresh")
        self.assertEqual(data["cache_status"]["label"], "Last refreshed")
        self.assertFalse(data["cache_status"]["needs_refresh"])

    def test_dashboard_data_groups_posts_and_preserves_row_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            with connect(db_path) as conn:
                import_seed(conn, _two_post_payload())

            data = dashboard_data(db_path)

        self.assertEqual(data["latest_post"]["title"], "Latest")
        self.assertEqual([entry["row_id"] for entry in data["latest_post"]["entries"]], ["entry-latest"])
        self.assertEqual([post["title"] for post in data["archive_posts"]], ["Previous"])
        self.assertEqual([entry["row_id"] for entry in data["archive_posts"][0]["entries"]], ["entry-previous"])
        self.assertEqual([entry["row_id"] for entry in data["archive_entries"]], ["entry-previous"])

    def test_selected_track_uris_flattens_album_children_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            with connect(db_path) as conn:
                import_seed(conn, _seed_payload())

            uris = selected_track_uris(["entry-latest"], db_path)

        self.assertEqual(uris, ["spotify:track:one", "spotify:track:two"])

    def test_selected_track_uris_orders_posts_entries_and_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            with connect(db_path) as conn:
                import_seed(conn, _two_post_payload())

            uris = selected_track_uris(["entry-previous", "entry-latest"], db_path)

        self.assertEqual(uris, ["spotify:track:one", "spotify:track:two", "spotify:track:previous"])

    def test_parse_spotify_url_without_api_call(self):
        parsed = parse_spotify_url("https://open.spotify.com/intl-en/album/abc123?si=token")

        self.assertEqual(parsed["uri"], "spotify:album:abc123")
        self.assertEqual(parsed["item_type"], "album")

    def test_incremental_refresh_stops_at_cached_unchanged_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            with connect(db_path) as conn:
                replace_cache(conn, _seed_payload(), cache_source="flowstate")

            posts = [
                {"title": "Latest", "url": "https://www.flowstate.fm/p/latest", "date": "2026-05-22"},
                {"title": "Older", "url": "https://www.flowstate.fm/p/older", "date": "2026-05-21"},
            ]
            with patch("flowcrate.cache.get_recent_posts", return_value=posts), patch(
                "flowcrate.cache.extract_source_post"
            ) as extract:
                count = refresh_from_flowstate(limit=2, db_path=db_path)

            self.assertEqual(count, 0)
            extract.assert_not_called()

    def test_incremental_refresh_persists_raw_snapshot_and_direct_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            post = {"title": "New", "url": "https://www.flowstate.fm/p/new", "date": "2026-05-24"}
            source = {
                "title": "New",
                "source_date": "2026-05-24",
                "raw_html": "<html>snapshot</html>",
                "items": [
                    {
                        "artist": "Artist",
                        "name": "Song",
                        "type": "track",
                        "spotify_link": "https://open.spotify.com/track/track123?si=abc",
                        "metadata": "5m",
                        "raw_text": "Song - Artist",
                        "source_url": post["url"],
                        "source_date": "2026-05-24",
                    }
                ],
            }

            with patch("flowcrate.cache.get_recent_posts", return_value=[post]), patch(
                "flowcrate.cache.extract_source_post", return_value=source
            ):
                count = refresh_from_flowstate(limit=1, db_path=db_path)

            data = dashboard_data(db_path)
            self.assertEqual(count, 1)
            self.assertEqual(data["latest_entries"][0]["spotify_uri"], "spotify:track:track123")
            self.assertEqual(data["latest_entries"][0]["readiness_status"], "Ready")
            with connect(db_path) as conn:
                row = conn.execute("SELECT raw_source_html FROM posts WHERE url = ?", (post["url"],)).fetchone()
            self.assertEqual(row["raw_source_html"], "<html>snapshot</html>")

    def test_row_readiness_statuses(self):
        cases = [
            ({"spotify_uri": "spotify:track:one", "match_status": "FOUND"}, "Ready"),
            ({"spotify_uri": "spotify:album:one", "match_status": "FOUND"}, "Linked"),
            ({"match_status": "NEEDS_MATCH"}, "Needs Match"),
            ({"match_status": "NOT_FOUND"}, "Not Found"),
            ({"match_status": "FAILED", "failure_reason": "Nope"}, "Failed"),
        ]

        for entry, expected in cases:
            self.assertEqual(row_readiness(entry)["readiness_status"], expected)

    def test_selected_track_uris_expands_only_selected_linked_albums(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "flowcrate.db"
            with connect(db_path) as conn:
                import_seed(conn, _linked_album_payload())

            spotify = Mock()
            spotify.get_album_track_rows.return_value = [
                {"uri": "spotify:track:a", "artist": "A", "name": "A1", "track_number": 1, "disc_number": 1},
                {"uri": "spotify:track:b", "artist": "A", "name": "A2", "track_number": 2, "disc_number": 1},
            ]
            uris = selected_track_uris(["linked-one"], db_path=db_path, spotify=spotify, expand_albums=True)

        self.assertEqual(uris, ["spotify:track:a", "spotify:track:b"])
        spotify.get_album_track_rows.assert_called_once_with("spotify:album:linked")

    def test_spotify_rate_limit_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "spotify_state.json"
            with patch("flowcrate.spotify.SPOTIFY_STATE_FILE", state_file):
                retry_until = set_spotify_rate_limit(30)
                state = spotify_service_state()

        self.assertTrue(state["active"])
        self.assertEqual(state["retry_until"], retry_until)
        self.assertIn("rate-limited", state["message"])

    def test_spotify_429_fails_fast_and_persists_state(self):
        manager = SpotifyManager.__new__(SpotifyManager)
        calls = Mock(side_effect=SpotifyException(429, -1, "limited", headers={"Retry-After": "20"}))
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "spotify_state.json"
            with patch("flowcrate.spotify.SPOTIFY_STATE_FILE", state_file):
                with self.assertRaises(SpotifyRateLimitError):
                    manager._with_retry(calls)
                state = spotify_service_state()

        self.assertEqual(calls.call_count, 1)
        self.assertTrue(state["active"])


def _seed_payload():
    return {
        "posts": [
            {
                "title": "Latest",
                "url": "https://www.flowstate.fm/p/latest",
                "date": "2026-05-22",
                "entries": [
                    {
                        "row_id": "entry-latest",
                        "raw_scraped_text": "Album - Artist (39m, no vocals) Spotify",
                        "parsed_artist": "Artist",
                        "parsed_name": "Album",
                        "parsed_type": "album",
                        "source_url": "https://www.flowstate.fm/p/latest",
                        "source_date": "2026-05-22",
                        "match_status": "FOUND",
                        "match_type": "DIRECT_LINK",
                        "spotify_uri": "spotify:album:album",
                        "spotify_artist": "Artist",
                        "spotify_name": "Album",
                        "spotify_link": "https://open.spotify.com/album/album",
                        "is_album_expanded": True,
                        "children": [
                            {
                                "row_id": "track-one",
                                "spotify_uri": "spotify:track:one",
                                "spotify_artist": "Artist",
                                "spotify_name": "One",
                                "duration_ms": 120000,
                                "track_number": 1,
                                "disc_number": 1,
                            },
                            {
                                "row_id": "track-two",
                                "spotify_uri": "spotify:track:two",
                                "spotify_artist": "Artist",
                                "spotify_name": "Two",
                                "duration_ms": 180000,
                                "track_number": 2,
                                "disc_number": 1,
                            },
                        ],
                    }
                ],
            }
        ]
    }


def _two_post_payload():
    payload = _seed_payload()
    payload["posts"].append(
        {
            "title": "Previous",
            "url": "https://www.flowstate.fm/p/previous",
            "date": "2026-05-18",
            "entries": [
                {
                    "row_id": "entry-previous",
                    "raw_scraped_text": "Track - Previous Artist (5m) Spotify",
                    "parsed_artist": "Previous Artist",
                    "parsed_name": "Track",
                    "parsed_type": "track",
                    "source_url": "https://www.flowstate.fm/p/previous",
                    "source_date": "2026-05-18",
                    "match_status": "FOUND",
                    "match_type": "DIRECT_LINK",
                    "spotify_uri": "spotify:track:previous",
                    "spotify_artist": "Previous Artist",
                    "spotify_name": "Track",
                    "spotify_link": "https://open.spotify.com/track/previous",
                }
            ],
        }
    )
    return payload


def _linked_album_payload():
    return {
        "posts": [
            {
                "title": "Linked",
                "url": "https://www.flowstate.fm/p/linked",
                "date": "2026-05-25",
                "entries": [
                    {
                        "row_id": "linked-one",
                        "raw_scraped_text": "Album - Artist Spotify",
                        "parsed_artist": "Artist",
                        "parsed_name": "Album",
                        "parsed_type": "album",
                        "source_url": "https://www.flowstate.fm/p/linked",
                        "source_date": "2026-05-25",
                        "match_status": "FOUND",
                        "match_type": "DIRECT_LINK",
                        "spotify_uri": "spotify:album:linked",
                        "spotify_artist": "Artist",
                        "spotify_name": "Album",
                        "spotify_link": "https://open.spotify.com/album/linked",
                    },
                    {
                        "row_id": "linked-two",
                        "raw_scraped_text": "Other - Artist Spotify",
                        "parsed_artist": "Artist",
                        "parsed_name": "Other",
                        "parsed_type": "album",
                        "source_url": "https://www.flowstate.fm/p/linked",
                        "source_date": "2026-05-25",
                        "match_status": "FOUND",
                        "match_type": "DIRECT_LINK",
                        "spotify_uri": "spotify:album:other",
                        "spotify_artist": "Artist",
                        "spotify_name": "Other",
                        "spotify_link": "https://open.spotify.com/album/other",
                    },
                ],
            }
        ]
    }


if __name__ == "__main__":
    unittest.main()
