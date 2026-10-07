"""
Guitar Splitter Module
Splits the separated guitar stem into lead and rhythm guitar by where each sound sits
in the stereo image. In a typical band mix the rhythm guitars are double-tracked and
panned left and right while the lead (solo) sits near the centre.

Plain mid/side cannot do this: the mid (L + R) holds the lead *and* half of each side
guitar, so the lead track was mostly rhythm guitar. Instead every time-frequency bin
is compared between the channels: a centred sound is the same in both (same level,
same phase), while a guitar panned to one side, or two separate takes on the two
sides, is not. Centre bins go to the lead, the rest to the rhythm guitar.

Some mixes do it the other way round (one rhythm guitar in the middle, the solo panned
to a side), so the part that plays chords becomes the rhythm guitar.
"""

import os
import numpy as np
import soundfile as sf
import librosa
from scipy.ndimage import uniform_filter

FFT_SEC = 0.093                  # ~4096 samples at 44.1 kHz: fine frequency resolution keeps
                                 # lead and chord partials apart
SMOOTH = (3, 3)                  # bins x frames compared together
# Channel similarity -> centre mask, soft in between: a sound panned up to ~25% off
# centre still counts as centred, rhythm guitars panned ~60% or more do not
CENTER_LO, CENTER_HI = 0.85, 0.98
MONO_SIDE_RATIO = 0.01           # side/mid energy below this: no stereo image to split by
ABSENT_RATIO = 0.03              # a part with less than this share of the energy is absent
BLOCK_SEC = 30.0                 # processed in blocks: a 4-minute stem in one go needs ~1 GB
PAD_SEC = 1.0                    # context around each block, far beyond the STFT window
SWAP_MARGIN = 1.0                # centre this many more pitch classes per frame than the sides: it holds the chords


def channel_similarity(left_spec: np.ndarray, right_spec: np.ndarray) -> np.ndarray:
    """
    Per time-frequency bin, how alike the channels are, 0..1: 1 for a centred sound,
    2ab/(a^2+b^2) for one panned with gains a, b, and ~0 for unrelated sounds in the
    two channels. Uses the real part of the cross-spectrum, so equal-level sounds with
    different phases (two takes of one part) do not count as centred.
    """
    sll = uniform_filter(np.abs(left_spec) ** 2, SMOOTH)
    srr = uniform_filter(np.abs(right_spec) ** 2, SMOOTH)
    slr = uniform_filter(np.real(left_spec * np.conj(right_spec)), SMOOTH)
    return np.clip(2 * slr / (sll + srr + 1e-12), 0.0, 1.0)


def center_mask(similarity: np.ndarray) -> np.ndarray:
    """Soft mask, 1 for centred bins and 0 for panned ones."""
    x = np.clip((similarity - CENTER_LO) / (CENTER_HI - CENTER_LO), 0.0, 1.0)
    return x * x * (3 - 2 * x)  # smoothstep


def fft_size(sr: int) -> int:
    return 2 ** int(round(np.log2(FFT_SEC * sr)))


def _split_block(left: np.ndarray, right: np.ndarray, n_fft: int) -> tuple:
    hop = n_fft // 4
    L = librosa.stft(left, n_fft=n_fft, hop_length=hop)
    R = librosa.stft(right, n_fft=n_fft, hop_length=hop)
    mask = center_mask(channel_similarity(L, R))
    n = len(left)
    center = librosa.istft(mask * 0.5 * (L + R), hop_length=hop, length=n)
    side_l = librosa.istft((1 - mask) * L, hop_length=hop, length=n)
    side_r = librosa.istft((1 - mask) * R, hop_length=hop, length=n)
    return center, side_l, side_r


def split_center_sides(left: np.ndarray, right: np.ndarray, sr: int) -> tuple:
    """
    Centre (mono) and sides (stereo) of a stereo signal. The sides keep their two
    channels: two takes left and right add up when downmixed, while a [side, -side]
    pair would cancel to silence.
    """
    n = len(left)
    n_fft = fft_size(sr)
    hop = n_fft // 4
    block, pad = int(BLOCK_SEC * sr) // hop * hop, int(PAD_SEC * sr)
    center = np.zeros(n, dtype=np.float32)
    sides = np.zeros((n, 2), dtype=np.float32)
    for start in range(0, n, block):
        end = min(n, start + block)
        a, b = max(0, (start - pad) // hop * hop), min(n, end + pad)  # frames on the global grid
        c, sl, sr_ = _split_block(left[a:b], right[a:b], n_fft)
        center[start:end] = c[start - a:end - a]
        sides[start:end, 0] = sl[start - a:end - a]
        sides[start:end, 1] = sr_[start - a:end - a]
    return center, sides


def chord_density(y: np.ndarray, sr: int) -> float:
    """Strong pitch classes per loud frame: about 1-2 for a single-note line, 3-4 for chords."""
    y = librosa.resample(y, orig_sr=sr, target_sr=11025)
    chroma = librosa.feature.chroma_cqt(y=y, sr=11025, hop_length=512)
    rms = librosa.feature.rms(y=y, hop_length=512)[0][:chroma.shape[1]]
    chroma = chroma[:, :len(rms)]
    loud = rms > 0.2 * np.percentile(rms, 95)
    if not loud.any():
        return 0.0
    frames = chroma[:, loud] / (chroma[:, loud].max(axis=0, keepdims=True) + 1e-9)
    return float(np.mean((frames > 0.5).sum(axis=0)))


def _normalize(sig: np.ndarray) -> np.ndarray:
    peak = np.max(np.abs(sig))
    return sig / peak * 0.95 if peak > 1e-6 else sig


def split_guitar_track(input_wav_path: str, output_dir: str) -> dict:
    """
    Splits a guitar stem into lead and rhythm parts.

    Returns {"lead": path or None, "rhythm": path, "method": ...}; method is
    "stereo" (centre = lead, sides = rhythm), "stereo-swapped" (the centre plays the
    chords: sides = lead, centre = rhythm), or "mono" when the stem has no stereo image
    to split by (both parts then get the whole guitar). "lead" is None when only one
    guitar part is there to hear, e.g. two panned rhythm guitars and nothing between.
    """
    os.makedirs(output_dir, exist_ok=True)
    lead_path = os.path.join(output_dir, "guitar_lead.wav")
    rhythm_path = os.path.join(output_dir, "guitar_rhythm.wav")

    audio, sr = sf.read(input_wav_path, always_2d=True, dtype="float32")
    left, right = audio[:, 0], audio[:, -1]
    mid = 0.5 * (left + right)
    side = 0.5 * (left - right)
    if np.sum(side ** 2) < MONO_SIDE_RATIO * np.sum(mid ** 2):
        # Nothing to tell the guitars apart by: both parts get the whole guitar (the
        # lead transcriber follows the strongest line, the chord recognizer the harmony)
        whole = _normalize(mid)
        sf.write(lead_path, whole, sr)
        sf.write(rhythm_path, whole, sr)
        return {"lead": lead_path, "rhythm": rhythm_path, "method": "mono"}

    center, sides = split_center_sides(left, right, sr)
    if np.dot(sides[:, 0], sides[:, 1]) < -0.3 * np.sum(sides ** 2) / 2:
        sides[:, 1] *= -1  # opposite-polarity sides (a widener) would cancel when downmixed
    center_energy = np.sum(center ** 2)
    sides_energy = np.sum(sides.mean(axis=1) ** 2)
    total = center_energy + sides_energy + 1e-12
    method = "stereo"
    if sides_energy < ABSENT_RATIO * total:
        # Everything is centred: one guitar part, give the chord recognizer all of it
        lead, rhythm = None, mid
    elif center_energy < ABSENT_RATIO * total:
        lead, rhythm = None, sides  # only bleed in the centre: no lead guitar
    elif chord_density(center, sr) > chord_density(sides.mean(axis=1), sr) + SWAP_MARGIN:
        lead, rhythm, method = sides.mean(axis=1), center, "stereo-swapped"
    else:
        lead, rhythm = center, sides

    if lead is None:
        if os.path.exists(lead_path):
            os.remove(lead_path)  # a lead from an earlier run of this song is not this one's
        lead_path = None
    else:
        sf.write(lead_path, _normalize(lead), sr)
    sf.write(rhythm_path, _normalize(rhythm), sr)
    return {"lead": lead_path, "rhythm": rhythm_path, "method": method}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        res = split_guitar_track(sys.argv[1], sys.argv[2])
        print("Guitar split complete:", res)
