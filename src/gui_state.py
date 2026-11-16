"""
GUI State Module
What the desktop GUI keeps between runs without touching any widget: the user's last
choices (saved to a small JSON file) and the songs already converted under an output
folder, so they can be reopened from a list.
"""

import glob
import json
import os

SCORE_PATTERNS = ("*.musicxml", "*.xml")


def load_settings(path: str) -> dict:
    """The saved settings, or {} when there are none or the file is unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: str, settings: dict) -> None:
    """Writes the settings; a failure (read-only folder) only loses them."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def matching_pdf(score_path: str):
    """The PDF exported from a MusicXML score: same name first, else the newest beside it."""
    base = os.path.splitext(score_path)[0]
    for candidate in (f"{base}_TAB.pdf", f"{base}_TAB_Score.pdf", f"{base}.pdf"):
        if os.path.exists(candidate):
            return candidate
    pdfs = glob.glob(os.path.join(os.path.dirname(score_path), "*.pdf"))
    return max(pdfs, key=os.path.getmtime) if pdfs else None


def _newest_score(scores_dir: str):
    scores = [p for pattern in SCORE_PATTERNS for p in glob.glob(os.path.join(scores_dir, pattern))]
    return max(scores, key=os.path.getmtime) if scores else None


def _tags(scores_dir: str, pdf) -> list:
    tags = []
    if pdf:
        name = os.path.basename(pdf)
        tags.append("TAB + 오선보" if name.endswith("_TAB_Score.pdf") else "TAB" if name.endswith("_TAB.pdf") else "오선보")
    if glob.glob(os.path.join(scores_dir, "*_lyrics.txt")):
        tags.append("가사")
    return tags


def find_songs(output_root: str) -> list:
    """
    Converted songs under `output_root`, newest first: one per <song>/scores/ folder
    holding a MusicXML score (plus output/scores/ from older versions), as dicts with
    name, dir (the song's folder), score, pdf (or None), tags (what was made: the PDF
    style, lyrics) and mtime.
    """
    songs = []
    if not os.path.isdir(output_root):
        return songs
    for name in os.listdir(output_root):
        song_dir = os.path.join(output_root, name)
        scores_dir = os.path.join(song_dir, "scores")
        if name == "scores":  # results of versions that wrote everything to output/scores/
            song_dir, scores_dir = output_root, os.path.join(output_root, "scores")
        score = _newest_score(scores_dir) if os.path.isdir(scores_dir) else None
        if score:
            pdf = matching_pdf(score)
            songs.append({"name": name, "dir": song_dir, "score": score, "pdf": pdf,
                          "tags": _tags(scores_dir, pdf), "mtime": os.path.getmtime(score)})
    return sorted(songs, key=lambda s: s["mtime"], reverse=True)
