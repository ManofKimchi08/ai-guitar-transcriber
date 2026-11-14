import os
import re
import subprocess
import sys

# Ensure root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.pdf_exporter import find_lilypond

_, _, lilypond_exe = find_lilypond()
if not lilypond_exe:
    raise RuntimeError("LilyPond not found!")


orig_ly = "output/scores/Petalburg City Theme_full_score.ly"
with open(orig_ly, "r", encoding="utf-8") as f:
    text = f.read()

# Replace Staff with TabStaff for Bass, Rhythm, Lead
# For Bass:
text = re.sub(
    r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Bass Guitar"',
    r'\\new TabStaff \\with { stringTunings = #bass-tuning } << \\set TabStaff.instrumentName = "Bass TAB"',
    text
)
# For Rhythm:
text = re.sub(
    r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Rhythm Guitar"',
    r'\\new TabStaff \\with { stringTunings = #guitar-tuning } << \\set TabStaff.instrumentName = "Rhythm TAB"',
    text
)
# For Lead:
text = re.sub(
    r'\\new Staff\s*<<\s*\\set Staff\.instrumentName = "Lead Guitar"',
    r'\\new TabStaff \\with { stringTunings = #guitar-tuning } << \\set TabStaff.instrumentName = "Lead TAB"',
    text
)

tab_ly = "output/scores/Petalburg_City_Theme_TAB.ly"
with open(tab_ly, "w", encoding="utf-8") as f:
    f.write(text)

tab_ly_abs = os.path.abspath(tab_ly)
tab_pdf_base = "output/scores/Petalburg_City_Theme_TAB"
tab_pdf_base_abs = os.path.abspath(tab_pdf_base)
cwd_abs = os.path.abspath("output/scores")

cmd = [lilypond_exe, "--pdf", "-o", tab_pdf_base_abs, tab_ly_abs]
proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd_abs)
print("Return code:", proc.returncode)
print("Stdout:", proc.stdout)
print("Stderr:", proc.stderr)

tab_pdf = tab_pdf_base_abs + ".pdf"
print("TAB PDF exists:", os.path.exists(tab_pdf))
if os.path.exists(tab_pdf):
    print("TAB PDF Size:", os.path.getsize(tab_pdf), "bytes")
