import os
import unittest
from unittest.mock import MagicMock, patch

from flowcrate import sonos
from flowcrate.config import QUEUE_APPEND, QUEUE_REPLACE, AppConfig, load_config


class QueueAndPlayTests(unittest.TestCase):
    def _run(self, replace, play=True):
        speaker = MagicMock(player_name="Office")
        plugin = MagicMock()
        plugin.add_share_link_to_queue.side_effect = [1, 2] if replace else [186, 187]
        with patch("flowcrate.sonos.ShareLinkPlugin", return_value=plugin):
            result = sonos.queue_and_play(speaker, ["spotify:album:a", "spotify:album:b"], play=play, replace=replace)
        return speaker, result

    def test_replace_clears_queue_then_plays_from_top(self):
        speaker, result = self._run(replace=True)
        speaker.clear_queue.assert_called_once()
        speaker.play_from_queue.assert_called_once_with(0)
        self.assertEqual(result["queued"], 2)

    def test_append_keeps_existing_queue(self):
        speaker, result = self._run(replace=False)
        speaker.clear_queue.assert_not_called()
        speaker.play_from_queue.assert_called_once_with(185)
        self.assertEqual(result["first_position"], 186)


class QueueModeConfigTests(unittest.TestCase):
    def test_default_is_replace(self):
        self.assertEqual(AppConfig().sonos_queue_mode, QUEUE_REPLACE)

    def test_load_config_reads_append_and_ignores_unknown(self):
        for raw, expected in (("append", QUEUE_APPEND), ("bogus", QUEUE_REPLACE), ("", QUEUE_REPLACE)):
            with patch("flowcrate.config.load_dotenv"), patch.dict(os.environ, {"SONOS_QUEUE_MODE": raw}):
                self.assertEqual(load_config().sonos_queue_mode, expected, raw)
