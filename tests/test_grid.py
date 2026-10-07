"""
Tests for the score's rhythmic grid: ordinary passages stay on 16th notes even with
human timing, a beat switches to 32nd notes only where its notes are that fast (runs,
drum rolls) and to triplets where they divide it in three, a shuffle is written in
12/8, bars of 3 are written in 3/4, and rests fill out each beat in readable values.
"""

import os
import sys
import unittest
import xml.etree.ElementTree as ET

import numpy as np
import pretty_midi

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.tab_builder import build_musicxml_score, rest_values, BAR_DIVISIONS, THIRTY_SECOND

OUT_DIR = os.path.join(os.path.dirname(__file__), "test_output", "grid")
BPM = 100.0
BEAT = 60.0 / BPM


def build(name, notes, part="lead", **score):
    """Writes one part to MusicXML; returns [(onset in beats, type, chord?, rest?)] in order."""
    os.makedirs(OUT_DIR, exist_ok=True)
    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=29, is_drum=(part == "drums"))
    inst.notes = notes
    pm.instruments.append(inst)
    mid = os.path.join(OUT_DIR, name + ".mid")
    pm.write(mid)
    paths = {"drums": "", "bass": "", "lead": "", "rhythm": ""}
    paths[part] = mid
    xml = os.path.join(OUT_DIR, name + ".musicxml")
    build_musicxml_score(paths["drums"], paths["bass"], paths["lead"], paths["rhythm"], xml,
                         selected_parts=[part], bpm=BPM, downbeat=0.0, **score)
    root = ET.parse(xml).getroot()
    divisions = int(root.findtext(".//divisions"))
    beats, beat_type = int(root.findtext(".//time/beats")), int(root.findtext(".//time/beat-type"))
    bar = beats * divisions * 4 // beat_type
    beat = divisions * (3 if beat_type == 8 else 2) // 2  # a dotted quarter in compound meter
    events, pos = [], 0
    for measure in root.iter("measure"):
        total = 0
        for n in measure.findall("note"):
            chord = n.find("chord") is not None
            if chord:
                events.append((events[-1][0], n.findtext("type"), True, False))
                continue
            events.append((pos / beat, n.findtext("type"), False, n.find("rest") is not None))
            dur = int(n.findtext("duration"))
            pos += dur
            total += dur
        assert total == bar, f"{name}: measure {measure.get('number')} holds {total}"
    build.root = root
    return events


def note(pitch, beat, length_beats=0.2, jitter=0.0):
    start = beat * BEAT + jitter
    return pretty_midi.Note(velocity=90, pitch=pitch, start=start, end=start + length_beats * BEAT)


class TestScoreGrid(unittest.TestCase):
    def test_human_timing_stays_on_sixteenths(self):
        """Eighths and sixteenths played up to ~20 ms off the beat are written as such."""
        rng = np.random.default_rng(0)
        beats = [b + k / 2 for b in range(0, 8) for k in range(2)] + [b + k / 4 for b in range(8, 16) for k in range(4)]
        notes = [note(60 + i % 12, b, 0.22, rng.uniform(-0.02, 0.02)) for i, b in enumerate(beats)]
        events = build("human", notes)
        played = [(on, t) for on, t, chord, rest in events if not rest]
        self.assertEqual([on for on, _ in played], beats)
        self.assertNotIn("32nd", [t for _, t, _, _ in events])

    def test_fast_run_uses_thirty_seconds_in_its_beat_only(self):
        """A 32nd-note run keeps every note, in its own beat; the beats around it stay plain."""
        beats = [0, 1] + [2 + k / 8 for k in range(8)] + [3]
        notes = [note(60 + i, b, 0.1) for i, b in enumerate(beats)]
        events = build("run", notes)
        played = [(on, t) for on, t, chord, rest in events if not rest]
        self.assertEqual([on for on, _ in played], beats)
        self.assertEqual([t for on, t in played if 2 <= on < 3], ["32nd"] * 8)
        self.assertNotIn("32nd", [t for on, t, _, _ in events if not 2 <= on < 3])
        self.assertFalse(any(chord for _, _, chord, _ in events))

    def test_drum_roll_versus_kick_with_hat(self):
        """A snare roll is written in 32nds; kick and hat a little apart stay one chord."""
        hits = [note(36, 0, 0.1), note(42, 0, 0.1, jitter=0.04)]                       # one event
        hits += [note(38, 1 + k / 8, 0.05) for k in range(8)]                          # snare roll
        hits += [note(36, 2, 0.1), note(42, 2.5, 0.1)]
        events = build("roll", hits, part="drums")
        played = [(on, t, chord) for on, t, chord, rest in events if not rest]
        self.assertEqual(played[:2], [(0, "16th", False), (0, "16th", True)])
        self.assertEqual([on for on, _, _ in played[2:10]], [1 + k / 8 for k in range(8)])
        self.assertEqual({t for _, t, _ in played[2:10]}, {"32nd"})

    def test_triplet_beats(self):
        """Beats divided in three are written as triplets with a bracket; the others stay straight."""
        pattern = [[0, 1 / 2], [0, 1 / 3, 2 / 3], [0, 1 / 4, 1 / 2, 3 / 4], [0, 1 / 3, 2 / 3]] * 2
        beats = [b + f for b, fracs in enumerate(pattern) for f in fracs]
        events = build("triplets", [note(60 + i % 12, b, 0.1) for i, b in enumerate(beats)])
        played = [on for on, t, chord, rest in events if not rest]
        self.assertEqual([round(on, 3) for on in played], [round(b, 3) for b in beats])
        notes = [n for n in build.root.iter("note") if n.find("rest") is None]
        triplet = [n.find("time-modification") is not None for n in notes]
        self.assertEqual(triplet, [b % 2 == 1 and len(pattern[b]) == 3 for b, fracs in enumerate(pattern) for _ in fracs])
        starts = build.root.findall(".//tuplet[@type='start']")
        self.assertEqual(len(starts), 4)
        self.assertEqual(len(build.root.findall(".//tuplet[@type='stop']")), 4)

    def test_shuffle_is_written_in_twelve_eight(self):
        """Swung eighths on every beat: 12/8, each note on its eighth, no triplet brackets."""
        beats = [b + f for b in range(16) for f in (0, 2 / 3)]
        events = build("shuffle", [note(60 + i % 12, b, 0.25, jitter=0.01 * ((-1) ** i)) for i, b in enumerate(beats)])
        self.assertEqual((build.root.findtext(".//time/beats"), build.root.findtext(".//time/beat-type")), ("12", "8"))
        played = [on for on, t, chord, rest in events if not rest]
        self.assertEqual([round(on, 3) for on in played], [round(b, 3) for b in beats])
        self.assertEqual(build.root.findall(".//time-modification"), [])

    def test_bars_of_three_and_whole_bar_rests(self):
        """3/4 bars hold three beats; an empty bar is one whole-bar rest."""
        events = build("waltz", [note(60, b, 0.9) for b in range(6)] + [note(62, 9, 0.9)], beats_per_bar=3)
        self.assertEqual((build.root.findtext(".//time/beats"), build.root.findtext(".//time/beat-type")), ("3", "4"))
        self.assertEqual([on for on, t, chord, rest in events if not rest], [0, 1, 2, 3, 4, 5, 9])
        whole_bar = build.root.findall(".//rest[@measure='yes']")
        self.assertEqual(len(whole_bar), 1)  # bar 3 (beats 6-8)

    def test_rests_fill_out_the_beat(self):
        """Rests start with the small value that reaches the next beat (no dotted rests)."""
        t = THIRTY_SECOND
        self.assertEqual(rest_values(0, BAR_DIVISIONS), [BAR_DIVISIONS])  # whole-bar rest
        self.assertEqual(rest_values(t, 7 * t), [t, 2 * t, 4 * t])          # 32nd, 16th, 8th to beat 2
        self.assertEqual(rest_values(8 * t, 24 * t), [8 * t, 16 * t])       # quarter, then half
        self.assertEqual(rest_values(2 * t, 14 * t), [2 * t, 4 * t, 8 * t])


if __name__ == "__main__":
    unittest.main()
