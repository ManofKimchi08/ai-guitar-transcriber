"""
Drum Transcriber Module
Transcribes a drum audio track (drums.wav) into a standard General MIDI drum file (drums.mid).

Each hit is found where a frequency band's level jumps (a real attack, not just loudness),
then classified by what that attack looks like:
- Kick (36): attack in the low band (40-120 Hz).
- Snare (38): attack in the snare-wire noise band (1-5 kHz) together with the shell
  body (150-350 Hz), so kick harmonics or a hi-hat alone are not taken for a snare.
- Hi-hat / cymbal (42 / 46 / 49): attack in the high band (7-16 kHz); a crash also
  reaches down strongly into 3-6 kHz, an open hi-hat keeps ringing (fading slowly), a
  closed one dies within ~100 ms.
Levels are gated relative to each band's loud hits, so softer strokes still count.
"""

import os
import numpy as np
import soundfile as sf
import pretty_midi
from scipy.signal import butter, sosfilt

HOP_SEC = 0.005           # envelope resolution (the exact step is hop_samples(sr) / sr)
RISE_FRAMES = 3           # level rise measured over 15 ms
RISE_DB = 8.0             # a hit raises its band by at least this much
FLOOR_DB = 40.0           # ...and peaks no lower than this below the band's loudest hits
AUDIBLE_DB = 50.0         # ...and no lower than this below the whole track's loud level
# The level window is centred (20 ms), so it starts rising ~10 ms before the sound does,
# and the attack frame is the last one before the rise: the hit itself is this much later
ATTACK_DELAY_SEC = 0.015
PEAK_FRAMES = 10          # a band's level peaks within 50 ms of the attack (low bands rise slowly)
OPEN_FADE_DB = 10.0       # an open hat fades slower than this (dB per 100 ms); a closed one ~40
CRASH_FADE_DB = 6.0       # ...and a crash slower still


def hop_samples(sr: int) -> int:
    return max(1, int(HOP_SEC * sr))


def band_level_db(audio: np.ndarray, sr: int, low: float, high: float) -> np.ndarray:
    """Short-time level (dB) of one frequency band, one value per hop_samples(sr)."""
    nyq = 0.5 * sr
    sos = butter(4, [max(low / nyq, 1e-4), min(high / nyq, 0.99)], btype="band", output="sos")
    # Causal filtering: a zero-phase filter rings before the hit and moved kicks up to 60 ms early
    band = sosfilt(sos, audio)
    hop = hop_samples(sr)
    win = 4 * hop
    padded = np.pad(band ** 2, (win // 2, win // 2))
    power = np.convolve(padded, np.ones(win) / win, mode="valid")[::hop]
    return 10 * np.log10(power + 1e-12)


def detect_hits(level_db: np.ndarray, min_gap_sec: float = 0.06, rise_db: float = RISE_DB,
                rise_frames: int = RISE_FRAMES) -> list:
    """
    Attacks as (start_frame, peak_frame): the level jumps by `rise_db` within `rise_frames`
    (15 ms by default), and the peak (within 50 ms) is no more than FLOOR_DB below the
    band's loud hits.
    """
    rise = np.zeros_like(level_db)
    rise[rise_frames:] = level_db[rise_frames:] - level_db[:-rise_frames]
    loud = np.percentile(level_db, 99.5)
    min_gap = max(1, int(min_gap_sec / HOP_SEC))
    hits = []
    i = 0
    while i < len(rise):
        if rise[i] >= rise_db:
            j = i
            while j + 1 < len(rise) and rise[j + 1] >= rise_db:
                j += 1
            peak = i + int(np.argmax(level_db[i:min(j + PEAK_FRAMES + 1, len(level_db))]))
            # The attack is the rise that leads into the peak: a faint click just before a
            # hit would otherwise claim the hit's peak and move it tens of ms early
            start = peak
            while start > i and rise[start] < rise_db:
                start -= 1
            while start > i and rise[start - 1] >= rise_db:
                start -= 1
            # ...and it began at the lowest level of the rise window before it
            low = max(0, start - rise_frames)
            attack = low + int(np.argmin(level_db[low:start + 1]))
            if level_db[peak] >= loud - FLOOR_DB and (not hits or peak - hits[-1][1] >= min_gap):
                hits.append((attack, peak))
            i = j + 1
        else:
            i += 1
    return hits


def _near(hits: list, frame: int, tolerance: int) -> bool:
    return any(abs(peak - frame) <= tolerance for _, peak in hits)


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

    full = band_level_db(audio, sr, 20, 0.45 * sr)
    full_loud = np.percentile(full, 99.5)
    low = band_level_db(audio, sr, 40, 120)
    body = band_level_db(audio, sr, 150, 350)
    wires = band_level_db(audio, sr, 1000, 5000)
    mid_high = band_level_db(audio, sr, 3000, 6000)
    # Cymbal bands up to 16 kHz; a low sample rate (22.05 kHz: top 9.9 kHz) shifts them down
    top = min(16000, 0.45 * sr)
    high = band_level_db(audio, sr, min(7000, 0.6 * top), top)
    very_high = band_level_db(audio, sr, min(10000, 0.75 * top), top)

    loud = {}

    def rel(level, f):
        """Level relative to the band's loud hits (dB, <= ~0)."""
        if id(level) not in loud:
            loud[id(level)] = np.percentile(level, 99.5)
        return level[f] - loud[id(level)]

    def hits_in(level, gate_db, **kw):
        """Attacks in a band, loud for that band (`gate_db` below its loud hits) and audible
        in the track (a band with no real hits, e.g. no cymbals, has clicks as loud hits)."""
        return [h for h in detect_hits(level, **kw)
                if rel(level, h[1]) > gate_db and level[h[1]] > full_loud - AUDIBLE_DB]

    def velocity(level, f, lo=40, hi=127):
        return int(np.clip(hi + rel(level, f) * 2.0, lo, hi))

    pm = pretty_midi.PrettyMIDI(initial_tempo=120.0)
    drum_instrument = pretty_midi.Instrument(program=0, is_drum=True, name="Drum Kit")

    # Frames -> seconds with the exact hop: 220 samples at 44.1 kHz is 4.988 ms, and
    # rounding it to 5 ms put hits 50 ms late by 20 s into a song
    frame_sec = hop_samples(sr) / sr

    def add(pitch, attack, dur, vel):
        t = attack * frame_sec + ATTACK_DELAY_SEC  # the attack, not the later level peak
        drum_instrument.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t, end=t + dur))

    # Kick: a loud low-band attack (quiet ones are other drums' low end). The low band
    # rises slowly, more so over a previous kick's tail, hence the 25 ms rise window.
    kick_hits = hits_in(low, -15.0, min_gap_sec=0.09, rise_frames=5)
    for attack, f in kick_hits:
        add(36, attack, 0.15, velocity(low, f))

    # Snare: wire noise together with the shell body (a kick's click or a cymbal's low
    # end brings one without the other). Both are gated relative to the loud hits, loosely
    # enough for softer strokes; the wires rise less under a ringing cymbal.
    body_hits = hits_in(body, -12.0, min_gap_sec=0.06, rise_db=6.0)
    wire_hits = hits_in(wires, -15.0, min_gap_sec=0.08, rise_db=5.0)

    # A kick reaches into the body band too, so on a kick (with, say, a crash supplying
    # the noise) the body must stand clearly above what this kit's kicks give on their own
    def body_over_low(attack, peak):
        return body[attack:attack + PEAK_FRAMES + 1].max() - low[peak]
    own = [body_over_low(a, f) for a, f in kick_hits if not _near(wire_hits, f, 6)]
    kick_body = float(np.median(own)) if own else -12.0

    snare_hits = []
    for attack, f in wire_hits:
        if not _near(body_hits, f, 4):
            continue
        kick = next((k for k in kick_hits if abs(k[1] - f) <= 6), None)
        if kick and body_over_low(*kick) < kick_body + 4.0:
            continue
        snare_hits.append((attack, f))
    for attack, f in snare_hits:
        add(38, attack, 0.12, velocity(wires, f))

    # Hi-hats and cymbals. A hat under a ringing cymbal adds only a few dB, hence the
    # lower rise threshold. Kinds: crash = loud, spreads into 3-6 kHz and keeps ringing;
    # open hat = keeps ringing; closed hat = dies within ~100 ms.
    cym_hits = hits_in(high, -25.0, min_gap_sec=0.06, rise_db=5.0)
    for k, (attack, f) in enumerate(cym_hits):
        spread = mid_high[f] - high[f]
        if _near(snare_hits, f, 4) and spread > -3.0 and very_high[f] - wires[f] < -12.0:
            continue  # only the snare's own noise reaching into the high band (no hat above 10 kHz)
        # Ringing, measured above 10 kHz (where a snare's noise tail is weak) ~120 ms later:
        # how much of this hit's own rise above the level just before it is still there (a
        # closed hat under a ringing crash falls back to the crash level), and how fast it
        # fades (after silence any audible tail is "still there"; a closed hat or a snare's
        # tail fades far faster than an open hat or a crash).
        next_hit = cym_hits[k + 1][0] if k + 1 < len(cym_hits) else len(high)
        probe = min(f + int(0.12 / HOP_SEC), next_hit - 1)
        if probe > f + int(0.05 / HOP_SEC):
            before = very_high[attack]
            sustain = (very_high[probe] - before) / max(very_high[f] - before, 1e-6)
            fade = (very_high[f] - very_high[probe]) / ((probe - f) * HOP_SEC / 0.1)  # dB per 100 ms
        else:
            sustain, fade = 0.0, np.inf
        if spread > -12.0 and sustain > 0.6 and fade < CRASH_FADE_DB and rel(high, f) > -4.0:
            add(49, attack, 1.0, velocity(high, f))
        elif sustain > 0.5 and fade < OPEN_FADE_DB:
            add(46, attack, 0.25, velocity(high, f, 35, 120))
        else:
            add(42, attack, 0.08, velocity(high, f, 30, 115))

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
