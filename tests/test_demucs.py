import os
import sys
import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.separator import separate_stems

test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_output"))
os.makedirs(test_dir, exist_ok=True)

# Generate 2-second stereo synthetic mix
sr = 44100
duration = 2.0
t = np.linspace(0, duration, int(sr * duration), endpoint=False)

# Mix of low thud (kick), bass tone, and high pitch
kick = 0.5 * np.sin(2 * np.pi * 60 * t)
tone = 0.3 * np.sin(2 * np.pi * 440 * t)
stereo_mix = np.stack([kick + tone, kick - tone], axis=1)

test_song = os.path.join(test_dir, "test_demucs_song.wav")
sf.write(test_song, stereo_mix, sr)

print(f"[*] Testing Demucs separation on CUDA (Device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'})...")
stems = separate_stems(test_song, os.path.join(test_dir, "demucs_out"), model_name="htdemucs_6s")

print(f"[+] Demucs separation succeeded! Generated stems: {list(stems.keys())}")
for name, path in stems.items():
    print(f"    - {name}: {path} (exists: {os.path.exists(path)})")
print("[✓] Demucs test completed successfully!")
