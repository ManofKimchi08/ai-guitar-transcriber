import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.fretboard_editor import find_alternative_positions, ScoreFretEditor

def test_alternatives():
    print("--- Test 1: find_alternative_positions ---")
    # Pitch 60 is C4 (Middle C)
    # Guitar candidates: (2, 1), (3, 5), (4, 10), (5, 15), (6, 20)
    alts = find_alternative_positions(60, instrument_type="guitar", current_string=2, current_fret=1)
    print(f"C4 Guitar alternatives ({len(alts)} found):")
    for a in alts:
        print(f"  String {a['string']}, Fret {a['fret']}: {a['description']} (span: {a['span_distance']})")
    
    positions = {(a["string"], a["fret"]) for a in alts}
    assert (2, 1) in positions
    assert (3, 5) in positions
    assert (4, 10) in positions
    assert (5, 15) in positions
    assert (6, 20) in positions
    print("✓ C4 guitar alternative positions verified!")

    # Pitch 43 is G2 (Lowest string 4 fret 3 on bass, or string 1 open fret 0)
    bass_alts = find_alternative_positions(43, instrument_type="bass", current_string=4, current_fret=15)
    print(f"\nG2 Bass alternatives ({len(bass_alts)} found):")
    for a in bass_alts:
        print(f"  String {a['string']}, Fret {a['fret']}: {a['description']}")
    bass_pos = {(a["string"], a["fret"]) for a in bass_alts}
    assert (1, 0) in bass_pos
    assert (2, 5) in bass_pos
    assert (3, 10) in bass_pos
    assert (4, 15) in bass_pos
    print("✓ G2 bass alternative positions verified!")

def test_xml_editing():
    print("\n--- Test 2: ScoreFretEditor on real MusicXML ---")
    xml_path = "output/scores/guitar_lead_rhythm_bass_score.musicxml"
    if not os.path.exists(xml_path):
        print(f"MusicXML not found at {xml_path}, skipping XML editing test.")
        return

    editor = ScoreFretEditor(xml_path)
    print("Loaded parts:")
    for p in editor.parts:
        print(f"  [{p['id']}] {p['name']} ({p['instrument_type']}), Measures: {p['num_measures']}")

    lead_part = next((p for p in editor.parts if "lead" in p["name"].lower()), editor.parts[0])
    pid = lead_part["id"]

    # Check measure 3 notes
    notes = editor.get_measure_notes(pid, 3)
    print(f"\nMeasure 3 notes count in {lead_part['name']}: {len(notes)}")
    if notes:
        n0 = notes[0]
        print(f"Note 0: {n0['pitch_name']} | Current: String {n0['current_string']}, Fret {n0['current_fret']}")
        print(f"Alternatives available: {len(n0['alternatives'])}")
        for alt in n0["alternatives"]:
            print(f"  -> {alt['description']}")

        # Test changing string and fret
        alt_choice = next(a for a in n0["alternatives"] if not a["is_current"])
        print(f"\nSwitching Note 0 from String {n0['current_string']} Fret {n0['current_fret']} to String {alt_choice['string']} Fret {alt_choice['fret']}...")
        ok = editor.update_note_position(pid, 3, 0, alt_choice["string"], alt_choice["fret"])
        assert ok, "Failed to update note position"

        # Verify update in memory
        updated_notes = editor.get_measure_notes(pid, 3)
        assert updated_notes[0]["current_string"] == alt_choice["string"]
        assert updated_notes[0]["current_fret"] == alt_choice["fret"]
        print("✓ Memory update verified!")

        # Re-export PDF test
        test_out_xml = "output/scores/test_edited_score.musicxml"
        editor.save_xml(test_out_xml)
        print(f"Saved edited XML to: {test_out_xml}")
        
        test_editor = ScoreFretEditor(test_out_xml)
        pdf = test_editor.reexport_pdf(style="tab")
        print(f"Re-exported PDF: {pdf}")
        assert pdf and os.path.exists(pdf), "PDF re-export failed"
        print("✓ PDF successfully re-exported from edited score!")

if __name__ == "__main__":
    test_alternatives()
    test_xml_editing()
    print("\n🎉 ALL TESTS PASSED!")
