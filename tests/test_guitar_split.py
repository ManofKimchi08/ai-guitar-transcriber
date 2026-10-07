"""
Tests for splitting the guitar stem into lead and rhythm guitar by stereo position, on
synthetic mixes: a centred lead melody over two rhythm guitar takes panned left and
right, the reverse layout, a mono guitar, and a single guitar panned to one side.
"""

import os
import sys
import unittest

import librosa
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.guitar_splitter import split_guitar_track
from src.chord_recognizer import recognize_chords, STANDARD_VOICINGS, GUITAR_BASE_PITCHES

SR = 22050
BEAT = 0.5  # 120 BPM
PROGRESSION = ["C", "G", "Am", "F"]
MELODY = [76, 74, 72, 74, 76, 76, 76, None, 74, 74, 76, 79, 79, 77, 76, None]
OUT_DIR = os.path.join(os.path.dirname(__file__), "test_output", "guitar_split")


def pluck(pitch, dur, rng, cents=0.0):
    """Plucked string: decaying harmonics with random phases (each take sounds different)."""
    t = np.arange(int(dur * SR)) / SR
    f0 = 440 * 2 ** ((pitch - 69 + cents / 100) / 12)
    sig = sum(np.sin(2 * np.pi * k * f0 * t + rng.uniform(0, 2 * np.pi)) / k * np.exp(-t * (2 + k))
              for k in range(1, 8) if k * f0 < SR / 2)
    return sig * np.minimum(1, t / 0.003)


def place(y, sig, at):
    i = int(at * SR)
    y[i:i + len(sig)] += sig[:max(0, len(y) - i)]


def rhythm_take(n, seed, cents=0.0):
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    for b, chord in enumerate(PROGRESSION):
        for k in range(8):  # eighth-note strums
            at = 0.25 + b * 4 * BEAT + k * BEAT / 2 + rng.normal(0, 0.005)
            for j, (s, f) in enumerate(sorted(STANDARD_VOICINGS[chord], reverse=True)):
                place(y, 0.2 * pluck(GUITAR_BASE_PITCHES[s] + f, BEAT / 2 + 0.05, rng, cents), at + 0.006 * j)
    return y


def lead_line(n, seed):
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    for k, p in enumerate(MELODY):
        if p is not None:
            place(y, 1.2 * pluck(p, BEAT * 0.95, rng), 0.25 + k * BEAT)
    return y


def pan(sig, p):
    """Constant-power pan, p from -1 (left) to 1 (right)."""
    a = (p + 1) * np.pi / 4
    return np.stack([sig * np.cos(a), sig * np.sin(a)], axis=1)


def magnitude_sdr(estimate, reference):
    """Scale-invariant SDR of STFT magnitudes (dB): how much of the estimate is the reference."""
    n = min(len(estimate), len(reference))
    est = np.abs(librosa.stft(estimate[:n], n_fft=2048)).ravel()
    ref = np.abs(librosa.stft(reference[:n], n_fft=2048)).ravel()
    target = (est @ ref) / (ref @ ref) * ref
    return 10 * np.log10(np.sum(target ** 2) / np.sum((est - target) ** 2))


def mono(y):
    return y.mean(axis=1) if y.ndim > 1 else y


class TestGuitarSplit(unittest.TestCase):
    n = int((0.25 + len(PROGRESSION) * 4 * BEAT + 1.0) * SR)

    def split(self, name, stereo):
        out = os.path.join(OUT_DIR, name)
        os.makedirs(out, exist_ok=True)
        wav = os.path.join(out, "guitar.wav")
        sf.write(wav, stereo / np.max(np.abs(stereo)) * 0.9, SR)
        return split_guitar_track(wav, out)

    def test_centred_lead_over_panned_rhythm_takes(self):
        lead = lead_line(self.n, 1)
        takes = pan(rhythm_take(self.n, 2), -1) + pan(rhythm_take(self.n, 3, cents=4), 1)
        res = self.split("double", pan(lead, 0) + takes)
        self.assertEqual(res["method"], "stereo")
        lead_out, rhythm_out = mono(sf.read(res["lead"])[0]), mono(sf.read(res["rhythm"])[0])

        # The lead track is mostly the lead line (plain mid/side was mostly rhythm guitar)
        mix_mono = (pan(lead, 0) + takes).mean(axis=1)
        self.assertGreater(magnitude_sdr(lead_out, lead), magnitude_sdr(mix_mono, lead) + 6.0)
        self.assertGreater(magnitude_sdr(rhythm_out, takes.mean(axis=1)), 8.0)

        # ...and the rhythm track still gives the chords
        chords = recognize_chords(res["rhythm"])
        for b, chord in enumerate(PROGRESSION):
            mid_bar = 0.25 + (b + 0.5) * 4 * BEAT
            self.assertEqual(next(c["chord"] for c in chords if c["start"] <= mid_bar < c["end"]), chord)

    def test_chords_in_the_centre_become_rhythm(self):
        """One rhythm guitar in the middle and the solo panned to a side."""
        lead = lead_line(self.n, 4)
        rhythm = rhythm_take(self.n, 5)
        res = self.split("swapped", pan(lead, 0.7) + pan(rhythm, 0))
        self.assertEqual(res["method"], "stereo-swapped")
        self.assertGreater(magnitude_sdr(mono(sf.read(res["lead"])[0]), lead), 3.0)

    def test_mono_guitar_goes_to_both_parts(self):
        res = self.split("mono", pan(lead_line(self.n, 6) + rhythm_take(self.n, 7), 0))
        self.assertEqual(res["method"], "mono")
        self.assertTrue(os.path.exists(res["lead"]) and os.path.exists(res["rhythm"]))

    def test_one_guitar_on_one_side_has_no_lead(self):
        """A single guitar panned to one side: nothing in the centre to call a lead."""
        res = self.split("one_side", pan(rhythm_take(self.n, 8), -0.9))
        self.assertIsNone(res["lead"])
        self.assertGreater(np.sqrt(np.mean(mono(sf.read(res["rhythm"])[0]) ** 2)), 0.05)


if __name__ == "__main__":
    unittest.main()
