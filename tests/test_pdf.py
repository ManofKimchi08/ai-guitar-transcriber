import os
import subprocess

musescore_exe = r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe"
xml_abs = os.path.abspath("output/scores/Petalburg City Theme_full_score.musicxml")
pdf_abs = os.path.abspath("output/scores/Petalburg City Theme_full_score.pdf")

print("MuseScore exists:", os.path.exists(musescore_exe))
print("XML exists:", os.path.exists(xml_abs))
print("Target PDF:", pdf_abs)

cmd = [musescore_exe, "-o", pdf_abs, xml_abs]
print("Running command:", cmd)

proc = subprocess.run(cmd, capture_output=True, text=True)
print("Return code:", proc.returncode)
print("Stdout:", proc.stdout)
print("Stderr:", proc.stderr)
print("PDF created:", os.path.exists(pdf_abs))
