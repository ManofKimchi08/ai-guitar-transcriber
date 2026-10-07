"""
Guitar Chord Recognizer Module
Analyzes audio using Constant-Q Transform (CQT) Chroma analysis,
matches against 48 major / minor / dominant-7th / power-chord profiles,
and maps detected chords into authentic 6-string guitar chord fingerings.
"""

import os
import numpy as np
import librosa
import pretty_midi


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
    "C#7": [(5, 4), (4, 3), (3, 4), (2, 2)],
    "C#5": [(5, 4), (4, 6)],

    "D":  [(4, 0), (3, 2), (2, 3), (1, 2)],
    "Dm": [(4, 0), (3, 2), (2, 3), (1, 1)],
    "D7": [(4, 0), (3, 2), (2, 1), (1, 2)],
    "D5": [(4, 0), (3, 2)],

    "D#":  [(5, 6), (4, 5), (3, 3), (2, 4)],
    "D#m": [(5, 6), (4, 8), (3, 8), (2, 7), (1, 6)],
    "D#7": [(5, 6), (4, 8), (3, 6), (2, 8), (1, 6)],
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
    "F#7": [(6, 2), (5, 4), (4, 2), (3, 3), (2, 2), (1, 2)],
    "F#5": [(6, 2), (5, 4)],

    "G":  [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)],
    "Gm": [(6, 3), (5, 5), (4, 5), (3, 3), (2, 3), (1, 3)],
    "G7": [(6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 1)],
    "G5": [(6, 3), (5, 5)],

    "G#":  [(6, 4), (5, 6), (4, 6), (3, 5), (2, 4), (1, 4)],
    "G#m": [(6, 4), (5, 6), (4, 6), (3, 4), (2, 4), (1, 4)],
    "G#7": [(6, 4), (5, 6), (4, 4), (3, 5), (2, 4), (1, 4)],
    "G#5": [(6, 4), (5, 6)],

    "A":  [(5, 0), (4, 2), (3, 2), (2, 2), (1, 0)],
    "Am": [(5, 0), (4, 2), (3, 2), (2, 1), (1, 0)],
    "A7": [(5, 0), (4, 2), (3, 0), (2, 2), (1, 0)],
    "A5": [(5, 0), (4, 2)],

    "A#":  [(5, 1), (4, 3), (3, 3), (2, 3), (1, 1)],
    "A#m": [(5, 1), (4, 3), (3, 3), (2, 2), (1, 1)],
    "A#7": [(5, 1), (4, 3), (3, 1), (2, 3), (1, 1)],
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


# A frame whose best template similarity stays below this is "no chord" (noise, single
# notes, bleed); a flat 12-note chroma already scores ~0.58 against a 7th chord.
NO_CHORD_SCORE = 0.62
# Frames this far below the track's loud level (95th percentile RMS) count as silence
SILENCE_RATIO = 0.1
# String harmonics reinforce root and fifth, so a triad's third can look weak; a power
# chord must beat the triads by this margin (tuned on synthetic strums: 88% -> 96%)
POWER_CHORD_PENALTY = 0.05


def recognize_chords(audio_path: str, segment_sec: float = 0.5, strums: bool = True):
    """
    Recognizes the chord progression and, within it, the strums.

    1. Chroma (CQT) of the harmonic part, per channel and added up (two guitar takes
       panned left and right, slightly out of tune with each other, would cancel
       each other's notes now and then if mixed down first); frames far below the
       track's loud level are silence.
    2. Every frame is scored against the 48 chord templates and a "no chord" state.
    3. A Viterbi pass (HMM with sticky self-transitions) smooths the labels so a
       chord does not flicker on passing tones; chords shorter than `segment_sec`
       merge into a neighbour.
    4. Chord changes snap to the nearest strum (onset), and with `strums` every
       strum inside a chord re-attacks it, so the TAB shows the strumming rhythm.

    Returns [{'start': t0, 'end': t1, 'chord': 'Am', 'voicing': [(s, f), ...]}].
    """
    channels, sr = librosa.load(audio_path, sr=22050, mono=False)
    channels = np.atleast_2d(channels)
    y = channels.mean(axis=0)
    hop = 512
    if len(y) < hop * 4:
        return []

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    loud = float(np.percentile(rms, 95))
    if loud < 1e-4:
        return []

    chroma = sum(librosa.feature.chroma_cqt(y=librosa.effects.harmonic(np.ascontiguousarray(ch)), sr=sr,
                                            hop_length=hop, norm=None)
                 for ch in channels)
    n = min(chroma.shape[1], len(rms))
    chroma, rms = chroma[:, :n], rms[:n]
    silent = rms < loud * SILENCE_RATIO

    names = list(CHORD_PROFILES)
    templates = np.stack([CHORD_PROFILES[c] / np.linalg.norm(CHORD_PROFILES[c]) for c in names])
    unit = chroma / (np.linalg.norm(chroma, axis=0, keepdims=True) + 1e-9)
    is_power = np.array([c.endswith("5") for c in names], dtype=float)[:, None]
    scores = np.vstack([templates @ unit - POWER_CHORD_PENALTY * is_power, np.full((1, n), NO_CHORD_SCORE)])
    scores[:-1, silent] = 0.0
    scores[-1, silent] = 1.0

    probs = np.exp(25.0 * (scores - scores.max(axis=0, keepdims=True)))
    probs /= probs.sum(axis=0, keepdims=True)
    path = librosa.sequence.viterbi(probs, librosa.sequence.transition_loop(len(names) + 1, 0.97))

    # Runs of equal states -> [start_frame, end_frame, state]. A too-short chord right
    # after another chord is flicker and joins it; "no chord" runs (silence) stay as they
    # are, so a chord never spreads into the silence around it.
    no_chord = len(names)
    bounds = np.flatnonzero(np.diff(path)) + 1
    runs = [[a, b, int(path[a])] for a, b in zip(np.r_[0, bounds], np.r_[bounds, n])]
    min_frames = segment_sec * sr / hop
    merged = []
    for run in runs:
        if merged and run[2] == merged[-1][2]:
            merged[-1][1] = run[1]
        elif (merged and run[1] - run[0] < min_frames
              and run[2] != no_chord and merged[-1][2] != no_chord):
            merged[-1][1] = run[1]
        else:
            merged.append(run)

    times = librosa.frames_to_time(np.arange(n + 1), sr=sr, hop_length=hop)
    # Strums: onsets with a real rise in energy (the onset envelope is normalized, so a
    # sustained chord would otherwise show spurious "attacks" on small fluctuations)
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, hop_length=hop, units="frames", wait=4)
    onsets = [float(times[f]) for f in onset_frames
              if 0 < f < n and rms[f] >= 1.3 * rms[max(0, f - 4):f].min()]

    results = []
    for a, b, state in merged:
        if state == no_chord:
            continue
        start, end = float(times[a]), float(times[b])
        near = [t for t in onsets if abs(t - start) <= 0.15]
        if near:
            start = min(near, key=lambda t: abs(t - start))
        attacks = [start]
        if strums:
            attacks += [float(t) for t in onsets if start + 0.08 < t < end - 0.08]
        chord = names[state]
        for s, e in zip(attacks, attacks[1:] + [end]):
            results.append({"start": s, "end": e, "chord": chord, "voicing": STANDARD_VOICINGS[chord]})
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
