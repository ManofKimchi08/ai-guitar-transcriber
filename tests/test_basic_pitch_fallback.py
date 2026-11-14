"""
The basic-pitch fallback (used when TorchCREPE fails), installed without TensorFlow so it
runs on onnxruntime: every note of a melody comes back in one instrument. Skipped when
basic-pitch is not installed.
"""

import os
import sys
import unittest

import numpy as np
import pretty_midi
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import basic_pitch.inference  # noqa: F401
    HAVE_BASIC_PITCH = True
except ImportError:
    HAVE_BASIC_PITCH = False

from src.pitch_transcriber import transcribe_with_basic_pitch


@unittest.skipUnless(HAVE_BASIC_PITCH, "basic-pitch is not installed")
class TestBasicPitchFallback(unittest.TestCase):
    def test_melody_comes_back_in_one_instrument(self):
        sr = 22050
        melody = [64, 67, 69, 71, 72, 71, 69, 67]
        y = np.zeros(int(sr * 4.5))
        for k, p in enumerate(melody):
            t = np.arange(int(0.45 * sr)) / sr
            f = 440 * 2 ** ((p - 69) / 12)
            i = int((0.25 + k * 0.5) * sr)
            y[i:i + len(t)] += 0.3 * sum(np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3)) * np.exp(-3 * t)
        out_dir = os.path.join(os.path.dirname(__file__), "test_output", "basic_pitch")
        os.makedirs(out_dir, exist_ok=True)
        wav = os.path.join(out_dir, "melody.wav")
        sf.write(wav, y, sr)
        midi = pretty_midi.PrettyMIDI(transcribe_with_basic_pitch(wav, os.path.join(out_dir, "melody.mid"),
                                                                  instrument_name="guitar_lead"))
        self.assertEqual(len(midi.instruments), 1)
        notes = midi.instruments[0].notes
        for k, pitch in enumerate(melody):
            near = {n.pitch for n in notes if abs(n.start - (0.25 + k * 0.5)) < 0.06}
            self.assertIn(pitch, near, f"note {k}")


if __name__ == "__main__":
    unittest.main()
