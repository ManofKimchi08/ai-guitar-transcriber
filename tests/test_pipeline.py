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
from src.tab_builder import optimize_tablature, build_musicxml_score, GUITAR_TUNING, BASS_TUNING


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


if __name__ == "__main__":
    unittest.main()
