"""
Drum Transcriber Module
Transcribes a drum audio track (drums.wav) into a standard General MIDI drum file (drums.mid).
Uses multi-band transient detection and spectral energy envelope classification:
- Sub-bass (30~120 Hz): Kick Drum (MIDI 36)
- Mid-frequency + noise burst (160~500 Hz): Snare Drum (MIDI 38)
- High-frequency short decay: Closed Hi-Hat (MIDI 42)
- High-frequency long decay: Open Hi-Hat (MIDI 46) / Crash (MIDI 49)
"""

import os
import numpy as np
import soundfile as sf
import pretty_midi
from scipy.signal import butter, filtfilt


def bandpass_filter(data, lowcut, highcut, fs, order=3):
    nyq = 0.5 * fs
    low = max(lowcut / nyq, 0.001)
    high = min(highcut / nyq, 0.999)
    b, a = butter(order, [low, high], btype='band')
    return filtfilt(b, a, data, axis=0)


def detect_onsets_adaptive(signal, sr, hop_length=256, delta_factor=0.8, min_dist_sec=0.08):
    """
    Detects transient peak onsets using spectral flux / onset strength with local adaptive peak picking.
    """
    import librosa
    try:
        env = librosa.onset.onset_strength(y=signal, sr=sr, hop_length=hop_length)
        if len(env) == 0 or np.max(env) < 1e-4:
            return []
        
        # Adaptive delta threshold based on mean + std of energy
        delta = float(np.mean(env) * delta_factor + np.std(env) * 0.25)
        wait_frames = max(2, int(min_dist_sec * sr / hop_length))
        
        peaks = librosa.util.peak_pick(
            env,
            pre_max=3,
            post_max=3,
            pre_avg=3,
            post_avg=5,
            delta=delta,
            wait=wait_frames
        )
        
        times = librosa.frames_to_time(peaks, sr=sr, hop_length=hop_length)
        amplitudes = env[peaks]
        return list(zip(times, amplitudes))
    except Exception as e:
        # Fallback if librosa onset fails
        return []


def transcribe_drums(drum_wav_path: str, output_midi_path: str) -> str:
    """
    Transcribes drum audio to a MIDI file with GM drum mapping.
    
    Args:
        drum_wav_path: Path to drums.wav
        output_midi_path: Path to write drums.mid
        
    Returns:
        output_midi_path
    """
    os.makedirs(os.path.dirname(output_midi_path) or '.', exist_ok=True)
    
    audio, sr = sf.read(drum_wav_path)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)  # downmix to mono for envelope analysis
        
    hop_length = 256
    
    # 1. Kick Band (35 Hz ~ 120 Hz)
    kick_signal = bandpass_filter(audio, 35, 120, sr)
    kick_onsets = detect_onsets_adaptive(kick_signal, sr, hop_length, delta_factor=0.9, min_dist_sec=0.10)
    
    # 2. Snare Band (180 Hz ~ 650 Hz)
    snare_signal = bandpass_filter(audio, 180, 650, sr)
    snare_onsets = detect_onsets_adaptive(snare_signal, sr, hop_length, delta_factor=0.85, min_dist_sec=0.09)
    
    # 3. Cymbal / Hi-Hat Band (5000 Hz ~ 14000 Hz)
    cym_signal = bandpass_filter(audio, 5000, 14000, sr)
    cym_onsets = detect_onsets_adaptive(cym_signal, sr, hop_length, delta_factor=0.6, min_dist_sec=0.07)
    
    # Construct PrettyMIDI drum track (Channel 10)
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    drum_instrument = pretty_midi.Instrument(program=0, is_drum=True, name="Drum Kit")
    
    def add_notes(onsets, pitch, default_dur=0.1):
        if not onsets:
            return
        max_amp = max(amp for _, amp in onsets) if onsets else 1.0
        for t, amp in onsets:
            velocity = int(np.clip(45 + (amp / (max_amp + 1e-9)) * 82, 35, 127))
            note = pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=t,
                end=t + default_dur
            )
            drum_instrument.notes.append(note)
            
    # Add Kicks (MIDI 36)
    add_notes(kick_onsets, 36, default_dur=0.15)
    
    # Add Snares (MIDI 38)
    add_notes(snare_onsets, 38, default_dur=0.12)
    
    # Add Hi-Hats: 42 = Closed, 46 = Open
    if cym_onsets:
        max_cym = max(amp for _, amp in cym_onsets)
        for t, amp in cym_onsets:
            is_open = amp > max_cym * 0.7
            pitch = 46 if is_open else 42
            velocity = int(np.clip(35 + (amp / (max_cym + 1e-9)) * 90, 30, 120))
            note = pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=t,
                end=t + (0.25 if is_open else 0.08)
            )
            drum_instrument.notes.append(note)
            
    # Sort notes by start time
    drum_instrument.notes.sort(key=lambda n: n.start)
    pm.instruments.append(drum_instrument)
    
    pm.write(output_midi_path)
    return output_midi_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        res = transcribe_drums(sys.argv[1], sys.argv[2])
        print("Drum transcription saved to:", res)
