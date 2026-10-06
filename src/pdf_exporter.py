"""
PDF Exporter Module
Converts MusicXML score files into authentic Guitar/Bass Tablature (TAB) PDF sheet music using LilyPond.
"""

import os
import re
import glob
import subprocess


def find_lilypond():
    """Finds LilyPond executable and python environment on Windows, Linux, and macOS."""
    import shutil

    # 1. Check if lilypond is in system PATH
    lp_which = shutil.which("lilypond") or shutil.which("lilypond.exe")
    if lp_which:
        lp_dir = os.path.dirname(os.path.abspath(lp_which))
        py = os.path.join(lp_dir, "python.exe") if os.name == 'nt' else shutil.which("python3") or shutil.which("python")
        m2l = os.path.join(lp_dir, "musicxml2ly.py")
        if not os.path.exists(m2l):
            m2l_which = shutil.which("musicxml2ly") or shutil.which("musicxml2ly.py")
            if m2l_which:
                m2l = m2l_which
        if os.path.exists(lp_which) and os.path.exists(py) and os.path.exists(m2l):
            return py, m2l, lp_which

    # 2. Check Windows WinGet / Program Files standard paths
    appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    search_patterns = [
        os.path.join(appdata, r"Microsoft\WinGet\Packages\*LilyPond*\*\bin"),
        os.path.join(appdata, r"Microsoft\WinGet\Packages\*LilyPond*\bin"),
        os.path.join(appdata, r"Programs\*LilyPond*\bin"),
        os.path.join(program_files, r"LilyPond*\bin"),
        os.path.join(program_files_x86, r"LilyPond*\bin"),
    ]

    candidates = []
    for pat in search_patterns:
        candidates.extend(glob.glob(pat))

    for d in candidates:
        lp = os.path.join(d, "lilypond.exe")
        py = os.path.join(d, "python.exe")
        m2l = os.path.join(d, "musicxml2ly.py")
        if os.path.exists(lp) and os.path.exists(py) and os.path.exists(m2l):
            return py, m2l, lp

    return None, None, None


# Percussion clef glyph with treble-clef note positions (MusicXML drum display positions)
PERCUSSION_CLEF = (
    '\\set Staff.clefGlyph = #"clefs.percussion" \\set Staff.clefPosition = #0 '
    '\\set Staff.middleCPosition = #-6 \\set Staff.middleCClefPosition = #-6'
)


VOCAL_BLOCK = re.compile(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Vocals".*?>>\s*>>', re.DOTALL)


def _voice_definition(part: str) -> re.Pattern:
    """A voice definition runs until the next unindented line (next definition or \\score)."""
    return re.compile(rf'^{part}\s*=.*?(?=^\S)', re.DOTALL | re.MULTILINE)


def _convert_vocals(ly_content: str, style: str, vocal_clef: str) -> str:
    """
    Lays out the vocal melody for the style, keeping its lyrics in every style:
    standard staff (guitar string numbers hidden; they only drive the TAB), TAB with
    the lyrics under it, or both.
    """
    m = VOCAL_BLOCK.search(ly_content)
    if not m:
        return ly_content
    lyrics = re.search(r'\\new Lyrics \\lyricsto "PartPFiveVoiceOne" \{[^}]*\}', m.group(0))
    lyrics_line = ("            " + lyrics.group(0) + "\n") if lyrics else ""
    staff = (
        '\\new Staff\n'
        '        <<\n'
        '            \\set Staff.instrumentName = "Vocals"\n'
        '            \\set Staff.shortInstrumentName = "Vox"\n'
        '            \\override Staff.StringNumber.stencil = ##f\n'
        f'            \\context Voice = "PartPFiveVoiceOne" {{ \\clef "{vocal_clef}" \\PartPFiveVoiceOne }}\n'
        f'{lyrics_line}'
        '        >>'
    )
    tab = (
        '\\new TabStaff \\with { stringTunings = #guitar-tuning }\n'
        '        <<\n'
        '            \\set TabStaff.instrumentName = "Vocals (TAB)"\n'
        '            \\set TabStaff.shortInstrumentName = "Vox"\n'
        '            \\context TabVoice = "PartPFiveVoiceOne" { \\PartPFiveVoiceOne }\n'
        f'{lyrics_line if style == "tab" else ""}'
        '        >>'
    )
    replacement = {"standard": staff, "both": staff + "\n        " + tab, "tab": tab}[style]
    return ly_content[:m.start()] + replacement + ly_content[m.end():]


def convert_ly_to_tab_style(ly_content: str, style: str = "tab") -> str:
    """
    Transforms LilyPond Staff contexts into genuine Guitar & Bass TabStaff
    with fret numbers rendered directly on 6-line and 4-line TAB staves.
    Supports 'tab' (pure TAB), 'standard' (standard 5-line notation),
    and 'both' (paired 5-line notation + TAB staves).
    """
    # 1. Sanitize invalid string numbers on rests (r, R, s)
    # musicxml2ly sometimes attaches string annotations (\1, \2, ...) to rests,
    # which crashes LilyPond's Guile interpreter with: Wrong type (expecting exact integer): ()
    ly_content = re.sub(r'([rRs]\d*[\.*]*(?:\*\d+)?)(?:\s*\\[0-9]+)+', r'\1', ly_content)

    # Drums: LilyPond's DrumStaff only places \drummode notes, so musicxml2ly's
    # unpitched notes would all land on the middle line. Draw the drum voice on a
    # plain staff with the percussion clef glyph and treble-clef note positions.
    ly_content = ly_content.replace("\\new DrumStaff", "\\new Staff")
    ly_content = ly_content.replace("\\set DrumStaff.", "\\set Staff.")
    ly_content = ly_content.replace("\\context DrumStaff", "\\context Staff")
    ly_content = ly_content.replace("\\context DrumVoice", "\\context Voice")
    ly_content = ly_content.replace('\\clef "percussion"', PERCUSSION_CLEF)

    # Lyrics: no "1." stanza label in front of a single verse
    ly_content = re.sub(r'\\set stanza = "[^"]*"\s*', '', ly_content)

    # The vocal clef (treble or treble_8, by voice range) before any clef is stripped
    vocal_def = _voice_definition("PartPFiveVoiceOne").search(ly_content)
    vocal_clef = re.search(r'\\clef\s+"([^"]+)"', vocal_def.group(0)) if vocal_def else None
    vocal_clef = vocal_clef.group(1) if vocal_clef else "treble"

    if style == "standard":
        return _convert_vocals(ly_content, style, vocal_clef)

    # 2. Strip every \clef (e.g. "treble_8", "bass_8") from instrument voices P2-P5
    # so LilyPond doesn't force a 5-line musical notation staff inside the TabStaff.
    for part in ["PartPTwoVoiceOne", "PartPThreeVoiceOne", "PartPFourVoiceOne", "PartPFiveVoiceOne"]:
        ly_content = _voice_definition(part).sub(
            lambda m: re.sub(r'\\clef\s+"[^"]+"\s*', '', m.group(0)), ly_content)
    ly_content = _convert_vocals(ly_content, style, vocal_clef)

    if style == "both":
        # Both: Paired Standard 5-line Staff + 6-line/4-line TabStaff
        both_bass = (
            '\\new Staff\n'
            '        <<\n'
            '            \\set Staff.instrumentName = "Bass"\n'
            '            \\set Staff.shortInstrumentName = "Ba."\n'
            '            \\context Voice = "PartPTwoVoiceOne" { \\clef "bass_8" \\PartPTwoVoiceOne }\n'
            '        >>\n'
            '        \\new TabStaff \\with { stringTunings = #bass-tuning }\n'
            '        <<\n'
            '            \\set TabStaff.instrumentName = "Bass (TAB)"\n'
            '            \\set TabStaff.shortInstrumentName = "TAB"\n'
            '            \\context TabVoice = "PartPTwoVoiceOne" { \\PartPTwoVoiceOne }\n'
            '        >>'
        )
        both_rhythm = (
            '\\new Staff\n'
            '        <<\n'
            '            \\set Staff.instrumentName = "Rhythm Guitar"\n'
            '            \\set Staff.shortInstrumentName = "Rhy."\n'
            '            \\context Voice = "PartPThreeVoiceOne" { \\clef "treble_8" \\PartPThreeVoiceOne }\n'
            '        >>\n'
            '        \\new TabStaff \\with { stringTunings = #guitar-tuning }\n'
            '        <<\n'
            '            \\set TabStaff.instrumentName = "Rhythm (TAB)"\n'
            '            \\set TabStaff.shortInstrumentName = "TAB"\n'
            '            \\context TabVoice = "PartPThreeVoiceOne" { \\PartPThreeVoiceOne }\n'
            '        >>'
        )
        both_lead = (
            '\\new Staff\n'
            '        <<\n'
            '            \\set Staff.instrumentName = "Lead Guitar"\n'
            '            \\set Staff.shortInstrumentName = "Lead"\n'
            '            \\context Voice = "PartPFourVoiceOne" { \\clef "treble_8" \\PartPFourVoiceOne }\n'
            '        >>\n'
            '        \\new TabStaff \\with { stringTunings = #guitar-tuning }\n'
            '        <<\n'
            '            \\set TabStaff.instrumentName = "Lead (TAB)"\n'
            '            \\set TabStaff.shortInstrumentName = "TAB"\n'
            '            \\context TabVoice = "PartPFourVoiceOne" { \\PartPFourVoiceOne }\n'
            '        >>'
        )
        ly_content = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Bass Guitar".*?>>\s*>>', lambda m: both_bass, ly_content, flags=re.DOTALL)
        ly_content = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Rhythm Guitar".*?>>\s*>>', lambda m: both_rhythm, ly_content, flags=re.DOTALL)
        ly_content = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Lead Guitar".*?>>\s*>>', lambda m: both_lead, ly_content, flags=re.DOTALL)
        return ly_content

    # Pure TAB mode
    bass_block = (
        '\\new TabStaff \\with { stringTunings = #bass-tuning }\n'
        '        <<\n'
        '            \\set TabStaff.instrumentName = "Bass (TAB)"\n'
        '            \\set TabStaff.shortInstrumentName = "Bass"\n'
        '            \\context TabVoice = "PartPTwoVoiceOne" { \\PartPTwoVoiceOne }\n'
        '        >>'
    )
    rhythm_block = (
        '\\new TabStaff \\with { stringTunings = #guitar-tuning }\n'
        '        <<\n'
        '            \\set TabStaff.instrumentName = "Rhythm (TAB)"\n'
        '            \\set TabStaff.shortInstrumentName = "Rhythm"\n'
        '            \\context TabVoice = "PartPThreeVoiceOne" { \\PartPThreeVoiceOne }\n'
        '        >>'
    )
    lead_block = (
        '\\new TabStaff \\with { stringTunings = #guitar-tuning }\n'
        '        <<\n'
        '            \\set TabStaff.instrumentName = "Lead (TAB)"\n'
        '            \\set TabStaff.shortInstrumentName = "Lead"\n'
        '            \\context TabVoice = "PartPFourVoiceOne" { \\PartPFourVoiceOne }\n'
        '        >>'
    )

    ly_content = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Bass Guitar".*?>>\s*>>', lambda m: bass_block, ly_content, flags=re.DOTALL)
    ly_content = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Rhythm Guitar".*?>>\s*>>', lambda m: rhythm_block, ly_content, flags=re.DOTALL)
    ly_content = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Lead Guitar".*?>>\s*>>', lambda m: lead_block, ly_content, flags=re.DOTALL)

    return ly_content


def export_score_to_pdf(musicxml_path: str, output_pdf_path: str = None, style: str = "tab") -> str:
    """
    Exports a MusicXML file to PDF (TAB or Standard).
    """
    if not os.path.exists(musicxml_path):
        return None

    xml_abs = os.path.abspath(musicxml_path)
    if output_pdf_path is None:
        base_name = os.path.splitext(xml_abs)[0]
        if style == "tab":
            output_pdf_path = f"{base_name}_TAB.pdf"
        elif style == "both":
            output_pdf_path = f"{base_name}_TAB_Score.pdf"
        else:
            output_pdf_path = f"{base_name}.pdf"
            
    pdf_abs = os.path.abspath(output_pdf_path)
    os.makedirs(os.path.dirname(pdf_abs) or '.', exist_ok=True)

    py_bin, m2l_py, lp_bin = find_lilypond()
    if py_bin and m2l_py and lp_bin:
        try:
            ly_path = os.path.splitext(pdf_abs)[0] + ".ly"
            
            # Step 1: musicxml to .ly
            subprocess.run(
                [py_bin, m2l_py, "-o", ly_path, xml_abs],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True
            )
            
            # Step 2: Apply authentic TAB transformation & sanitize invalid articulations
            if os.path.exists(ly_path):
                with open(ly_path, "r", encoding="utf-8") as f:
                    content = f.read()
                transformed = convert_ly_to_tab_style(content, style=style)
                with open(ly_path, "w", encoding="utf-8") as f:
                    f.write(transformed)

            # Step 3: .ly to vector .pdf
            base_out = os.path.splitext(pdf_abs)[0]
            proc = subprocess.run(
                [lp_bin, "--pdf", "-o", base_out, ly_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.path.dirname(pdf_abs)
            )
            
            if proc.returncode != 0:
                err_text = proc.stderr.decode('utf-8', errors='ignore') if proc.stderr else ""
                print(f"[-] LilyPond compilation warning/error (code {proc.returncode}):\n{err_text}")

            if os.path.exists(pdf_abs):
                return pdf_abs
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            print(f"[-] LilyPond tool failed (exit code {e.returncode}):\n{err_msg}")
        except Exception as e:
            print(f"[-] LilyPond PDF conversion error: {e}")

    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        st = sys.argv[2] if len(sys.argv) > 2 else "tab"
        res = export_score_to_pdf(sys.argv[1], style=st)
        print("Exported PDF path:", res)
