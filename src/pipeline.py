"""
Transcription Pipeline Module
The single audio -> stems -> MIDI -> MusicXML/PDF flow shared by the CLI (main.py)
and the GUI (app_gui.py). Every song gets its own folder so runs never mix:
    <output_dir>/<song>/stems, <output_dir>/<song>/midi, <output_dir>/<song>/scores
"""

import os
import time
from pathlib import Path

from src.separator import separate_stems
from src.guitar_splitter import split_guitar_track
from src.drum_transcriber import transcribe_drums
from src.pitch_transcriber import transcribe_pitch
from src.tab_builder import build_musicxml_score
from src.youtube_downloader import download_youtube_audio
from src.pdf_exporter import export_score_to_pdf


ALL_PARTS = ['drums', 'bass', 'rhythm', 'lead', 'vocals']
DEFAULT_PARTS = ['drums', 'bass', 'rhythm', 'lead']

# GPU memory profiles. Peak activations measured per setting: htdemucs ~0.7 GB at
# its default 7.8 s segment (~0.5 GB at 5 s); TorchCREPE 'full' ~2.5 GB per 1024
# frames. Both steps also retry with smaller settings if the GPU still runs out.
# Whisper (lyrics): large-v3 in float16 needs ~4.5 GB; large-v3-turbo in int8 far less.
VRAM_PROFILES = {
    "8gb": {"label": "8GB 이상 (표준)", "demucs_segment": None, "crepe_batch_size": 1024,
            "whisper_model": "large-v3", "whisper_compute": "float16"},
    "4gb": {"label": "4GB (저사양)", "demucs_segment": 5, "crepe_batch_size": 512,
            "whisper_model": "large-v3-turbo", "whisper_compute": "int8_float16"},
}


def vram_profile_for(total_gb: float) -> str:
    """Maps a GPU's total memory to a VRAM profile ("8 GB" cards report slightly under 8 GiB)."""
    return "8gb" if total_gb >= 7.0 else "4gb"


def detect_vram_profile() -> str:
    """Picks the VRAM profile for the first CUDA GPU; '8gb' when running on CPU."""
    try:
        import torch
        if torch.cuda.is_available():
            return vram_profile_for(torch.cuda.get_device_properties(0).total_memory / 1024 ** 3)
    except Exception:
        pass
    return "8gb"


def is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def analyze_beats(audio_path: str, default_bpm: float = 120.0) -> tuple:
    """
    Estimates (bpm, downbeat_seconds) for a 4/4 song.

    The tempo and beat phase come from a least-squares fit through all tracked
    beats, which is far finer than the beat tracker's ~23 ms frames (a 1% tempo
    error would drift the bar lines by ~0.3 s over 12 bars). Tempos below 75 BPM
    are taken as half-time tracking and doubled. The downbeat is the beat
    position (of 4) where low-frequency attacks (kick, bass) and chord changes
    are strongest on average.
    """
    import numpy as np
    import librosa
    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True, duration=600.0)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beats = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        return default_bpm, 0.0
    if len(beats) < 8:
        bpm = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else default_bpm
        return (bpm if 50 <= bpm <= 220 else default_bpm), 0.0

    # Number the beats interval by interval (a skipped beat counts as two), then fit
    # time = offset + period * n, dropping beats that sit far off the fitted grid.
    # Numbering from the first beat with one rough period would slip by a beat once
    # its small error accumulates (librosa's intervals often alternate by a frame).
    intervals = np.diff(beats)
    period = float(np.median(intervals))
    numbers = np.concatenate([[0.0], np.cumsum(np.maximum(1.0, np.round(intervals / period)))])
    keep = np.ones(len(beats), dtype=bool)
    for _ in range(2):
        period, offset = np.polyfit(numbers[keep], beats[keep], 1)
        keep = np.abs(beats - (offset + period * numbers)) < period / 4
        if keep.sum() < 8:
            keep[:] = True
            break
    period, offset = np.polyfit(numbers[keep], beats[keep], 1)

    bpm = 60.0 / period
    if bpm > 220:  # tracked eighth notes: count every other one as the beat
        period *= 2.0
    elif bpm < 75:  # half-time tracking: the real beat falls in between
        period /= 2.0
    bpm = 60.0 / period
    if not 50 <= bpm <= 220:
        return default_bpm, 0.0

    phase = offset % period
    # At slow tempos the tracker may follow every other beat (160 BPM tracked as 80,
    # on beats 2 and 4) or the off-beats; either way the bar can start between
    # tracked beats, so look for the downbeat on half-beats as well.
    half_time_suspect = bpm < 100
    step = period / 2 if half_time_suspect else period
    positions = 8 if half_time_suspect else 4
    grid = np.arange(phase, len(y) / sr, step)
    if len(grid) < 2 * positions:
        return bpm, float(phase)

    frames = librosa.time_to_frames(grid, sr=sr)
    low_onsets = librosa.onset.onset_strength(y=y, sr=sr, fmax=200, n_mels=8)
    low = low_onsets[np.clip(frames, 0, len(low_onsets) - 1)]

    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    bounds = list(np.clip(frames, 0, chroma.shape[1] - 1)) + [chroma.shape[1]]
    beat_chroma = np.stack([chroma[:, a:max(b, a + 1)].mean(axis=1) for a, b in zip(bounds[:-1], bounds[1:])])
    unit = beat_chroma / (np.linalg.norm(beat_chroma, axis=1, keepdims=True) + 1e-9)
    change = np.concatenate([[0.0], 1.0 - np.sum(unit[1:] * unit[:-1], axis=1)])

    def zscore(x):
        return (x - x.mean()) / (x.std() + 1e-9)

    evidence = zscore(low) + zscore(change)
    position = int(np.argmax([evidence[j::positions].mean() for j in range(positions)]))
    return bpm, float(grid[position])


def recognize_lyrics(vocals_wav: str, vocal_midi: str, vram_profile: dict, language: str,
                     text_path: str, log=print) -> list:
    """
    Runs Whisper on the vocal stem, writes the lyrics as plain text, and places them
    syllable by syllable on the vocal melody notes.
    Returns [{"start", "pitch", "text", "syllabic"}] for build_musicxml_score.
    """
    import pretty_midi
    import torch
    from src.lyrics import transcribe_lyrics, words_to_syllables, align_syllables_to_notes

    cuda = torch.cuda.is_available()
    if cuda:
        torch.cuda.empty_cache()  # hand TorchCREPE's cached GPU memory over to Whisper
    model = vram_profile["whisper_model"] if cuda else "large-v3-turbo"
    compute = vram_profile["whisper_compute"] if cuda else "int8"
    log(f"  - Whisper {model} ({compute}) 가사 인식 중... (처음 한 번은 모델을 내려받습니다)")
    try:
        result = transcribe_lyrics(vocals_wav, model_size=model, device="cuda" if cuda else "cpu",
                                   compute_type=compute, language=language)
    except Exception as e:
        if not cuda:
            raise
        # e.g. CTranslate2 not finding its CUDA libraries: the CPU still works, just slower
        log(f"  ! GPU 가사 인식 실패 ({e}) → CPU (large-v3-turbo, int8)로 다시 시도합니다...")
        result = transcribe_lyrics(vocals_wav, model_size="large-v3-turbo", device="cpu",
                                   compute_type="int8", language=language)

    with open(text_path, "w", encoding="utf-8") as f:
        f.write("\n".join(result["lines"]) + "\n")

    notes = sorted(pretty_midi.PrettyMIDI(vocal_midi).instruments[0].notes, key=lambda n: n.start)
    placed = align_syllables_to_notes(words_to_syllables(result["words"], result["language"]), notes)
    log(f"  ✓ 가사 {len(result['words'])}단어 인식 (언어: {result['language']}), 음표 {len(placed)}개에 배치")
    log(f"  ✓ 가사 텍스트: {os.path.basename(text_path)}")
    return [{"start": n.start, "pitch": n.pitch, "text": t, "syllabic": s} for n, t, s in placed]


def run_pipeline(
    input_audio: str,
    output_dir: str,
    parts: list = None,
    style: str = "tab",
    skip_separation: bool = False,
    vram: str = None,
    lyrics: bool = True,
    lyrics_language: str = None,
    log=print,
    progress=None
) -> dict:
    """
    Runs the full transcription pipeline for one song.

    Args:
        input_audio: Path to an audio file or a YouTube URL.
        output_dir: Root output folder; results go to <output_dir>/<song>/.
        parts: Subset of ALL_PARTS to transcribe (default: DEFAULT_PARTS, the band
            without vocals). 'vocals' adds the vocal melody with its lyrics.
        style: PDF style 'tab', 'standard' or 'both'.
        skip_separation: Reuse this song's existing stems instead of re-running Demucs.
        vram: Key of VRAM_PROFILES ('8gb' or '4gb'); None detects it from the GPU.
        lyrics: Recognize lyrics (Whisper) for the vocal melody.
        lyrics_language: Whisper language code ('ko', 'en', 'ja', ...); None detects it.
        log: Called with each log line.
        progress: Called as progress(status_text, fraction) at each stage.

    Returns:
        dict with song_title, song_dir, score_xml, pdf_path, lyrics_txt, bpm, downbeat,
        midi_paths, elapsed.
    """
    progress = progress or (lambda text, fraction: None)
    start_time = time.time()
    out_root = os.path.abspath(output_dir)
    os.makedirs(out_root, exist_ok=True)

    if parts is None:
        parts = DEFAULT_PARTS
    selected_parts = [p.strip().lower() for p in parts if p.strip()]
    if not selected_parts:
        selected_parts = ['lead']
        log("! 선택된 파트가 없어 기본값(리드 기타)으로 악보를 생성합니다.")

    if is_url(input_audio):
        progress("유튜브 음원 다운로드 및 변환 중 (최고 음질 WAV)...", 0.05)
        log(f"[*] 유튜브 링크 감지: {input_audio}")
        log("[*] yt-dlp & FFmpeg를 통해 최고 음질 오디오 추출 중...")
        input_path, video_title = download_youtube_audio(input_audio, os.path.join(out_root, "downloads"))
        log(f"✓ 다운로드 및 변환 완료: {video_title}")
        log(f"✓ 로컬 음원 파일: {input_path}")
    else:
        input_path = os.path.abspath(input_audio)
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"입력 파일을 찾을 수 없습니다: {input_path}")
        log(f"[*] 로컬 음원 변환 시작: {os.path.basename(input_path)}")

    song_title = Path(input_path).stem
    song_dir = os.path.join(out_root, song_title)
    stems_dir = os.path.join(song_dir, "stems")
    midi_dir = os.path.join(song_dir, "midi")
    score_dir = os.path.join(song_dir, "scores")
    for d in (stems_dir, midi_dir, score_dir):
        os.makedirs(d, exist_ok=True)

    vram_profile = VRAM_PROFILES[vram or detect_vram_profile()]

    log(f"[*] 출력 폴더: {song_dir}")
    log(f"[*] 선택된 파트: {', '.join(selected_parts)}")
    log(f"[*] GPU 메모리 모드: {vram_profile['label']}")

    # -------------------------------------------------------------
    # Step 1: Stem Separation (Demucs)
    # -------------------------------------------------------------
    drums_wav = os.path.join(stems_dir, "drums.wav")
    bass_wav = os.path.join(stems_dir, "bass.wav")
    guitar_wav = os.path.join(stems_dir, "guitar.wav")
    vocals_wav = os.path.join(stems_dir, "vocals.wav")

    needed = [drums_wav, bass_wav, guitar_wav] + ([vocals_wav] if "vocals" in selected_parts else [])
    if skip_separation and all(os.path.exists(p) for p in needed):
        log("\n--- [Step 1] 기존 분리 음원 재사용 (분리 건너뜀) ---")
    else:
        progress("1/4 단계: Meta Demucs 6-Stem 음원 분리 중 (GPU)...", 0.15)
        log("\n--- [Step 1] AI 음원 분리 (Demucs htdemucs_6s) ---")
        stems = separate_stems(input_path, song_dir, model_name="htdemucs_6s",
                               segment=vram_profile["demucs_segment"])
        drums_wav = stems.get("drums", drums_wav)
        bass_wav = stems.get("bass", bass_wav)
        guitar_wav = stems.get("guitar", guitar_wav)
        vocals_wav = stems.get("vocals", vocals_wav)
        log("✓ 분리 완료: 드럼, 베이스, 기타, 보컬 스템 생성됨")

    # -------------------------------------------------------------
    # Step 2: Split Guitar into Lead and Rhythm
    # -------------------------------------------------------------
    progress("2/4 단계: 리드 기타 / 리듬 기타 분할 중 (Mid-Side)...", 0.45)
    log("\n--- [Step 2] 기타 파트 분리 (Mid-Side 디코딩) ---")
    lead_wav, rhythm_wav = None, None
    if os.path.exists(guitar_wav):
        guitar_parts = split_guitar_track(guitar_wav, stems_dir)
        lead_wav = guitar_parts["lead"]
        rhythm_wav = guitar_parts["rhythm"]
        log(f"✓ 리드 기타(솔로/센터): {os.path.basename(lead_wav)}")
        log(f"✓ 리듬 기타(백킹/사이드): {os.path.basename(rhythm_wav)}")
    else:
        log("! 기타 트랙 없음 (건너뜀)")

    # -------------------------------------------------------------
    # Step 3: Transcription to MIDI
    # -------------------------------------------------------------
    progress("3/4 단계: 각 악기별 고정밀 MIDI 변환 중 (TorchCREPE & CQT Chords)...", 0.65)
    log("\n--- [Step 3] 고정밀 MIDI 전사 (TorchCREPE GPU & CQT Chords) ---")

    crepe_batch = vram_profile["crepe_batch_size"]
    jobs = [
        ("drums", drums_wav, "drums.mid", "드럼 트랙 분석 중 (Kick, Snare, Hi-Hat 적응형 온셋)...",
         lambda wav, mid: transcribe_drums(wav, mid)),
        ("bass", bass_wav, "bass.mid", "베이스 기타 심층 F0 추적 중 (TorchCREPE GPU 360-bin)...",
         lambda wav, mid: transcribe_pitch(wav, mid, instrument_name="bass", crepe_batch_size=crepe_batch)),
        ("lead", lead_wav, "lead_guitar.mid", "리드 기타 솔로 멜로디 추적 중 (TorchCREPE GPU)...",
         lambda wav, mid: transcribe_pitch(wav, mid, instrument_name="guitar_lead", crepe_batch_size=crepe_batch)),
        ("rhythm", rhythm_wav, "rhythm_guitar.mid", "리듬 기타 다성 화음 분석 중 (CQT 크로마 & 6줄 타브 폼)...",
         lambda wav, mid: transcribe_pitch(wav, mid, instrument_name="guitar_rhythm", crepe_batch_size=crepe_batch)),
        ("vocals", vocals_wav, "vocals.mid", "보컬 멜로디 추적 중 (TorchCREPE GPU)...",
         lambda wav, mid: transcribe_pitch(wav, mid, instrument_name="vocals", crepe_batch_size=crepe_batch)),
    ]

    # Only MIDIs written in this run go into the score, never leftovers on disk.
    midi_paths = {}
    for part, wav, midi_name, message, transcribe in jobs:
        if part not in selected_parts:
            continue
        if not wav or not os.path.exists(wav):
            log(f"  ! {part} 음원이 없어 건너뜁니다.")
            continue
        log(f"  - {message}")
        midi_path = os.path.join(midi_dir, midi_name)
        transcribe(wav, midi_path)
        midi_paths[part] = midi_path
        log(f"  ✓ {part} MIDI: {midi_name}")

    # Lyrics for the vocal melody (optional: the melody stands on its own if this fails)
    vocal_lyrics, lyrics_txt = None, None
    if "vocals" in midi_paths and lyrics:
        progress("가사 인식 중 (Whisper)...", 0.80)
        log("\n--- [Step 3+] 가사 인식 (Whisper) ---")
        try:
            lyrics_txt = os.path.join(score_dir, f"{song_title}_lyrics.txt")
            vocal_lyrics = recognize_lyrics(vocals_wav, midi_paths["vocals"], vram_profile,
                                            lyrics_language, lyrics_txt, log=log)
        except Exception as e:
            lyrics_txt = None
            log(f"  ! 가사 인식을 건너뜁니다 (멜로디 악보는 그대로 생성): {e}")

    # -------------------------------------------------------------
    # Step 4: Tablature Optimization & MusicXML / PDF
    # -------------------------------------------------------------
    progress("4/4 단계: 프렛보드 운지 최적화 및 맞춤 악보(MusicXML/PDF) 생성 중...", 0.90)
    log("\n--- [Step 4] 동적 계획법(DP) 타브 운지 최적화 & 조판 ---")

    bpm, downbeat = analyze_beats(input_path)
    log(f"  ✓ 곡 템포 자동 분석: {bpm:.1f} BPM, 첫 강박 {downbeat:.2f}초 (마디선 정렬)")

    part_suffix = "" if sorted(selected_parts) == sorted(DEFAULT_PARTS) else f"_{'_'.join(selected_parts)}"
    score_xml = os.path.join(score_dir, f"{song_title}{part_suffix}_score.musicxml")
    build_musicxml_score(
        drum_midi_path=midi_paths.get("drums", ""),
        bass_midi_path=midi_paths.get("bass", ""),
        lead_midi_path=midi_paths.get("lead", ""),
        rhythm_midi_path=midi_paths.get("rhythm", ""),
        output_xml_path=score_xml,
        song_title=song_title,
        selected_parts=selected_parts,
        bpm=bpm,
        downbeat=downbeat,
        vocal_midi_path=midi_paths.get("vocals", ""),
        lyrics=vocal_lyrics
    )
    log(f"  ✓ MusicXML 악보: {os.path.basename(score_xml)}")

    pdf_path = export_score_to_pdf(score_xml, style=style)
    if pdf_path:
        log(f"  📄 PDF 악보 생성 완료 ({style}): {os.path.basename(pdf_path)}")
    else:
        log("ℹ️ [알림] PDF를 만들지 못했습니다. LilyPond 설치를 확인하거나, MuseScore에서 [파일 > 내보내기 > PDF]로 저장할 수 있습니다.")

    elapsed = time.time() - start_time
    log("\n=======================================================")
    log(f"🎉 완료! 총 소요 시간: {elapsed:.1f}초")
    log(f"📁 결과 폴더: {song_dir}")
    log(f"🎼 총보 파일: {score_xml}")
    if pdf_path:
        log(f"📄 PDF 파일: {pdf_path}")
    log("=======================================================")

    return {
        "song_title": song_title,
        "song_dir": song_dir,
        "score_xml": score_xml,
        "pdf_path": pdf_path,
        "lyrics_txt": lyrics_txt,
        "bpm": bpm,
        "downbeat": downbeat,
        "midi_paths": midi_paths,
        "elapsed": elapsed,
    }
