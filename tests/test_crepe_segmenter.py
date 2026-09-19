import os
import torch
import torchcrepe
import librosa
import numpy as np
import pretty_midi
from scipy.signal import medfilt

def segment_pitch_to_notes(
    pitch_hz: np.ndarray,
    periodicity: np.ndarray,
    rms: np.ndarray,
    hop_length: int = 160,
    sr: int = 16000,
    periodicity_thresh: float = 0.40,
    rms_thresh: float = 0.02,
    min_note_duration: float = 0.06,
    instrument_name: str = "bass"
):
    """
    Converts continuous TorchCREPE pitch contour into discrete musical MIDI notes.
    """
    time_step = hop_length / sr
    min_len = min(len(pitch_hz), len(periodicity), len(rms))
    pitch_hz = pitch_hz[:min_len]
    periodicity = periodicity[:min_len]
    rms = rms[:min_len]

    # Mask unvoiced / quiet frames
    voiced_mask = (periodicity >= periodicity_thresh) & (rms >= rms_thresh)

    # Convert Hz to fractional MIDI pitch
    midi_pitches = np.zeros(min_len)
    valid_idx = np.where(voiced_mask & (pitch_hz > 20))[0]
    midi_pitches[valid_idx] = librosa.hz_to_midi(pitch_hz[valid_idx])

    # Temporal median filter to eliminate 1-2 frame outliers
    smoothed_pitches = np.copy(midi_pitches)
    if len(valid_idx) > 5:
        smoothed_pitches = medfilt(midi_pitches, kernel_size=5)

    notes = []
    current_pitch = None
    note_start = 0.0
    accum_rms = []
    pitch_samples = []

    def save_note(start_t, end_t, p_val, rms_list):
        dur = end_t - start_t
        if dur >= min_note_duration:
            note_pitch = int(round(p_val))
            if instrument_name == "bass":
                # Standard bass: E1 (28) ~ G3 (55) / C4 (60)
                if note_pitch > 60:
                    note_pitch -= 12
                if 28 <= note_pitch <= 60:
                    avg_rms = np.mean(rms_list) if rms_list else 0.05
                    vel = int(np.clip(40 + avg_rms * 250, 40, 115))
                    notes.append(pretty_midi.Note(
                        velocity=vel,
                        pitch=note_pitch,
                        start=start_t,
                        end=end_t
                    ))
            elif instrument_name == "guitar_lead":
                if 40 <= note_pitch <= 90:
                    avg_rms = np.mean(rms_list) if rms_list else 0.05
                    vel = int(np.clip(45 + avg_rms * 250, 45, 120))
                    notes.append(pretty_midi.Note(
                        velocity=vel,
                        pitch=note_pitch,
                        start=start_t,
                        end=end_t
                    ))

    for i in range(min_len):
        t = i * time_step
        p = smoothed_pitches[i]
        is_v = voiced_mask[i] and p > 0

        if is_v:
            if current_pitch is None:
                current_pitch = p
                note_start = t
                accum_rms = [rms[i]]
                pitch_samples = [p]
            else:
                if abs(p - current_pitch) >= 0.85:
                    save_note(note_start, t, np.median(pitch_samples), accum_rms)
                    current_pitch = p
                    note_start = t
                    accum_rms = [rms[i]]
                    pitch_samples = [p]
                else:
                    accum_rms.append(rms[i])
                    pitch_samples.append(p)
                    current_pitch = 0.8 * current_pitch + 0.2 * p
        else:
            if current_pitch is not None:
                save_note(note_start, t, np.median(pitch_samples), accum_rms)
                current_pitch = None
                accum_rms = []
                pitch_samples = []

    if current_pitch is not None:
        save_note(note_start, min_len * time_step, np.median(pitch_samples), accum_rms)

    return notes

if __name__ == "__main__":
    audio_path = "output/stems/bass.wav"
    y, sr = librosa.load(audio_path, sr=16000, mono=True)
    y_norm = y / (np.max(np.abs(y)) + 1e-9)
    hop_len = 160
    rms = librosa.feature.rms(y=y_norm, frame_length=1024, hop_length=hop_len)[0]

    audio_t = torch.from_numpy(y_norm).unsqueeze(0).float().cuda()
    pitch, periodicity = torchcrepe.predict(
        audio_t,
        sample_rate=16000,
        hop_length=hop_len,
        fmin=35.0,
        fmax=350.0,
        model='full',
        batch_size=2048,
        device='cuda',
        return_periodicity=True
    )
    p_np = pitch.squeeze(0).cpu().numpy()
    conf_np = periodicity.squeeze(0).cpu().numpy()

    notes = segment_pitch_to_notes(p_np, conf_np, rms, hop_length=hop_len, sr=16000, instrument_name="bass")
    print(f"Extracted {len(notes)} bass notes!")
    pitches = [n.pitch for n in notes]
    print(f"Bass pitch range: min={min(pitches)}, max={max(pitches)}")
    print("First 15 bass notes (MIDI pitch & duration):")
    for n in notes[:15]:
        pname = pretty_midi.note_number_to_name(n.pitch)
        print(f"  {pname} ({n.pitch}) | start: {n.start:.2f}s, dur: {n.end-n.start:.2f}s, vel: {n.velocity}")
