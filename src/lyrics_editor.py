"""
Lyrics Editor Module
Reads and edits the lyrics under a part's notes in a MusicXML score, one syllable per note.
In the editing text a syllable that continues its word on the next note ends with "-"
(e.g. "beau-", "ti-", "ful"); MusicXML's syllabic (begin/middle/end/single) follows from that.
"""

import os
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional

from src.fretboard_editor import STEP_OFFSETS, pitch_to_name

SYLLABIC = {(True, False): "single", (True, True): "begin", (False, True): "middle", (False, False): "end"}


class ScoreLyricsEditor:
    """
    Lists the sung notes of a score's vocal part(s) measure by measure and edits their lyrics.
    """
    def __init__(self, musicxml_path: str):
        self.xml_path = os.path.abspath(musicxml_path)
        if not os.path.exists(self.xml_path):
            raise FileNotFoundError(f"MusicXML file not found: {self.xml_path}")

        self.tree = ET.parse(self.xml_path)
        self.root = self.tree.getroot()
        self.parts = self._load_parts()

    def _load_parts(self) -> List[Dict]:
        """Parts that carry lyrics: the vocal part, and any other part that already has lyrics."""
        parts = []
        for score_part in self.root.findall("part-list/score-part"):
            pid = score_part.get("id")
            name = score_part.findtext("part-name", pid)
            part_el = self.root.find(f"./part[@id='{pid}']")
            if part_el is None or part_el.find("./measure/attributes/clef[sign='percussion']") is not None:
                continue
            if "vocal" in name.lower() or part_el.find(".//lyric") is not None:
                parts.append({
                    "id": pid,
                    "name": name,
                    "num_measures": len(part_el.findall("measure")),
                    "part_element": part_el,
                })
        return parts

    def _part_element(self, part_id: str) -> Optional[ET.Element]:
        part = next((p for p in self.parts if p["id"] == part_id), None)
        return part["part_element"] if part else None

    @staticmethod
    def _sung_notes(measure_el: ET.Element) -> List[ET.Element]:
        """Notes that take a syllable: pitched attacks (no rests, chord tones or tie continuations)."""
        return [n for n in measure_el.findall("note")
                if n.find("pitch") is not None and n.find("chord") is None
                and n.find("tie[@type='stop']") is None]

    def get_measure_lyrics(self, part_id: str, measure_number: int) -> List[Dict]:
        """[{"note_element", "pitch_name", "text"}], text ending in "-" when the word continues."""
        part_el = self._part_element(part_id)
        measure_el = part_el.find(f"./measure[@number='{measure_number}']") if part_el is not None else None
        if measure_el is None:
            return []

        result = []
        for note_el in self._sung_notes(measure_el):
            pitch_el = note_el.find("pitch")
            midi_pitch = ((int(pitch_el.findtext("octave", "4")) + 1) * 12
                          + STEP_OFFSETS.get(pitch_el.findtext("step", "C"), 0)
                          + int(float(pitch_el.findtext("alter", "0"))))
            text = ""
            lyric = note_el.find("lyric")
            if lyric is not None:
                text = lyric.findtext("text", "")
                if lyric.findtext("syllabic") in ("begin", "middle"):
                    text += "-"
            result.append({"note_element": note_el, "pitch_name": pitch_to_name(midi_pitch), "text": text})
        return result

    def set_measure_lyrics(self, part_id: str, measure_number: int, texts: List[str]) -> bool:
        """Sets one text per sung note of the measure ("" removes the lyric)."""
        notes = self.get_measure_lyrics(part_id, measure_number)
        if len(texts) != len(notes):
            return False
        for note, text in zip(notes, texts):
            self._set_lyric(note["note_element"], text.strip())
        self._derive_syllabic(part_id)
        return True

    @staticmethod
    def _set_lyric(note_el: ET.Element, text: str):
        lyric = note_el.find("lyric")
        word_part = text.rstrip("-").strip()
        if not word_part:
            if lyric is not None:
                note_el.remove(lyric)
            return
        if lyric is None:
            lyric = ET.SubElement(note_el, "lyric", number="1")
        number = lyric.get("number", "1")
        lyric.clear()
        lyric.set("number", number)
        # Placeholder: only "continues or not" matters until _derive_syllabic runs
        ET.SubElement(lyric, "syllabic").text = "begin" if text.endswith("-") else "single"
        ET.SubElement(lyric, "text").text = word_part

    def _derive_syllabic(self, part_id: str):
        """Sets begin/middle/end/single from which syllables continue onto the next sung note."""
        prev_continues = False
        for lyric in self._part_element(part_id).iter("lyric"):
            syllabic = lyric.find("syllabic")
            if syllabic is None:
                syllabic = ET.Element("syllabic")
                lyric.insert(0, syllabic)
            continues = syllabic.text in ("begin", "middle")
            syllabic.text = SYLLABIC[(not prev_continues, continues)]
            prev_continues = continues

    def full_text(self, part_id: str) -> str:
        """The part's lyrics as running text (syllables joined into words)."""
        words, current = [], ""
        for lyric in self._part_element(part_id).iter("lyric"):
            current += lyric.findtext("text", "")
            if lyric.findtext("syllabic") not in ("begin", "middle"):
                words.append(current)
                current = ""
        if current:
            words.append(current)
        return " ".join(words)

    def save_xml(self, output_path: Optional[str] = None) -> str:
        target_path = output_path or self.xml_path
        ET.indent(self.tree, space="  ", level=0)
        self.tree.write(target_path, encoding="utf-8", xml_declaration=True)
        return target_path

    def reexport_pdf(self, style: str = "tab") -> Optional[str]:
        from src.pdf_exporter import export_score_to_pdf
        self.save_xml()
        return export_score_to_pdf(self.xml_path, style=style)
