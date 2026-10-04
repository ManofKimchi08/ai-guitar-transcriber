"""
Guitar Splitter Module
Separates a composite guitar track into Lead Guitar and Rhythm Guitar.
Uses Mid-Side (M/S) matrix processing for stereo double-tracked tracks,
supplemented by spectral crossover for mono or centered recordings.
"""

import os
import numpy as np
import soundfile as sf
from scipy.signal import butter, filtfilt


def butter_filter(data, cutoff, fs, btype='low', order=4):
    """Applies a Butterworth lowpass or highpass filter."""
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    b, a = butter(order, normal_cutoff, btype=btype, analog=False)
    return filtfilt(b, a, data, axis=0)


def split_guitar_track(input_wav_path: str, output_dir: str) -> dict:
    """
    Separates a guitar audio file into lead and rhythm components.
    
    Args:
        input_wav_path: Path to the input guitar.wav
        output_dir: Directory where lead_guitar.wav and rhythm_guitar.wav will be saved.
        
    Returns:
        dict: Paths to generated files {"lead": ..., "rhythm": ...}
    """
    os.makedirs(output_dir, exist_ok=True)
    lead_path = os.path.join(output_dir, "guitar_lead.wav")
    rhythm_path = os.path.join(output_dir, "guitar_rhythm.wav")

    # Read audio
    audio, sr = sf.read(input_wav_path)
    
    # Handle single channel (mono) vs dual channel (stereo)
    if audio.ndim == 1:
        # Mono file: highpass at 80Hz for lead to eliminate sub-bass rumble while preserving full guitar range (E2=82Hz)
        # Rhythm gets lowpass to focus on rhythm chord fundamentals
        rhythm_audio = butter_filter(audio, 1200.0, sr, btype='low')
        lead_audio = butter_filter(audio, 80.0, sr, btype='high')
    else:
        # Stereo file: analyze stereo correlation
        left = audio[:, 0]
        right = audio[:, 1]
        
        # Calculate cross-correlation / similarity
        norm_l = np.linalg.norm(left) + 1e-9
        norm_r = np.linalg.norm(right) + 1e-9
        correlation = np.dot(left, right) / (norm_l * norm_r)
        
        # Mid-Side transformation:
        # Mid (Center) = (L + R) / 2  --> Center-panned lead/solo
        # Side (Difference) = (L - R) / 2 --> Wide double-tracked rhythm guitars
        mid = (left + right) * 0.5
        side = (left - right) * 0.5
        
        energy_mid = np.mean(mid ** 2)
        energy_side = np.mean(side ** 2)
        
        # If significant stereo separation exists (typical in studio rock/pop)
        if correlation < 0.90 and energy_side > 0.05 * (energy_mid + 1e-9):
            # Lead is center (Mid) with sub-bass cut, Rhythm is stereo side
            lead_audio = butter_filter(mid, 80.0, sr, btype='high')
            # Reconstruct stereo rhythm with original spatial feel
            rhythm_audio = np.stack([side, -side], axis=1)
        else:
            # Low stereo separation: Hybrid M/S preserving guitar fundamental range
            lead_audio = butter_filter(mid, 80.0, sr, btype='high') + side * 0.3
            rhythm_low = butter_filter(mid, 1200.0, sr, btype='low')
            rhythm_audio = np.stack([rhythm_low + side * 0.7, rhythm_low - side * 0.7], axis=1)

    # Normalize audio to prevent clipping
    def normalize(sig):
        max_val = np.max(np.abs(sig))
        if max_val > 1e-6:
            return sig / max_val * 0.95
        return sig

    lead_audio = normalize(lead_audio)
    rhythm_audio = normalize(rhythm_audio)

    # Save to disk
    sf.write(lead_path, lead_audio, sr)
    sf.write(rhythm_path, rhythm_audio, sr)

    return {
        "lead": lead_path,
        "rhythm": rhythm_path
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        res = split_guitar_track(sys.argv[1], sys.argv[2])
        print("Guitar split complete:", res)
