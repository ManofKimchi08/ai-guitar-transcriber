"""
AI Band Transcriber - Modern Desktop GUI
Built with CustomTkinter for Windows.
Provides one-click audio selection, GPU status, progress bar, real-time logs,
and direct opening of results in Windows Explorer / MuseScore.
"""

import os
import sys
import threading
import time
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

# Add project root to sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from src.separator import separate_stems
from src.guitar_splitter import split_guitar_track
from src.drum_transcriber import transcribe_drums
from src.pitch_transcriber import transcribe_pitch
from src.tab_builder import build_musicxml_score
from src.youtube_downloader import download_youtube_audio
from src.pdf_exporter import export_score_to_pdf
from src.fretboard_gui import FretboardEditorDialog

# Set CustomTkinter theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class BandTranscriberApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("AI Band Transcriber & TAB Generator")
        self.geometry("860x780")
        self.minsize(640, 640)

        # Check GPU
        self.gpu_name = "CPU Only"
        self.has_cuda = False
        try:
            import torch
            if torch.cuda.is_available():
                self.has_cuda = True
                self.gpu_name = f"NVIDIA {torch.cuda.get_device_name(0)} (CUDA 가속 활성)"
        except Exception:
            pass

        self.is_running = False
        self.last_score_path = None
        self.last_pdf_path = None
        self.last_output_dir = None

        self._build_ui()

    def _build_ui(self):
        # 1. Bottom Button Row (Open Output / Open Score / PDF / Fret Editor)
        # CRITICAL: Docked permanently at the bottom so it can NEVER be pushed off-screen or cut off!
        bottom_frame = ctk.CTkFrame(self, corner_radius=10)
        bottom_frame.pack(side="bottom", fill="x", padx=16, pady=(4, 10))
        bottom_frame.grid_columnconfigure(0, weight=1)
        bottom_frame.grid_columnconfigure(1, weight=1)

        self.lbl_active_score = ctk.CTkLabel(
            bottom_frame,
            text="🎼 선택된 악보: (변환 완료 시 또는 [타브 운지 수정] 클릭 시 파일 선택)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="gray75"
        )
        self.lbl_active_score.grid(row=0, column=0, columnspan=2, padx=12, pady=(6, 2), sticky="w")

        self.btn_open_folder = ctk.CTkButton(
            bottom_frame,
            text="📁 결과 폴더 열기",
            state="disabled",
            height=38,
            command=self._open_folder
        )
        self.btn_open_folder.grid(row=1, column=0, padx=6, pady=(2, 4), sticky="ew")

        self.btn_open_score = ctk.CTkButton(
            bottom_frame,
            text="🎼 MuseScore / 악보 프로그램으로 열기",
            state="disabled",
            height=38,
            fg_color="#2B7DE9",
            hover_color="#1F5FC2",
            command=self._open_score
        )
        self.btn_open_score.grid(row=1, column=1, padx=6, pady=(2, 4), sticky="ew")

        self.btn_open_pdf = ctk.CTkButton(
            bottom_frame,
            text="📄 PDF 악보 열기",
            state="disabled",
            height=38,
            fg_color="#E05252",
            hover_color="#B83A3A",
            command=self._open_pdf
        )
        self.btn_open_pdf.grid(row=2, column=0, padx=6, pady=(2, 8), sticky="ew")

        self.btn_fret_editor = ctk.CTkButton(
            bottom_frame,
            text="🎸 타브 운지 수정 (Fretboard Editor)",
            height=38,
            fg_color="#107C41",
            hover_color="#0B5C30",
            command=self._open_fret_editor
        )
        self.btn_fret_editor.grid(row=2, column=1, padx=6, pady=(2, 8), sticky="ew")

        # 2. Main Scrollable Container (Takes all remaining vertical space, scrolls smoothly)
        scroll_body = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")
        scroll_body.pack(side="top", fill="both", expand=True, padx=8, pady=(4, 2))

        # 1. Header Frame
        header_frame = ctk.CTkFrame(scroll_body, corner_radius=10)
        header_frame.pack(fill="x", padx=10, pady=(4, 6))

        title_label = ctk.CTkLabel(
            header_frame,
            text="🎸 AI Band Transcriber",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        title_label.pack(anchor="w", padx=16, pady=(10, 2))

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="음원을 드럼, 베이스, 리드 기타, 리듬 기타로 분리하고 오선보/타브(TAB) 악보를 자동 생성합니다.",
            font=ctk.CTkFont(size=12),
            text_color="gray75"
        )
        subtitle_label.pack(anchor="w", padx=16, pady=(0, 4))

        gpu_status_color = "#34C759" if self.has_cuda else "#FF9500"
        gpu_label = ctk.CTkLabel(
            header_frame,
            text=f"⚡ 연산 장치: {self.gpu_name}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=gpu_status_color
        )
        gpu_label.pack(anchor="w", padx=16, pady=(0, 8))

        # 2. File Selection Frame
        files_frame = ctk.CTkFrame(scroll_body, corner_radius=10)
        files_frame.pack(fill="x", padx=10, pady=5)

        lbl_in = ctk.CTkLabel(files_frame, text="음원 파일 / 유튜브 URL:", font=ctk.CTkFont(weight="bold"))
        lbl_in.grid(row=0, column=0, sticky="w", padx=15, pady=(10, 4))

        self.entry_input = ctk.CTkEntry(
            files_frame,
            placeholder_text="음악 파일(MP3/WAV/FLAC) 선택 또는 유튜브 링크(https://www.youtube.com/...) 붙여넣기",
            width=520
        )
        self.entry_input.grid(row=0, column=1, padx=10, pady=(10, 4), sticky="ew")

        btn_browse_in = ctk.CTkButton(files_frame, text="파일 찾기...", width=100, command=self._browse_input)
        btn_browse_in.grid(row=0, column=2, padx=15, pady=(10, 4))

        lbl_out = ctk.CTkLabel(files_frame, text="저장 폴더 선택:", font=ctk.CTkFont(weight="bold"))
        lbl_out.grid(row=1, column=0, sticky="w", padx=15, pady=(6, 10))

        default_out = os.path.join(PROJECT_DIR, "output")
        self.entry_output = ctk.CTkEntry(files_frame, width=520)
        self.entry_output.insert(0, default_out)
        self.entry_output.grid(row=1, column=1, padx=10, pady=(6, 10), sticky="ew")

        btn_browse_out = ctk.CTkButton(files_frame, text="폴더 찾기...", width=100, command=self._browse_output)
        btn_browse_out.grid(row=1, column=2, padx=15, pady=(6, 10))

        files_frame.grid_columnconfigure(1, weight=1)

        # 3. Options Frame
        opt_frame = ctk.CTkFrame(scroll_body, corner_radius=10)
        opt_frame.pack(fill="x", padx=10, pady=5)

        lbl_opt = ctk.CTkLabel(opt_frame, text="생성할 악기 파트:", font=ctk.CTkFont(weight="bold"))
        lbl_opt.pack(anchor="w", padx=15, pady=(8, 4))

        chk_container = ctk.CTkFrame(opt_frame, fg_color="transparent")
        chk_container.pack(fill="x", padx=15, pady=(0, 8))

        self.chk_drums = ctk.CTkCheckBox(chk_container, text="🥁 드럼 (Drums)")
        self.chk_drums.select()
        self.chk_drums.pack(side="left", padx=(0, 18))

        self.chk_bass = ctk.CTkCheckBox(chk_container, text="🎸 베이스 (Bass TAB)")
        self.chk_bass.select()
        self.chk_bass.pack(side="left", padx=18)

        self.chk_lead = ctk.CTkCheckBox(chk_container, text="🎸 리드 기타 (Lead TAB)")
        self.chk_lead.select()
        self.chk_lead.pack(side="left", padx=18)

        self.chk_rhythm = ctk.CTkCheckBox(chk_container, text="🎸 리듬 기타 (Rhythm TAB)")
        self.chk_rhythm.select()
        self.chk_rhythm.pack(side="left", padx=18)

        lbl_style = ctk.CTkLabel(opt_frame, text="악보 출력 형태 (PDF):", font=ctk.CTkFont(weight="bold"))
        lbl_style.pack(anchor="w", padx=15, pady=(2, 4))

        self.seg_style = ctk.CTkSegmentedButton(
            opt_frame,
            values=["🎸 타브 악보만 (TAB)", "🎼 오선보만 (Notation)", "📄 둘 다 생성 (TAB + 오선보)"]
        )
        self.seg_style.set("🎸 타브 악보만 (TAB)")
        self.seg_style.pack(fill="x", padx=15, pady=(0, 10))

        # 4. Action & Progress Frame
        action_frame = ctk.CTkFrame(scroll_body, corner_radius=10)
        action_frame.pack(fill="x", padx=10, pady=5)

        self.btn_run = ctk.CTkButton(
            action_frame,
            text="🚀 악보 및 타브 생성 시작",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=42,
            command=self._start_pipeline_thread
        )
        self.btn_run.pack(fill="x", padx=20, pady=(10, 6))

        self.lbl_status = ctk.CTkLabel(action_frame, text="준비 완료. 변환할 음악 파일을 선택해 주세요.", font=ctk.CTkFont(size=13))
        self.lbl_status.pack(anchor="w", padx=20, pady=(0, 4))

        self.prog_bar = ctk.CTkProgressBar(action_frame, height=14)
        self.prog_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.prog_bar.set(0)

        # 5. Log & Console Output Frame
        log_frame = ctk.CTkFrame(scroll_body, corner_radius=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        lbl_log = ctk.CTkLabel(log_frame, text="실시간 처리 로그:", font=ctk.CTkFont(weight="bold"))
        lbl_log.pack(anchor="w", padx=15, pady=(6, 2))

        self.txt_log = ctk.CTkTextbox(log_frame, height=120, font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_log.pack(fill="both", expand=True, padx=15, pady=(0, 8))

    def _browse_input(self):
        filetypes = [
            ("오디오 파일 (*.mp3, *.wav, *.flac, *.m4a, *.ogg)", "*.mp3 *.wav *.flac *.m4a *.ogg"),
            ("모든 파일 (*.*)", "*.*")
        ]
        chosen = filedialog.askopenfilename(title="변환할 음원 파일 선택", filetypes=filetypes)
        if chosen:
            self.entry_input.delete(0, "end")
            self.entry_input.insert(0, os.path.normpath(chosen))

    def _browse_output(self):
        chosen = filedialog.askdirectory(title="저장할 폴더 선택")
        if chosen:
            self.entry_output.delete(0, "end")
            self.entry_output.insert(0, os.path.normpath(chosen))

    def _log(self, text: str):
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")

    def _set_status(self, text: str, progress: float):
        self.lbl_status.configure(text=text)
        self.prog_bar.set(progress)

    def _start_pipeline_thread(self):
        if self.is_running:
            return

        input_audio = self.entry_input.get().strip()
        output_dir = self.entry_output.get().strip()

        is_url = input_audio.startswith("http://") or input_audio.startswith("https://")
        if not input_audio or (not is_url and not os.path.exists(input_audio)):
            messagebox.showerror("오류", "유효한 음원 파일 경로를 선택하거나 유튜브 링크(URL)를 입력해 주세요.")
            return

        if not output_dir:
            messagebox.showerror("오류", "결과를 저장할 폴더 경로를 입력해 주세요.")
            return

        self.is_running = True
        self.btn_run.configure(state="disabled", text="⏳ 변환 처리 중...")
        self.btn_open_folder.configure(state="disabled")
        self.btn_open_score.configure(state="disabled")
        self.txt_log.delete("1.0", "end")

        thread = threading.Thread(target=self._run_pipeline, args=(input_audio, output_dir), daemon=True)
        thread.start()

    def _run_pipeline(self, input_audio: str, output_dir: str):
        start_time = time.time()
        stems_dir = os.path.join(output_dir, "stems")
        midi_dir = os.path.join(output_dir, "midi")
        score_dir = os.path.join(output_dir, "scores")

        os.makedirs(stems_dir, exist_ok=True)
        os.makedirs(midi_dir, exist_ok=True)
        os.makedirs(score_dir, exist_ok=True)

        try:
            is_url = input_audio.startswith("http://") or input_audio.startswith("https://")
            if is_url:
                self._set_status("유튜브 음원 다운로드 및 변환 중 (최고 음질 WAV)...", 0.05)
                self._log(f"[*] 유튜브 링크 감지: {input_audio}")
                self._log("[*] yt-dlp & FFmpeg를 통해 최고 음질 오디오 추출 중...")
                local_wav, video_title = download_youtube_audio(input_audio, output_dir)
                input_audio = local_wav
                song_title = Path(input_audio).stem
                self._log(f"✓ 다운로드 및 변환 완료: {video_title}")
                self._log(f"✓ 로컬 음원 파일: {local_wav}\n")
            else:
                song_title = Path(input_audio).stem
                self._log(f"[*] 로컬 음원 변환 시작: {os.path.basename(input_audio)}")

            self._log(f"[*] 출력 폴더: {output_dir}")

            # ----------------------------------------------------
            # Step 1: Demucs 6-Stem Separation
            # ----------------------------------------------------
            self._set_status("1/4 단계: Meta Demucs 6-Stem 음원 분리 중 (GPU)...", 0.15)
            self._log("\n--- [Step 1] AI 음원 분리 (Demucs htdemucs_6s) ---")
            
            stems = separate_stems(input_audio, output_dir, model_name="htdemucs_6s")
            drums_wav = stems.get("drums", os.path.join(stems_dir, "drums.wav"))
            bass_wav = stems.get("bass", os.path.join(stems_dir, "bass.wav"))
            guitar_wav = stems.get("guitar", os.path.join(stems_dir, "guitar.wav"))

            self._log(f"✓ 분리 완료: 드럼, 베이스, 기타 스템 생성됨")
            self.prog_bar.set(0.40)

            # ----------------------------------------------------
            # Step 2: Guitar Split (Lead vs Rhythm)
            # ----------------------------------------------------
            self._set_status("2/4 단계: 리드 기타 / 리듬 기타 분할 중 (Mid-Side)...", 0.45)
            self._log("\n--- [Step 2] 기타 파트 분리 (Mid-Side 디코딩) ---")
            
            lead_wav, rhythm_wav = None, None
            if os.path.exists(guitar_wav):
                guitar_parts = split_guitar_track(guitar_wav, stems_dir)
                lead_wav = guitar_parts["lead"]
                rhythm_wav = guitar_parts["rhythm"]
                self._log(f"✓ 리드 기타(솔로/센터): {os.path.basename(lead_wav)}")
                self._log(f"✓ 리듬 기타(백킹/사이드): {os.path.basename(rhythm_wav)}")
            else:
                self._log("! 기타 트랙 없음 (건너뜀)")

            self.prog_bar.set(0.60)

            # ----------------------------------------------------
            # Step 3: MIDI Transcription
            # ----------------------------------------------------
            self._set_status("3/4 단계: 각 악기별 고정밀 MIDI 변환 중 (TorchCREPE & CQT Chords)...", 0.65)
            self._log("\n--- [Step 3] 고정밀 MIDI 전사 (TorchCREPE GPU & CQT Chords) ---")

            drum_midi = os.path.join(midi_dir, "drums.mid")
            if self.chk_drums.get() and os.path.exists(drums_wav):
                self._log("  - 드럼 트랙 분석 중 (Kick, Snare, Hi-Hat 적응형 온셋)...")
                transcribe_drums(drums_wav, drum_midi)
                self._log(f"  ✓ 드럼 MIDI: {os.path.basename(drum_midi)}")

            bass_midi = os.path.join(midi_dir, "bass.mid")
            if self.chk_bass.get() and os.path.exists(bass_wav):
                self._log("  - 베이스 기타 심층 F0 추적 중 (TorchCREPE GPU 360-bin)...")
                transcribe_pitch(bass_wav, bass_midi, instrument_name="bass")
                self._log(f"  ✓ 베이스 MIDI: {os.path.basename(bass_midi)}")

            lead_midi = os.path.join(midi_dir, "lead_guitar.mid")
            if self.chk_lead.get() and lead_wav and os.path.exists(lead_wav):
                self._log("  - 리드 기타 솔로 멜로디 추적 중 (TorchCREPE GPU)...")
                transcribe_pitch(lead_wav, lead_midi, instrument_name="guitar_lead")
                self._log(f"  ✓ 리드 기타 MIDI: {os.path.basename(lead_midi)}")

            rhythm_midi = os.path.join(midi_dir, "rhythm_guitar.mid")
            if self.chk_rhythm.get() and rhythm_wav and os.path.exists(rhythm_wav):
                self._log("  - 리듬 기타 다성 화음 분석 중 (CQT 크로마 & 6줄 타브 폼)...")
                transcribe_pitch(rhythm_wav, rhythm_midi, instrument_name="guitar_rhythm")
                self._log(f"  ✓ 리듬 기타 MIDI: {os.path.basename(rhythm_midi)}")

            self.prog_bar.set(0.85)

            # ----------------------------------------------------
            # Step 4: TAB Optimization & MusicXML
            # ----------------------------------------------------
            self._set_status("4/4 단계: 프렛보드 운지 최적화 및 맞춤 악보(MusicXML/PDF) 생성 중...", 0.90)
            self._log("\n--- [Step 4] 동적 계획법(DP) 타브 운지 최적화 & 조판 ---")

            # Collect selected instrument parts
            selected_parts = []
            if self.chk_drums.get(): selected_parts.append('drums')
            if self.chk_bass.get(): selected_parts.append('bass')
            if self.chk_rhythm.get(): selected_parts.append('rhythm')
            if self.chk_lead.get(): selected_parts.append('lead')
            if not selected_parts:
                selected_parts = ['lead']
                self._log("! 선택된 파트가 없어 기본값(리드 기타)으로 악보를 생성합니다.")

            # Dynamic BPM tracking for precision measure & beat sync
            import librosa
            try:
                y_bpm, sr_bpm = librosa.load(input_audio, sr=22050, duration=45.0)
                detected_tempo, _ = librosa.beat.beat_track(y=y_bpm, sr=sr_bpm)
                bpm_val = float(detected_tempo)
                if hasattr(bpm_val, '__len__'):
                    bpm_val = float(bpm_val[0])
                if bpm_val < 50:
                    bpm_val = 120.0
                elif bpm_val > 220:
                    bpm_val = bpm_val / 2.0
            except Exception:
                bpm_val = 120.0

            self._log(f"  ✓ 곡 템포 자동 분석: {bpm_val:.1f} BPM (정밀 박자/마디 동기화 적용)")
            self._log(f"  ✓ 악보 포함 파트: {', '.join(selected_parts)}")

            part_suffix = "" if len(selected_parts) == 4 else f"_{'_'.join(selected_parts)}"
            full_score_xml = os.path.join(score_dir, f"{song_title}{part_suffix}_score.musicxml")
            build_musicxml_score(
                drum_midi_path=drum_midi if 'drums' in selected_parts else "",
                bass_midi_path=bass_midi if 'bass' in selected_parts else "",
                lead_midi_path=lead_midi if 'lead' in selected_parts else "",
                rhythm_midi_path=rhythm_midi if 'rhythm' in selected_parts else "",
                output_xml_path=full_score_xml,
                song_title=song_title,
                selected_parts=selected_parts,
                bpm=bpm_val
            )

            # Headless PDF Export via LilyPond
            chosen_style = self.seg_style.get()
            if "둘 다" in chosen_style:
                st = "both"
            elif "오선보" in chosen_style:
                st = "standard"
            else:
                st = "tab"

            pdf_path = export_score_to_pdf(full_score_xml, style=st)
            if pdf_path:
                self._log(f"  📄 PDF 악보 생성 완료 ({chosen_style}): {os.path.basename(pdf_path)}")
                self.last_pdf_path = pdf_path
                self.btn_open_pdf.configure(state="normal")
            else:
                self._log("ℹ️ [알림] PDF 생성 엔진 오류 시 'MuseScore로 열기'를 통해 직접 [파일 > 내보내기 > PDF]로도 즉시 저장 가능합니다.")

            elapsed = time.time() - start_time
            self._set_status(f"🎉 모든 작업 완료! ({elapsed:.1f}초 소요)", 1.0)
            self._log(f"\n=======================================================")
            self._log(f"🎉 성공적으로 완성되었습니다! 총 소요 시간: {elapsed:.1f}초")
            self._log(f"📁 총보 파일: {full_score_xml}")
            if self.last_pdf_path:
                self._log(f"📄 PDF 파일: {self.last_pdf_path}")
            self._log(f"=======================================================")

            self.last_score_path = full_score_xml
            self.last_output_dir = output_dir

            # Update selected score label
            self.lbl_active_score.configure(
                text=f"🎼 선택된 악보: {os.path.basename(full_score_xml)}",
                text_color="#34C759"
            )

            self.btn_open_folder.configure(state="normal")
            self.btn_open_score.configure(state="normal")

        except Exception as e:
            self._set_status("❌ 오류가 발생했습니다. 로그를 확인해 주세요.", 0.0)
            self._log(f"\n[!] 오류 발생: {str(e)}")
            import traceback
            self._log(traceback.format_exc())
            messagebox.showerror("변환 오류", f"처리 중 오류가 발생했습니다:\n{str(e)}")
        finally:
            self.is_running = False
            self.btn_run.configure(state="normal", text="🚀 악보 및 타브 생성 시작")

    def _open_folder(self):
        if self.last_output_dir and os.path.exists(self.last_output_dir):
            os.startfile(self.last_output_dir)

    def _open_score(self):
        if self.last_score_path and os.path.exists(self.last_score_path):
            try:
                os.startfile(self.last_score_path)
            except Exception as e:
                messagebox.showinfo("알림", f"파일을 열 수 없습니다:\n{e}\n\nMuseScore 4 또는 TuxGuitar를 먼저 설치해 주세요.")

    def _open_pdf(self):
        if self.last_pdf_path and os.path.exists(self.last_pdf_path):
            try:
                os.startfile(self.last_pdf_path)
            except Exception as e:
                messagebox.showinfo("알림", f"PDF 파일을 열 수 없습니다:\n{e}")

    def _open_fret_editor(self):
        score_path = self.last_score_path
        default_scores = os.path.join(PROJECT_DIR, "output", "scores")
        
        # If no score was generated in current session, ask user to select one
        if not score_path or not os.path.exists(score_path):
            chosen = filedialog.askopenfilename(
                title="운지를 수정할 타브 악보(MusicXML) 파일을 선택하세요",
                initialdir=default_scores if os.path.exists(default_scores) else PROJECT_DIR,
                filetypes=[("MusicXML 악보 (*.musicxml, *.xml)", "*.musicxml *.xml"), ("모든 파일 (*.*)", "*.*")]
            )
            if chosen and os.path.exists(chosen):
                score_path = chosen
            else:
                return

        self.last_score_path = score_path
        self.lbl_active_score.configure(
            text=f"🎼 선택된 악보: {os.path.basename(score_path)}",
            text_color="#34C759"
        )
        self._log(f"\n🎸 프렛보드 편집기 실행: {os.path.basename(score_path)}")

        chosen_style = self.seg_style.get()
        st = "both" if "둘 다" in chosen_style else ("standard" if "오선보" in chosen_style else "tab")
        FretboardEditorDialog(self, xml_path=score_path, pdf_callback=self._on_pdf_reexported, style=st)

    def _on_pdf_reexported(self, new_pdf_path):
        self.last_pdf_path = new_pdf_path
        self.btn_open_pdf.configure(state="normal")
        self._log(f"📄 프렛보드 편집기를 통해 PDF 악보가 갱신되었습니다: {os.path.basename(new_pdf_path)}")


def main():
    app = BandTranscriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()
