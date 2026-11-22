"""
Beat Map Module
Finds where every beat of a song falls, so the score's bar lines follow the band
even when its tempo drifts (no click track), speeds up or breathes.

1. librosa's beat tracker gives the pulse it locks onto: the beat, or often the
   eighth notes when hi-hats run in eighths, or half the beat.
2. The pulses are numbered interval by interval (a skipped pulse counts twice) and
   smoothed by a robust local quadratic fit: each pulse time comes from its
   neighbours (about two bars either side), which removes the tracker's frame jitter
   while following tempo changes, and curving tempo too. A single straight line
   through the whole song drifted off as soon as the tempo moved.
3. The metrical level is chosen so that beats are loud and what falls between them
   is quieter (kick and snare on the beat, only hats between): a fast pulse whose
   every other pulse is clearly quieter is an eighth-note pulse (the loud ones are
   the beats); a slow pulse with equally loud hits halfway between, and no kick /
   snare alternation from pulse to pulse, is half-time (a 160 BPM song tracked at
   80 on the snares), so the halfway points are beats too. On drifting tempos the
   tracker often locks onto the eighth notes.
4. The bar: 3 or 4 beats, whichever period the low-frequency attacks and chord
   changes repeat with more clearly (4 unless 3 is clearly stronger), and the
   downbeat is the beat position where they are strongest.
"""

import numpy as np

HALF_WINDOW = 8          # pulses either side in each local fit (about two bars)
HOP = 256                # ~12 ms frames at 22.05 kHz (tracked beats scatter 3.5 ms, vs 7 ms at 512)
# librosa's onset envelope peaks this long after a sharp attack (measured on synthetic
# drums at HOP): beats are moved back by it so they line up with the notes' onsets
ENVELOPE_LATENCY = 0.016
SUBDIVISION_RATIO = 0.55  # every other pulse this much quieter: the pulse is the eighth note
HALF_TIME_RATIO = 0.8     # points between slow pulses this loud: the pulse is every other beat...
BACKBEAT_RATIO = 0.7      # ...unless kick and snare alternate pulse by pulse (a real backbeat)
TRIPLE_METER_MARGIN = 1.25  # bars of 3 must stand out this much more than bars of 4...
TRIPLE_METER_FLOOR = 0.3    # ...and clearly at all (z-score units): 4/4 songs with no clear
                            # bar pattern scored 0.07 vs 0.04 and were read as 3/4; waltzes score 0.75+
# The half-time rule doubles a slow pulse only up to this tempo. Measured on 20 Slakh band
# mixes and 40 GuitarSet recordings, its loudness cues fired on correctly tracked 90-103
# BPM songs (busy eighth notes are as loud as the beats) and doubled them to 180-207; a
# 176 BPM song tracked at 88 still doubles. The trade-off: songs over 180 tracked at half
# speed stay there (Ballroom quicksteps, ~200 BPM, now read at ~100: the same cues could
# not tell them from the 100 BPM songs), a readable score in cut time.
MAX_DOUBLED_BPM = 180
# librosa's tracker leans towards 120 BPM, and on waltzes it often settled on a pulse the
# music does not repeat with (Ballroom: waltzes at ~87 BPM tracked at ~130, Viennese waltzes
# at ~177 tracked at ~116). Such a pulse stands out: the onset envelope's autocorrelation at
# its period is well under the strongest one (median 0.56-0.74 of it, against 0.94-1.00 for
# correctly tracked songs). The song is then tracked again at the strong period nearest to it
# (up to MAX_DOUBLED_BPM).
WEAK_PULSE = 0.7         # autocorrelation at the tracked period under this share of the strongest...
STRONG_PERIOD = 0.85     # ...re-track at the nearest period with at least this share
TEMPO_RANGE = (40.0, 240.0)
# A fast pulse (over 130 BPM) whose low attacks and chord changes repeat every 6 pulses
# rather than every 8 is in threes. Measured on Ballroom (6 minus 8, z-score units): waltzes
# +0.56 (tracked in eighths) and +1.09 (Viennese, tracked on the beat), 4/4 dances -0.15 to
# -1.3, 90% of them under +0.06. When the pulse halfway through those 6 looks like a bar
# start too (stronger than the other four), the pulse is the beat and 6 pulses are two bars
# (Viennese waltzes); otherwise it is the eighth notes of a slower waltz, and every other
# pulse is a beat.
TRIPLE_PULSE_MARGIN = 0.3


def _autocorrelation_curve(envelope: np.ndarray, fps: float) -> tuple:
    """(tempi in BPM, the onset envelope's normalized autocorrelation at their periods)."""
    import librosa
    max_lag = int(np.ceil(60.0 * fps / TEMPO_RANGE[0])) + 2
    ac = librosa.autocorrelate(envelope - envelope.mean(), max_size=max_lag)
    ac = ac / (ac[0] + 1e-12)
    bpms = np.arange(TEMPO_RANGE[0], TEMPO_RANGE[1] + 0.25, 0.5)
    return bpms, np.interp(60.0 * fps / bpms, np.arange(len(ac)), ac)


def corrected_tempo(envelope: np.ndarray, fps: float, tracked_bpm: float):
    """The tempo to track again at when `tracked_bpm` is a weak period, else None."""
    from scipy.signal import find_peaks
    if not TEMPO_RANGE[0] <= tracked_bpm <= TEMPO_RANGE[1]:
        return None
    bpms, curve = _autocorrelation_curve(envelope, fps)
    strongest = curve.max()
    if strongest <= 0 or curve[np.argmin(np.abs(bpms - tracked_bpm))] >= WEAK_PULSE * strongest:
        return None
    peaks, _ = find_peaks(curve)
    # strong periods only, and none faster than the half-time rule doubles to (on drum-less
    # guitar recordings the eighth notes repeat as strongly: 100 BPM songs went to 195-200)
    peaks = peaks[(curve[peaks] >= STRONG_PERIOD * strongest) & (bpms[peaks] <= MAX_DOUBLED_BPM)]
    if not len(peaks):
        return None
    return float(bpms[peaks][np.argmin(np.abs(np.log2(bpms[peaks] / tracked_bpm)))])


def _number_pulses(times: np.ndarray) -> np.ndarray:
    """Pulse numbers 0, 1, 2, ... with a skipped pulse counting twice (against the local period)."""
    intervals = np.diff(times)
    k = 4
    padded = np.pad(intervals, k, mode="edge")
    local = np.array([np.median(padded[i:i + 2 * k + 1]) for i in range(len(intervals))])
    return np.concatenate([[0.0], np.cumsum(np.maximum(1.0, np.round(intervals / local)))])


def _smooth_pulses(numbers: np.ndarray, times: np.ndarray, iterations: int = 3) -> np.ndarray:
    """
    Pulse times for every number 0..last: a weighted quadratic through the tracked
    pulses within HALF_WINDOW numbers (tricube weights), refitted with robust weights
    so a misplaced pulse does not pull its neighbours.
    """
    robust = np.ones(len(times))

    def fit(n0):
        w = HALF_WINDOW
        while True:
            near = np.abs(numbers - n0) <= w
            if near.sum() >= 4 or w > 8 * HALF_WINDOW:
                break
            w *= 2
        d = np.abs(numbers[near] - n0) / (w + 1)
        weights = (1 - d ** 3) ** 3 * robust[near]
        if weights.sum() < 1e-9 or near.sum() < 2:
            return float(np.interp(n0, numbers, times))
        degree = 2 if np.count_nonzero(weights > 1e-3) >= 5 else 1
        # Weighted least squares; the constant term is the fitted time at n0
        root = np.sqrt(weights)
        design = np.vander(numbers[near] - n0, degree + 1) * root[:, None]
        coef = np.linalg.lstsq(design, times[near] * root, rcond=None)[0]
        return float(coef[-1])

    for _ in range(iterations):
        residual = times - np.array([fit(n) for n in numbers])
        scale = 6 * np.median(np.abs(residual)) + 1e-4
        u = residual / scale
        robust = np.where(np.abs(u) < 1, (1 - u ** 2) ** 2, 0.0)
    return np.array([fit(n) for n in np.arange(int(numbers[-1]) + 1)])


def _extend(beats: np.ndarray, start: float, end: float) -> np.ndarray:
    """Continues the beat grid to cover start..end at the tempo of its first / last beats."""
    first = float(np.mean(np.diff(beats[:5])))
    last = float(np.mean(np.diff(beats[-5:])))
    before = beats[0] - first * np.arange(int(np.ceil((beats[0] - start) / first)), 0, -1)
    after = beats[-1] + last * np.arange(1, int(np.ceil((end - beats[-1]) / last)) + 2)
    return np.concatenate([before, beats, after])


def _strength_at(envelope: np.ndarray, times: np.ndarray, sr: int, hop: int) -> np.ndarray:
    """An envelope's peak just after each time (a beat's attack shows up in the next ~60 ms)."""
    frames = np.round(np.asarray(times) * sr / hop).astype(int)
    ahead = max(2, int(round(0.06 * sr / hop)))
    return np.array([envelope[max(0, f - 1):max(1, f + ahead)].max() if 0 <= f < len(envelope) else 0.0
                     for f in frames])


def _zscore(x: np.ndarray) -> np.ndarray:
    return (x - x.mean()) / (x.std() + 1e-9)


def _bar_evidence(times: np.ndarray, low_onsets: np.ndarray, chroma: np.ndarray, sr: int, hop: int) -> np.ndarray:
    """Per time: how much it looks like a bar start (a low attack and a chord change), z-scored."""
    import librosa
    frames = np.clip(librosa.time_to_frames(times, sr=sr, hop_length=hop), 0, chroma.shape[1] - 1)
    low = _strength_at(low_onsets, times, sr, hop)
    bounds = list(frames) + [chroma.shape[1]]
    beat_chroma = np.stack([chroma[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(bounds[:-1], bounds[1:])])
    unit = beat_chroma / (np.linalg.norm(beat_chroma, axis=1, keepdims=True) + 1e-9)
    change = np.concatenate([[0.0], 1.0 - np.sum(unit[1:] * unit[:-1], axis=1)])
    return _zscore(low) + _zscore(change)


def _standout(evidence: np.ndarray, period: int) -> tuple:
    """(how far the strongest of `period` positions stands above the mean, that position)."""
    means = [evidence[j::period].mean() for j in range(period)]
    return max(means) - float(np.mean(evidence)), int(np.argmax(means))


def constant_beats(bpm: float, downbeat: float, start: float, end: float) -> np.ndarray:
    """A steady beat grid through `downbeat` covering start..end."""
    period = 60.0 / bpm
    first = downbeat - period * np.ceil((downbeat - start) / period)
    return first + period * np.arange(int(np.ceil((end - first) / period)) + 2)


def analyze_beat_map(audio_path: str, default_bpm: float = 120.0) -> dict:
    """
    Returns {"bpm": average tempo, "downbeat": time of a downbeat (s),
    "beats": every beat time (s) across the recording, the downbeat among them,
    "beats_per_bar": 3 or 4}. Falls back to a steady 4/4 grid at `default_bpm` when no
    beat can be found.
    """
    import librosa
    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=600.0)
    except Exception:
        return {"bpm": default_bpm, "downbeat": 0.0, "beats": constant_beats(default_bpm, 0.0, 0.0, 600.0),
                "beats_per_bar": 4}
    duration = len(y) / sr
    hop = HOP
    envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, frames = librosa.beat.beat_track(onset_envelope=envelope, sr=sr, hop_length=hop)
    pulses = librosa.frames_to_time(frames, sr=sr, hop_length=hop) - ENVELOPE_LATENCY
    if len(pulses) >= 8:
        better = corrected_tempo(envelope, sr / hop, 60.0 / float(np.median(np.diff(pulses))))
        if better:
            tempo, frames = librosa.beat.beat_track(onset_envelope=envelope, sr=sr, hop_length=hop, bpm=better)
            pulses = librosa.frames_to_time(frames, sr=sr, hop_length=hop) - ENVELOPE_LATENCY
    if len(pulses) < 8:
        bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else default_bpm
        bpm = bpm if 50 <= bpm <= 220 else default_bpm
        return {"bpm": bpm, "downbeat": 0.0, "beats": constant_beats(bpm, 0.0, 0.0, duration), "beats_per_bar": 4}

    grid = _smooth_pulses(_number_pulses(pulses), pulses)

    # Metrical level, from loudness (not onset strength: hats alone make strong onsets)
    bpm = 60.0 / float(np.median(np.diff(grid)))
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    loudness = _strength_at(rms, grid, sr, hop)
    even, odd = float(np.mean(loudness[0::2])), float(np.mean(loudness[1::2]))
    alternates = min(even, odd) < SUBDIVISION_RATIO * max(even, odd)
    halfway = (grid[:-1] + grid[1:]) / 2
    between = float(np.mean(_strength_at(rms, halfway, sr, hop))) / (float(np.mean(loudness)) + 1e-9)
    low_onsets = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, fmax=200, n_mels=8)
    kicks = _strength_at(low_onsets, grid, sr, hop)
    backbeat = min(kicks[0::2].mean(), kicks[1::2].mean()) < BACKBEAT_RATIO * max(kicks[0::2].mean(), kicks[1::2].mean())
    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=hop)
    in_threes = False
    if bpm > 130 and len(grid) >= 48:
        pulse_evidence = _bar_evidence(grid, low_onsets, chroma, sr, hop)
        (six, six_at), (eight, _) = _standout(pulse_evidence, 6), _standout(pulse_evidence, 8)
        in_threes = six - eight > TRIPLE_PULSE_MARGIN
    if in_threes:
        by_position = np.roll([pulse_evidence[j::6].mean() for j in range(6)], -six_at)
        if by_position[3] < max(by_position[[1, 2, 4, 5]]) and bpm / 2 >= 50:
            grid = grid[six_at % 2::2]  # eighth notes of a waltz: the bar's pulse parity are the beats
    elif bpm > 220 or (bpm > 130 and alternates and bpm / 2 >= 60):
        grid = grid[0::2] if even >= odd else grid[1::2]  # eighth-note pulse: the loud ones are beats
    elif bpm < 110 and between >= HALF_TIME_RATIO and not backbeat and bpm * 2 <= MAX_DOUBLED_BPM:
        grid = np.sort(np.concatenate([grid, halfway]))  # half-time pulse
    bpm = 60.0 * (len(grid) - 1) / float(grid[-1] - grid[0])  # the average over the tracked span
    if not 50 <= bpm <= 220 or len(grid) < 8:
        return {"bpm": default_bpm, "downbeat": 0.0, "beats": constant_beats(default_bpm, 0.0, 0.0, duration),
                "beats_per_bar": 4}
    beats = _extend(grid, 0.0, duration)

    # Downbeat: the beat position (of 4) where low attacks and chord changes are
    # strongest. Under 100 BPM the tracker may follow every other beat or the
    # off-beats, so half-beats are candidates too.
    half_time_suspect = bpm < 100
    candidates = np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) / 2])) if half_time_suspect else beats
    positions = 8 if half_time_suspect else 4
    candidates = candidates[(candidates >= 0) & (candidates < duration)]
    if len(candidates) < 2 * positions:
        return {"bpm": float(bpm), "downbeat": float(beats[0]), "beats": beats, "beats_per_bar": 4}

    evidence = _bar_evidence(candidates, low_onsets, chroma, sr, hop)
    position = int(np.argmax([evidence[j::positions].mean() for j in range(positions)]))
    downbeat = float(candidates[position])
    if half_time_suspect and np.min(np.abs(beats - downbeat)) > 1e-6:
        # The bar starts between tracked beats: the beats are the half-beats
        beats = _extend((beats[:-1] + beats[1:]) / 2, 0.0, duration)

    # Bars of 3 or 4: the period the evidence repeats with, over the beats themselves
    on_beat = np.array([np.min(np.abs(beats - c)) < 1e-6 for c in candidates])
    beat_evidence, beat_times = evidence[on_beat], candidates[on_beat]
    beats_per_bar = 4
    if len(beat_evidence) >= 24 or (in_threes and len(beat_evidence) >= 6):
        (triple, phase), (quadruple, _) = _standout(beat_evidence, 3), _standout(beat_evidence, 4)
        if in_threes or (triple > TRIPLE_METER_FLOOR and triple > TRIPLE_METER_MARGIN * max(quadruple, 1e-9)):
            beats_per_bar, downbeat = 3, float(beat_times[phase])
    return {"bpm": float(bpm), "downbeat": downbeat, "beats": beats, "beats_per_bar": beats_per_bar}
