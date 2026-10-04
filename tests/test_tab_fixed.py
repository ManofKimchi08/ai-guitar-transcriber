import os
import re
import subprocess

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.pdf_exporter import find_lilypond

_, _, lilypond_exe = find_lilypond()

orig_ly = "output/scores/Petalburg City Theme_full_score.ly"
with open(orig_ly, "r", encoding="utf-8") as f:
    text = f.read()

# 1. Remove \clef "treble" and \clef "bass" from the guitar and bass voices
text = re.sub(r'PartPTwoVoiceOne\s*=\s*\\relative\s+\w+\s*\{(\s*\\clef\s+"[^"]+")?', r'PartPTwoVoiceOne = \\relative f { ', text)
text = re.sub(r'PartPThreeVoiceOne\s*=\s*\\relative\s+\w+\s*\{(\s*\\clef\s+"[^"]+")?', r'PartPThreeVoiceOne = \\relative gis { ', text)
text = re.sub(r'PartPFourVoiceOne\s*=\s*\\relative\s+\w+\s*\{(\s*\\clef\s+"[^"]+")?', r'PartPFourVoiceOne = \\relative c\' { ', text)

# 2. Replace the entire Staff blocks in \score
bass_block = r'''        \new TabStaff \with { stringTunings = #bass-tuning }
        <<
            \set TabStaff.instrumentName = "Bass (TAB)"
            \set TabStaff.shortInstrumentName = "Bass"
            \context TabVoice = "PartPTwoVoiceOne" { \PartPTwoVoiceOne }
        >>'''

rhythm_block = r'''        \new TabStaff \with { stringTunings = #guitar-tuning }
        <<
            \set TabStaff.instrumentName = "Rhythm (TAB)"
            \set TabStaff.shortInstrumentName = "Rhythm"
            \context TabVoice = "PartPThreeVoiceOne" { \PartPThreeVoiceOne }
        >>'''

lead_block = r'''        \new TabStaff \with { stringTunings = #guitar-tuning }
        <<
            \set TabStaff.instrumentName = "Lead (TAB)"
            \set TabStaff.shortInstrumentName = "Lead"
            \context TabVoice = "PartPFourVoiceOne" { \PartPFourVoiceOne }
        >>'''

text = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Bass Guitar".*?>>\s*>>', lambda m: bass_block, text, flags=re.DOTALL)
text = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Rhythm Guitar".*?>>\s*>>', lambda m: rhythm_block, text, flags=re.DOTALL)
text = re.sub(r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Lead Guitar".*?>>\s*>>', lambda m: lead_block, text, flags=re.DOTALL)

out_ly = os.path.abspath("output/scores/Petalburg_REAL_TAB.ly")
with open(out_ly, "w", encoding="utf-8") as f:
    f.write(text)

out_png_base = os.path.abspath("output/scores/Petalburg_REAL_TAB")
cmd = [lilypond_exe, "--png", "-dresolution=150", "-o", out_png_base, out_ly]
proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.abspath("output/scores"))
print("Return code:", proc.returncode)
print("Stdout:", proc.stdout)
print("Stderr:", proc.stderr)
