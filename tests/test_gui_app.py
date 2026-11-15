"""
Tests that drive the real GUI window with a fake pipeline: the stage checklist follows
a run, the finished song is listed and selected, cancelling stops the run, and the
chosen options come back after a restart. Skipped where no window can be opened.
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import tkinter as tk
    import customtkinter  # noqa: F401
except ImportError as e:
    raise unittest.SkipTest(f"no Tk GUI toolkit: {e}")

import app_gui
from src.cancellation import PipelineCancelled


def fake_pipeline(input_audio, output_dir, parts=None, style="tab", vram=None, lyrics_language=None,
                  log=print, progress=None, stage=None, cancel=None):
    """Announces the stages a file run goes through and writes a score and its PDF."""
    for i, key in enumerate(["separate", "split", "transcribe"] + (["lyrics"] if "vocals" in parts else []) + ["score"]):
        if cancel.is_set():
            raise PipelineCancelled()
        stage(key)
        progress(f"{key}...", 0.2 * (i + 1))
        log(f"[*] {key}")
        time.sleep(0.05)
    song_dir = os.path.join(output_dir, "Test Song")
    score = os.path.join(song_dir, "scores", "Test Song.musicxml")
    os.makedirs(os.path.dirname(score), exist_ok=True)
    for path in (score, score.replace(".musicxml", "_TAB.pdf")):
        open(path, "w").close()
    return {"song_title": "Test Song", "song_dir": song_dir, "score_xml": score,
            "pdf_path": score.replace(".musicxml", "_TAB.pdf"), "elapsed": 0.3}


def endless_pipeline(input_audio, output_dir, log=print, progress=None, stage=None, cancel=None, **options):
    """Stays in the first stage until cancelled."""
    stage("separate")
    while not cancel.is_set():
        log("   working")
        time.sleep(0.02)
    raise PipelineCancelled()


class TestGuiApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = os.path.join(self.tmp.name, "song.wav")
        open(self.audio, "w").close()
        self.saved = (app_gui.SETTINGS_PATH, app_gui.OUTPUT_ROOT, app_gui.run_pipeline)
        app_gui.SETTINGS_PATH = os.path.join(self.tmp.name, "gui_settings.json")
        app_gui.OUTPUT_ROOT = os.path.join(self.tmp.name, "output")
        self.addCleanup(self.restore)
        self.app = self.open_app()

    def restore(self):
        app_gui.SETTINGS_PATH, app_gui.OUTPUT_ROOT, app_gui.run_pipeline = self.saved

    def open_app(self):
        try:
            app = app_gui.BandTranscriberApp()
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.addCleanup(self.close_app, app)
        app.update()
        return app

    @staticmethod
    def close_app(app):
        try:
            app.destroy()
        except tk.TclError:
            pass

    def pump(self, until, timeout=15.0):
        """Runs the window's event loop until `until()` holds."""
        deadline = time.time() + timeout
        while not until():
            self.assertLess(time.time(), deadline, "the window did not get there in time")
            self.app.update()
            time.sleep(0.01)

    def start(self, pipeline):
        app_gui.run_pipeline = pipeline
        self.app.entry_input.insert(0, self.audio)
        self.app._start_pipeline_thread()
        self.assertTrue(self.app.is_running)

    def test_run_fills_the_checklist_and_selects_the_song(self):
        self.start(fake_pipeline)
        self.assertEqual(list(self.app.stage_state), ["separate", "split", "transcribe", "score"])  # no vocals, no URL
        self.assertEqual(self.app.btn_cancel.cget("state"), "normal")
        self.pump(lambda: not self.app.is_running)
        self.assertEqual(set(self.app.stage_state.values()), {"done"})
        self.assertEqual(self.app.prog_bar.get(), 1.0)
        self.assertEqual([s["name"] for s in self.app.songs], ["Test Song"])
        self.assertEqual(self.app.lbl_song_title.cget("text"), "Test Song")
        self.assertTrue(self.app.last_pdf_path.endswith("Test Song_TAB.pdf"))
        self.assertEqual(self.app.btn_open_pdf.cget("state"), "normal")
        self.assertEqual(self.app.btn_run.cget("state"), "normal")
        self.assertEqual(self.app.btn_cancel.cget("state"), "disabled")
        self.assertIn("[*] score", self.app.txt_log.get("1.0", "end"))

    def test_vocals_add_the_lyrics_stage(self):
        self.app._set_part("vocals", True)
        self.start(fake_pipeline)
        self.assertIn("lyrics", self.app.stage_state)
        self.pump(lambda: not self.app.is_running)
        self.assertEqual(self.app.stage_state["lyrics"], "done")

    def test_cancel_stops_the_run(self):
        self.start(endless_pipeline)
        self.pump(lambda: self.app.stage_state["separate"] == "now")
        self.app._cancel_pipeline()
        self.pump(lambda: not self.app.is_running)
        self.assertEqual(self.app.stage_state["separate"], "stopped")
        self.assertEqual(self.app.stage_state["score"], "todo")
        self.assertIn("취소", self.app.lbl_status.cget("text"))
        self.assertEqual(self.app.btn_run.cget("state"), "normal")
        self.assertEqual(self.app.songs, [])

    def test_choices_come_back_after_a_restart(self):
        self.app._set_part("drums", False)
        self.app._set_part("vocals", True)
        self.app.opt_lyrics_lang.set("한국어")
        self.app.seg_style.set(app_gui.STYLE_LABELS["both"])
        self.app._on_close()  # saves, then closes (no run in progress)
        again = self.open_app()
        self.assertEqual(again._selected_parts(), ["bass", "rhythm", "lead", "vocals"])
        self.assertEqual(again.opt_lyrics_lang.get(), "한국어")
        self.assertEqual(again._selected_style(), "both")

    def test_search_filters_the_song_list(self):
        for name in ("Hotel California", "Wonderwall"):
            score = os.path.join(app_gui.OUTPUT_ROOT, name, "scores", f"{name}.musicxml")
            os.makedirs(os.path.dirname(score))
            open(score, "w").close()
        self.app.entry_output.delete(0, "end")
        self.app.entry_output.insert(0, app_gui.OUTPUT_ROOT)
        self.app._refresh_songs()
        self.assertEqual(len(self.app.song_list.winfo_children()), 2)
        self.app.entry_search.insert(0, "wonder")
        self.app._render_songs()
        self.assertEqual(len(self.app.song_list.winfo_children()), 1)
        self.app._select_song(next(s for s in self.app.songs if s["name"] == "Wonderwall"))
        self.assertEqual(self.app.lbl_song_title.cget("text"), "Wonderwall")
        self.assertEqual(self.app.btn_open_pdf.cget("state"), "disabled")  # no PDF was made for it


if __name__ == "__main__":
    unittest.main()
