import os
import subprocess

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.pdf_exporter import find_lilypond

lily_py, musicxml2ly_py, lilypond_exe = find_lilypond()

xml_path = os.path.abspath("output/scores/Petalburg City Theme_full_score.musicxml")
ly_path = os.path.abspath("output/scores/Petalburg City Theme_full_score.ly")
pdf_path = os.path.abspath("output/scores/Petalburg City Theme_full_score.pdf")

print("1. Converting MusicXML to LilyPond (.ly)...")
cmd1 = [lily_py, musicxml2ly_py, "-o", ly_path, xml_path]
proc1 = subprocess.run(cmd1, capture_output=True, text=True)
print("xml2ly code:", proc1.returncode)
print("ly exists:", os.path.exists(ly_path))

print("2. Compiling .ly to .pdf with LilyPond...")
cmd2 = [lilypond_exe, "--pdf", "-o", os.path.splitext(pdf_path)[0], ly_path]
proc2 = subprocess.run(cmd2, capture_output=True, text=True, cwd=os.path.dirname(pdf_path))
print("lilypond code:", proc2.returncode)
print("PDF exists:", os.path.exists(pdf_path))
if os.path.exists(pdf_path):
    print("PDF size:", os.path.getsize(pdf_path), "bytes")
