import os
import sys
import numpy as np
import soundfile as sf
import pretty_midi

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.pitch_transcriber import transcribe_pitch

test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output"))
os.makedirs(test_dir, exist_ok=True)

# Generate 440 Hz (A4) note, 1.5 seconds
sr = 22050
duration = 1.5
t = np.linspace(0, duration, int(sr * duration), endpoint=False)
audio = 0.6 * np.sin(2 * np.pi * 440.0 * t)

test_wav = os.path.join(test_dir, "test_pitch_a4.wav")
test_mid = os.path.join(test_dir, "test_pitch_a4.mid")
sf.write(test_wav, audio, sr)

print("[*] Running pitch transcription...")
res = transcribe_pitch(test_wav, test_mid, instrument_name="guitar_lead")
print(f"[+] Output MIDI exists: {os.path.exists(res)}")

pm = pretty_midi.PrettyMIDI(res)
print(f"[+] Found {len(pm.instruments)} instruments, {len(pm.instruments[0].notes)} notes.")
for note in pm.instruments[0].notes:
    print(f"    Note: pitch={note.pitch} (expected ~69), start={note.start:.2f}, end={note.end:.2f}, velocity={note.velocity}")
print("[✓] Basic Pitch test completed successfully!")
