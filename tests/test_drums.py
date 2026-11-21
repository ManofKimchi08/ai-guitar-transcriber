"""
Tests for drum transcription on a synthetic rock beat with a known hit list: kick, snare,
closed / open (choked) hi-hat and crash, at several tempos, with uneven stroke strength
and tonal bleed from other instruments.
"""

import os
import sys
import unittest

import numpy as np
import pretty_midi
import soundfile as sf
from scipy.signal import butter, sosfilt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.drum_transcriber import transcribe_drums

KICK, SNARE, CLOSED, OPEN, CRASH = 36, 38, 42, 46, 49
OUT_DIR = os.path.join(os.path.dirname(__file__), "test_output", "drums")


def make_beat(bpm=110, bars=4, sr=44100, seed=0, vary=0.0, bleed=0.0, crash_bars=(0,)):
    """
    Eighth-note hats (open on the "and" of 4, closed again on the next eighth), kick on
    1, 3 and the "and" of 3, snare on 2 and 4, a crash replacing the hat on the first
    beat of `crash_bars` (the hats go on under its ring). `vary` lowers each stroke by
    up to that fraction; `bleed` adds sustained tones.
    """
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    y = np.zeros(int((bars * 4 * beat + 2.5) * sr))

    def noise(lo, hi, n):
        sos = butter(4, [lo / (sr / 2), min(hi / (sr / 2), 0.99)], btype="band", output="sos")
        return sosfilt(sos, rng.standard_normal(n))

    def decay(n, tau):
        return np.exp(-np.arange(n) / sr / tau)

    n = int(0.3 * sr)
    t = np.arange(n) / sr
    kick = 0.9 * np.sin(2 * np.pi * np.cumsum(50 + 70 * np.exp(-t / 0.03)) / sr) * decay(n, 0.09)
    n_closed, n_crash = int(0.12 * sr), int(2.0 * sr)
    n_open = int((beat / 2 + 0.01) * sr)
    choke = np.ones(n_open)
    choke[-int(0.01 * sr):] = np.linspace(1, 0, int(0.01 * sr))
    sounds = {
        KICK: lambda: kick,
        SNARE: lambda: 0.5 * noise(1000, 8000, n) * decay(n, 0.06) + 0.4 * np.sin(2 * np.pi * 190 * t) * decay(n, 0.05),
        CLOSED: lambda: 0.25 * noise(7000, 16000, n_closed) * decay(n_closed, 0.02),
        OPEN: lambda: 0.25 * noise(6000, 16000, n_open) * decay(n_open, 0.25) * choke,
        CRASH: lambda: 0.45 * noise(3000, 16000, n_crash) * decay(n_crash, 0.6),
    }
    truth = []

    def hit(pitch, when):
        sig = sounds[pitch]() * (1.0 - vary * rng.uniform())
        i = int(when * sr)
        y[i:i + len(sig)] += sig[:len(y) - i]
        truth.append((when, pitch))

    for b in range(bars):
        bar = 0.5 + b * 4 * beat
        if b in crash_bars:
            hit(CRASH, bar)
        for k in range(8):
            if not (b in crash_bars and k == 0):
                hit(OPEN if k == 7 else CLOSED, bar + k * beat / 2)
        for k in (0, 2, 2.5):
            hit(KICK, bar + k * beat)
        for k in (1, 3):
            hit(SNARE, bar + k * beat)
    if bleed:
        tt = np.arange(len(y)) / sr
        y += bleed * (np.sin(2 * np.pi * 110 * tt) + np.sin(2 * np.pi * 165 * tt) + 0.5 * np.sin(2 * np.pi * 440 * tt))
    return y / np.max(np.abs(y)) * 0.9, sr, truth


def transcribe(name, y, sr):
    os.makedirs(OUT_DIR, exist_ok=True)
    wav = os.path.join(OUT_DIR, name + ".wav")
    sf.write(wav, y, sr)
    pm = pretty_midi.PrettyMIDI(transcribe_drums(wav, os.path.join(OUT_DIR, name + ".mid")))
    return [(n.start, n.pitch) for inst in pm.instruments for n in inst.notes]


def match(truth, pred, pitch, tol=0.04):
    """(found, total, false, timing errors in s) for one instrument."""
    tr = [t for t, p in truth if p == pitch]
    pr = [t for t, p in pred if p == pitch]
    errors = [min(pr, key=lambda u: abs(u - t)) - t for t in tr if any(abs(u - t) < tol for u in pr)]
    false = sum(not any(abs(u - t) < tol for t in tr) for u in pr)
    return len(errors), len(tr), false, errors


class TestDrumTranscription(unittest.TestCase):
    def check(self, name, min_hat_recall=0.75, **beat):
        y, sr, truth = make_beat(**beat)
        pred = transcribe(name, y, sr)
        for pitch in (KICK, SNARE, CLOSED, OPEN, CRASH):
            found, total, false, errors = match(truth, pred, pitch)
            # Closed hats may hide under a ringing crash; everything else is found
            recall = min_hat_recall if pitch == CLOSED else 1.0
            self.assertGreaterEqual(found, recall * total, f"{name}: drum {pitch} found {found}/{total}")
            self.assertEqual(false, 0, f"{name}: drum {pitch} has {false} false hits")
            self.assertLess(max(abs(e) for e in errors), 0.025, f"{name}: drum {pitch} timing")

    def test_steady_beat(self):
        self.check("steady", bpm=110)

    def test_soft_strokes_and_bleed(self):
        self.check("varied_slow", bpm=85, seed=4, vary=0.5, bleed=0.03)

    def test_fast_tempo(self):
        self.check("fast", bpm=170, seed=6, vary=0.4, min_hat_recall=0.6)

    def test_hats_under_a_ringing_crash(self):
        """With a crash on every bar, most of the hats played under its ring still count."""
        y, sr, truth = make_beat(bars=4, seed=0, crash_bars=(0, 1, 2, 3))
        pred = transcribe("crash_every_bar", y, sr)
        found, total, false, _ = match(truth, pred, CLOSED)
        self.assertGreaterEqual(found, 15, f"closed hats under the crash: {found}/{total}")
        self.assertEqual(false, 0)
        self.assertEqual(match(truth, pred, CRASH)[:3], (4, 4, 0))

    def test_shimmering_crash_gives_no_hats(self):
        """A crash whose ring wobbles (+-2.3 dB, like beating cymbal modes) is not taken for hi-hats."""
        sr, beat = 44100, 0.6
        rng = np.random.default_rng(7)
        y = np.zeros(int(8 * 4 * beat * sr + 3 * sr))
        sos = butter(4, [3000 / (sr / 2), 16000 / (sr / 2)], btype="band", output="sos")
        t = np.arange(int(2.4 * sr)) / sr
        tk = np.arange(int(0.3 * sr)) / sr
        kick = 0.9 * np.sin(2 * np.pi * np.cumsum(50 + 70 * np.exp(-tk / 0.03)) / sr) * np.exp(-tk / 0.09)
        for b in range(8):
            wobble = 1 + 0.3 * np.sin(2 * np.pi * rng.uniform(4, 9) * t + rng.uniform(0, 6)) * np.sin(2 * np.pi * rng.uniform(0.5, 1.5) * t)
            crash = 0.45 * sosfilt(sos, rng.standard_normal(len(t))) * np.exp(-t / 0.6) * wobble
            i = int((0.5 + b * 4 * beat) * sr)
            y[i:i + len(crash)] += crash
            for k in (0, 2):
                j = int((0.5 + (4 * b + k) * beat) * sr)
                y[j:j + len(kick)] += kick
        pred = transcribe("shimmer", y / np.max(np.abs(y)) * 0.9, sr)
        self.assertEqual([t for t, p in pred if p in (CLOSED, OPEN)], [])
        self.assertEqual(sum(p == CRASH for _, p in pred), 8)

    def test_timing_does_not_drift(self):
        """Hits late in a song land where they were played (the frame step is not 5 ms exactly)."""
        y, sr, truth = make_beat(bpm=120, bars=24, seed=1)
        pred = transcribe("long", y, sr)
        late = [(t, p) for t, p in truth if t > 40]
        found, total, _, errors = match(late, [(t, p) for t, p in pred if t > 39], KICK)
        self.assertEqual(found, total)
        self.assertLess(abs(np.median(errors)), 0.01)

    def test_kicks_alone_give_no_cymbals(self):
        """With no cymbals at all, the faint clicks of the kicks are not taken for hi-hats."""
        sr = 22050
        t = np.arange(sr * 6) / sr
        y = np.zeros_like(t)
        for k in range(12):
            i = int(k * 0.5 * sr)
            y[i:i + 2000] += np.sin(2 * np.pi * 60 * t[:2000]) * np.exp(-30 * t[:2000])
        pred = transcribe("kicks_only", y, sr)
        self.assertEqual([p for _, p in pred], [KICK] * 12)

    def test_hats_in_band_limited_audio(self):
        """A stem separated from a 16 kHz mix is stored at 44.1 kHz with nothing above 8 kHz:
        the hats are still found (the cymbal bands follow the audio, not the file's rate)."""
        import librosa
        y, sr, truth = make_beat(bpm=110, seed=2)
        y = librosa.resample(librosa.resample(y, orig_sr=sr, target_sr=16000), orig_sr=16000, target_sr=sr)
        pred = transcribe("band_limited", y, sr)
        for pitch in (KICK, SNARE):
            self.assertEqual(match(truth, pred, pitch)[:3], (12, 12, 0) if pitch == KICK else (8, 8, 0))
        found, total, false, _ = match(truth, pred, CLOSED)
        self.assertGreaterEqual(found, 0.75 * total, f"closed hats {found}/{total}")
        self.assertEqual(false, 0)

    def test_bandwidth_ignores_separation_artifacts(self):
        """Above an 8 kHz source's band, Demucs leaves a noise floor ~55 dB down: that is not
        content (it hid the hats of separated stems), while real cymbals 25 dB down are."""
        import librosa
        from src.drum_transcriber import content_bandwidth
        sr = 44100
        rng = np.random.default_rng(0)
        noise = rng.standard_normal(sr * 4)
        # a 16 kHz source: nothing above 8 kHz
        low = librosa.resample(librosa.resample(noise, orig_sr=sr, target_sr=16000), orig_sr=16000, target_sr=sr)
        high = sosfilt(butter(8, 8500 / (sr / 2), btype="high", output="sos"), rng.standard_normal(sr * 4))
        self.assertLess(content_bandwidth(low + 10 ** (-55 / 20) * high, sr), 8500)
        # ...even with a bump just under Nyquist (seen on the PC's GPU-separated stems)
        top = sosfilt(butter(8, 21500 / (sr / 2), btype="high", output="sos"), rng.standard_normal(sr * 4))
        self.assertLess(content_bandwidth(low + 10 ** (-55 / 20) * high + 10 ** (-30 / 20) * top, sr), 8500)
        self.assertGreater(content_bandwidth(low + 10 ** (-25 / 20) * high, sr), 15000)

    def test_boomy_snare_is_not_a_kick(self):
        """An electronic snare with a big 150-200 Hz body (more there than below 80 Hz) is a
        snare, not a kick, even when it is as loud in the low band as the kicks."""
        y, sr, truth = make_beat(bpm=100, seed=3)
        t = np.arange(int(0.25 * sr)) / sr
        body = 1.2 * (np.sin(2 * np.pi * 170 * t) + 0.6 * np.sin(2 * np.pi * 120 * t)) * np.exp(-t / 0.08)
        for when, pitch in truth:
            if pitch == SNARE:
                i = int(when * sr)
                y[i:i + len(body)] += body[:len(y) - i]
        pred = transcribe("boomy_snare", y / np.max(np.abs(y)) * 0.9, sr)
        self.assertEqual(match(truth, pred, KICK)[:3], (12, 12, 0))

    def test_ride_pattern_is_a_ride(self):
        """Eighth notes on a ride (darker than a hat, a wash ringing on between hits) are
        written as ride (51), and the kick and snare under them stay."""
        sr, beat = 44100, 0.5
        rng = np.random.default_rng(9)
        y = np.zeros(int(16 * beat * sr + sr))
        n_ride = int(1.5 * sr)
        tr = np.arange(n_ride) / sr
        # Shaped like the median MDB-Drums ride: a stick ping fading fast (7-12 kHz, little
        # above 10 kHz) over a long, darker wash ringing ~20 dB under it
        ping = sosfilt(butter(4, [7000 / (sr / 2), 12000 / (sr / 2)], btype="band", output="sos"), rng.standard_normal(n_ride)) * np.exp(-tr / 0.045)
        wash = 0.08 * sosfilt(butter(4, [5000 / (sr / 2), 10000 / (sr / 2)], btype="band", output="sos"), rng.standard_normal(n_ride)) * np.exp(-tr / 1.5)
        tk = np.arange(int(0.3 * sr)) / sr
        kick = 0.9 * np.sin(2 * np.pi * np.cumsum(50 + 70 * np.exp(-tk / 0.03)) / sr) * np.exp(-tk / 0.09)
        rides = []
        for k in range(32):
            t0 = 0.5 + k * beat / 2
            i = int(t0 * sr)
            y[i:i + n_ride] += 0.3 * (ping + wash)[:len(y) - i]
            rides.append(t0)
            if k % 4 == 0:
                y[i:i + len(kick)] += kick[:len(y) - i]
        pred = transcribe("ride", y / np.max(np.abs(y)) * 0.9, sr)
        written = [p for t, p in pred if p != KICK and any(abs(t - r) < 0.04 for r in rides)]
        self.assertGreaterEqual(written.count(51), 0.7 * len(rides), f"ride hits written as {written}")
        self.assertEqual(sum(p == KICK for _, p in pred), 8)

    def test_silence_gives_no_hits(self):
        self.assertEqual(transcribe("silence", np.zeros(44100 * 2), 44100), [])


if __name__ == "__main__":
    unittest.main()
