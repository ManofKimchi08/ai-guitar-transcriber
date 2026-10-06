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


ALL_PARTS = ['drums', 'bass', 'rhythm', 'lead']

# GPU memory profiles. Peak activations measured per setting: htdemucs ~0.7 GB at
# its default 7.8 s segment (~0.5 GB at 5 s); TorchCREPE 'full' ~2.5 GB per 1024
# frames. Both steps also retry with smaller settings if the GPU still runs out.
VRAM_PROFILES = {
    "8gb": {"label": "8GB 이상 (표준)", "demucs_segment": None, "crepe_batch_size": 1024},
    "4gb": {"label": "4GB (저사양)", "demucs_segment": 5, "crepe_batch_size": 512},
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


def estimate_bpm(audio_path: str, default: float = 120.0) -> float:
    """Estimates the song tempo from its first 45 seconds, folding implausible values."""
    import numpy as np
    import librosa
    try:
        y, sr = librosa.load(audio_path, sr=22050, duration=45.0)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(np.atleast_1d(tempo)[0])
    except Exception:
        return default
    if bpm < 50:
        return default
    if bpm > 220:
        return bpm / 2.0
    return bpm


def run_pipeline(
    input_audio: str,
    output_dir: str,
    parts: list = None,
    style: str = "tab",
    skip_separation: bool = False,
    vram: str = None,
    log=print,
    progress=None
) -> dict:
    """
    Runs the full transcription pipeline for one song.

    Args:
        input_audio: Path to an audio file or a YouTube URL.
        output_dir: Root output folder; results go to <output_dir>/<song>/.
        parts: Subset of ALL_PARTS to transcribe (default: all).
        style: PDF style 'tab', 'standard' or 'both'.
        skip_separation: Reuse this song's existing stems instead of re-running Demucs.
        vram: Key of VRAM_PROFILES ('8gb' or '4gb'); None detects it from the GPU.
        log: Called with each log line.
        progress: Called as progress(status_text, fraction) at each stage.

    Returns:
        dict with song_title, song_dir, score_xml, pdf_path, bpm, midi_paths, elapsed.
    """
    progress = progress or (lambda text, fraction: None)
    start_time = time.time()
    out_root = os.path.abspath(output_dir)
    os.makedirs(out_root, exist_ok=True)

    if parts is None:
        parts = ALL_PARTS
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

    if skip_separation and all(os.path.exists(p) for p in (drums_wav, bass_wav, guitar_wav)):
        log("\n--- [Step 1] 기존 분리 음원 재사용 (분리 건너뜀) ---")
    else:
        progress("1/4 단계: Meta Demucs 6-Stem 음원 분리 중 (GPU)...", 0.15)
        log("\n--- [Step 1] AI 음원 분리 (Demucs htdemucs_6s) ---")
        stems = separate_stems(input_path, song_dir, model_name="htdemucs_6s",
                               segment=vram_profile["demucs_segment"])
        drums_wav = stems.get("drums", drums_wav)
        bass_wav = stems.get("bass", bass_wav)
        guitar_wav = stems.get("guitar", guitar_wav)
        log("✓ 분리 완료: 드럼, 베이스, 기타 스템 생성됨")

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

    # -------------------------------------------------------------
    # Step 4: Tablature Optimization & MusicXML / PDF
    # -------------------------------------------------------------
    progress("4/4 단계: 프렛보드 운지 최적화 및 맞춤 악보(MusicXML/PDF) 생성 중...", 0.90)
    log("\n--- [Step 4] 동적 계획법(DP) 타브 운지 최적화 & 조판 ---")

    bpm = estimate_bpm(input_path)
    log(f"  ✓ 곡 템포 자동 분석: {bpm:.1f} BPM")

    part_suffix = "" if len(selected_parts) == len(ALL_PARTS) else f"_{'_'.join(selected_parts)}"
    score_xml = os.path.join(score_dir, f"{song_title}{part_suffix}_score.musicxml")
    build_musicxml_score(
        drum_midi_path=midi_paths.get("drums", ""),
        bass_midi_path=midi_paths.get("bass", ""),
        lead_midi_path=midi_paths.get("lead", ""),
        rhythm_midi_path=midi_paths.get("rhythm", ""),
        output_xml_path=score_xml,
        song_title=song_title,
        selected_parts=selected_parts,
        bpm=bpm
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
        "bpm": bpm,
        "midi_paths": midi_paths,
        "elapsed": elapsed,
    }
