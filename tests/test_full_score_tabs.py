import os
import sys
import pretty_midi
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.tab_builder import build_musicxml_score

test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output"))
os.makedirs(test_dir, exist_ok=True)

# 1. Create Drum MIDI
pm_drum = pretty_midi.PrettyMIDI()
inst_drum = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
inst_drum.notes.append(pretty_midi.Note(velocity=100, pitch=36, start=0.0, end=0.2))  # Kick
inst_drum.notes.append(pretty_midi.Note(velocity=100, pitch=38, start=1.0, end=1.2))  # Snare
pm_drum.instruments.append(inst_drum)
drum_mid = os.path.join(test_dir, "synth_drum.mid")
pm_drum.write(drum_mid)

# 2. Create Bass MIDI (E1, A1, D2, G2)
pm_bass = pretty_midi.PrettyMIDI()
inst_bass = pretty_midi.Instrument(program=33, is_drum=False, name="Bass")
for i, p in enumerate([28, 33, 38, 43]):
    inst_bass.notes.append(pretty_midi.Note(velocity=90, pitch=p, start=i*0.5, end=(i+1)*0.5))
pm_bass.instruments.append(inst_bass)
bass_mid = os.path.join(test_dir, "synth_bass.mid")
pm_bass.write(bass_mid)

# 3. Create Lead Guitar MIDI (A Minor Pentatonic Solo: 57, 60, 62, 64, 67, 69)
pm_lead = pretty_midi.PrettyMIDI()
inst_lead = pretty_midi.Instrument(program=29, is_drum=False, name="Lead Guitar")
for i, p in enumerate([57, 60, 62, 64, 67, 69]):
    inst_lead.notes.append(pretty_midi.Note(velocity=95, pitch=p, start=i*0.3, end=(i+1)*0.3))
pm_lead.instruments.append(inst_lead)
lead_mid = os.path.join(test_dir, "synth_lead.mid")
pm_lead.write(lead_mid)

# 4. Create Rhythm Guitar MIDI (Power Chords: E5=40,47, G5=43,50, A5=45,52)
pm_rhythm = pretty_midi.PrettyMIDI()
inst_rhythm = pretty_midi.Instrument(program=27, is_drum=False, name="Rhythm Guitar")
chords = [(40, 47), (43, 50), (45, 52)]
for i, chord in enumerate(chords):
    for p in chord:
        inst_rhythm.notes.append(pretty_midi.Note(velocity=85, pitch=p, start=i*0.8, end=(i+1)*0.8))
pm_rhythm.instruments.append(inst_rhythm)
rhythm_mid = os.path.join(test_dir, "synth_rhythm.mid")
pm_rhythm.write(rhythm_mid)

# 5. Build MusicXML Score
xml_out = os.path.join(test_dir, "full_band_sample_score.musicxml")
build_musicxml_score(
    drum_midi_path=drum_mid,
    bass_midi_path=bass_mid,
    lead_midi_path=lead_mid,
    rhythm_midi_path=rhythm_mid,
    output_xml_path=xml_out,
    song_title="AI Rock Band Full Score"
)

print(f"[+] Score generated at: {xml_out}")

# Verify XML has fret and string technical notations
tree = ET.parse(xml_out)
root = tree.getroot()
fret_elements = root.findall(".//technical/fret")
string_elements = root.findall(".//technical/string")

print(f"[+] Total fret tags found: {len(fret_elements)}")
print(f"[+] Total string tags found: {len(string_elements)}")

assert len(fret_elements) > 0, "No fret elements generated!"
assert len(string_elements) > 0, "No string elements generated!"
print("[✓] MusicXML Tablature validation passed with flying colors!")
