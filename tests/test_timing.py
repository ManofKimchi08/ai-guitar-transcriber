"""
Tests for timing analysis on synthetic audio with a known answer: tempo and downbeat
estimation (bar lines), and splitting re-struck notes of the same pitch.
Needs librosa, torch and torchcrepe (TorchCREPE runs on CPU here).
"""

import os
import sys
import unittest

import numpy as np
import soundfile as sf
import pretty_midi

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pipeline import analyze_beats
from src.pitch_transcriber import transcribe_with_crepe


def make_band_audio(bpm, lead_in, bars=12, sr=22050, seed=0):
    """4/4 groove: kick on 1 and 3, snare on 2 and 4, eighth hi-hats, and a bass note
    plus chord pad that change on every downbeat. The first downbeat is at `lead_in` s."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    y = np.zeros(int((lead_in + bars * 4 * beat + 1.0) * sr))

    def add(sig, t):
        i = int(t * sr)
        y[i:i + len(sig)] += sig[:max(0, len(y) - i)]

    tt = np.arange(int(0.25 * sr)) / sr
    kick = 0.9 * np.sin(2 * np.pi * (50 + 60 * np.exp(-30 * tt)) * tt) * np.exp(-12 * tt)
    snare = 0.5 * rng.standard_normal(len(tt)) * np.exp(-18 * tt) + 0.3 * np.sin(2 * np.pi * 200 * tt) * np.exp(-20 * tt)
    th = np.arange(int(0.05 * sr)) / sr
    hat = 0.15 * rng.standard_normal(len(th)) * np.exp(-60 * th)
    progression = [(36, (48, 52, 55)), (33, (45, 48, 52)), (31, (43, 47, 50)), (28, (40, 43, 47))]
    for b in range(bars):
        t0 = lead_in + b * 4 * beat
        root, triad = progression[b % 4]
        bar_t = np.arange(int(4 * beat * sr)) / sr
        add(0.5 * np.sin(2 * np.pi * pretty_midi.note_number_to_hz(root) * bar_t) * np.exp(-1.5 * bar_t), t0)
        add(sum(0.08 * np.sin(2 * np.pi * pretty_midi.note_number_to_hz(p + 12) * bar_t) for p in triad), t0)
        for k in range(4):
            add(kick if k in (0, 2) else snare, t0 + k * beat)
            add(hat, t0 + k * beat)
            add(hat, t0 + (k + 0.5) * beat)
    return y / np.max(np.abs(y)), sr


class TestTiming(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output", "timing"))
        os.makedirs(self.test_dir, exist_ok=True)

    def test_tempo_and_downbeat(self):
        # 115 BPM: librosa's beat intervals alternate by a frame; numbering must not slip.
        # 160 BPM: tracked at half tempo on beats 2 and 4; bar lines must still land on beat 1.
        for bpm, lead_in, seed in ((100, 0.9, 0), (115, 0.5, 4), (160, 1.1, 5)):
            y, sr = make_band_audio(bpm, lead_in, seed=seed)
            path = os.path.join(self.test_dir, f"band_{bpm}.wav")
            sf.write(path, y, sr)

            est_bpm, downbeat = analyze_beats(path)

            ratio = est_bpm / bpm
            self.assertTrue(any(abs(ratio - octave) < 0.005 for octave in (0.5, 1.0, 2.0)),
                            f"{bpm} BPM estimated as {est_bpm:.2f}")
            bar = 4 * 60.0 / bpm
            bar_line_error = ((downbeat - lead_in + bar / 2) % bar) - bar / 2
            self.assertLess(abs(bar_line_error), 0.06, f"{bpm} BPM: downbeat {downbeat:.3f}s")

    def test_re_struck_notes_are_split(self):
        sr = 16000

        def pluck_train(f0, n, gap, floor=0.35):
            t = np.arange(int(gap * sr)) / sr
            env = floor + (1 - floor) * np.exp(-12 * t)  # decays but never goes quiet
            return 0.5 * np.tile(env * sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3)), n)

        t = np.arange(int(2.0 * sr)) / sr
        vibrato = 0.5 * np.exp(-0.3 * t) * np.sin(2 * np.pi * 440 * t + 0.8 * np.pi * np.sin(2 * np.pi * 5.5 * t))
        cases = [
            ("guitar_lead", pluck_train(440.0, 6, 0.18), [69] * 6),
            ("bass", np.concatenate([pluck_train(82.41, 4, 0.3), pluck_train(110.0, 4, 0.3)]), [40] * 4 + [45] * 4),
            ("guitar_lead", vibrato, [69]),  # one held note: vibrato must not split it
        ]
        for i, (instrument, audio, expected) in enumerate(cases):
            wav = os.path.join(self.test_dir, f"pluck_{i}.wav")
            sf.write(wav, audio, sr)
            mid = transcribe_with_crepe(wav, os.path.join(self.test_dir, f"pluck_{i}.mid"),
                                        instrument_name=instrument, device="cpu")
            pitches = [n.pitch for n in pretty_midi.PrettyMIDI(mid).instruments[0].notes]
            self.assertEqual(pitches, expected, f"case {i}")


if __name__ == "__main__":
    unittest.main()
