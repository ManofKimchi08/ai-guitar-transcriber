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

from src.beat_map import constant_beats


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

# Score grid: 24 divisions per quarter note, so 32nd notes (3) and triplet 16ths (4)
# both fall on it. Ordinary passages are written on 16ths; a beat uses 32nds only where
# its notes are that fast, and triplets where its notes divide it in three.
DIVISIONS = 24
THIRTY_SECOND, SIXTEENTH, EIGHTH, QUARTER = 3, 6, 12, 24
TRIPLET_SIXTEENTH, TRIPLET_EIGHTH = 4, 8
BEATS_PER_BAR = 4
BAR_DIVISIONS = BEATS_PER_BAR * QUARTER  # a 4/4 bar
TEMPO_CHANGE = 0.03  # a bar this much faster or slower than playback so far gets its own tempo
COMPOUND_SHARE = 0.6  # this share of the beats with notes inside them divided in three: compound meter

# MusicXML standard durations, in divisions
DURATION_TO_TYPE = {
    96: ("whole", False),
    72: ("half", True),
    48: ("half", False),
    36: ("quarter", True),
    24: ("quarter", False),
    18: ("eighth", True),
    12: ("eighth", False),
    9: ("16th", True),
    6: ("16th", False),
    3: ("32nd", False),
}
# Inside a triplet beat (three in the time of two)
TRIPLET_TYPE = {16: "quarter", 8: "eighth", 4: "16th"}


class Meter:
    """
    The bar: `beats_per_bar` beats, each a quarter note (simple meter: 3/4, 4/4) or a
    dotted quarter of three eighths (compound meter: 6/8, 9/8, 12/8, for shuffles and
    songs whose every beat divides in three).
    """

    def __init__(self, beats_per_bar: int = BEATS_PER_BAR, compound: bool = False):
        self.beats_per_bar = beats_per_bar
        self.compound = compound
        self.beat = 36 if compound else QUARTER
        self.bar = self.beat * beats_per_bar
        self.time_signature = (beats_per_bar * 3, 8) if compound else (beats_per_bar, 4)
        # plain rest values, each starting on a multiple of its own length
        self.rest_sizes = [72, 36, 12, 6, 3] if compound else [48, 24, 12, 6, 3]


def decompose_duration(dur: int) -> list:
    """Decomposes a duration in divisions into standard musical values to avoid non-standard rests."""
    chunks = []
    standards = [96, 72, 48, 36, 24, 18, 12, 9, 6, 3]
    remaining = int(dur)
    while remaining > 0:
        for s in standards:
            if s <= remaining:
                chunks.append(s)
                remaining -= s
                break
        else:
            chunks.append(remaining)
            remaining = 0
    return chunks


def decompose_triplet(dur: int) -> list:
    """A stretch inside a triplet beat as triplet quarters, eighths and 16ths."""
    chunks = []
    remaining = int(dur)
    while remaining > 0:
        size = next((s for s in (16, 8, 4) if s <= remaining), remaining)
        chunks.append(size)
        remaining -= size
    return chunks


def add_type_and_dot(note_el: ET.Element, dur: int, triplet: bool = False):
    """Adds standard <type> and optional <dot/> tags (and the 3:2 ratio for a triplet)."""
    if triplet:
        ET.SubElement(note_el, "type").text = TRIPLET_TYPE.get(dur, "eighth")
        modification = ET.SubElement(note_el, "time-modification")
        ET.SubElement(modification, "actual-notes").text = "3"
        ET.SubElement(modification, "normal-notes").text = "2"
    elif dur in DURATION_TO_TYPE:
        t, dot = DURATION_TO_TYPE[dur]
        ET.SubElement(note_el, "type").text = t
        if dot:
            ET.SubElement(note_el, "dot")
    else:
        for std in [96, 48, 24, 12, 6, 3]:
            if dur >= std:
                t, _ = DURATION_TO_TYPE[std]
                ET.SubElement(note_el, "type").text = t
                break
        else:
            ET.SubElement(note_el, "type").text = "32nd"


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


def triplet_beats(beat_positions: list) -> set:
    """
    Beats whose notes divide them in three: given onsets in (fractional) beats from bar 1,
    a beat is a triplet beat when a note inside it lies clearly off the 16th grid (more
    than 0.06 of a beat: beyond timing jitter) and the notes fit thirds of the beat at
    least twice as well as quarters (a swung eighth on the beat's last third counts).
    """
    by_beat = {}
    for p in beat_positions:
        beat = int(np.floor(p + 0.04))
        frac = p - beat
        if 0.04 < frac < 0.96:
            by_beat.setdefault(beat, []).append(frac)
    chosen = set()
    for beat, fracs in by_beat.items():
        f = np.asarray(fracs)
        off_quarters = np.abs(f - np.round(f * 4) / 4)
        off_thirds = np.abs(f - np.round(f * 3) / 3)
        if off_quarters.max() > 0.06 and off_thirds.mean() < 0.5 * off_quarters.mean():
            chosen.add(beat)
    return chosen


def quantize_onsets(items: list, seconds_per_div: float = None, merge_same_slot: bool = False,
                    onset_tol: float = 0.035, origin: float = 0.0, coarse: int = SIXTEENTH,
                    to_grid=None, beat: int = QUARTER, triplets: set = frozenset()) -> list:
    """
    Snaps (note, string, fret) items onto the score grid, slot 0 being the first beat of
    bar 1: `to_grid(seconds)` gives the (fractional) position in divisions along the
    song's beat map, or divisions are `seconds_per_div` long from time `origin`.
    Returns [(slot, [items...], dur_in_divs), ...] with strictly increasing slots.

    Each beat (`beat` divisions) is written on 16ths (`coarse`), or on triplet eighths
    when it is in `triplets`, so timing jitter does not scatter 32nds through ordinary
    passages; a beat switches to the finer step (32nds, triplet 16ths) only where its
    notes are that fast. A note's end snaps to the grid of the beat it ends in.

    Notes struck together (within onset_tol) share a slot as one chord. For pitched
    parts, a separately struck note that still rounds into an occupied slot moves to the
    next grid step if that is at most a 16th late, and is dropped otherwise: stacking it
    would turn a fast run into an unplayable same-string chord.
    With merge_same_slot (drums), different instruments may share a slot instead.
    """
    groups = []
    for it in sorted(items, key=lambda it: (it[0].start, it[0].pitch)):
        if groups and it[0].start - groups[-1][0][0].start <= onset_tol:
            groups[-1].append(it)
        else:
            groups.append([it])
    if not merge_same_slot:
        # A chord can hold only one note per string; keep the first on each
        for k, group in enumerate(groups):
            kept, used_strings = [], set()
            for it in group:
                if it[1] not in used_strings:
                    used_strings.add(it[1])
                    kept.append(it)
            groups[k] = kept

    if to_grid is None:
        def to_grid(t):
            return (t - origin) / seconds_per_div
    positions = [to_grid(g[0][0].start) for g in groups]

    def nearest(pos, step):
        return int(np.floor(pos / step + 0.5)) * step  # halves round up, consistently

    def beat_of(pos):
        return int(np.floor(pos + 0.5)) // beat

    def steps(b):
        return (TRIPLET_EIGHTH, TRIPLET_SIXTEENTH) if b in triplets else (coarse, coarse // 2)

    # Beats too fast for their grid: two notes that would share a grid step, clearly
    # apart (not jitter). The beat that switches to the finer step is that of the note
    # off the coarse grid. For drums, only the same piece struck twice (a roll) counts;
    # different pieces close together just play together.
    fine_beats = set()
    for k in range(1, len(groups)):
        a, b = positions[k - 1], positions[k]
        big, small = steps(beat_of(b))
        if nearest(a, big) != nearest(b, big) or b - a < 0.6 * small:
            continue
        if merge_same_slot and not ({n.pitch for n, _, _ in groups[k - 1]} & {n.pitch for n, _, _ in groups[k]}):
            continue
        off_grid = [p for p in (a, b) if nearest(p, steps(beat_of(p))[1]) % steps(beat_of(p))[0]]
        fine_beats.update(beat_of(p) for p in (off_grid or [b]))

    def step_at(pos):
        b = beat_of(pos)
        big, small = steps(b)
        return small if b in fine_beats else big

    events = []
    for group, pos in zip(groups, positions):
        step = step_at(pos)
        big = steps(beat_of(pos))[0]
        slot = max(0, nearest(pos, step))
        end_pos = max(to_grid(n.end) for n, _, _ in group)
        end = nearest(end_pos, step_at(end_pos))
        dur = max(step, end - slot)
        if events and slot <= events[-1][0]:
            last_slot, last_items, last_dur = events[-1]
            if merge_same_slot:
                seen = {n.pitch for n, _, _ in last_items}
                last_items.extend(it for it in group if it[0].pitch not in seen)
                events[-1] = (last_slot, last_items, max(last_dur, dur))
                continue
            slot = last_slot + step
            if slot - pos > big:
                continue  # too dense even for the grid
            dur = max(step, end - slot)
        events.append((slot, group, dur))
    return events


def _spans(start: int, end: int, bar: int, beat: int, triplets: set) -> list:
    """
    Cuts start..end at bar lines and at the edges of triplet beats:
    [(position, length, inside a triplet beat)].
    """
    out = []
    pos = start
    while pos < end:
        b = pos // beat
        if b in triplets:
            stop = min(end, (b + 1) * beat)
        else:
            stop = min(end, (pos // bar + 1) * bar)
            nxt = next((t for t in sorted(triplets) if t > b and t * beat < stop), None)
            if nxt is not None:
                stop = nxt * beat
        out.append((pos, stop - pos, b in triplets))
        pos = stop
    return out


def split_into_bars(events: list, bar_divisions: int = BAR_DIVISIONS, tie: bool = True,
                    beat: int = QUARTER, triplets: set = frozenset()) -> tuple:
    """
    Lays quantized events out bar by bar. Each event lasts until its own end or the
    next event, whichever comes first. A note that crosses a bar line or a triplet
    beat's edge, or lasts a value with no single note symbol (e.g. 5 sixteenths),
    becomes tied standard values; without `tie` (drum hits) only the first value is kept.

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
        for pos, length, in_triplet in _spans(slot, end, bar_divisions, beat, triplets):
            for d in (decompose_triplet(length) if in_triplet else decompose_duration(length)):
                pieces.append((pos, d))
                pos += d
        if not pieces:
            continue
        if not tie:
            pieces = pieces[:1]

        for k, (p, d) in enumerate(pieces):
            by_bar.setdefault(p // bar_divisions + 1, []).append(
                (p % bar_divisions, items, d, k > 0, k < len(pieces) - 1))
        end_slot = max(end_slot, pieces[-1][0] + pieces[-1][1])
    return by_bar, end_slot


def rest_values(pos: int, dur: int, sizes: list = None) -> list:
    """
    Splits a rest from bar position `pos` lasting `dur` divisions into plain values
    (`sizes`, largest first) that each start on a multiple of their own length, so rests
    fill out the beat before the next one (a 32nd rest, then a 16th, then an 8th...).
    """
    sizes = sizes or [BAR_DIVISIONS, 48, 24, 12, 6, 3]
    chunks = []
    while dur > 0:
        size = next((s for s in sizes if s <= dur and pos % s == 0), min(dur, sizes[-1]))
        chunks.append(size)
        pos += size
        dur -= size
    return chunks


def _append_rest(measure_el: ET.Element, dur: int, triplet: bool = False, tuplet: str = None,
                 whole_bar: bool = False):
    r_note = ET.SubElement(measure_el, "note")
    ET.SubElement(r_note, "rest", measure="yes") if whole_bar else ET.SubElement(r_note, "rest")
    ET.SubElement(r_note, "duration").text = str(dur)
    ET.SubElement(r_note, "voice").text = "1"
    if not whole_bar:
        add_type_and_dot(r_note, dur, triplet)
    if tuplet:
        _add_tuplet(ET.SubElement(r_note, "notations"), tuplet)


def _add_tuplet(notations: ET.Element, tuplet: str):
    """tuplet: 'start', 'stop' or 'start stop' (a bracket over one triplet beat)."""
    for kind in tuplet.split():
        ET.SubElement(notations, "tuplet", type=kind, bracket="yes")


def _bar_elements(segments: list, meter: Meter, bar_index: int, triplets: set) -> list:
    """
    One bar's notes and rests in order, as dicts {pos, dur, items, tie_stop, tie_start,
    triplet, tuplet}; items None = a rest. Gaps become rests (plain values, or triplet
    values inside a triplet beat); an empty bar is one whole-bar rest. Each triplet beat
    gets a tuplet bracket from its first to its last element.
    """
    bar_start = bar_index * meter.bar
    local = {t - bar_start // meter.beat for t in triplets
             if bar_start <= t * meter.beat < bar_start + meter.bar}
    if not segments:
        return [{"pos": 0, "dur": meter.bar, "items": None, "whole_bar": True}]

    elements = []

    def rests(start, end):
        for pos, length, in_triplet in _spans(start, end, meter.bar, meter.beat, local):
            chunks = decompose_triplet(length) if in_triplet else rest_values(pos, length, meter.rest_sizes)
            for chunk in chunks:
                elements.append({"pos": pos, "dur": chunk, "items": None})
                pos += chunk

    cursor = 0
    for slot, items, dur, tie_stop, tie_start in segments:
        if slot > cursor:
            rests(cursor, slot)
        elements.append({"pos": slot, "dur": dur, "items": items, "tie_stop": tie_stop, "tie_start": tie_start})
        cursor = slot + dur
    if cursor < meter.bar:
        rests(cursor, meter.bar)

    for e in elements:
        e["triplet"] = e["pos"] // meter.beat in local
    for b in local:
        inside = [e for e in elements if e["pos"] // meter.beat == b]
        if inside:
            inside[0]["tuplet"] = "start"
            inside[-1]["tuplet"] = "stop" if len(inside) > 1 else "start stop"
    return elements


def _append_note(measure_el: ET.Element, note, string_num, fret_num, dur: int, chord: bool,
                 fifths: int = 0, drum_part_id: str = None,
                 tie_stop: bool = False, tie_start: bool = False, lyric: tuple = None,
                 triplet: bool = False, tuplet: str = None):
    """
    Appends one pitched (or, for drum parts, unpitched) note with optional string/fret,
    ties, a (text, syllabic) lyric, and for a note in a triplet beat its 3:2 ratio and
    (on the beat's first / last element) the tuplet bracket.
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
    add_type_and_dot(n_el, dur, triplet)
    if notehead:
        ET.SubElement(n_el, "notehead").text = notehead

    has_tab = string_num is not None and fret_num is not None
    if tie_stop or tie_start or has_tab or tuplet:
        notations = ET.SubElement(n_el, "notations")
        if tie_stop:
            ET.SubElement(notations, "tied", type="stop")
        if tie_start:
            ET.SubElement(notations, "tied", type="start")
        if tuplet:
            _add_tuplet(notations, tuplet)
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
                                  fifths: int, mode: str, octave_down: bool = True,
                                  time_signature: tuple = (4, 4)):
    attrib = ET.SubElement(measure_el, "attributes")
    ET.SubElement(attrib, "divisions").text = str(divisions)

    key_el = ET.SubElement(attrib, "key")
    ET.SubElement(key_el, "fifths").text = "0" if ptype == "drum" else str(fifths)
    if ptype != "drum":
        ET.SubElement(key_el, "mode").text = mode

    time_el = ET.SubElement(attrib, "time")
    ET.SubElement(time_el, "beats").text = str(time_signature[0])
    ET.SubElement(time_el, "beat-type").text = str(time_signature[1])

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


def _add_tempo(measure_el: ET.Element, bpm: float, playback: float = None, compound: bool = False):
    """
    The printed tempo mark (the song's average beats per minute; a dotted quarter in
    compound meter) and the tempo bar 1 plays at (<sound tempo> counts quarter notes).
    """
    direction = ET.SubElement(measure_el, "direction", placement="above")
    direction_type = ET.SubElement(direction, "direction-type")
    metronome = ET.SubElement(direction_type, "metronome")
    ET.SubElement(metronome, "beat-unit").text = "quarter"
    if compound:
        ET.SubElement(metronome, "beat-unit-dot")
    ET.SubElement(metronome, "per-minute").text = str(int(round(bpm)))
    quarters = 1.5 if compound else 1.0
    ET.SubElement(direction, "sound", tempo=f"{(playback or bpm) * quarters:.2f}")


class Timeline:
    """
    Maps seconds to score positions along a beat map (the time of every beat; a steady
    grid when only a tempo is known), so bar lines follow a band whose tempo drifts.
    Bar 1 starts on the downbeat of the bar holding the earliest note (minus half a
    16th of slack): music before a downbeat becomes a pickup, a silent intro adds no
    empty bars.
    """

    def __init__(self, beats, downbeat: float, earliest: float, beats_per_bar: int = BEATS_PER_BAR):
        self.beats = np.asarray(beats, dtype=float)
        self.index = np.arange(len(self.beats), dtype=float)
        self.beats_per_bar = beats_per_bar
        d = self.beat_position(downbeat)
        slack = 0.5 * SIXTEENTH / QUARTER
        self.origin = d + beats_per_bar * np.floor((self.beat_position(earliest) - d + slack) / beats_per_bar)

    def beat_position(self, t: float) -> float:
        """Fractional beat number at time t, continued at the edge tempo outside the map."""
        b = self.beats
        if t < b[0]:
            return (t - b[0]) / (b[1] - b[0])
        if t > b[-1]:
            return len(b) - 1 + (t - b[-1]) / (b[-1] - b[-2])
        return float(np.interp(t, b, self.index))

    def time_at(self, beat: float) -> float:
        b = self.beats
        if beat < 0:
            return b[0] + beat * (b[1] - b[0])
        if beat > len(b) - 1:
            return b[-1] + (beat - len(b) + 1) * (b[-1] - b[-2])
        return float(np.interp(beat, self.index, b))

    def beats_from_start(self, t: float) -> float:
        """Position of time t in beats from the start of bar 1."""
        return self.beat_position(t) - self.origin

    def grid(self, beat_divisions: int = QUARTER):
        """time -> score position in divisions from the start of bar 1."""
        return lambda t: self.beats_from_start(t) * beat_divisions

    def to_grid(self, t: float) -> float:
        return self.beats_from_start(t) * QUARTER

    def bar_tempo(self, bar_number: int) -> float:
        first = self.origin + self.beats_per_bar * (bar_number - 1)
        seconds = self.time_at(first + self.beats_per_bar) - self.time_at(first)
        return 60.0 * self.beats_per_bar / max(seconds, 1e-6)


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
    lyrics: list = None,
    beats: list = None,
    beats_per_bar: int = BEATS_PER_BAR
) -> str:
    """
    Combines transcribed MIDIs into a clean, multi-part or single-part MusicXML score
    with Tablature, Notation, accurate measure-beat quantization, rests, ties and chords.
    `downbeat` is the time (s) of any downbeat; bar lines are aligned to it. With
    `beats` (the time of every beat, the downbeat among them) the grid follows the
    band's tempo beat by beat; otherwise it runs steadily at `bpm`. The printed tempo
    is `bpm` (the average); where the bars drift from it, playback tempo follows.
    Bars hold `beats_per_bar` beats; beats whose notes divide them in three are written
    as triplets, and a song where most beats do so is written in compound meter
    (6/8, 9/8, 12/8).
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
    divisions = DIVISIONS

    # Snap every part onto one global grid whose bar lines fall on the downbeats
    notes_all = [n if ptype == "drum" else n[0] for *_, data, ptype in parts_info for n in data]
    earliest = min((n.start for n in notes_all), default=downbeat)
    latest = max((n.end for n in notes_all), default=downbeat)
    if beats is None or len(beats) < 4:
        beats = constant_beats(bpm, downbeat, min(earliest, downbeat) - 8 * seconds_per_beat,
                               max(latest, downbeat) + 8 * seconds_per_beat)
    timeline = Timeline(beats, downbeat, earliest, beats_per_bar)

    # Beats divided in three: triplets, or compound meter when most beats are
    part_onsets = []
    for *_, data, ptype in parts_info:
        starts = sorted(n.start if ptype == "drum" else n[0].start for n in data)
        starts = [t for k, t in enumerate(starts) if k == 0 or t - starts[k - 1] > 0.035]
        part_onsets.append([timeline.beats_from_start(t) for t in starts])
    part_triplets = [triplet_beats(onsets) for onsets in part_onsets]
    inner_beats = sum(len({int(np.floor(p + 0.04)) for p in onsets if 0.04 < p - np.floor(p + 0.04) < 0.96})
                      for onsets in part_onsets)
    ternary = sum(len(t) for t in part_triplets)
    compound = ternary >= 8 and ternary >= COMPOUND_SHARE * inner_beats
    meter = Meter(beats_per_bar, compound)
    if compound:
        part_triplets = [set() for _ in part_triplets]  # the meter itself divides beats in three
    to_grid = timeline.grid(meter.beat)

    part_segments = []
    last_slot = int(round(2.0 / seconds_per_beat * meter.beat))  # at least ~2 s of score
    for (part_key, pid, pname, pabbr, data, ptype), triplets in zip(parts_info, part_triplets):
        items = [(n, None, None) for n in data] if ptype == "drum" else data
        events = quantize_onsets(items, merge_same_slot=(ptype == "drum"), to_grid=to_grid,
                                 beat=meter.beat, triplets=triplets)
        segments, end_slot = split_into_bars(events, meter.bar, tie=(ptype != "drum"),
                                             beat=meter.beat, triplets=triplets)
        last_slot = max(last_slot, end_slot)
        part_segments.append(segments)

    total_bars = max(1, int(np.ceil(last_slot / meter.bar)))
    quarters_per_beat = meter.beat / QUARTER

    for part_idx, ((part_key, pid, pname, pabbr, data, ptype), segments, triplets) in enumerate(
            zip(parts_info, part_segments, part_triplets)):
        part_el = ET.SubElement(score, "part", id=pid)
        drum_part_id = pid if ptype == "drum" else None

        for bar_num in range(1, total_bars + 1):
            measure_el = ET.SubElement(part_el, "measure", number=str(bar_num))

            if bar_num == 1:
                _add_first_measure_attributes(measure_el, ptype, divisions, fifths, mode,
                                              octave_down=(ptype != "vocal" or low_voice),
                                              time_signature=meter.time_signature)
            if part_idx == 0:
                bar_tempo = timeline.bar_tempo(bar_num)
                if bar_num == 1:
                    _add_tempo(measure_el, bpm, playback=bar_tempo, compound=meter.compound)
                    playing = bar_tempo
                elif abs(bar_tempo / playing - 1) >= TEMPO_CHANGE:
                    ET.SubElement(measure_el, "sound", tempo=f"{bar_tempo * quarters_per_beat:.2f}")
                    playing = bar_tempo

            # Notes, and rests filling the gaps (a whole-bar rest if it held no notes)
            for e in _bar_elements(segments.get(bar_num, []), meter, bar_num - 1, triplets):
                if e["items"] is None:
                    _append_rest(measure_el, e["dur"], e.get("triplet", False), e.get("tuplet"),
                                 e.get("whole_bar", False))
                    continue
                for idx, (n_obj, s_num, f_num) in enumerate(e["items"]):
                    lyric = None
                    if ptype == "vocal" and idx == 0 and not e["tie_stop"]:
                        lyric = lyric_map.get((round(n_obj.start, 3), n_obj.pitch))
                    _append_note(measure_el, n_obj, s_num, f_num, e["dur"], chord=idx > 0,
                                 fifths=fifths, drum_part_id=drum_part_id,
                                 tie_stop=e["tie_stop"], tie_start=e["tie_start"], lyric=lyric,
                                 triplet=e["triplet"], tuplet=e.get("tuplet") if idx == 0 else None)

    tree = ET.ElementTree(score)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_xml_path, encoding="utf-8", xml_declaration=True)
    return output_xml_path


if __name__ == "__main__":
    import sys
    print("Tab Builder module ready.")
