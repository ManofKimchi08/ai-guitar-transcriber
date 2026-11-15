"""
Tests for what the desktop GUI relies on outside its widgets: saved settings, the list
of converted songs, and cancelling a run (the pipeline stops at its next update, and a
running subprocess such as Demucs is killed).
"""

import os
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import torch  # noqa: F401  (the separator imports it; only its subprocess runner is tested)
except ImportError:
    sys.modules["torch"] = types.ModuleType("torch")

from src.gui_state import load_settings, save_settings, find_songs, matching_pdf
from src.separator import run_streaming
from src.cancellation import PipelineCancelled, cancellable


def touch(path, age=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()
    os.utime(path, (time.time() - age,) * 2)


class TestSettings(unittest.TestCase):
    def test_round_trip_and_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gui_settings.json")
            self.assertEqual(load_settings(path), {})  # no file yet
            save_settings(path, {"parts": ["bass", "vocals"], "lyrics_language": "한국어", "vram": "4gb"})
            self.assertEqual(load_settings(path)["parts"], ["bass", "vocals"])
            self.assertEqual(load_settings(path)["lyrics_language"], "한국어")
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(load_settings(path), {})  # a broken file falls back to defaults


class TestSongs(unittest.TestCase):
    def test_songs_newest_first_with_their_pdf(self):
        with tempfile.TemporaryDirectory() as root:
            touch(os.path.join(root, "Old Song", "scores", "Old Song.musicxml"), age=7200)
            touch(os.path.join(root, "Old Song", "scores", "Old Song_TAB.pdf"))
            touch(os.path.join(root, "New Song", "scores", "New Song.musicxml"), age=60)
            os.makedirs(os.path.join(root, "Unfinished", "stems"))  # separated, never scored
            songs = find_songs(root)
            self.assertEqual([s["name"] for s in songs], ["New Song", "Old Song"])
            self.assertIsNone(songs[0]["pdf"])
            self.assertTrue(songs[1]["pdf"].endswith("Old Song_TAB.pdf"))
            self.assertEqual(songs[1]["dir"], os.path.join(root, "Old Song"))
            self.assertEqual((songs[0]["tags"], songs[1]["tags"]), ([], ["TAB"]))
            touch(os.path.join(root, "New Song", "scores", "New Song_TAB_Score.pdf"))
            touch(os.path.join(root, "New Song", "scores", "New Song_lyrics.txt"))
            self.assertEqual(find_songs(root)[0]["tags"], ["TAB + 오선보", "가사"])
            self.assertEqual(find_songs(os.path.join(root, "missing")), [])

    def test_pdf_falls_back_to_the_newest_beside_the_score(self):
        with tempfile.TemporaryDirectory() as d:
            score = os.path.join(d, "song.musicxml")
            touch(score)
            self.assertIsNone(matching_pdf(score))
            touch(os.path.join(d, "older.pdf"), age=100)
            touch(os.path.join(d, "newer.pdf"))
            self.assertTrue(matching_pdf(score).endswith("newer.pdf"))


class TestCancel(unittest.TestCase):
    def test_cancel_is_not_swallowed_by_fallbacks(self):
        cancel = threading.Event()
        log = cancellable(lambda text: None, cancel)
        log("still running")
        cancel.set()
        with self.assertRaises(PipelineCancelled):
            try:
                log("next update")
            except Exception:  # a stage's fallback must not catch the cancel
                self.fail("PipelineCancelled was caught as an Exception")

    def test_running_subprocess_is_killed(self):
        """A callback that raises (a cancel) stops a long-running command at once."""
        cancel = threading.Event()
        script = "import time\nfor i in range(600):\n    print(i, flush=True)\n    time.sleep(0.05)\n"
        log = cancellable(lambda text: None, cancel)
        threading.Timer(0.5, cancel.set).start()
        start = time.time()
        with self.assertRaises(PipelineCancelled):
            run_streaming([sys.executable, "-c", script], log=log)
        self.assertLess(time.time() - start, 5.0)  # the command alone would take 30 s


if __name__ == "__main__":
    unittest.main()
