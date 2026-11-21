"""
Stem Gate Module
Demucs leaves some of every other instrument in each stem. Where an instrument is not
playing, its stem holds only that bleed, 30-80 dB below the mix, and the transcribers
(after the guitar split normalizes its parts) took it for notes: on a Slakh song whose
guitar plays 50 notes, 567 guitar notes came out, 93% of them other instruments.

A stem's windows that sit far below the mix (the sum of all the song's stems) are
silenced before transcription; the windows around a playing passage are kept so its
attacks and tails stay whole.
"""

import os

import numpy as np
import soundfile as sf

GATE_WINDOW_SEC = 0.5
# How far below the mix a stem may sit and still count as playing, per part. Measured:
# - guitar (Slakh): playing ~11 dB below the mix (median), silent ~48 dB below, 90% of
#   the silent windows under -31 dB;
# - bass / drums: a bass Demucs separated badly still sat ~36 dB below while playing,
#   but a missing bass or drum kit (guitar-only recordings) leaves 50-80 dB below
ABSENT_DB = {"guitar": -30.0, "vocals": -30.0, "bass": -45.0, "drums": -45.0}
DEFAULT_ABSENT_DB = -30.0
FADE_SEC = 0.02     # gain ramps at the edges of a silenced stretch (no clicks to take for onsets)
KEEP_AROUND = 1     # windows kept either side of a playing window


def presence(stem: np.ndarray, mix: np.ndarray, sr: int, absent_db: float = DEFAULT_ABSENT_DB) -> np.ndarray:
    """Per window: True where the stem is within `absent_db` of the mix, widened by KEEP_AROUND."""
    hop = int(GATE_WINDOW_SEC * sr)
    n = max(1, int(np.ceil(len(stem) / hop)))

    def rms(x):
        return np.array([np.sqrt(np.mean(x[k * hop:(k + 1) * hop] ** 2)) if len(x[k * hop:(k + 1) * hop]) else 0.0
                         for k in range(n)])

    rel = 20 * np.log10((rms(stem) + 1e-12) / (rms(mix) + 1e-12))
    playing = rel > absent_db
    keep = playing.copy()
    for k in range(1, KEEP_AROUND + 1):
        keep[k:] |= playing[:-k]
        keep[:-k] |= playing[k:]
    return keep


def gain_curve(keep: np.ndarray, length: int, sr: int) -> np.ndarray:
    """Sample gains: 1 in kept windows, 0 elsewhere, with FADE_SEC linear ramps."""
    hop = int(GATE_WINDOW_SEC * sr)
    gain = np.repeat(keep.astype(float), hop)[:length]
    gain = np.pad(gain, (0, length - len(gain)))
    fade = max(1, int(FADE_SEC * sr))
    kernel = np.ones(fade) / fade
    return np.clip(np.convolve(gain, kernel, mode="same"), 0.0, 1.0)


def mono(x: np.ndarray) -> np.ndarray:
    return x.mean(axis=1) if x.ndim > 1 else x


def gate_stem(stem_path: str, mix_paths: list, out_path: str, part: str = None) -> float:
    """
    Writes `stem_path` to `out_path` with the windows where it is absent silenced, the
    mix being the sum of `mix_paths` (the song's stems). Returns the share of the song
    kept (0..1); 0 means the instrument never plays.
    """
    stem, sr = sf.read(stem_path, always_2d=True)
    mix = np.zeros(len(stem))
    for p in mix_paths:
        if os.path.exists(p):
            other, other_sr = sf.read(p, always_2d=True)
            if other_sr == sr:
                m = mono(other)[:len(mix)]
                mix[:len(m)] += m
    keep = presence(mono(stem), mix, sr, ABSENT_DB.get(part, DEFAULT_ABSENT_DB))
    gain = gain_curve(keep, len(stem), sr)
    sf.write(out_path, stem * gain[:, None], sr)
    return float(keep.mean())
