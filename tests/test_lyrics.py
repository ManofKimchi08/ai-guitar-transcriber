"""
Tests for the vocal melody + lyrics path: syllable splitting, aligning Whisper's word
timestamps to melody notes, the vocal part in MusicXML, the lyrics editor, and the
LilyPond layout. Whisper itself is replaced by a fake model (no download needed).
"""

import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import pretty_midi

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.lyrics import split_syllables, words_to_syllables, align_syllables_to_notes, transcribe_lyrics
from src.lyrics_editor import ScoreLyricsEditor
from src.tab_builder import build_musicxml_score
from src.pdf_exporter import convert_ly_to_tab_style

BEAT = 0.5  # 120 BPM
# (start beat, beats, pitch): 사 랑 해 요 | 그 대 를~ | beau ti ful | my my love
MELODY = [(0, 1, 64), (1, 1, 67), (2, 0.5, 69), (2.5, 1.5, 67),
          (4, 1, 72), (5, 0.5, 71), (5.5, 0.5, 69), (6, 2, 67),
          (8, 1, 64), (9, 1, 62), (10, 2, 60),
          (12, 1, 64), (13, 1, 64), (14, 2, 67)]
WORDS = [  # Whisper-like word timestamps, slightly early/late
    {"start": 0.02, "end": 1.95, "text": " 사랑해요"},
    {"start": 2.05, "end": 3.10, "text": " 그대를"},
    {"start": 3.98, "end": 5.40, "text": " beautiful"},
    {"start": 6.05, "end": 6.40, "text": " my"},
    {"start": 6.48, "end": 6.90, "text": " my"},
    {"start": 7.00, "end": 7.90, "text": " love."},
]


class TestLyrics(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output", "lyrics"))
        os.makedirs(self.test_dir, exist_ok=True)

    def _vocal_midi(self):
        pm = pretty_midi.PrettyMIDI()
        inst = pretty_midi.Instrument(program=53)
        inst.notes = [pretty_midi.Note(90, p, s * BEAT, (s + d) * BEAT - 0.02) for s, d, p in MELODY]
        pm.instruments.append(inst)
        path = os.path.join(self.test_dir, "vocals.mid")
        pm.write(path)
        # Read back exactly as the pipeline does
        return path, sorted(pretty_midi.PrettyMIDI(path).instruments[0].notes, key=lambda n: n.start)

    def test_split_syllables(self):
        self.assertEqual(split_syllables(" 사랑해요,"), ["사", "랑", "해", "요"])
        self.assertEqual(split_syllables("beautiful", "en"), ["beau", "ti", "ful"])
        self.assertEqual(split_syllables("きょうは", "ja"), ["きょ", "う", "は"])  # small kana joins its mora
        self.assertEqual(split_syllables("love.", "en"), ["love"])
        self.assertEqual(split_syllables("...", "en"), [])

    def test_alignment_follows_the_melody(self):
        _, notes = self._vocal_midi()
        placed = align_syllables_to_notes(words_to_syllables(WORDS, "ko"), notes)
        by_start = {round(n.start, 2): (text, syllabic) for n, text, syllabic in placed}
        self.assertEqual(by_start, {
            0.0: ("사", "begin"), 0.5: ("랑", "middle"), 1.0: ("해", "middle"), 1.25: ("요", "end"),
            2.0: ("그", "begin"), 2.5: ("대", "middle"), 2.75: ("를", "end"),   # 3.0 s: melisma, no syllable
            4.0: ("beau", "begin"), 4.5: ("ti", "middle"), 5.0: ("ful", "end"),
            6.0: ("my", "single"), 6.5: ("my", "single"), 7.0: ("love", "single"),
        })

    def test_unmatched_syllables_are_kept(self):
        """More syllables than notes (fast passage): extra ones join a neighbouring note."""
        notes = [pretty_midi.Note(90, 60, 0.0, 0.5), pretty_midi.Note(90, 62, 0.5, 1.0)]
        words = [{"start": 0.0, "end": 0.3, "text": "na"}, {"start": 0.1, "end": 0.3, "text": "na"},
                 {"start": 0.5, "end": 0.9, "text": "hey"}]
        placed = align_syllables_to_notes(words_to_syllables(words, "en"), notes)
        self.assertEqual([t for _, t, _ in placed], ["na na", "hey"])

    def test_whisper_output_is_filtered(self):
        W = types.SimpleNamespace
        segments = [
            W(text=" 사랑해요", no_speech_prob=0.1, compression_ratio=1.2,
              words=[W(start=0.0, end=1.0, word=" 사랑해요", probability=0.9),
                     W(start=1.0, end=1.2, word=" 음", probability=0.05)]),       # unsure word
            W(text=" 시청해주셔서 감사합니다", no_speech_prob=0.2, compression_ratio=1.1,
              words=[W(start=2.0, end=3.0, word=" 시청해주셔서", probability=0.9)]),  # credit hallucination
            W(text=" la la la la la la", no_speech_prob=0.1, compression_ratio=3.0,
              words=[W(start=4.0, end=5.0, word=" la", probability=0.9)]),      # repetition loop
        ]

        class FakeModel:
            def __init__(self, *args, **kwargs):
                pass

            def transcribe(self, *args, **kwargs):
                return iter(segments), W(language="ko")

        with mock.patch.dict(sys.modules, {"faster_whisper": types.SimpleNamespace(WhisperModel=FakeModel)}):
            result = transcribe_lyrics("vocals.wav", device="cpu")
        self.assertEqual(result["language"], "ko")
        self.assertEqual([w["text"] for w in result["words"]], ["사랑해요"])
        self.assertEqual(result["lines"], ["사랑해요"])

    def test_gpu_failure_falls_back_to_cpu(self):
        import src.pipeline as pipeline
        _, notes = self._vocal_midi()
        mid = os.path.join(self.test_dir, "vocals.mid")
        result = {"language": "ko", "lines": ["사랑해요 그대를"], "words": WORDS[:2]}
        calls = []

        def fake_transcribe(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("Library cudnn_ops64_9.dll is not found")
            return result

        text_path = os.path.join(self.test_dir, "lyrics.txt")
        with mock.patch("torch.cuda.is_available", return_value=True), \
                mock.patch("torch.cuda.empty_cache"), \
                mock.patch("src.lyrics.transcribe_lyrics", side_effect=fake_transcribe):
            lyrics = pipeline.recognize_lyrics("vocals.wav", mid, pipeline.VRAM_PROFILES["8gb"], "ko",
                                               text_path, log=lambda m: None)

        self.assertEqual((calls[0]["device"], calls[0]["model_size"]), ("cuda", "large-v3"))
        self.assertEqual((calls[1]["device"], calls[1]["compute_type"]), ("cpu", "int8"))
        self.assertEqual(open(text_path, encoding="utf-8").read().strip(), "사랑해요 그대를")
        self.assertEqual([l["text"] for l in lyrics], ["사", "랑", "해", "요", "그", "대", "를"])

    def _vocal_score(self, name="vocal_score.musicxml"):
        mid, notes = self._vocal_midi()
        placed = align_syllables_to_notes(words_to_syllables(WORDS, "ko"), notes)
        lyrics = [{"start": n.start, "pitch": n.pitch, "text": t, "syllabic": s} for n, t, s in placed]
        xml_out = os.path.join(self.test_dir, name)
        build_musicxml_score("", "", "", "", xml_out, selected_parts=["vocals"], bpm=120.0,
                             vocal_midi_path=mid, lyrics=lyrics)
        return xml_out

    def test_vocal_part_in_score(self):
        root = ET.parse(self._vocal_score()).getroot()
        self.assertEqual(root.findtext("./part-list/score-part/part-name"), "Vocals")
        sung = [n for n in root.iter("note") if n.find("rest") is None]
        lyrics = [(n.findtext("lyric/text"), n.findtext("lyric/syllabic")) for n in sung if n.find("lyric") is not None]
        self.assertEqual(len(lyrics), 13)
        self.assertEqual(lyrics[:4], [("사", "begin"), ("랑", "middle"), ("해", "middle"), ("요", "end")])
        for n in sung:
            if n.find("tie[@type='stop']") is not None:  # tied continuations never get a syllable
                self.assertIsNone(n.find("lyric"))
        # A melody in guitar range also carries guitar TAB; the high voice keeps a plain treble clef
        self.assertTrue(all(n.find("notations/technical/fret") is not None for n in sung))
        self.assertIsNone(root.find("./part/measure/attributes/clef/clef-octave-change"))

    def test_lyrics_editor_hyphen_convention(self):
        xml_path = self._vocal_score("vocal_edit.musicxml")
        editor = ScoreLyricsEditor(xml_path)
        self.assertEqual([p["name"] for p in editor.parts], ["Vocals"])
        pid = editor.parts[0]["id"]

        bar3 = editor.get_measure_lyrics(pid, 3)  # beau- ti- ful
        self.assertEqual([n["text"] for n in bar3], ["beau-", "ti-", "ful"])

        # Fix the recognition: "beautiful" was really "wonderful"
        self.assertTrue(editor.set_measure_lyrics(pid, 3, ["won-", "der-", "ful"]))
        # Make the second "my" continue into "love" as one word, and clear nothing else
        bar4 = editor.get_measure_lyrics(pid, 4)
        self.assertTrue(editor.set_measure_lyrics(pid, 4, [bar4[0]["text"], "my-", "love"]))
        editor.save_xml()

        reread = ScoreLyricsEditor(xml_path)
        self.assertEqual([n["text"] for n in reread.get_measure_lyrics(pid, 3)], ["won-", "der-", "ful"])
        self.assertEqual(reread.full_text(pid), "사랑해요 그대를 wonderful my mylove")
        syllabics = [l.findtext("syllabic") for l in reread.root.iter("lyric")]
        self.assertEqual(syllabics[-3:], ["single", "begin", "end"])

        # Clearing an entry removes that lyric
        self.assertTrue(reread.set_measure_lyrics(pid, 4, ["", "my", "love"]))
        self.assertEqual([n["text"] for n in reread.get_measure_lyrics(pid, 4)], ["", "my", "love"])

    def test_lilypond_vocal_layout(self):
        ly = (
            'PartPFiveVoiceOne =  \\relative e\' {\n'
            '    \\clef "treble_8" \\key c \\major e4 \\1 g4 \\1 }\n'
            'PartPFiveVoiceOneLyricsOne =  \\lyricmode {\\set ignoreMelismata = ##t "사" -- "랑" }\n'
            '\\score {\n'
            '        \\new Staff\n'
            '        <<\n'
            '            \\set Staff.instrumentName = "Vocals"\n'
            '            \\context Staff << \n'
            '                \\context Voice = "PartPFiveVoiceOne" {  \\PartPFiveVoiceOne }\n'
            '                \\new Lyrics \\lyricsto "PartPFiveVoiceOne" { \\set stanza = "1." \\PartPFiveVoiceOneLyricsOne }\n'
            '                >>\n'
            '            >>\n'
            '    }\n'
        )
        for style in ("standard", "tab", "both"):
            out = convert_ly_to_tab_style(ly, style=style)
            self.assertNotIn("stanza", out, style)
            self.assertEqual(out.count("\\lyricsto"), 1, style)  # lyrics kept exactly once
            self.assertEqual("TabStaff" in out, style != "standard", style)
            self.assertEqual('\\clef "treble_8" \\PartPFiveVoiceOne' in out, style != "tab", style)
            if style != "tab":
                self.assertIn("StringNumber.stencil = ##f", out)  # no guitar string numbers for singers


if __name__ == "__main__":
    unittest.main()
