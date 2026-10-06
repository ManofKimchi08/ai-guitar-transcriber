"""
Unit and Integration Tests for AI Band Transcriber
"""

import os
import sys
import unittest
import numpy as np
import soundfile as sf
import pretty_midi
import xml.etree.ElementTree as ET

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.guitar_splitter import split_guitar_track
from src.drum_transcriber import transcribe_drums
from src.tab_builder import optimize_tablature, build_musicxml_score, assign_chord_strings, quantize_onsets, GUITAR_TUNING, BASS_TUNING
from src.tab_builder import estimate_key
from src.chord_recognizer import STANDARD_VOICINGS
from src.pdf_exporter import convert_ly_to_tab_style
from src.fretboard_editor import ScoreFretEditor


class TestBandTranscriber(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output"))
        os.makedirs(self.test_dir, exist_ok=True)

    def test_guitar_splitter_mid_side(self):
        """Test Mid-Side separation on a synthetic stereo guitar track."""
        sr = 22050
        duration = 1.0  # 1 second
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        
        # Center signal (Lead): 880 Hz tone
        center_lead = 0.5 * np.sin(2 * np.pi * 880 * t)
        # Side signal (Rhythm): 220 Hz tone with opposite phase
        side_rhythm = 0.5 * np.sin(2 * np.pi * 220 * t)
        
        left = center_lead + side_rhythm
        right = center_lead - side_rhythm
        stereo_audio = np.stack([left, right], axis=1)
        
        test_wav = os.path.join(self.test_dir, "test_guitar.wav")
        sf.write(test_wav, stereo_audio, sr)
        
        res = split_guitar_track(test_wav, self.test_dir)
        self.assertTrue(os.path.exists(res["lead"]))
        self.assertTrue(os.path.exists(res["rhythm"]))
        
        # Verify lead file is mono/center and rhythm is created
        lead_data, _ = sf.read(res["lead"])
        rhythm_data, _ = sf.read(res["rhythm"])
        self.assertGreater(len(lead_data), 0)
        self.assertGreater(len(rhythm_data), 0)

        # Chord recognition downmixes to mono; the rhythm stem must survive that.
        rhythm_mono = rhythm_data if rhythm_data.ndim == 1 else rhythm_data.mean(axis=1)
        self.assertGreater(np.sqrt(np.mean(rhythm_mono ** 2)), 0.1)

    def test_drum_transcriber(self):
        """Test drum transient and onset detection with synthetic kick and snare hits."""
        sr = 22050
        duration = 2.0
        audio = np.zeros(int(sr * duration))
        
        # Kick drum at t = 0.2s (60 Hz decaying sine)
        k_t = np.linspace(0, 0.15, int(sr * 0.15), endpoint=False)
        kick_hit = 0.8 * np.sin(2 * np.pi * 60 * k_t) * np.exp(-15 * k_t)
        idx_kick = int(0.2 * sr)
        audio[idx_kick:idx_kick + len(kick_hit)] += kick_hit
        
        # Snare drum at t = 1.0s (250 Hz + noise decaying)
        s_t = np.linspace(0, 0.15, int(sr * 0.15), endpoint=False)
        snare_hit = 0.7 * (np.sin(2 * np.pi * 250 * s_t) + 0.5 * np.random.randn(len(s_t))) * np.exp(-18 * s_t)
        idx_snare = int(1.0 * sr)
        audio[idx_snare:idx_snare + len(snare_hit)] += snare_hit
        
        drum_wav = os.path.join(self.test_dir, "test_drums.wav")
        sf.write(drum_wav, audio, sr)
        
        drum_mid = os.path.join(self.test_dir, "test_drums.mid")
        transcribe_drums(drum_wav, drum_mid)
        
        self.assertTrue(os.path.exists(drum_mid))
        pm = pretty_midi.PrettyMIDI(drum_mid)
        self.assertTrue(len(pm.instruments) > 0)
        self.assertTrue(pm.instruments[0].is_drum)
        
        pitches = [n.pitch for n in pm.instruments[0].notes]
        # Should detect kick (36) or snare (38)
        self.assertTrue(36 in pitches or 38 in pitches)

    def test_viterbi_tab_optimizer(self):
        """Test DP tab optimizer on an A minor pentatonic phrase: A3, C4, D4, E4, G4, A4."""
        # A minor pentatonic: 57 (A3), 60 (C4), 62 (D4), 64 (E4), 67 (G4), 69 (A4)
        midi_pitches = [57, 60, 62, 64, 67, 69]
        notes = [
            pretty_midi.Note(velocity=100, pitch=p, start=i * 0.25, end=(i + 1) * 0.25)
            for i, p in enumerate(midi_pitches)
        ]
        
        tab_result = optimize_tablature(notes, GUITAR_TUNING)
        self.assertEqual(len(tab_result), len(notes))
        
        # Check that frets and strings are valid
        for note, s, f in tab_result:
            self.assertIn(s, range(1, 7))
            self.assertIn(f, range(0, 23))
            # Verify pitch match: GUITAR_TUNING[s] + f == note.pitch
            self.assertEqual(GUITAR_TUNING[s] + f, note.pitch)

    def test_musicxml_generation(self):
        """Test building full multi-track MusicXML score."""
        drum_mid = os.path.join(self.test_dir, "test_drums.mid")
        xml_out = os.path.join(self.test_dir, "test_score.musicxml")
        
        build_musicxml_score(
            drum_midi_path=drum_mid,
            bass_midi_path="",
            lead_midi_path="",
            rhythm_midi_path="",
            output_xml_path=xml_out,
            song_title="Test Unit Score"
        )
        
        self.assertTrue(os.path.exists(xml_out))
        tree = ET.parse(xml_out)
        root = tree.getroot()
        self.assertEqual(root.tag, "score-partwise")

    def test_chord_voicings_stay_playable(self):
        """Every recognizer voicing must come back from MIDI as a playable shape."""
        for name, voicing in STANDARD_VOICINGS.items():
            notes = [pretty_midi.Note(velocity=80, pitch=GUITAR_TUNING[s] + f, start=0.0, end=1.0)
                     for s, f in voicing]
            shape = [(s, f) for _, s, f in assign_chord_strings(notes, GUITAR_TUNING)]

            self.assertEqual(len(shape), len(voicing), name)
            self.assertEqual(len({s for s, _ in shape}), len(shape), f"{name}: string collision")
            self.assertEqual(sorted(GUITAR_TUNING[s] + f for s, f in shape),
                             sorted(n.pitch for n in notes), name)
            fretted = [f for _, f in shape if f > 0]
            if fretted:
                self.assertLessEqual(max(fretted) - min(fretted), 3, f"{name}: {shape}")

        # Regression: the Cm barre used to come back as x3-13-0-1-3
        cm = STANDARD_VOICINGS["Cm"]
        notes = [pretty_midi.Note(velocity=80, pitch=GUITAR_TUNING[s] + f, start=0.0, end=1.0) for s, f in cm]
        shape = sorted(((s, f) for _, s, f in assign_chord_strings(notes, GUITAR_TUNING)), reverse=True)
        self.assertEqual(shape, sorted(cm, reverse=True))

    def _build_single_part(self, part, notes, bpm=120.0):
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=0, is_drum=(part == "drums"))
        inst.notes = notes
        pm.instruments.append(inst)
        mid = os.path.join(self.test_dir, f"test_{part}_only.mid")
        pm.write(mid)
        xml_out = os.path.join(self.test_dir, f"test_{part}_only.musicxml")
        paths = {"drums": "", "bass": "", "lead": "", "rhythm": ""}
        paths[part] = mid
        build_musicxml_score(
            drum_midi_path=paths["drums"], bass_midi_path=paths["bass"],
            lead_midi_path=paths["lead"], rhythm_midi_path=paths["rhythm"],
            output_xml_path=xml_out, selected_parts=[part], bpm=bpm
        )
        return ET.parse(xml_out).getroot()

    def test_fast_run_never_stacks_into_chords(self):
        """Sequential notes denser than the 16th grid must not become a same-string chord."""
        run = [pretty_midi.Note(velocity=90, pitch=p, start=i * 0.07, end=i * 0.07 + 0.065)
               for i, p in enumerate([64, 65, 67, 69, 71, 72, 74, 76])]
        root = self._build_single_part("lead", run)
        self.assertEqual(root.findall(".//note/chord"), [])

    def test_measures_are_exactly_full(self):
        """Every measure must add up to one 4/4 bar (16 divisions), chords counted once."""
        notes = []
        for i in range(40):  # strummed chords on uneven timing, some crossing bar lines
            start = i * 0.37
            for p in (40, 47, 52):
                notes.append(pretty_midi.Note(velocity=80, pitch=p, start=start, end=start + 0.9))
        root = self._build_single_part("rhythm", notes, bpm=97.0)
        for measure in root.iter("measure"):
            total = sum(int(n.findtext("duration")) for n in measure.findall("note")
                        if n.find("chord") is None)
            self.assertEqual(total, 16, f"measure {measure.get('number')}")

    def test_quantized_chords_hold_one_note_per_string(self):
        """Even if upstream grouping disagrees, a written chord never reuses a string."""
        a = pretty_midi.Note(velocity=80, pitch=60, start=0.000, end=0.5)
        b = pretty_midi.Note(velocity=80, pitch=61, start=0.020, end=0.5)
        events = quantize_onsets([(a, 2, 1), (b, 2, 2)], seconds_per_div=0.125)
        for _, items, _ in events:
            strings = [s for _, s, _ in items]
            self.assertEqual(len(strings), len(set(strings)))

    def test_drum_hits_on_one_beat_share_a_slot(self):
        """Kick and hi-hat a few ms apart are one drum event, not two sequential ones."""
        hits = [pretty_midi.Note(velocity=100, pitch=36, start=0.0, end=0.1),
                pretty_midi.Note(velocity=100, pitch=42, start=0.04, end=0.1)]
        root = self._build_single_part("drums", hits)
        first = root.find("./part/measure[@number='1']")
        played = [n for n in first.findall("note") if n.find("rest") is None]
        self.assertEqual(len(played), 2)
        self.assertIsNotNone(played[1].find("chord"))

    def test_key_estimation(self):
        def scale(pitches, weights=None):
            weights = weights or [1.0] * len(pitches)
            notes, t = [], 0.0
            for p, w in zip(pitches, weights):
                notes.append(pretty_midi.Note(velocity=80, pitch=p, start=t, end=t + w))
                t += w
            return notes

        self.assertEqual(estimate_key(scale([60, 62, 64, 65, 67, 69, 71], [3, 1, 2, 1, 3, 1, 1]))[0], 0)    # C major
        self.assertEqual(estimate_key(scale([65, 67, 69, 70, 72, 74, 76], [3, 1, 2, 1, 3, 1, 1]))[0], -1)   # F major
        self.assertEqual(estimate_key(scale([64, 66, 67, 69, 71, 72, 74], [3, 1, 2, 1, 3, 1, 1])), (1, "minor"))  # E minor
        self.assertEqual(estimate_key([])[0], 0)

    def test_score_notation_details(self):
        """Tempo, octave clefs, string tuning, drum notation and flat spelling."""
        def write(name, notes, drum=False):
            pm = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(program=0, is_drum=drum)
            inst.notes = notes
            pm.instruments.append(inst)
            path = os.path.join(self.test_dir, name)
            pm.write(path)
            return path

        N = pretty_midi.Note
        drums = write("nd.mid", [N(100, 36, 0.0, 0.1), N(100, 42, 0.0, 0.1), N(100, 38, 0.5, 0.6)], drum=True)
        bass = write("nb.mid", [N(90, 29, i * 0.5, i * 0.5 + 0.4) for i in range(4)])  # F1
        lead = write("nl.mid", [N(90, p, i * 0.5, i * 0.5 + 0.4) for i, p in enumerate([65, 70, 69, 65, 72, 70])])  # F major, with Bb
        xml_out = os.path.join(self.test_dir, "test_notation.musicxml")
        build_musicxml_score(drums, bass, lead, "", xml_out, selected_parts=["drums", "bass", "lead"], bpm=97.0)
        root = ET.parse(xml_out).getroot()

        # Tempo once, in the first part
        self.assertEqual(len(root.findall(".//direction/sound[@tempo]")), 1)
        self.assertEqual(root.find("./part[@id='P1']//metronome/per-minute").text, "97")

        # Guitar/bass: octave clef and one staff-tuning line per string, lowest string on line 1
        for pid, strings, lowest in (("P2", 4, ("E", "1")), ("P4", 6, ("E", "2"))):
            attrs = root.find(f"./part[@id='{pid}']/measure/attributes")
            self.assertEqual(attrs.findtext("clef/clef-octave-change"), "-1")
            tunings = attrs.findall("staff-details/staff-tuning")
            self.assertEqual(len(tunings), strings)
            line1 = attrs.find("staff-details/staff-tuning[@line='1']")
            self.assertEqual((line1.findtext("tuning-step"), line1.findtext("tuning-octave")), lowest)

        # Flat key: Bb is spelled B-flat, not A-sharp
        self.assertEqual(root.find("./part[@id='P4']/measure/attributes/key/fifths").text, "-1")
        steps = {(p.findtext("step"), p.findtext("alter")) for p in root.findall("./part[@id='P4']//pitch")}
        self.assertIn(("B", "-1"), steps)
        self.assertNotIn(("A", "1"), steps)

        # Drums: unpitched kit pieces that point at declared instruments; hi-hat as x
        declared = {i.get("id") for i in root.findall("./part-list/score-part[@id='P1']/score-instrument")}
        drum_notes = [n for n in root.findall("./part[@id='P1']//note") if n.find("rest") is None]
        self.assertTrue(all(n.find("unpitched") is not None for n in drum_notes))
        self.assertTrue(all(n.find("instrument").get("id") in declared for n in drum_notes))
        hihat = next(n for n in drum_notes if n.find("instrument").get("id") == "P1-I42")
        self.assertEqual((hihat.findtext("unpitched/display-step"), hihat.findtext("notehead")), ("G", "x"))

        # The fretboard editor offers only string instruments
        self.assertEqual([p["id"] for p in ScoreFretEditor(xml_out).parts], ["P2", "P4"])

    def test_long_notes_are_tied_across_bar_lines(self):
        """A note crossing a bar line, or lasting 5 sixteenths, becomes tied standard values."""
        notes = [pretty_midi.Note(velocity=90, pitch=64, start=1.5, end=3.0),   # beat 4 of bar 1 -> bar 2
                 pretty_midi.Note(velocity=90, pitch=67, start=3.0, end=3.625)]  # 5 sixteenths at 120 BPM
        root = self._build_single_part("lead", notes)
        played = [(m.get("number"), n) for m in root.iter("measure") for n in m.findall("note")
                  if n.find("rest") is None]
        summary = [(bar, int(n.findtext("duration")), [t.get("type") for t in n.findall("tie")])
                   for bar, n in played]
        self.assertEqual(summary, [("1", 4, ["start"]), ("2", 8, ["stop"]),
                                   ("2", 4, ["start"]), ("2", 1, ["stop"])])
        for _, n in played:  # notation programs draw ties from <tied>
            self.assertEqual(len(n.findall("notations/tied")), len(n.findall("tie")))
            self.assertIsNotNone(n.find("notations/technical/fret"))

    def test_bar_lines_follow_the_downbeat(self):
        """Bar 1 starts on the downbeat; earlier notes become a pickup after leading rests."""
        notes = [pretty_midi.Note(velocity=90, pitch=60, start=0.2, end=0.4),   # before the downbeat
                 pretty_midi.Note(velocity=90, pitch=62, start=0.7, end=0.9)]   # on the downbeat
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=29)
        inst.notes = notes
        pm.instruments.append(inst)
        mid = os.path.join(self.test_dir, "test_downbeat.mid")
        pm.write(mid)
        xml_out = os.path.join(self.test_dir, "test_downbeat.musicxml")
        build_musicxml_score("", "", mid, "", xml_out, selected_parts=["lead"], bpm=120.0, downbeat=0.7)
        root = ET.parse(xml_out).getroot()

        def onset_slots(measure):
            pos, result = 0, []
            for n in measure.findall("note"):
                if n.find("rest") is None and n.find("chord") is None:
                    result.append((pos, n.findtext("pitch/step")))
                if n.find("chord") is None:
                    pos += int(n.findtext("duration"))
            return result

        bar1, bar2 = root.findall("./part/measure")[:2]
        self.assertEqual(onset_slots(bar1), [(12, "C")])  # 0.5 s before the downbeat = 4 sixteenths early
        self.assertEqual(onset_slots(bar2), [(0, "D")])

    def test_silent_intro_adds_no_empty_bars(self):
        """Music starting on the third downbeat begins in bar 1, not after two empty bars."""
        notes = [pretty_midi.Note(velocity=90, pitch=60, start=4.3, end=4.8)]
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=29)
        inst.notes = notes
        pm.instruments.append(inst)
        mid = os.path.join(self.test_dir, "test_late_start.mid")
        pm.write(mid)
        xml_out = os.path.join(self.test_dir, "test_late_start.musicxml")
        build_musicxml_score("", "", mid, "", xml_out, selected_parts=["lead"], bpm=120.0, downbeat=0.3)
        first = ET.parse(xml_out).getroot().find("./part/measure[@number='1']")
        self.assertIsNone(first.findall("note")[0].find("rest"))  # the note opens bar 1

    def test_editor_moves_a_whole_tie_chain(self):
        notes = [pretty_midi.Note(velocity=90, pitch=64, start=1.5, end=3.0)]
        self._build_single_part("lead", notes)
        xml_path = os.path.join(self.test_dir, "test_lead_only.musicxml")
        editor = ScoreFretEditor(xml_path)
        self.assertEqual(len(editor.get_measure_notes("P4", 1)), 1)
        self.assertEqual(editor.get_measure_notes("P4", 2), [])  # the tied continuation is not a new note
        self.assertTrue(editor.update_note_position("P4", 1, 0, 2, 5))
        editor.save_xml()
        frets = [(n.findtext("notations/technical/string"), n.findtext("notations/technical/fret"))
                 for n in ET.parse(xml_path).getroot().iter("note") if n.find("rest") is None]
        self.assertEqual(frets, [("2", "5"), ("2", "5")])

    def test_lilypond_drums_and_tab_clefs(self):
        ly = (
            'PartPOneVoiceOne =  \\relative f\' {\n'
            '    \\clef "percussion" \\time 4/4 f4 \\tempo 4=120 g\'\'4 }\n'
            'PartPTwoVoiceOne =  \\relative e,, {\n'
            '    \\tempo 4=120 \\clef "bass_8" \\key g \\major e4 e4\n'
            '    }\n'
            '\\score {\n'
            '        \\new DrumStaff\n'
            '        <<\n'
            '            \\set DrumStaff.instrumentName = "Drums"\n'
            '            \\context DrumVoice = "PartPOneVoiceOne" {  \\PartPOneVoiceOne }\n'
            '            >>\n'
            '    }\n'
        )
        out = convert_ly_to_tab_style(ly, style="tab")
        self.assertNotIn("DrumStaff", out)
        self.assertNotIn("DrumVoice", out)
        self.assertIn('clefs.percussion', out)
        # The bass clef is removed even when it does not directly follow the opening brace
        bass_def = out[out.index("PartPTwoVoiceOne ="):out.index("\\score")]
        self.assertNotIn("\\clef", bass_def)


if __name__ == "__main__":
    unittest.main()
