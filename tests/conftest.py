"""
pytest configuration.

The scripts below are manual experiments, not tests: they run on import and need a
GPU, model downloads, local songs under output/ or a MuseScore install. Run them
directly (python tests/<script>.py); `pytest tests/` runs the automated tests only.
"""

collect_ignore = [
    "test_basic_pitch.py",
    "test_crepe_segmenter.py",
    "test_demucs.py",
    "test_full_score_tabs.py",
    "test_lily.py",
    "test_live_edit.py",
    "test_pdf.py",
    "test_tab_fixed.py",
    "test_tab_pdf.py",
]
