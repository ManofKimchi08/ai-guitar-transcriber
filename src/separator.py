"""
Audio Stem Separator Module
Separates input song into isolated instrument stems using Meta AI's Demucs (htdemucs_6s).
Extracts: drums.wav, bass.wav, guitar.wav, vocals.wav, piano.wav, other.wav
Supports GPU CUDA acceleration on NVIDIA RTX GPUs.
"""

import os
import sys
import shutil
import subprocess
import torch


# Demucs's documented low-memory settings, used when the GPU runs out of memory
LOW_MEMORY_SEGMENT = 3


def separate_stems(
    audio_path: str,
    output_dir: str,
    model_name: str = "htdemucs_6s",
    device: str = None,
    segment: int = None
) -> dict:
    """
    Separates an audio file into stems using Demucs.

    Args:
        audio_path: Path to input audio file (MP3, WAV, FLAC, etc.)
        output_dir: Directory where separated stems will be stored.
        model_name: 'htdemucs_6s' (6 stems including guitar) or 'htdemucs' (4 stems)
        device: 'cuda' or 'cpu' (defaults to cuda if available)
        segment: Seconds of audio processed on the GPU at once (lower = less VRAM,
            at most 7 for Hybrid Transformer models). None keeps Demucs's default.

    Returns:
        dict: Mapping of stem names to file paths.
    """
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
    os.makedirs(output_dir, exist_ok=True)
    abs_audio = os.path.abspath(audio_path)
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    
    print(f"[*] Running Demucs separation with model '{model_name}' on device '{device}'...")
    
    # Run Demucs via Python module execution in current virtual environment
    python_exe = sys.executable

    def run_demucs(seg, env=None):
        cmd = [
            python_exe, "-m", "demucs.separate",
            "-n", model_name,
            "-d", device,
            "-o", output_dir,
        ]
        if seg:
            cmd += ["--segment", str(seg)]
        cmd.append(abs_audio)
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, env=env)

    try:
        try:
            proc = run_demucs(segment)
        except subprocess.CalledProcessError as e:
            if device != "cuda" or "out of memory" not in (e.stdout or "").lower():
                raise
            print(f"[!] GPU memory exhausted; retrying Demucs in low-memory mode (segment {LOW_MEMORY_SEGMENT}s)...")
            proc = run_demucs(LOW_MEMORY_SEGMENT, env=dict(os.environ, PYTORCH_NO_CUDA_MEMORY_CACHING="1"))
        print(proc.stdout)
    except subprocess.CalledProcessError as e:
        print(f"[-] Demucs execution failed with code {e.returncode}:")
        print(e.stdout)
        raise RuntimeError(f"Demucs separation failed: {e}")

    # Demucs saves files in: output_dir / model_name / base_name / stem.wav
    stem_dir = os.path.join(output_dir, model_name, base_name)
    model_dir = os.path.join(output_dir, model_name)
    if not os.path.exists(stem_dir) and os.path.isdir(model_dir):
        # Demucs may rename the track folder; take this model's newest one
        # (never a sibling folder such as stems/ or midi/).
        tracks = [os.path.join(model_dir, d) for d in os.listdir(model_dir)
                  if os.path.isdir(os.path.join(model_dir, d))]
        if tracks:
            stem_dir = max(tracks, key=os.path.getmtime)

    expected_stems = ["drums", "bass", "guitar", "vocals", "piano", "other"]
    stems = {}
    
    # Move stems to a cleaner root inside output_dir / "stems"
    clean_stems_dir = os.path.join(output_dir, "stems")
    os.makedirs(clean_stems_dir, exist_ok=True)
    
    for stem in expected_stems:
        wav_name = f"{stem}.wav"
        src_file = os.path.join(stem_dir, wav_name)
        dst_file = os.path.join(clean_stems_dir, wav_name)
        if os.path.exists(src_file):
            shutil.copy2(src_file, dst_file)
            stems[stem] = dst_file
            
    print(f"[+] Successfully separated {len(stems)} stems to: {clean_stems_dir}")
    return stems


if __name__ == "__main__":
    if len(sys.argv) > 2:
        res = separate_stems(sys.argv[1], sys.argv[2])
        print("Separated stems:", res)
