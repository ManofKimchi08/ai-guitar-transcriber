"""
Tab Builder & MusicXML Generator Module
Converts MIDI tracks (Drums, Bass, Lead Guitar, Rhythm Guitar) into:
1. Dynamic Programming (Viterbi) optimized Guitar and Bass Tablature (fret and string positions).
2. Standard MusicXML score containing both standard musical staves and TAB staves.
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


def assign_chord_strings(chord_notes: list, tuning: dict, max_fret: int = 16) -> list:
    """
    Assigns each simultaneous note in a guitar/bass chord to a unique physical string.
    Guarantees 0 string collisions per chord.
    """
    res = []
    used_strings = set()
    sorted_notes = sorted(chord_notes, key=lambda x: x.pitch)
    string_order = sorted(list(tuning.keys()), reverse=True)  # [6, 5, 4, 3, 2, 1]
    
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


def build_musicxml_score(
    drum_midi_path: str,
    bass_midi_path: str,
    lead_midi_path: str,
    rhythm_midi_path: str,
    output_xml_path: str,
    song_title: str = "AI Band Transcription",
    selected_parts: list = None,
    bpm: float = 120.0
) -> str:
    """
    Combines transcribed MIDIs into a clean, multi-part or single-part MusicXML score
    with Tablature, Notation, accurate measure-beat quantization, rests, and chords.
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

    # Optimize tablatures
    bass_notes = sorted(bass_pm.instruments[0].notes, key=lambda x: x.start) if bass_pm and bass_pm.instruments else []
    lead_notes = sorted(lead_pm.instruments[0].notes, key=lambda x: x.start) if lead_pm and lead_pm.instruments else []
    rhythm_notes = sorted(rhythm_pm.instruments[0].notes, key=lambda x: x.start) if rhythm_pm and rhythm_pm.instruments else []
    drum_notes = sorted(drum_pm.instruments[0].notes, key=lambda x: x.start) if drum_pm and drum_pm.instruments else []

    bass_tab = optimize_tablature(bass_notes, BASS_TUNING, max_fret=15) if 'bass' in selected_parts else []
    lead_tab = optimize_tablature(lead_notes, GUITAR_TUNING, max_fret=22) if 'lead' in selected_parts else []
    rhythm_tab = optimize_tablature(rhythm_notes, GUITAR_TUNING, max_fret=16) if 'rhythm' in selected_parts else []

    # Construct clean MusicXML DOM
    score = ET.Element("score-partwise", version="3.1")
    
    # Movement title
    work = ET.SubElement(score, "work")
    work_title = ET.SubElement(work, "work-title")
    work_title.text = song_title
    
    # Part list
    part_list = ET.SubElement(score, "part-list")
    
    available_parts = [
        ("drums", "P1", "Drums", "Drums", drum_notes, "drum"),
        ("bass", "P2", "Bass Guitar", "Bass", bass_tab, "bass"),
        ("rhythm", "P3", "Rhythm Guitar", "Rhythm", rhythm_tab, "guitar"),
        ("lead", "P4", "Lead Guitar", "Lead", lead_tab, "guitar")
    ]
    parts_info = [p[1:] for p in available_parts if p[0] in selected_parts]
    if not parts_info:
        # Fallback to lead if all deselected
        parts_info = [available_parts[3][1:]]
    
    for pid, pname, pabbr, data, ptype in parts_info:
        score_part = ET.SubElement(part_list, "score-part", id=pid)
        name_el = ET.SubElement(score_part, "part-name")
        name_el.text = pname
        abbr_el = ET.SubElement(score_part, "part-abbreviation")
        abbr_el.text = pabbr

    # Metric & Tempo setup
    bpm = max(45.0, min(240.0, float(bpm)))
    seconds_per_beat = 60.0 / bpm
    seconds_per_bar = seconds_per_beat * 4.0
    divisions = 4  # 16th note divisions per quarter beat (16 divisions per 4/4 bar)
    bar_divisions = 16
    seconds_per_div = seconds_per_beat / divisions
    
    all_end_times = [2.0]
    for pid, pname, pabbr, data, ptype in parts_info:
        if ptype == "drum":
            all_end_times.extend([n.end for n in data])
        else:
            all_end_times.extend([n.end for n, s, f in data])

    max_time = max(all_end_times)
    total_bars = int(np.ceil(max_time / seconds_per_bar)) + 1
    
    pitch_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    for pid, pname, pabbr, data, ptype in parts_info:
        part_el = ET.SubElement(score, "part", id=pid)
        
        data_by_bar = [[] for _ in range(total_bars + 1)]
        if ptype == "drum":
            for n in data:
                b_idx = int(n.start // seconds_per_bar) + 1
                if 1 <= b_idx <= total_bars:
                    data_by_bar[b_idx].append((n, None, None))
        else:
            for n, s, f in data:
                b_idx = int(n.start // seconds_per_bar) + 1
                if 1 <= b_idx <= total_bars:
                    data_by_bar[b_idx].append((n, s, f))

        for bar_num in range(1, total_bars + 1):
            measure_el = ET.SubElement(part_el, "measure", number=str(bar_num))
            bar_start_time = (bar_num - 1) * seconds_per_bar
            
            # Attributes on bar 1
            if bar_num == 1:
                attrib = ET.SubElement(measure_el, "attributes")
                ET.SubElement(attrib, "divisions").text = str(divisions)
                
                key_el = ET.SubElement(attrib, "key")
                ET.SubElement(key_el, "fifths").text = "0"
                
                time_el = ET.SubElement(attrib, "time")
                ET.SubElement(time_el, "beats").text = "4"
                ET.SubElement(time_el, "beat-type").text = "4"
                
                clef = ET.SubElement(attrib, "clef")
                sign = ET.SubElement(clef, "sign")
                line = ET.SubElement(clef, "line")
                if ptype == "drum":
                    sign.text = "percussion"
                    line.text = "2"
                elif ptype == "bass":
                    sign.text = "F"
                    line.text = "4"
                else:
                    sign.text = "G"
                    line.text = "2"

            bar_notes = data_by_bar[bar_num]
            if not bar_notes:
                # Whole measure rest
                note_el = ET.SubElement(measure_el, "note")
                ET.SubElement(note_el, "rest")
                ET.SubElement(note_el, "duration").text = str(bar_divisions)
                ET.SubElement(note_el, "voice").text = "1"
                add_type_and_dot(note_el, bar_divisions)
            else:
                # Group and quantize notes by division slot (0..15)
                slots = {}
                for note_obj, string_num, fret_num in bar_notes:
                    rel_start = max(0.0, note_obj.start - bar_start_time)
                    slot = min(15, max(0, int(round(rel_start / seconds_per_div))))
                    raw_dur = max(1, int(round((note_obj.end - note_obj.start) / seconds_per_div)))
                    if slot not in slots:
                        slots[slot] = []
                    slots[slot].append((note_obj, string_num, fret_num, raw_dur))

                sorted_slots = sorted(slots.keys())
                cursor = 0

                for i, slot in enumerate(sorted_slots):
                    # Fill rest gap before this note slot using standard rest durations
                    if slot > cursor:
                        gap_dur = slot - cursor
                        for chunk in decompose_duration(gap_dur):
                            r_note = ET.SubElement(measure_el, "note")
                            ET.SubElement(r_note, "rest")
                            ET.SubElement(r_note, "duration").text = str(chunk)
                            ET.SubElement(r_note, "voice").text = "1"
                            add_type_and_dot(r_note, chunk)
                        cursor = slot

                    # Determine note duration strictly bounded by next slot or end of bar
                    next_slot = sorted_slots[i + 1] if (i + 1 < len(sorted_slots)) else bar_divisions
                    max_allowed = max(1, next_slot - slot)

                    slot_items = slots[slot]
                    raw_slot_dur = max(item[3] for item in slot_items)
                    slot_dur = min(max_allowed, raw_slot_dur)

                    for idx, (n_obj, s_num, f_num, _) in enumerate(slot_items):
                        n_el = ET.SubElement(measure_el, "note")
                        if idx > 0:
                            ET.SubElement(n_el, "chord")

                        pitch_el = ET.SubElement(n_el, "pitch")
                        p_name = pitch_names[n_obj.pitch % 12]
                        ET.SubElement(pitch_el, "step").text = p_name[0]
                        if len(p_name) > 1:
                            ET.SubElement(pitch_el, "alter").text = "1"
                        ET.SubElement(pitch_el, "octave").text = str(n_obj.pitch // 12 - 1)

                        ET.SubElement(n_el, "duration").text = str(slot_dur)
                        ET.SubElement(n_el, "voice").text = "1"
                        add_type_and_dot(n_el, slot_dur)

                        # Tablature notation for string & fret
                        if s_num is not None and f_num is not None:
                            notations = ET.SubElement(n_el, "notations")
                            tech = ET.SubElement(notations, "technical")
                            ET.SubElement(tech, "string").text = str(s_num)
                            ET.SubElement(tech, "fret").text = str(f_num)

                    cursor += slot_dur

                # Fill trailing rest if measure is not full using standard rest durations
                if cursor < bar_divisions:
                    trail_dur = bar_divisions - cursor
                    for chunk in decompose_duration(trail_dur):
                        r_note = ET.SubElement(measure_el, "note")
                        ET.SubElement(r_note, "rest")
                        ET.SubElement(r_note, "duration").text = str(chunk)
                        ET.SubElement(r_note, "voice").text = "1"
                        add_type_and_dot(r_note, chunk)

    tree = ET.ElementTree(score)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_xml_path, encoding="utf-8", xml_declaration=True)
    return output_xml_path


if __name__ == "__main__":
    import sys
    print("Tab Builder module ready.")
