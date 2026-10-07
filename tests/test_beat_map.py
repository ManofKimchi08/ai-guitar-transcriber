"""
Tests for the beat map on synthetic band audio with known beat times: bar lines that
follow a drifting tempo, the tempo octave (eighth-note or half-time tracking), and the
score's playback tempo following the bars.
"""

import os
import sys
import unittest
import xml.etree.ElementTree as ET

import numpy as np
import pretty_midi
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.beat_map import analyze_beat_map
from src.tab_builder import build_musicxml_score

OUT_DIR = os.path.join(os.path.dirname(__file__), "test_output", "beat_map")
SR = 22050


def beat_times(bpm_per_beat, lead_in=0.6):
    return lead_in + np.concatenate([[0.0], np.cumsum(60.0 / np.asarray(bpm_per_beat)[:-1])])


def band(beats, seed=0):
    """Kick on 1 and 3, snare on 2 and 4, eighth hi-hats, a bass note and chord per bar."""
    rng = np.random.default_rng(seed)
    y = np.zeros(int((beats[-1] + 2.0) * SR))

    def add(sig, t):
        i = int(t * SR)
        y[i:i + len(sig)] += sig[:max(0, len(y) - i)]

    tt = np.arange(int(0.25 * SR)) / SR
    kick = 0.9 * np.sin(2 * np.pi * (50 + 60 * np.exp(-30 * tt)) * tt) * np.exp(-12 * tt)
    snare = 0.5 * rng.standard_normal(len(tt)) * np.exp(-18 * tt) + 0.3 * np.sin(2 * np.pi * 200 * tt) * np.exp(-20 * tt)
    th = np.arange(int(0.05 * SR)) / SR
    hat = 0.15 * rng.standard_normal(len(th)) * np.exp(-60 * th)
    chords = [(36, (48, 52, 55)), (33, (45, 48, 52)), (31, (43, 47, 50)), (28, (40, 43, 47))]
    period = np.diff(beats, append=2 * beats[-1] - beats[-2])
    for j, t in enumerate(beats):
        if j % 4 == 0:
            root, triad = chords[(j // 4) % 4]
            bar_t = np.arange(int(4 * period[j] * SR)) / SR
            add(0.5 * np.sin(2 * np.pi * pretty_midi.note_number_to_hz(root) * bar_t) * np.exp(-1.5 * bar_t), t)
            add(sum(0.08 * np.sin(2 * np.pi * pretty_midi.note_number_to_hz(p + 12) * bar_t) for p in triad), t)
        add(kick if j % 2 == 0 else snare, t)
        add(hat, t)
        add(hat, t + period[j] / 2)
    return y / np.max(np.abs(y))


def write(name, y):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name + ".wav")
    sf.write(path, y, SR)
    return path


class TestBeatMap(unittest.TestCase):
    def test_bars_follow_a_drifting_tempo(self):
        """An eighth-note line stays on its bar and beat while the band speeds up and drifts."""
        n = 96
        rng = np.random.default_rng(3)
        curves = {"ramp": 100 * (1 + 0.15 * np.arange(n) / n),                       # +15% over the song
                  "drift": 100 * (1 + np.repeat(np.clip(np.cumsum(rng.normal(0, 0.012, n // 4)), -0.08, 0.08), 4))}
        for name, curve in curves.items():
            beats = beat_times(curve)
            beat_map = analyze_beat_map(write(name, band(beats)))
            self.assertLess(abs(beat_map["bpm"] / np.mean(curve) - 1), 0.05, name)

            notes, want = [], []
            for j in range(n - 1):
                for half in (0, 1):
                    t = beats[j] + half * (beats[j + 1] - beats[j]) / 2
                    notes.append(pretty_midi.Note(velocity=90, pitch=60 + (2 * j + half) % 12, start=t, end=t + 0.2))
                    want.append((1 + j // 4, (j % 4) + half / 2))
            pm = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(program=29)
            inst.notes = notes
            pm.instruments.append(inst)
            mid = os.path.join(OUT_DIR, name + ".mid")
            pm.write(mid)
            xml = os.path.join(OUT_DIR, name + ".musicxml")
            build_musicxml_score("", "", mid, "", xml, selected_parts=["lead"], bpm=beat_map["bpm"],
                                 downbeat=beat_map["downbeat"], beats=beat_map["beats"])

            written = []
            for m in ET.parse(xml).getroot().find("part").findall("measure"):
                pos = 0
                for note in m.findall("note"):
                    if note.find("rest") is None and note.find("tie") is None:
                        written.append((int(m.get("number")), pos / 8))
                    pos += int(note.findtext("duration"))
            self.assertEqual(written, want, name)

    def test_tempo_octave_and_downbeat(self):
        """Slow songs are not doubled (eighth-note tracking), fast ones not halved (half-time tracking)."""
        for bpm in (68, 82, 176):
            beats = beat_times(np.full(48, float(bpm)))
            beat_map = analyze_beat_map(write(f"steady_{bpm}", band(beats, seed=bpm)))
            self.assertLess(abs(beat_map["bpm"] / bpm - 1), 0.01, f"{bpm} BPM read as {beat_map['bpm']:.1f}")
            bar = 4 * 60.0 / bpm
            error = (beat_map["downbeat"] - beats[0] + bar / 2) % bar - bar / 2
            self.assertLess(abs(error), 0.03, f"{bpm} BPM downbeat off by {error:.3f}s")

    def test_playback_tempo_follows_the_bars(self):
        """The printed tempo is the average; bars that drift from it get their own playback tempo."""
        beats = beat_times(np.linspace(100, 115, 64))
        note = pretty_midi.Note(velocity=90, pitch=60, start=beats[0], end=beats[-1])
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=29)
        inst.notes = [note]
        pm.instruments.append(inst)
        os.makedirs(OUT_DIR, exist_ok=True)
        mid = os.path.join(OUT_DIR, "tempo.mid")
        pm.write(mid)
        xml = os.path.join(OUT_DIR, "tempo.musicxml")
        build_musicxml_score("", "", mid, "", xml, selected_parts=["lead"], bpm=107.0, downbeat=beats[0], beats=beats)
        root = ET.parse(xml).getroot()
        self.assertEqual(root.findtext(".//metronome/per-minute"), "107")
        tempos = [float(s.get("tempo")) for s in root.iter("sound")]
        self.assertAlmostEqual(tempos[0], 100.7, delta=1.0)    # bar 1 plays at its own tempo
        self.assertLess(abs(114.3 / tempos[-1] - 1), 0.03)     # ...and the last within 3% of the end's
        self.assertTrue(all(b > a for a, b in zip(tempos, tempos[1:])))


if __name__ == "__main__":
    unittest.main()
