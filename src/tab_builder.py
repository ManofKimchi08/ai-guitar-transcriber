"""
Tab Builder & MusicXML Generator Module
Converts MIDI tracks (Drums, Bass, Lead Guitar, Rhythm Guitar) into:
1. Dynamic Programming (Viterbi) optimized Guitar and Bass Tablature (fret and string positions).
2. A MusicXML score: guitar/bass staves carry their string tuning and per-note string/fret,
   drums use standard drum-set notation, plus tempo and an estimated key signature.
3. Compatible with MuseScore 4, TuxGuitar, and Guitar Pro.
"""

import os
import numpy as np
import pretty_midi
import xml.etree.ElementTree as ET


# Tuning definitions (String number 1 is highest pitch, highest number is lowest pitch)
GUITAR_TUNING = {
    1: 64,  # E4
    2: 59,  # B3
    3: 55,  # G3
    4: 50,  # D3
    5: 45,  # A2
    6: 40,  # E2
}

BASS_TUNING = {
    1: 43,  # G2
    2: 38,  # D2
    3: 33,  # A1
    4: 28,  # E1
}

# Drum-set notation on a percussion staff: GM drum note -> (display step, octave, notehead, name)
DRUM_NOTATION = {
    35: ("F", 4, None, "Acoustic Bass Drum"),
    36: ("F", 4, None, "Bass Drum"),
    38: ("C", 5, None, "Snare Drum"),
    40: ("C", 5, None, "Electric Snare"),
    41: ("G", 4, None, "Low Floor Tom"),
    43: ("A", 4, None, "High Floor Tom"),
    45: ("B", 4, None, "Low Tom"),
    47: ("D", 5, None, "Low-Mid Tom"),
    48: ("E", 5, None, "Hi-Mid Tom"),
    50: ("F", 5, None, "High Tom"),
    42: ("G", 5, "x", "Closed Hi-Hat"),
    44: ("D", 4, "x", "Pedal Hi-Hat"),
    46: ("G", 5, "circle-x", "Open Hi-Hat"),
    49: ("A", 5, "x", "Crash Cymbal"),
    51: ("F", 5, "x", "Ride Cymbal"),
    57: ("A", 5, "x", "Crash Cymbal 2"),
}

# Playback sound per pitched part type: (instrument name, 1-based General MIDI program)
PART_SOUNDS = {
    "bass": ("Electric Bass", 34),
    "rhythm": ("Electric Guitar", 28),
    "lead": ("Overdriven Guitar", 30),
    "vocals": ("Voice", 54),
}

# Pitch spelling (step, alter) per pitch class for sharp and flat keys
SHARP_SPELLING = [("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
                  ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0)]
FLAT_SPELLING = [("C", 0), ("D", -1), ("D", 0), ("E", -1), ("E", 0), ("F", 0),
                 ("G", -1), ("G", 0), ("A", -1), ("A", 0), ("B", -1), ("B", 0)]

# Krumhansl-Kessler key profiles and the key signature of each major key by tonic
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MAJOR_KEY_FIFTHS = [0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5]  # C, Db, D, Eb, E, F, F#, G, Ab, A, Bb, B


def spell_pitch(midi_pitch: int, fifths: int = 0) -> tuple:
    """Returns (step, alter, octave) for a MIDI pitch, using flats in flat keys."""
    step, alter = (FLAT_SPELLING if fifths < 0 else SHARP_SPELLING)[midi_pitch % 12]
    return step, alter, midi_pitch // 12 - 1


def estimate_key(notes: list) -> tuple:
    """Estimates (fifths, mode) from duration-weighted pitch classes (Krumhansl-Schmuckler)."""
    weights = np.zeros(12)
    for n in notes:
        weights[n.pitch % 12] += n.end - n.start
    if weights.sum() <= 0 or np.allclose(weights, weights[0]):
        return 0, "major"

    best_r, best_tonic, best_mode = -np.inf, 0, "major"
    for tonic in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(weights, np.roll(profile, tonic))[0, 1]
            if r > best_r:
                best_r, best_tonic, best_mode = r, tonic, mode
    relative_major = best_tonic if best_mode == "major" else (best_tonic + 3) % 12
    return MAJOR_KEY_FIFTHS[relative_major], best_mode

# MusicXML standard durations (at divisions=4 per quarter beat, 16 per 4/4 bar)
DURATION_TO_TYPE = {
    16: ("whole", False),
    12: ("half", True),
    8: ("half", False),
    6: ("quarter", True),
    4: ("quarter", False),
    3: ("eighth", True),
    2: ("eighth", False),
    1: ("16th", False),
}


def decompose_duration(dur: int) -> list:
    """Decomposes a duration in divisions into standard musical values to avoid non-standard rests."""
    chunks = []
    standards = [16, 12, 8, 6, 4, 3, 2, 1]
    remaining = int(dur)
    while remaining > 0:
        for s in standards:
            if s <= remaining:
                chunks.append(s)
                remaining -= s
                break
        else:
            chunks.append(1)
            remaining -= 1
    return chunks


def add_type_and_dot(note_el: ET.Element, dur: int):
    """Adds standard <type> and optional <dot/> tags to a note or rest element."""
    if dur in DURATION_TO_TYPE:
        t, dot = DURATION_TO_TYPE[dur]
        ET.SubElement(note_el, "type").text = t
        if dot:
            ET.SubElement(note_el, "dot")
    else:
        for std in [16, 8, 4, 2, 1]:
            if dur >= std:
                t, _ = DURATION_TO_TYPE[std]
                ET.SubElement(note_el, "type").text = t
                break
        else:
            ET.SubElement(note_el, "type").text = "16th"


def get_fret_candidates(pitch: int, tuning: dict, max_fret: int = 22):
    """Finds all valid (string, fret) pairs for a given MIDI pitch."""
    candidates = []
    for string_num, base_pitch in tuning.items():
        fret = pitch - base_pitch
        if 0 <= fret <= max_fret:
            candidates.append((string_num, fret))
    return candidates


def _chord_shape_cost(shape: list, string_order: list) -> float:
    """
    Playability cost of a chord shape [(string, fret), ...]: prefers a small fret
    span (stretches past 4 frets are heavily penalized), a low neck position, and
    no muted strings in the middle of the strum.
    """
    fretted = [f for _, f in shape if f > 0]
    span = (max(fretted) - min(fretted)) if fretted else 0
    position = min(fretted) if fretted else 0
    idx = sorted(string_order.index(s) for s, _ in shape)
    inner_gaps = (idx[-1] - idx[0] + 1) - len(idx)
    return span + max(0, span - 3) * 4.0 + position * 0.3 + inner_gaps * 2.5


def assign_chord_strings(chord_notes: list, tuning: dict, max_fret: int = 16) -> list:
    """
    Assigns each simultaneous note in a guitar/bass chord to a unique physical string.
    Ascending pitches go on ascending strings, and among those shapes the most
    playable one wins (see _chord_shape_cost), so chord voicings that lost their
    string info on the way through MIDI come back as playable shapes.
    Guarantees 0 string collisions per chord.
    """
    if not chord_notes:
        return []

    sorted_notes = sorted(chord_notes, key=lambda x: x.pitch)
    string_order = sorted(list(tuning.keys()), reverse=True)  # [6, 5, 4, 3, 2, 1]

    best_shape, best_cost = None, float('inf')

    def search(i, first_string_idx, shape):
        nonlocal best_shape, best_cost
        if i == len(sorted_notes):
            cost = _chord_shape_cost(shape, string_order)
            if cost < best_cost:
                best_shape, best_cost = list(shape), cost
            return
        for k in range(first_string_idx, len(string_order)):
            s = string_order[k]
            f = sorted_notes[i].pitch - tuning[s]
            if 0 <= f <= max_fret:
                shape.append((s, f))
                search(i + 1, k + 1, shape)
                shape.pop()

    search(0, 0, [])
    if best_shape is not None:
        return [(n, s, f) for n, (s, f) in zip(sorted_notes, best_shape)]

    # No shape fits every note (more notes than strings, or out of range):
    # place what fits greedily, lowest fret first.
    res = []
    used_strings = set()
    for n in sorted_notes:
        cands = []
        for s in string_order:
            if s in used_strings:
                continue
            f = n.pitch - tuning[s]
            if 0 <= f <= max_fret:
                cands.append((s, f))
        if cands:
            best = min(cands, key=lambda x: (x[1], -x[0]))
            used_strings.add(best[0])
            res.append((n, best[0], best[1]))
    return res


def optimize_tablature(notes: list, tuning: dict, max_fret: int = 22) -> list:
    """
    Finds the optimal sequence of (string, fret) for a list of notes.
    For chords (simultaneous notes): assigns distinct strings with 0 collisions.
    For monophonic lines: uses Dynamic Programming to minimize hand shifts.
    """
    if not notes:
        return []

    # Group notes by start time into clusters (simultaneous within 35ms)
    sorted_notes = sorted(notes, key=lambda x: (x.start, x.pitch))
    clusters = []
    curr_cluster = []
    for n in sorted_notes:
        if not curr_cluster:
            curr_cluster.append(n)
        elif abs(n.start - curr_cluster[0].start) <= 0.035:
            curr_cluster.append(n)
        else:
            clusters.append(curr_cluster)
            curr_cluster = [n]
    if curr_cluster:
        clusters.append(curr_cluster)

    has_polyphony = any(len(c) > 1 for c in clusters)
    
    if has_polyphony:
        # Polyphonic track (e.g. Rhythm Guitar chords)
        all_res = []
        for c in clusters:
            if len(c) > 1:
                all_res.extend(assign_chord_strings(c, tuning, max_fret=min(max_fret, 16)))
            else:
                # Single note in chord track
                cands = get_fret_candidates(c[0].pitch, tuning, max_fret=min(max_fret, 16))
                if cands:
                    best = min(cands, key=lambda x: (x[1], -x[0]))
                    all_res.append((c[0], best[0], best[1]))
        return all_res

    # Monophonic track (e.g. Bass or Lead Guitar solo): use DP Viterbi
    step_candidates = []
    valid_notes = []
    for note in sorted_notes:
        cands = get_fret_candidates(note.pitch, tuning, max_fret)
        if cands:
            step_candidates.append(cands)
            valid_notes.append(note)
            
    if not valid_notes:
        return []

    n_steps = len(valid_notes)
    dp = []
    
    first_step = []
    for s, f in step_candidates[0]:
        init_cost = 0.0 if f == 0 else abs(f - 5) * 0.4
        first_step.append((init_cost, -1))
    dp.append(first_step)
    
    for i in range(1, n_steps):
        current_step = []
        curr_cands = step_candidates[i]
        prev_cands = step_candidates[i - 1]
        
        for c_idx, (curr_s, curr_f) in enumerate(curr_cands):
            best_cost = float('inf')
            best_prev = -1
            
            for p_idx, (prev_s, prev_f) in enumerate(prev_cands):
                prev_cost = dp[i - 1][p_idx][0]
                
                if curr_f == 0 or prev_f == 0:
                    fret_dist = 0.5
                else:
                    diff = abs(curr_f - prev_f)
                    if diff <= 4:
                        fret_dist = diff * 1.0
                    else:
                        fret_dist = 4.0 + (diff - 4) * 3.5
                        
                string_dist = abs(curr_s - prev_s) * 0.4
                fret_penalty = max(0, curr_f - 14) * 0.3
                
                transition_cost = fret_dist + string_dist + fret_penalty
                total = prev_cost + transition_cost
                
                if total < best_cost:
                    best_cost = total
                    best_prev = p_idx
                    
            current_step.append((best_cost, best_prev))
        dp.append(current_step)
        
    best_last_idx = min(range(len(step_candidates[-1])), key=lambda idx: dp[-1][idx][0])
    
    optimal_path = []
    curr_idx = best_last_idx
    for i in range(n_steps - 1, -1, -1):
        s, f = step_candidates[i][curr_idx]
        optimal_path.append((valid_notes[i], s, f))
        curr_idx = dp[i][curr_idx][1]
        
    optimal_path.reverse()
    return optimal_path


def quantize_onsets(items: list, seconds_per_div: float, merge_same_slot: bool = False,
                    onset_tol: float = 0.035, origin: float = 0.0) -> list:
    """
    Snaps (note, string, fret) items onto a global grid of `seconds_per_div` slots
    starting at time `origin` (slot 0, the first beat of bar 1).
    Returns [(slot, [items...], dur_in_divs), ...] with strictly increasing slots.

    Notes struck together (within onset_tol) share a slot as one chord. For pitched
    parts, a separately struck note that rounds into an occupied slot moves to the
    next slot if that is at most one division late, and is dropped otherwise:
    stacking it would turn a fast run into an unplayable same-string chord.
    With merge_same_slot (drums), different instruments may share a slot instead.
    """
    groups = []
    for it in sorted(items, key=lambda it: (it[0].start, it[0].pitch)):
        if groups and it[0].start - groups[-1][0][0].start <= onset_tol:
            groups[-1].append(it)
        else:
            groups.append([it])

    events = []
    for group in groups:
        if not merge_same_slot:
            # A chord can hold only one note per string; keep the first on each
            kept, used_strings = [], set()
            for it in group:
                if it[1] not in used_strings:
                    used_strings.add(it[1])
                    kept.append(it)
            group = kept
        pos = (group[0][0].start - origin) / seconds_per_div
        slot = max(0, int(round(pos)))
        dur = max(1, int(round(max(n.end - n.start for n, _, _ in group) / seconds_per_div)))
        if events and slot <= events[-1][0]:
            last_slot, last_items, last_dur = events[-1]
            if merge_same_slot:
                seen = {n.pitch for n, _, _ in last_items}
                last_items.extend(it for it in group if it[0].pitch not in seen)
                events[-1] = (last_slot, last_items, max(last_dur, dur))
                continue
            slot = last_slot + 1
            if slot - pos > 1.0:
                continue  # too dense for a 16th-note grid
        events.append((slot, group, dur))
    return events


def split_into_bars(events: list, bar_divisions: int = 16, tie: bool = True) -> tuple:
    """
    Lays quantized events out bar by bar. Each event lasts until its own end or the
    next event, whichever comes first. A note that crosses a bar line, or lasts a
    value with no single note symbol (e.g. 5 sixteenths), becomes tied standard
    values; without `tie` (drum hits) only the first value is kept.

    Returns ({bar_number: [(slot_in_bar, items, dur, tie_stop, tie_start), ...]},
    end_slot) where end_slot is where the last sounding value ends.
    """
    by_bar = {}
    end_slot = 0
    for i, (slot, items, dur) in enumerate(events):
        end = slot + dur
        if i + 1 < len(events):
            end = min(end, events[i + 1][0])

        pieces = []
        pos = slot
        while pos < end:
            bar_end = (pos // bar_divisions + 1) * bar_divisions
            for d in decompose_duration(min(end, bar_end) - pos):
                pieces.append((pos, d))
                pos += d
        if not tie:
            pieces = pieces[:1]

        for k, (p, d) in enumerate(pieces):
            by_bar.setdefault(p // bar_divisions + 1, []).append(
                (p % bar_divisions, items, d, k > 0, k < len(pieces) - 1))
        end_slot = max(end_slot, pieces[-1][0] + pieces[-1][1])
    return by_bar, end_slot


def _append_rests(measure_el: ET.Element, dur: int):
    """Appends rests filling `dur` divisions, split into standard note values."""
    for chunk in decompose_duration(dur):
        r_note = ET.SubElement(measure_el, "note")
        ET.SubElement(r_note, "rest")
        ET.SubElement(r_note, "duration").text = str(chunk)
        ET.SubElement(r_note, "voice").text = "1"
        add_type_and_dot(r_note, chunk)


def _append_note(measure_el: ET.Element, note, string_num, fret_num, dur: int, chord: bool,
                 fifths: int = 0, drum_part_id: str = None,
                 tie_stop: bool = False, tie_start: bool = False, lyric: tuple = None):
    """
    Appends one pitched (or, for drum parts, unpitched) note with optional string/fret,
    ties and a (text, syllabic) lyric.
    """
    n_el = ET.SubElement(measure_el, "note")
    if chord:
        ET.SubElement(n_el, "chord")

    notehead = None
    if drum_part_id:
        step, octave, notehead, _ = DRUM_NOTATION.get(note.pitch, ("C", 5, None, ""))
        unpitched = ET.SubElement(n_el, "unpitched")
        ET.SubElement(unpitched, "display-step").text = step
        ET.SubElement(unpitched, "display-octave").text = str(octave)
    else:
        step, alter, octave = spell_pitch(note.pitch, fifths)
        pitch_el = ET.SubElement(n_el, "pitch")
        ET.SubElement(pitch_el, "step").text = step
        if alter:
            ET.SubElement(pitch_el, "alter").text = str(alter)
        ET.SubElement(pitch_el, "octave").text = str(octave)

    ET.SubElement(n_el, "duration").text = str(dur)
    if tie_stop:
        ET.SubElement(n_el, "tie", type="stop")
    if tie_start:
        ET.SubElement(n_el, "tie", type="start")
    if drum_part_id:
        ET.SubElement(n_el, "instrument", id=f"{drum_part_id}-I{note.pitch}")
    ET.SubElement(n_el, "voice").text = "1"
    add_type_and_dot(n_el, dur)
    if notehead:
        ET.SubElement(n_el, "notehead").text = notehead

    has_tab = string_num is not None and fret_num is not None
    if tie_stop or tie_start or has_tab:
        notations = ET.SubElement(n_el, "notations")
        if tie_stop:
            ET.SubElement(notations, "tied", type="stop")
        if tie_start:
            ET.SubElement(notations, "tied", type="start")
    # Tablature notation for string & fret
    if has_tab:
        tech = ET.SubElement(notations, "technical")
        ET.SubElement(tech, "string").text = str(string_num)
        ET.SubElement(tech, "fret").text = str(fret_num)

    if lyric:
        text, syllabic = lyric
        lyric_el = ET.SubElement(n_el, "lyric", number="1")
        ET.SubElement(lyric_el, "syllabic").text = syllabic
        ET.SubElement(lyric_el, "text").text = text


def _add_score_part(part_list: ET.Element, part_key: str, pid: str, pname: str, pabbr: str,
                    ptype: str, data: list, channel: int):
    """Declares a part with its playback instrument(s); drum parts declare one per kit piece."""
    score_part = ET.SubElement(part_list, "score-part", id=pid)
    ET.SubElement(score_part, "part-name").text = pname
    ET.SubElement(score_part, "part-abbreviation").text = pabbr

    if ptype == "drum":
        pieces = sorted({n.pitch for n in data})
        for pitch in pieces:
            inst = ET.SubElement(score_part, "score-instrument", id=f"{pid}-I{pitch}")
            ET.SubElement(inst, "instrument-name").text = DRUM_NOTATION.get(pitch, (None, None, None, f"Percussion {pitch}"))[3]
        for pitch in pieces:
            midi = ET.SubElement(score_part, "midi-instrument", id=f"{pid}-I{pitch}")
            ET.SubElement(midi, "midi-channel").text = "10"
            ET.SubElement(midi, "midi-unpitched").text = str(pitch + 1)
    else:
        sound_name, program = PART_SOUNDS[part_key]
        inst = ET.SubElement(score_part, "score-instrument", id=f"{pid}-I1")
        ET.SubElement(inst, "instrument-name").text = sound_name
        midi = ET.SubElement(score_part, "midi-instrument", id=f"{pid}-I1")
        ET.SubElement(midi, "midi-channel").text = str(channel)
        ET.SubElement(midi, "midi-program").text = str(program)


def _add_first_measure_attributes(measure_el: ET.Element, ptype: str, divisions: int,
                                  fifths: int, mode: str, octave_down: bool = True):
    attrib = ET.SubElement(measure_el, "attributes")
    ET.SubElement(attrib, "divisions").text = str(divisions)

    key_el = ET.SubElement(attrib, "key")
    ET.SubElement(key_el, "fifths").text = "0" if ptype == "drum" else str(fifths)
    if ptype != "drum":
        ET.SubElement(key_el, "mode").text = mode

    time_el = ET.SubElement(attrib, "time")
    ET.SubElement(time_el, "beats").text = "4"
    ET.SubElement(time_el, "beat-type").text = "4"

    clef = ET.SubElement(attrib, "clef")
    if ptype == "drum":
        ET.SubElement(clef, "sign").text = "percussion"
        ET.SubElement(clef, "line").text = "2"
        return

    # Guitar, bass (and low voices) sound an octave below written pitch: treble 8vb / bass 8vb
    ET.SubElement(clef, "sign").text = "F" if ptype == "bass" else "G"
    ET.SubElement(clef, "line").text = "4" if ptype == "bass" else "2"
    if octave_down:
        ET.SubElement(clef, "clef-octave-change").text = "-1"

    # String tuning, so notation programs read each note's string/fret correctly
    # (line 1 is the lowest string; a vocal melody carries guitar TAB)
    tuning = BASS_TUNING if ptype == "bass" else GUITAR_TUNING
    details = ET.SubElement(attrib, "staff-details")
    for line_no in range(1, len(tuning) + 1):
        step, alter, octave = spell_pitch(tuning[len(tuning) - line_no + 1])
        staff_tuning = ET.SubElement(details, "staff-tuning", line=str(line_no))
        ET.SubElement(staff_tuning, "tuning-step").text = step
        if alter:
            ET.SubElement(staff_tuning, "tuning-alter").text = str(alter)
        ET.SubElement(staff_tuning, "tuning-octave").text = str(octave)


def _add_tempo(measure_el: ET.Element, bpm: float):
    direction = ET.SubElement(measure_el, "direction", placement="above")
    direction_type = ET.SubElement(direction, "direction-type")
    metronome = ET.SubElement(direction_type, "metronome")
    ET.SubElement(metronome, "beat-unit").text = "quarter"
    ET.SubElement(metronome, "per-minute").text = str(int(round(bpm)))
    ET.SubElement(direction, "sound", tempo=f"{bpm:.2f}")


def build_musicxml_score(
    drum_midi_path: str,
    bass_midi_path: str,
    lead_midi_path: str,
    rhythm_midi_path: str,
    output_xml_path: str,
    song_title: str = "AI Band Transcription",
    selected_parts: list = None,
    bpm: float = 120.0,
    downbeat: float = 0.0,
    vocal_midi_path: str = "",
    lyrics: list = None
) -> str:
    """
    Combines transcribed MIDIs into a clean, multi-part or single-part MusicXML score
    with Tablature, Notation, accurate measure-beat quantization, rests, ties and chords.
    `downbeat` is the time (s) of any downbeat; bar lines are aligned to it.
    The vocal melody (part 'vocals') is written with guitar TAB for the notes a guitar
    can play, and `lyrics` ([{"start", "pitch", "text", "syllabic"}], matched to the
    vocal MIDI notes by onset and pitch) under its notes.
    """
    os.makedirs(os.path.dirname(output_xml_path) or '.', exist_ok=True)
    
    # Normalize selected parts
    if not selected_parts:
        selected_parts = ['drums', 'bass', 'rhythm', 'lead']
    selected_parts = [p.lower() for p in selected_parts]

    # Load individual MIDIs only if requested
    drum_pm = pretty_midi.PrettyMIDI(drum_midi_path) if ('drums' in selected_parts and drum_midi_path and os.path.exists(drum_midi_path)) else None
    bass_pm = pretty_midi.PrettyMIDI(bass_midi_path) if ('bass' in selected_parts and bass_midi_path and os.path.exists(bass_midi_path)) else None
    lead_pm = pretty_midi.PrettyMIDI(lead_midi_path) if ('lead' in selected_parts and lead_midi_path and os.path.exists(lead_midi_path)) else None
    rhythm_pm = pretty_midi.PrettyMIDI(rhythm_midi_path) if ('rhythm' in selected_parts and rhythm_midi_path and os.path.exists(rhythm_midi_path)) else None
    vocal_pm = pretty_midi.PrettyMIDI(vocal_midi_path) if ('vocals' in selected_parts and vocal_midi_path and os.path.exists(vocal_midi_path)) else None

    # Optimize tablatures
    bass_notes = sorted(bass_pm.instruments[0].notes, key=lambda x: x.start) if bass_pm and bass_pm.instruments else []
    lead_notes = sorted(lead_pm.instruments[0].notes, key=lambda x: x.start) if lead_pm and lead_pm.instruments else []
    rhythm_notes = sorted(rhythm_pm.instruments[0].notes, key=lambda x: x.start) if rhythm_pm and rhythm_pm.instruments else []
    drum_notes = sorted(drum_pm.instruments[0].notes, key=lambda x: x.start) if drum_pm and drum_pm.instruments else []
    vocal_notes = sorted(vocal_pm.instruments[0].notes, key=lambda x: x.start) if vocal_pm and vocal_pm.instruments else []

    bass_tab = optimize_tablature(bass_notes, BASS_TUNING, max_fret=15) if 'bass' in selected_parts else []
    lead_tab = optimize_tablature(lead_notes, GUITAR_TUNING, max_fret=22) if 'lead' in selected_parts else []
    rhythm_tab = optimize_tablature(rhythm_notes, GUITAR_TUNING, max_fret=16) if 'rhythm' in selected_parts else []

    # Vocal melody: guitar fingering where a guitar reaches, plain notes elsewhere
    vocal_tab = optimize_tablature(vocal_notes, GUITAR_TUNING, max_fret=22)
    tabbed = {id(n) for n, _, _ in vocal_tab}
    vocal_tab = sorted(vocal_tab + [(n, None, None) for n in vocal_notes if id(n) not in tabbed],
                       key=lambda item: item[0].start)
    low_voice = bool(vocal_notes) and float(np.median([n.pitch for n in vocal_notes])) < 60
    lyric_map = {(round(l["start"], 3), l["pitch"]): (l["text"], l["syllabic"]) for l in (lyrics or [])}

    # One key signature for all pitched parts
    fifths, mode = estimate_key([n for n, _, _ in bass_tab + lead_tab + rhythm_tab + vocal_tab])

    # Construct clean MusicXML DOM
    score = ET.Element("score-partwise", version="3.1")
    
    # Movement title
    work = ET.SubElement(score, "work")
    work_title = ET.SubElement(work, "work-title")
    work_title.text = song_title
    
    # Part list
    part_list = ET.SubElement(score, "part-list")
    
    available_parts = [
        ("vocals", "P5", "Vocals", "Vox", vocal_tab, "vocal"),
        ("drums", "P1", "Drums", "Drums", drum_notes, "drum"),
        ("bass", "P2", "Bass Guitar", "Bass", bass_tab, "bass"),
        ("rhythm", "P3", "Rhythm Guitar", "Rhythm", rhythm_tab, "guitar"),
        ("lead", "P4", "Lead Guitar", "Lead", lead_tab, "guitar")
    ]
    parts_info = [p for p in available_parts if p[0] in selected_parts]
    if not parts_info:
        # Fallback to lead if all deselected
        parts_info = [p for p in available_parts if p[0] == "lead"]
    
    for channel, (part_key, pid, pname, pabbr, data, ptype) in enumerate(parts_info, start=1):
        _add_score_part(part_list, part_key, pid, pname, pabbr, ptype, data, channel)

    # Metric & Tempo setup
    bpm = max(45.0, min(240.0, float(bpm)))
    seconds_per_beat = 60.0 / bpm
    divisions = 4  # 16th note divisions per quarter beat (16 divisions per 4/4 bar)
    bar_divisions = 16
    seconds_per_div = seconds_per_beat / divisions
    
    # Snap every part onto one global 16th-note grid whose bar lines fall on the
    # downbeats: bar 1 starts at the last downbeat before the music (minus a little
    # slack), so anything earlier is written as a pickup with leading rests.
    bar_seconds = seconds_per_div * bar_divisions
    origin = downbeat - bar_seconds * np.ceil((downbeat - seconds_per_div / 2) / bar_seconds)

    part_segments = []
    last_slot = int(round((2.0 - origin) / seconds_per_div))  # at least ~2 s of score
    for part_key, pid, pname, pabbr, data, ptype in parts_info:
        items = [(n, None, None) for n in data] if ptype == "drum" else data
        events = quantize_onsets(items, seconds_per_div, merge_same_slot=(ptype == "drum"), origin=origin)
        segments, end_slot = split_into_bars(events, bar_divisions, tie=(ptype != "drum"))
        last_slot = max(last_slot, end_slot)
        part_segments.append(segments)

    total_bars = max(1, int(np.ceil(last_slot / bar_divisions)))

    for part_idx, ((part_key, pid, pname, pabbr, data, ptype), segments) in enumerate(zip(parts_info, part_segments)):
        part_el = ET.SubElement(score, "part", id=pid)
        drum_part_id = pid if ptype == "drum" else None

        for bar_num in range(1, total_bars + 1):
            measure_el = ET.SubElement(part_el, "measure", number=str(bar_num))

            if bar_num == 1:
                _add_first_measure_attributes(measure_el, ptype, divisions, fifths, mode,
                                              octave_down=(ptype != "vocal" or low_voice))
                if part_idx == 0:
                    _add_tempo(measure_el, bpm)

            cursor = 0
            for slot, slot_items, dur, tie_stop, tie_start in segments.get(bar_num, []):
                # Fill rest gap before this note slot using standard rest durations
                if slot > cursor:
                    _append_rests(measure_el, slot - cursor)
                for idx, (n_obj, s_num, f_num) in enumerate(slot_items):
                    lyric = None
                    if ptype == "vocal" and idx == 0 and not tie_stop:
                        lyric = lyric_map.get((round(n_obj.start, 3), n_obj.pitch))
                    _append_note(measure_el, n_obj, s_num, f_num, dur, chord=idx > 0,
                                 fifths=fifths, drum_part_id=drum_part_id,
                                 tie_stop=tie_stop, tie_start=tie_start, lyric=lyric)
                cursor = slot + dur

            # Fill the rest of the bar (a whole-bar rest if it held no notes)
            if cursor < bar_divisions:
                _append_rests(measure_el, bar_divisions - cursor)

    tree = ET.ElementTree(score)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_xml_path, encoding="utf-8", xml_declaration=True)
    return output_xml_path


if __name__ == "__main__":
    import sys
    print("Tab Builder module ready.")
