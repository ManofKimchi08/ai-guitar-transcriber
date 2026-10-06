"""
Pitch Transcriber Module
High-Accuracy Transcription for Bass, Lead Guitar, and Rhythm Guitar:
1. Bass & Lead Guitar: TorchCREPE 360-bin Convolutional F0 Tracking on GPU (CUDA)
   - Zero octave jumping, overtone-filtered fundamental bass & solo lead tracking.
2. Rhythm Guitar: CQT Chroma-based Chord Recognition & 6-string Voicings
3. Fallback: Spotify Basic Pitch & Librosa YIN for environments without PyTorch/CUDA.
"""

import os
import pretty_midi


def transcribe_pitch(
    audio_path: str,
    output_midi_path: str,
    instrument_name: str = "guitar_lead",
    engine: str = "crepe",
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    minimum_note_length: float = 58.0,
    include_pitch_bends: bool = True,
    crepe_batch_size: int = 1024
) -> str:
    """
    Transcribes monophonic or polyphonic audio to MIDI using TorchCREPE or CQT Chords.

    Args:
        audio_path: Path to the input WAV audio.
        output_midi_path: Path where the resulting .mid will be saved.
        instrument_name: 'bass', 'guitar_lead', 'guitar_rhythm', or 'vocals'.
        engine: 'crepe' (SOTA high-precision) or 'basic_pitch'.
        crepe_batch_size: TorchCREPE frames per GPU batch (lower = less VRAM, same accuracy).

    Returns:
        Path to output_midi_path
    """
    os.makedirs(os.path.dirname(output_midi_path) or '.', exist_ok=True)

    # 1. Rhythm guitar uses CQT Chroma chord template matching
    if instrument_name == "guitar_rhythm":
        try:
            from src.chord_recognizer import transcribe_rhythm_guitar_chords
            return transcribe_rhythm_guitar_chords(audio_path, output_midi_path, segment_sec=0.5)
        except Exception as e:
            print(f"[!] Warning: Chord recognizer failed ({e}), falling back to basic pitch.")

    # 2. Bass and Lead Guitar use TorchCREPE GPU tracking when engine == 'crepe'
    if engine == "crepe":
        try:
            return transcribe_with_crepe(audio_path, output_midi_path, instrument_name=instrument_name,
                                         batch_size=crepe_batch_size)
        except Exception as e:
            print(f"[!] TorchCREPE failed ({e}), falling back to basic pitch.")

    # 3. Spotify Basic Pitch fallback
    return transcribe_with_basic_pitch(
        audio_path=audio_path,
        output_midi_path=output_midi_path,
        instrument_name=instrument_name,
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=minimum_note_length,
        include_pitch_bends=include_pitch_bends
    )


def transcribe_with_crepe(
    audio_path: str,
    output_midi_path: str,
    instrument_name: str = "bass",
    model: str = "full",
    device: str = None,
    batch_size: int = 1024
) -> str:
    """
    High-accuracy monophonic pitch tracking using TorchCREPE (CUDA accelerated).
    VRAM use grows with batch_size: the 'full' model needs about 2.5 GB of
    activations per 1024 frames.
    """
    import torch
    import torchcrepe
    import librosa
    import numpy as np
    from scipy.signal import medfilt

    os.makedirs(os.path.dirname(output_midi_path) or '.', exist_ok=True)
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    y, sr = librosa.load(audio_path, sr=16000, mono=True)
    y_norm = y / (np.max(np.abs(y)) + 1e-9)
    hop_length = 320
    time_step = hop_length / 16000.0

    # (fmin Hz, fmax Hz, confidence threshold, lowest MIDI note, highest MIDI note)
    fmin, fmax, conf_thresh, low_note, high_note = {
        "bass": (35.0, 350.0, 0.25, 28, 60),
        "vocals": (65.0, 1100.0, 0.45, 36, 90),  # C2..C#6; breathy/unvoiced frames dropped
    }.get(instrument_name, (75.0, 1400.0, 0.35, 40, 88))

    x = torch.from_numpy(y_norm).unsqueeze(0).float().to(device)

    def predict(batch):
        return torchcrepe.predict(
            x,
            sample_rate=16000,
            hop_length=hop_length,
            fmin=fmin,
            fmax=fmax,
            model=model,
            batch_size=batch,
            device=device,
            return_periodicity=True
        )

    try:
        pitch, periodicity = predict(batch_size)
    except torch.cuda.OutOfMemoryError:
        # Smaller batches give identical results, just more slowly
        torch.cuda.empty_cache()
        print(f"[!] GPU memory exhausted; retrying TorchCREPE with batch size {max(64, batch_size // 4)}...")
        pitch, periodicity = predict(max(64, batch_size // 4))
    p = pitch.squeeze(0).cpu().numpy()
    conf = periodicity.squeeze(0).cpu().numpy()

    # Calculate local RMS for note velocity and unvoiced gating
    rms = librosa.feature.rms(y=y_norm, frame_length=1024, hop_length=hop_length)[0]
    min_len = min(len(p), len(conf), len(rms))
    p = p[:min_len]
    conf = conf[:min_len]
    rms = rms[:min_len]

    # Voiced frame mask
    voiced = (conf >= conf_thresh) & (p >= fmin) & (rms >= 0.001)
    midi_p = np.zeros(min_len)
    midi_p[voiced] = librosa.hz_to_midi(p[voiced])

    # Median-smooth each contiguous voiced run on its own, so pitches of separate
    # notes are never blended across a silence
    run_start = None
    for i in range(min_len + 1):
        if i < min_len and voiced[i]:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            if i - run_start >= 5:
                midi_p[run_start:i] = medfilt(midi_p[run_start:i], kernel_size=5)
            run_start = None

    # Re-struck notes: an onset with a real rise in energy starts a new note even at
    # the same pitch (vibrato and bends move the spectrum without adding energy)
    onset_frames = librosa.onset.onset_detect(y=y_norm, sr=16000, hop_length=hop_length, units="frames")
    attacks = {int(f) for f in onset_frames
               if 0 < f < min_len and rms[f] >= 1.2 * rms[max(0, f - 3):f].min()}

    notes = []
    curr_pitch = None
    note_start = 0.0
    accum_rms = []
    accum_pitches = []
    min_dur = 0.06

    def emit_note(s_t, e_t, p_samples, r_samples):
        dur = e_t - s_t
        if dur >= min_dur:
            med_p = int(round(np.median(p_samples)))
            if instrument_name == "bass":
                if med_p > 60:
                    med_p -= 12
                if 28 <= med_p <= 60:
                    vel = int(np.clip(45 + np.mean(r_samples) * 300, 45, 115))
                    notes.append(pretty_midi.Note(
                        velocity=vel, pitch=med_p, start=s_t, end=e_t
                    ))
            else:
                if low_note <= med_p <= high_note:
                    vel = int(np.clip(50 + np.mean(r_samples) * 300, 50, 120))
                    notes.append(pretty_midi.Note(
                        velocity=vel, pitch=med_p, start=s_t, end=e_t
                    ))

    for i in range(min_len):
        t = i * time_step
        if voiced[i]:
            val = midi_p[i]
            if curr_pitch is None:
                curr_pitch = val
                note_start = t
                accum_rms = [rms[i]]
                accum_pitches = [val]
            else:
                re_struck = i in attacks and t - note_start >= min_dur
                if abs(val - curr_pitch) >= 0.8 or re_struck:
                    emit_note(note_start, t, accum_pitches, accum_rms)
                    curr_pitch = val
                    note_start = t
                    accum_rms = [rms[i]]
                    accum_pitches = [val]
                else:
                    accum_rms.append(rms[i])
                    accum_pitches.append(val)
                    curr_pitch = 0.7 * curr_pitch + 0.3 * val
        else:
            if curr_pitch is not None:
                emit_note(note_start, t, accum_pitches, accum_rms)
                curr_pitch = None
                accum_rms = []
                accum_pitches = []

    if curr_pitch is not None:
        emit_note(note_start, min_len * time_step, accum_pitches, accum_rms)

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    prog, inst_name = {"bass": (33, "Electric Bass"), "vocals": (53, "Vocals")}.get(
        instrument_name, (29, "Lead Guitar"))
    inst = pretty_midi.Instrument(program=prog, is_drum=False, name=inst_name)
    inst.notes = notes
    pm.instruments.append(inst)
    pm.write(output_midi_path)
    return output_midi_path


def transcribe_with_basic_pitch(
    audio_path: str,
    output_midi_path: str,
    instrument_name: str = "guitar_lead",
    onset_threshold: float = 0.5,
    frame_threshold: float = 0.3,
    minimum_note_length: float = 58.0,
    include_pitch_bends: bool = True
) -> str:
    """
    Transcribes audio using Spotify basic-pitch.
    """
    try:
        from basic_pitch.inference import predict
        
        min_freq = 30.0 if instrument_name == "bass" else None
        max_freq = 420.0 if instrument_name == "bass" else None

        model_output, midi_data, note_events = predict(
            audio_path,
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=minimum_note_length,
            minimum_frequency=min_freq,
            maximum_frequency=max_freq,
            multiple_pitch_bends=include_pitch_bends
        )
        
        for inst in midi_data.instruments:
            if instrument_name == "bass":
                inst.program = 33
                inst.name = "Electric Bass"
                filtered = []
                for n in inst.notes:
                    if 59 <= n.pitch <= 72:
                        n.pitch -= 12
                    if 28 <= n.pitch <= 60 and (n.end - n.start) >= 0.06 and n.velocity >= 30:
                        filtered.append(n)
                inst.notes = filtered
            elif instrument_name == "vocals":
                inst.program = 53
                inst.name = "Vocals"
                inst.notes = [
                    n for n in inst.notes
                    if 36 <= n.pitch <= 90 and (n.end - n.start) >= 0.06 and n.velocity >= 30
                ]
            elif instrument_name == "guitar_lead":
                inst.program = 29
                inst.name = "Lead Guitar"
                inst.notes = [
                    n for n in inst.notes
                    if 40 <= n.pitch <= 90 and (n.end - n.start) >= 0.045 and n.velocity >= 30
                ]
            else:
                inst.program = 27
                inst.name = "Rhythm Guitar"
                inst.notes = [
                    n for n in inst.notes
                    if 40 <= n.pitch <= 86 and (n.end - n.start) >= 0.05 and n.velocity >= 25
                ]

        midi_data.write(output_midi_path)
        return output_midi_path

    except ImportError:
        return _fallback_transcribe(audio_path, output_midi_path, instrument_name)


def _fallback_transcribe(audio_path: str, output_midi_path: str, instrument_name: str) -> str:
    """Lightweight fallback transcription using librosa YIN pitch estimation."""
    import librosa
    import numpy as np
    
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    fmin = 30.0 if instrument_name == "bass" else 75.0
    fmax = 400.0 if instrument_name == "bass" else 1200.0
    
    f0 = librosa.yin(y, fmin=fmin, fmax=fmax, sr=sr, frame_length=2048, hop_length=256)
    f0 = np.nan_to_num(f0, nan=0.0)
    times = librosa.times_like(f0, sr=sr, hop_length=256)
    
    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    prog = 33 if instrument_name == "bass" else 29
    inst = pretty_midi.Instrument(program=prog, is_drum=False, name=instrument_name)
    
    current_pitch = None
    note_start = 0.0
    
    for t, freq in zip(times, f0):
        if freq > 0:
            midi_pitch = int(round(librosa.hz_to_midi(freq)))
            if current_pitch is None:
                current_pitch = midi_pitch
                note_start = t
            elif abs(midi_pitch - current_pitch) >= 1:
                if t - note_start > 0.06:
                    inst.notes.append(pretty_midi.Note(velocity=90, pitch=current_pitch, start=note_start, end=t))
                current_pitch = midi_pitch
                note_start = t
        else:
            if current_pitch is not None:
                if t - note_start > 0.06:
                    inst.notes.append(pretty_midi.Note(velocity=90, pitch=current_pitch, start=note_start, end=t))
                current_pitch = None
                
    pm.instruments.append(inst)
    pm.write(output_midi_path)
    return output_midi_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        inst_type = sys.argv[3] if len(sys.argv) > 3 else "guitar_lead"
        res = transcribe_pitch(sys.argv[1], sys.argv[2], inst_type)
        print("Transcribed pitch saved to:", res)
