"""
Tests for the stem gate: a separated stem is silenced where its instrument is not
playing (only other instruments' bleed, far under the mix), kept where it plays, with
smooth edges; a stem that never plays is emptied.
"""

import os
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.stem_gate import gate_stem

SR = 22050


def tone(freq, seconds, level):
    t = np.arange(int(seconds * SR)) / SR
    return level * np.sin(2 * np.pi * freq * t)


class TestStemGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, y):
        path = os.path.join(self.tmp.name, name + ".wav")
        sf.write(path, y, SR)
        return path

    def song(self, guitar_db_when_silent):
        """10 s: a piano throughout, a guitar for 3-6 s and only bleed (at the given level
        under the mix) elsewhere."""
        piano = tone(262, 10, 0.3)
        guitar = tone(196, 10, 10 ** (guitar_db_when_silent / 20) * 0.3)
        guitar[3 * SR:6 * SR] = tone(196, 3, 0.3)
        return self.write("piano", piano), self.write("guitar", guitar)

    def test_bleed_is_silenced_and_the_playing_part_kept(self):
        piano, guitar = self.song(-50)
        out = os.path.join(self.tmp.name, "gated.wav")
        kept = gate_stem(guitar, [piano, guitar], out, part="guitar")
        y, _ = sf.read(out)
        self.assertAlmostEqual(kept, 0.5, delta=0.11)  # 3-6 s plus half a second either side
        self.assertTrue(np.allclose(y[:2 * SR], 0.0))
        self.assertTrue(np.allclose(y[7 * SR:], 0.0))
        np.testing.assert_allclose(y[3 * SR:6 * SR], tone(196, 3, 0.3), atol=1e-4)
        # the gain ramps instead of jumping (no click to be taken for a note onset)
        self.assertLess(np.max(np.abs(np.diff(y))), 0.05)

    def test_threshold_depends_on_the_part(self):
        """A bass Demucs separated poorly still sits ~36 dB under the mix while playing: it
        is kept, where a guitar that quiet is only bleed."""
        piano, quiet = self.song(-38)
        out = os.path.join(self.tmp.name, "gated.wav")
        self.assertEqual(gate_stem(quiet, [piano, quiet], out, part="bass"), 1.0)
        self.assertLess(gate_stem(quiet, [piano, quiet], out, part="guitar"), 0.6)

    def test_missing_instrument_is_emptied(self):
        piano = self.write("piano", tone(262, 6, 0.3))
        ghost = self.write("drums", tone(80, 6, 0.3 * 10 ** (-70 / 20)))
        out = os.path.join(self.tmp.name, "gated.wav")
        self.assertEqual(gate_stem(ghost, [piano, ghost], out, part="drums"), 0.0)
        self.assertTrue(np.allclose(sf.read(out)[0], 0.0))


if __name__ == "__main__":
    unittest.main()
