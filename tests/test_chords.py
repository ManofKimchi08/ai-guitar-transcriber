"""
Tests for rhythm guitar chord recognition on synthetic strummed guitar (Karplus-Strong
strings playing the recognizer's own voicings), with a known chord and strum timeline.
"""

import os
import sys
import unittest

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chord_recognizer import recognize_chords, STANDARD_VOICINGS, GUITAR_BASE_PITCHES


def pluck(freq, dur, sr, rng, decay=0.996):
    """Karplus-Strong plucked string."""
    period = max(2, int(round(sr / freq)))
    buf = rng.uniform(-1, 1, period)
    out = np.empty(int(dur * sr))
    for i in range(len(out)):
        out[i] = buf[i % period]
        buf[i % period] = decay * 0.5 * (buf[i % period] + buf[(i + 1) % period])
    return out


def strum_song(sections, bpm=100, sr=22050, seed=0, lead_in=0.3):
    """sections: [(chord | None | 'noise', beats, strums per beat)] -> audio, truth."""
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    y = np.zeros(int((lead_in + sum(b for _, b, _ in sections) * beat + 1.0) * sr))
    truth, t = [], lead_in
    for chord, beats, per_beat in sections:
        strums = []
        if chord == "noise":
            noise = rng.standard_normal(int(beats * beat * sr)) * 0.05
            y[int(t * sr):int(t * sr) + len(noise)] += noise
        elif chord:
            for k in range(int(beats * per_beat)):
                st = t + k * beat / per_beat
                strums.append(st)
                for j, (s, f) in enumerate(sorted(STANDARD_VOICINGS[chord], reverse=True)):
                    hz = 440 * 2 ** ((GUITAR_BASE_PITCHES[s] + f - 69) / 12)
                    sig = 0.25 * pluck(hz, beat / per_beat + 0.05, sr, rng)
                    i = int((st + j * 0.008) * sr)  # strummed low to high
                    y[i:i + len(sig)] += sig[:max(0, len(y) - i)]
        truth.append((t, t + beats * beat, chord, strums))
        t += beats * beat
    return y / np.max(np.abs(y)) * 0.8, sr, truth


class TestChordRecognition(unittest.TestCase):
    def test_progression_silence_and_strums(self):
        sections = [("C", 4, 1), ("G", 4, 2), ("Am", 4, 1), (None, 3, 0), ("noise", 3, 0),
                    ("F", 4, 1), ("Dm", 4, 1), ("E7", 4, 1)]
        y, sr, truth = strum_song(sections)
        out_dir = os.path.join(os.path.dirname(__file__), "test_output", "chords")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "strums.wav")
        sf.write(path, y, sr)

        pred = recognize_chords(path)

        def label_at(t):
            return next((p["chord"] for p in pred if p["start"] <= t < p["end"]), None)

        correct = total = false_chords = quiet = 0
        for start, end, chord, _ in truth:
            for t in np.arange(start + 0.2, end - 0.2, 0.05):
                if chord in (None, "noise"):
                    quiet += 1
                    false_chords += label_at(t) is not None
                else:
                    total += 1
                    correct += label_at(t) == chord
        self.assertGreaterEqual(correct / total, 0.9)
        self.assertEqual(false_chords, 0, "chords reported during silence or noise")

        # Every strum re-attacks the chord close to where it was played
        strums = [s for *_, times in truth for s in times]
        attacks = [p["start"] for p in pred]
        matched = sum(any(abs(s - a) < 0.07 for a in attacks) for s in strums)
        self.assertGreaterEqual(matched / len(strums), 0.95)
        self.assertLessEqual(len(attacks), len(strums) + 2)

    def test_held_chords_are_not_restrummed(self):
        """A sustained chord is one event, not a stream of spurious strums."""
        sr = 22050
        t = np.arange(sr * 4) / sr
        held = lambda freqs: sum(0.2 * np.sin(2 * np.pi * f * t) for f in freqs)
        out_dir = os.path.join(os.path.dirname(__file__), "test_output", "chords")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "held.wav")
        sf.write(path, np.concatenate([held([130.8, 164.8, 196.0]), held([110.0, 130.8, 164.8])]), sr)
        self.assertEqual([p["chord"] for p in recognize_chords(path)], ["C", "Am"])

    def test_silence_gives_no_chords(self):
        out_dir = os.path.join(os.path.dirname(__file__), "test_output", "chords")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "silence.wav")
        sf.write(path, np.zeros(22050 * 2), 22050)
        self.assertEqual(recognize_chords(path), [])


if __name__ == "__main__":
    unittest.main()
