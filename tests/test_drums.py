"""
Tests for drum transcription on a synthetic rock beat with a known hit list: kick, snare,
closed / open (choked) hi-hat and crash, at several tempos, with uneven stroke strength
and tonal bleed from other instruments.
"""

import os
import sys
import unittest

import numpy as np
import pretty_midi
import soundfile as sf
from scipy.signal import butter, sosfilt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.drum_transcriber import transcribe_drums

KICK, SNARE, CLOSED, OPEN, CRASH = 36, 38, 42, 46, 49
OUT_DIR = os.path.join(os.path.dirname(__file__), "test_output", "drums")


def make_beat(bpm=110, bars=4, sr=44100, seed=0, vary=0.0, bleed=0.0):
    """
    Eighth-note hats (open on the "and" of 4, closed again on the next eighth), kick on
    1, 3 and the "and" of 3, snare on 2 and 4, a crash replacing the hat on bar 1.
    `vary` lowers each stroke by up to that fraction; `bleed` adds sustained tones.
    """
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    y = np.zeros(int((bars * 4 * beat + 2.5) * sr))

    def noise(lo, hi, n):
        sos = butter(4, [lo / (sr / 2), min(hi / (sr / 2), 0.99)], btype="band", output="sos")
        return sosfilt(sos, rng.standard_normal(n))

    def decay(n, tau):
        return np.exp(-np.arange(n) / sr / tau)

    n = int(0.3 * sr)
    t = np.arange(n) / sr
    kick = 0.9 * np.sin(2 * np.pi * np.cumsum(50 + 70 * np.exp(-t / 0.03)) / sr) * decay(n, 0.09)
    n_closed, n_crash = int(0.12 * sr), int(2.0 * sr)
    n_open = int((beat / 2 + 0.01) * sr)
    choke = np.ones(n_open)
    choke[-int(0.01 * sr):] = np.linspace(1, 0, int(0.01 * sr))
    sounds = {
        KICK: lambda: kick,
        SNARE: lambda: 0.5 * noise(1000, 8000, n) * decay(n, 0.06) + 0.4 * np.sin(2 * np.pi * 190 * t) * decay(n, 0.05),
        CLOSED: lambda: 0.25 * noise(7000, 16000, n_closed) * decay(n_closed, 0.02),
        OPEN: lambda: 0.25 * noise(6000, 16000, n_open) * decay(n_open, 0.25) * choke,
        CRASH: lambda: 0.45 * noise(3000, 16000, n_crash) * decay(n_crash, 0.6),
    }
    truth = []

    def hit(pitch, when):
        sig = sounds[pitch]() * (1.0 - vary * rng.uniform())
        i = int(when * sr)
        y[i:i + len(sig)] += sig[:len(y) - i]
        truth.append((when, pitch))

    for b in range(bars):
        bar = 0.5 + b * 4 * beat
        if b == 0:
            hit(CRASH, bar)
        for k in range(8):
            if not (b == 0 and k == 0):
                hit(OPEN if k == 7 else CLOSED, bar + k * beat / 2)
        for k in (0, 2, 2.5):
            hit(KICK, bar + k * beat)
        for k in (1, 3):
            hit(SNARE, bar + k * beat)
    if bleed:
        tt = np.arange(len(y)) / sr
        y += bleed * (np.sin(2 * np.pi * 110 * tt) + np.sin(2 * np.pi * 165 * tt) + 0.5 * np.sin(2 * np.pi * 440 * tt))
    return y / np.max(np.abs(y)) * 0.9, sr, truth


def transcribe(name, y, sr):
    os.makedirs(OUT_DIR, exist_ok=True)
    wav = os.path.join(OUT_DIR, name + ".wav")
    sf.write(wav, y, sr)
    pm = pretty_midi.PrettyMIDI(transcribe_drums(wav, os.path.join(OUT_DIR, name + ".mid")))
    return [(n.start, n.pitch) for inst in pm.instruments for n in inst.notes]


def match(truth, pred, pitch, tol=0.04):
    """(found, total, false, timing errors in s) for one instrument."""
    tr = [t for t, p in truth if p == pitch]
    pr = [t for t, p in pred if p == pitch]
    errors = [min(pr, key=lambda u: abs(u - t)) - t for t in tr if any(abs(u - t) < tol for u in pr)]
    false = sum(not any(abs(u - t) < tol for t in tr) for u in pr)
    return len(errors), len(tr), false, errors


class TestDrumTranscription(unittest.TestCase):
    def check(self, name, min_hat_recall=0.75, **beat):
        y, sr, truth = make_beat(**beat)
        pred = transcribe(name, y, sr)
        for pitch in (KICK, SNARE, CLOSED, OPEN, CRASH):
            found, total, false, errors = match(truth, pred, pitch)
            # Closed hats may hide under a ringing crash; everything else is found
            recall = min_hat_recall if pitch == CLOSED else 1.0
            self.assertGreaterEqual(found, recall * total, f"{name}: drum {pitch} found {found}/{total}")
            self.assertEqual(false, 0, f"{name}: drum {pitch} has {false} false hits")
            self.assertLess(max(abs(e) for e in errors), 0.025, f"{name}: drum {pitch} timing")

    def test_steady_beat(self):
        self.check("steady", bpm=110)

    def test_soft_strokes_and_bleed(self):
        self.check("varied_slow", bpm=85, seed=4, vary=0.5, bleed=0.03)

    def test_fast_tempo(self):
        self.check("fast", bpm=170, seed=6, vary=0.4, min_hat_recall=0.6)

    def test_timing_does_not_drift(self):
        """Hits late in a song land where they were played (the frame step is not 5 ms exactly)."""
        y, sr, truth = make_beat(bpm=120, bars=24, seed=1)
        pred = transcribe("long", y, sr)
        late = [(t, p) for t, p in truth if t > 40]
        found, total, _, errors = match(late, [(t, p) for t, p in pred if t > 39], KICK)
        self.assertEqual(found, total)
        self.assertLess(abs(np.median(errors)), 0.01)

    def test_kicks_alone_give_no_cymbals(self):
        """With no cymbals at all, the faint clicks of the kicks are not taken for hi-hats."""
        sr = 22050
        t = np.arange(sr * 6) / sr
        y = np.zeros_like(t)
        for k in range(12):
            i = int(k * 0.5 * sr)
            y[i:i + 2000] += np.sin(2 * np.pi * 60 * t[:2000]) * np.exp(-30 * t[:2000])
        pred = transcribe("kicks_only", y, sr)
        self.assertEqual([p for _, p in pred], [KICK] * 12)

    def test_silence_gives_no_hits(self):
        self.assertEqual(transcribe("silence", np.zeros(44100 * 2), 44100), [])


if __name__ == "__main__":
    unittest.main()
