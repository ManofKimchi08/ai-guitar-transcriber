"""
Guitar Chord Recognizer Module
Analyzes audio using Constant-Q Transform (CQT) Chroma analysis,
matches against 24 Major/Minor and Power Chord profiles,
and maps detected chords into authentic 6-string guitar chord fingerings.
"""

import os
import numpy as np
import librosa
import pretty_midi
import soundfile as sf


# 12 chromatic pitch classes
PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Standard 12-bin binary chord templates
MAJOR_TEMPLATE = np.array([1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0], dtype=float)  # 1, 3, 5
MINOR_TEMPLATE = np.array([1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0], dtype=float)  # 1, b3, 5
DOM7_TEMPLATE  = np.array([1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0], dtype=float)  # 1, 3, 5, b7
POWER_TEMPLATE = np.array([1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0], dtype=float)  # 1, 5

# Build full dictionary of 12 keys
CHORD_PROFILES = {}
for root_idx, root_name in enumerate(PITCH_CLASSES):
    CHORD_PROFILES[f"{root_name}"]     = np.roll(MAJOR_TEMPLATE, root_idx)
    CHORD_PROFILES[f"{root_name}m"]    = np.roll(MINOR_TEMPLATE, root_idx)
    CHORD_PROFILES[f"{root_name}7"]    = np.roll(DOM7_TEMPLATE, root_idx)
    CHORD_PROFILES[f"{root_name}5"]    = np.roll(POWER_TEMPLATE, root_idx)


# Authentic 6-string Guitar Voicings: (string_num, fret_num)
# string_num 6 is low E, 1 is high E. None = muted/unplayed.
STANDARD_VOICINGS = {
    # Open chords
    "C":  [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0)],
    "Cm": [(5, 3), (4, 5), (3, 5), (2, 4), (1, 3)],
    "C7": [(5, 3), (4, 2), (3, 3), (2, 1)],
    "C5": [(5, 3), (4, 5)],

    "C#":  [(5, 4), (4, 3), (3, 1), (2, 2)],
    "C#m": [(5, 4), (4, 6), (3, 6), (2, 5), (1, 4)],
    "C#5": [(5, 4), (4, 6)],

    "D":  [(4, 0), (3, 2), (2, 3), (1, 2)],
    "Dm": [(4, 0), (3, 2), (2, 3), (1, 1)],
    "D7": [(4, 0), (3, 2), (2, 1), (1, 2)],
    "D5": [(4, 0), (3, 2)],

    "D#":  [(5, 6), (4, 5), (3, 3), (2, 4)],
    "D#m": [(5, 6), (4, 8), (3, 8), (2, 7), (1, 6)],
    "D#5": [(5, 6), (4, 8)],

    "E":  [(6, 0), (5, 2), (4, 2), (3, 1), (2, 0), (1, 0)],
    "Em": [(6, 0), (5, 2), (4, 2), (3, 0), (2, 0), (1, 0)],
    "E7": [(6, 0), (5, 2), (4, 0), (3, 1), (2, 0), (1, 0)],
    "E5": [(6, 0), (5, 2)],

    "F":  [(6, 1), (5, 3), (4, 3), (3, 2), (2, 1), (1, 1)],
    "Fm": [(6, 1), (5, 3), (4, 3), (3, 1), (2, 1), (1, 1)],
    "F7": [(6, 1), (5, 3), (4, 1), (3, 2), (2, 1), (1, 1)],
    "F5": [(6, 1), (5, 3)],

    "F#":  [(6, 2), (5, 4), (4, 4), (3, 3), (2, 2), (1, 2)],
    "F#m": [(6, 2), (5, 4), (4, 4), (3, 2), (2, 2), (1, 2)],
    "F#5": [(6, 2), (5, 4)],

    "G":  [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)],
    "Gm": [(6, 3), (5, 5), (4, 5), (3, 3), (2, 3), (1, 3)],
    "G7": [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 1)],
    "G5": [(6, 3), (5, 5)],

    "G#":  [(6, 4), (5, 6), (4, 6), (3, 5), (2, 4), (1, 4)],
    "G#m": [(6, 4), (5, 6), (4, 6), (3, 4), (2, 4), (1, 4)],
    "G#5": [(6, 4), (5, 6)],

    "A":  [(5, 0), (4, 2), (3, 2), (2, 2), (1, 0)],
    "Am": [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0)],
    "A7": [(5, 0), (4, 2), (3, 0), (2, 2), (1, 0)],
    "A5": [(5, 0), (4, 2)],

    "A#":  [(5, 1), (4, 3), (3, 3), (2, 3), (1, 1)],
    "A#m": [(5, 1), (4, 3), (3, 3), (2, 2), (1, 1)],
    "A#5": [(5, 1), (4, 3)],

    "B":  [(5, 2), (4, 4), (3, 4), (2, 4), (1, 2)],
    "Bm": [(5, 2), (4, 4), (3, 4), (2, 3), (1, 2)],
    "B7": [(5, 2), (4, 1), (3, 2), (2, 0), (1, 2)],
    "B5": [(5, 2), (4, 4)],
}

# Standard Guitar Tuning base pitches
GUITAR_BASE_PITCHES = {
    1: 64,  # E4
    2: 59,  # B3
    3: 55,  # G3
    4: 50,  # D3
    5: 45,  # A2
    6: 40,  # E2
}


def recognize_chords(audio_path: str, segment_sec: float = 0.5):
    """
    Analyzes audio segments and recognizes the primary guitar chord progression.
    Returns list of: [{'start': t0, 'end': t1, 'chord': 'Am', 'voicing': [(s, f), ...]}]
    """
    y, sr = sf.read(audio_path)
    if y.ndim > 1:
        y = np.mean(y, axis=1)

    duration = len(y) / sr
    hop_length = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    frames_per_sec = sr / hop_length
    seg_frames = max(1, int(segment_sec * frames_per_sec))

    results = []
    num_segs = int(np.ceil(chroma.shape[1] / seg_frames))

    last_chord = None
    chord_start = 0.0

    for i in range(num_segs):
        start_f = i * seg_frames
        end_f = min((i + 1) * seg_frames, chroma.shape[1])
        if start_f >= end_f:
            break

        seg_chroma = np.mean(chroma[:, start_f:end_f], axis=1)
        norm = np.linalg.norm(seg_chroma)
        if norm < 1e-4:
            curr_chord = None
        else:
            seg_chroma = seg_chroma / norm

            best_sim = -1.0
            best_chord = "C"

            for chord_name, template in CHORD_PROFILES.items():
                sim = np.dot(seg_chroma, template) / (np.linalg.norm(template) + 1e-9)
                if sim > best_sim:
                    best_sim = sim
                    best_chord = chord_name

            curr_chord = best_chord if best_sim > 0.45 else None

        t_now = start_f / frames_per_sec
        if curr_chord != last_chord:
            if last_chord is not None:
                results.append({
                    "start": chord_start,
                    "end": t_now,
                    "chord": last_chord,
                    "voicing": STANDARD_VOICINGS.get(last_chord, STANDARD_VOICINGS["C"])
                })
            last_chord = curr_chord
            chord_start = t_now

    if last_chord is not None:
        results.append({
            "start": chord_start,
            "end": duration,
            "chord": last_chord,
            "voicing": STANDARD_VOICINGS.get(last_chord, STANDARD_VOICINGS["C"])
        })

    return results


def transcribe_rhythm_guitar_chords(audio_path: str, output_midi_path: str, segment_sec: float = 0.5) -> str:
    """
    Transcribes rhythm guitar audio into authentic chord voicings saved as MIDI.
    """
    os.makedirs(os.path.dirname(output_midi_path) or '.', exist_ok=True)
    chords = recognize_chords(audio_path, segment_sec=segment_sec)

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    inst = pretty_midi.Instrument(program=27, is_drum=False, name="Rhythm Guitar")

    for ch in chords:
        voicing = ch["voicing"]
        start_t = ch["start"]
        end_t = ch["end"]

        for string_num, fret_num in voicing:
            base = GUITAR_BASE_PITCHES.get(string_num, 40)
            pitch = base + fret_num
            note = pretty_midi.Note(
                velocity=85,
                pitch=pitch,
                start=start_t,
                end=end_t
            )
            inst.notes.append(note)

    pm.instruments.append(inst)
    pm.write(output_midi_path)
    return output_midi_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        out = transcribe_rhythm_guitar_chords(sys.argv[1], sys.argv[2])
        print("Chords written to:", out)
