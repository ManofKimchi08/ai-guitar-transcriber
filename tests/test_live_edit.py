import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fretboard_editor import ScoreFretEditor

xml_path = "output/scores/guitar_lead_rhythm_bass_score.musicxml"
editor = ScoreFretEditor(xml_path)
lead = next(p for p in editor.parts if "lead" in p["name"].lower())
notes = editor.get_measure_notes(lead["id"], 3)
s = notes[0]["current_string"]
f = notes[0]["current_fret"]
print(f"Before edit, Measure 3 Note 0: {notes[0]['pitch_name']} | String {s}, Fret {f}")

# Change Note 0 from string 2 fret 6 to string 3 fret 10
editor.update_note_position(lead["id"], 3, 0, 3, 10)
editor.save_xml()

notes_after = editor.get_measure_notes(lead["id"], 3)
s2 = notes_after[0]["current_string"]
f2 = notes_after[0]["current_fret"]
print(f"After edit,  Measure 3 Note 0: {notes_after[0]['pitch_name']} | String {s2}, Fret {f2}")

pdf = editor.reexport_pdf(style="tab")
print(f"PDF re-exported to: {pdf}")
print("✓ Live edit and PDF re-export succeeded!")
