"""
Lyrics Module
Recognizes sung lyrics in the vocal stem with Whisper (faster-whisper, word timestamps)
and attaches them, syllable by syllable, to the transcribed vocal melody notes.
"""

import re

# Phrases Whisper is known to invent over music or silence (subtitle credits etc.)
HALLUCINATED_PHRASES = (
    "시청해주셔서 감사합니다", "구독과 좋아요", "자막 제공", "자막 by", "MBC 뉴스", "KBS 뉴스",
    "thanks for watching", "thank you for watching", "subtitles by", "please subscribe",
    "ご視聴ありがとうございました", "チャンネル登録",
)

# Small kana and the long-vowel mark belong to the preceding sung syllable (mora)
_ATTACHING_KANA = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー")


def _is_syllabic_char(ch: str) -> bool:
    """Hangul syllables, kana and CJK ideographs are sung one character per note."""
    code = ord(ch)
    return (0xAC00 <= code <= 0xD7A3 or 0x3040 <= code <= 0x30FF or
            0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF)


def transcribe_lyrics(vocals_wav: str, model_size: str = "large-v3", device: str = None,
                      compute_type: str = None, language: str = None) -> dict:
    """
    Runs Whisper on the vocal stem.

    Returns {"language": code, "words": [{"start", "end", "text"}], "lines": [str]}.
    Segments that look like hallucinations (no speech, repetition loops, subtitle
    credits) and very unsure words are dropped.
    """
    import gc
    import os
    if os.name == "nt":
        # CTranslate2 (under faster-whisper) needs cuBLAS/cuDNN; PyTorch's CUDA wheels ship them
        try:
            import torch
            torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
            if os.path.isdir(torch_lib):
                os.add_dll_directory(torch_lib)
        except Exception:
            pass
    from faster_whisper import WhisperModel

    if device is None:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    try:
        segments, info = model.transcribe(
            vocals_wav,
            language=language,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,  # avoids runaway repetition over long songs
            hallucination_silence_threshold=2.0,
            beam_size=5,
        )
        words, lines = [], []
        for seg in segments:
            text = seg.text.strip()
            if (not text or seg.no_speech_prob > 0.6 or seg.compression_ratio > 2.4
                    or any(p.lower() in text.lower() for p in HALLUCINATED_PHRASES)):
                continue
            seg_words = [w for w in (seg.words or []) if w.word.strip() and w.probability >= 0.2]
            if not seg_words:
                continue
            words.extend({"start": float(w.start), "end": float(w.end), "text": w.word.strip()}
                         for w in seg_words)
            lines.append(text)
        return {"language": info.language, "words": words, "lines": lines}
    finally:
        del model
        gc.collect()


def split_syllables(word: str, language: str = "en") -> list:
    """
    Splits a word into sung syllables: one per Hangul/kana/CJK character (small kana
    joining the previous one), dictionary hyphenation (pyphen) for other scripts.
    """
    text = re.sub(r"^[^\w']+|[^\w']+$", "", word.strip())
    if not text:
        return []

    if any(_is_syllabic_char(ch) for ch in text):
        syllables, latin = [], ""
        for ch in text:
            if _is_syllabic_char(ch):
                if latin:
                    syllables.append(latin)
                    latin = ""
                if ch in _ATTACHING_KANA and syllables:
                    syllables[-1] += ch
                else:
                    syllables.append(ch)
            elif ch.isalnum() or ch == "'":
                latin += ch
        if latin:
            syllables.append(latin)
        return syllables

    try:
        import pyphen
        dic = pyphen.Pyphen(lang=pyphen.language_fallback(language or "en") or "en_US")
        return [part for part in dic.inserted(text).split("-") if part] or [text]
    except Exception:
        return [text]


def words_to_syllables(words: list, language: str = "en") -> list:
    """Splits timed words into timed syllables, sharing each word's time span evenly."""
    syllables = []
    for word_id, w in enumerate(words):
        parts = split_syllables(w["text"], language)
        span = max(w["end"] - w["start"], 1e-3) / max(len(parts), 1)
        for k, part in enumerate(parts):
            syllables.append({
                "start": w["start"] + k * span,
                "text": part,
                "word": word_id,
                "first": k == 0,
                "last": k == len(parts) - 1,
            })
    return syllables


def align_syllables_to_notes(syllables: list, notes: list, max_offset: float = 0.6) -> list:
    """
    Assigns syllables to melody notes in order (dynamic programming over timing).

    Matching costs the onset difference (0.25 s ~ 1 point); a note may stay without a
    syllable (melisma, humming) cheaply, and a syllable with no fitting note is merged
    into a neighbour's text rather than lost.

    Returns [(note, text, syllabic), ...] with syllabic in single/begin/middle/end.
    """
    if not syllables or not notes:
        return []

    n, m = len(syllables), len(notes)
    skip_note, skip_syllable, inf = 0.15, 1.2, float("inf")

    def match_cost(i, j):
        offset = abs(notes[j].start - syllables[i]["start"])
        return inf if offset > max_offset else offset / 0.25

    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    move = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == inf:
                continue
            if i < n and j < m:
                mc = c + match_cost(i, j)
                if mc < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], move[i + 1][j + 1] = mc, "match"
            if j < m and c + skip_note < cost[i][j + 1]:
                cost[i][j + 1], move[i][j + 1] = c + skip_note, "skip_note"
            if i < n and c + skip_syllable < cost[i + 1][j]:
                cost[i + 1][j], move[i + 1][j] = c + skip_syllable, "skip_syllable"

    # Walk back: which note each syllable landed on (None = no fitting note)
    placement = [None] * n
    i, j = n, m
    while i > 0 or j > 0:
        step = move[i][j]
        if step == "match":
            placement[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif step == "skip_note":
            j -= 1
        else:
            i -= 1

    # Unplaced syllables join the previous placed syllable's note (or the next one)
    placed = [k for k in range(n) if placement[k] is not None]
    if not placed:
        return []
    for k in range(n):
        if placement[k] is None:
            before = [p for p in placed if p < k]
            placement[k] = placement[before[-1]] if before else placement[placed[0]]

    groups = {}
    for k, note_idx in enumerate(placement):
        groups.setdefault(note_idx, []).append(syllables[k])

    result = []
    for note_idx in sorted(groups):
        group = groups[note_idx]
        text = group[0]["text"]
        for prev, syl in zip(group, group[1:]):
            text += syl["text"] if syl["word"] == prev["word"] else " " + syl["text"]
        starts, ends = group[0]["first"], group[-1]["last"]
        syllabic = ("single" if starts and ends else "begin" if starts
                    else "end" if ends else "middle")
        result.append((notes[note_idx], text, syllabic))
    return result
