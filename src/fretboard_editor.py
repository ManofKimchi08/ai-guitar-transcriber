"""
Fretboard Editor & Enharmonic String Transposition Module
Provides tools to:
1. Identify all equivalent string/fret positions for any pitch (동음이현, 同音異絃).
2. Parse and edit MusicXML scores, updating string and fret technical notations.
3. Automatically re-export the score to PDF.
"""

import os
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple, Optional

# Standard guitar and bass tunings (String 1 is highest pitch)
GUITAR_TUNING = {
    1: 64,  # E4
    2: 59,  # B3
    3: 55,  # G3
    4: 50,  # D3
    5: 45,  # A2
    6: 40,  # E2
}

BASS_TUNING = {
    1: 43,  # G2
    2: 38,  # D2
    3: 33,  # A1
    4: 28,  # E1
}

STEP_OFFSETS = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def pitch_to_name(midi_pitch: int) -> str:
    """Converts MIDI pitch integer (e.g. 60) to name (e.g. 'C4')."""
    octave = (midi_pitch // 12) - 1
    name = NOTE_NAMES[midi_pitch % 12]
    return f"{name}{octave}"


def find_alternative_positions(
    midi_pitch: int,
    instrument_type: str = "guitar",
    max_fret: int = 22,
    current_string: Optional[int] = None,
    current_fret: Optional[int] = None
) -> List[Dict]:
    """
    Finds all valid string and fret combinations that produce the exact same MIDI pitch.
    Returns a list of dicts sorted by natural playing preference and hand distance.
    """
    tuning = BASS_TUNING if "bass" in instrument_type.lower() else GUITAR_TUNING
    candidates = []

    for s_num in sorted(tuning.keys(), reverse=True):  # From low strings to high strings
        base = tuning[s_num]
        fret = midi_pitch - base
        if 0 <= fret <= max_fret:
            is_current = (s_num == current_string and fret == current_fret)
            
            # Estimate hand movement distance if current position is provided
            if current_fret is not None and not is_current:
                # Open strings take 0 stretch
                if fret == 0 or current_fret == 0:
                    span_dist = 0.5
                else:
                    span_dist = abs(fret - current_fret)
            else:
                span_dist = 0.0

            candidates.append({
                "string": s_num,
                "fret": fret,
                "is_current": is_current,
                "span_distance": span_dist,
                "description": f"{s_num}번 줄 {fret}프렛" + (" (현재)" if is_current else "")
            })

    # Sort candidates so current is prominent, or by lowest comfortable fret
    return candidates


class ScoreFretEditor:
    """
    Parses a MusicXML score, navigates parts and measures,
    and updates fret/string assignments with live XML updates.
    """
    def __init__(self, musicxml_path: str):
        self.xml_path = os.path.abspath(musicxml_path)
        if not os.path.exists(self.xml_path):
            raise FileNotFoundError(f"MusicXML file not found: {self.xml_path}")

        self.tree = ET.parse(self.xml_path)
        self.root = self.tree.getroot()
        self.parts = self._load_parts()

    def _load_parts(self) -> List[Dict]:
        """Identifies instrument parts in the score."""
        parts_list = []
        part_list_el = self.root.find("part-list")
        if part_list_el is None:
            return parts_list

        for score_part in part_list_el.findall("score-part"):
            pid = score_part.get("id")
            name_el = score_part.find("part-name")
            pname = name_el.text if name_el is not None else pid
            
            # Determine instrument type
            lower_name = pname.lower()
            if "bass" in lower_name:
                itype = "bass"
            else:
                itype = "guitar"

            # Find matching part element
            part_el = self.root.find(f"./part[@id='{pid}']")
            num_measures = len(part_el.findall("measure")) if part_el is not None else 0

            # Drum parts have no strings or frets to edit
            if part_el is not None and part_el.find("./measure/attributes/clef[sign='percussion']") is not None:
                continue

            parts_list.append({
                "id": pid,
                "name": pname,
                "instrument_type": itype,
                "num_measures": num_measures,
                "part_element": part_el
            })
        return parts_list

    def get_measures_count(self, part_id: str) -> int:
        part = next((p for p in self.parts if p["id"] == part_id), None)
        return part["num_measures"] if part else 0

    def get_measure_notes(self, part_id: str, measure_number: int) -> List[Dict]:
        """
        Extracts all playable notes in a measure with their current string, fret, and pitch.
        """
        part = next((p for p in self.parts if p["id"] == part_id), None)
        if not part or not part["part_element"]:
            return []

        measure_el = part["part_element"].find(f"./measure[@number='{measure_number}']")
        if measure_el is None:
            return []

        notes_data = []
        note_idx = 0

        for note_el in measure_el.findall("note"):
            is_rest = (note_el.find("rest") is not None)
            if is_rest:
                continue

            pitch_el = note_el.find("pitch")
            if pitch_el is None:
                continue

            step = pitch_el.findtext("step", "C")
            alter = int(pitch_el.findtext("alter", "0"))
            octave = int(pitch_el.findtext("octave", "4"))
            
            midi_pitch = (octave + 1) * 12 + STEP_OFFSETS.get(step, 0) + alter
            pname = pitch_to_name(midi_pitch)
            dur_text = note_el.findtext("duration", "4")
            is_chord = (note_el.find("chord") is not None)

            # Read current string & fret if present
            s_num, f_num = None, None
            tech_el = note_el.find("./notations/technical")
            if tech_el is not None:
                s_str = tech_el.findtext("string")
                f_str = tech_el.findtext("fret")
                if s_str is not None and f_str is not None:
                    try:
                        s_num = int(s_str)
                        f_num = int(f_str)
                    except ValueError:
                        pass

            # Calculate alternatives
            itype = part["instrument_type"]
            alternatives = find_alternative_positions(
                midi_pitch=midi_pitch,
                instrument_type=itype,
                current_string=s_num,
                current_fret=f_num
            )

            notes_data.append({
                "note_index": note_idx,
                "note_element": note_el,
                "step": step,
                "alter": alter,
                "octave": octave,
                "midi_pitch": midi_pitch,
                "pitch_name": pname,
                "duration": dur_text,
                "is_chord": is_chord,
                "current_string": s_num,
                "current_fret": f_num,
                "alternatives": alternatives
            })
            note_idx += 1

        return notes_data

    def update_note_position(
        self,
        part_id: str,
        measure_number: int,
        note_index: int,
        new_string: int,
        new_fret: int
    ) -> bool:
        """
        Updates the <technical><string> and <technical><fret> elements in MusicXML for the target note.
        """
        measure_notes = self.get_measure_notes(part_id, measure_number)
        if not (0 <= note_index < len(measure_notes)):
            return False

        target_note_el = measure_notes[note_index]["note_element"]

        # Ensure <notations> exists
        notations_el = target_note_el.find("notations")
        if notations_el is None:
            notations_el = ET.SubElement(target_note_el, "notations")

        # Ensure <technical> exists
        tech_el = notations_el.find("technical")
        if tech_el is None:
            tech_el = ET.SubElement(notations_el, "technical")

        # Update or create string
        string_el = tech_el.find("string")
        if string_el is None:
            string_el = ET.SubElement(tech_el, "string")
        string_el.text = str(new_string)

        # Update or create fret
        fret_el = tech_el.find("fret")
        if fret_el is None:
            fret_el = ET.SubElement(tech_el, "fret")
        fret_el.text = str(new_fret)

        return True

    def save_xml(self, output_path: Optional[str] = None) -> str:
        """Saves changes back to MusicXML."""
        target_path = output_path or self.xml_path
        ET.indent(self.tree, space="  ", level=0)
        self.tree.write(target_path, encoding="utf-8", xml_declaration=True)
        return target_path

    def reexport_pdf(self, style: str = "tab") -> Optional[str]:
        """Re-exports the modified score to PDF via LilyPond."""
        from src.pdf_exporter import export_score_to_pdf
        self.save_xml()
        return export_score_to_pdf(self.xml_path, style=style)


if __name__ == "__main__":
    # Quick self-test
    print("Testing find_alternative_positions for C4 (60):")
    for alt in find_alternative_positions(60, "guitar", current_string=2, current_fret=1):
        print(" ", alt["description"])
