"""
AI Band Transcriber - Modern Desktop GUI
Built with CustomTkinter for Windows.
Provides one-click audio selection, GPU status, progress bar, real-time logs,
and direct opening of results in Windows Explorer / MuseScore.
"""

import os
import sys
import glob
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

# Add project root to sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from src.pipeline import run_pipeline, is_url, VRAM_PROFILES, vram_profile_for
from src.fretboard_gui import FretboardEditorDialog

OUTPUT_ROOT = os.path.join(PROJECT_DIR, "output")

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
        self.default_vram = "8gb"
        try:
            import torch
            if torch.cuda.is_available():
                self.has_cuda = True
                vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
                self.default_vram = vram_profile_for(vram_gb)
                self.gpu_name = f"NVIDIA {torch.cuda.get_device_name(0)} · VRAM {vram_gb:.1f}GB (CUDA 가속 활성)"
        except Exception:
            pass

        self.is_running = False
        self.last_score_path = None
        self.last_pdf_path = None
        self.last_output_dir = None

        # Worker thread -> UI thread messages (Tk widgets must only be touched by the UI thread)
        self._ui_queue = queue.Queue()

        self._build_ui()
        self._restore_recent_scores()

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

        self.entry_output = ctk.CTkEntry(files_frame, width=520)
        self.entry_output.insert(0, OUTPUT_ROOT)
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

        lbl_vram = ctk.CTkLabel(opt_frame, text="GPU 메모리 (VRAM):", font=ctk.CTkFont(weight="bold"))
        lbl_vram.pack(anchor="w", padx=15, pady=(2, 4))

        self.seg_vram = ctk.CTkSegmentedButton(
            opt_frame,
            values=[profile["label"] for profile in VRAM_PROFILES.values()]
        )
        self.seg_vram.set(VRAM_PROFILES[self.default_vram]["label"])
        self.seg_vram.pack(fill="x", padx=15, pady=(0, 10))

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

    def _selected_parts(self) -> list:
        parts = []
        if self.chk_drums.get(): parts.append('drums')
        if self.chk_bass.get(): parts.append('bass')
        if self.chk_rhythm.get(): parts.append('rhythm')
        if self.chk_lead.get(): parts.append('lead')
        return parts

    def _selected_style(self) -> str:
        chosen_style = self.seg_style.get()
        if "둘 다" in chosen_style:
            return "both"
        if "오선보" in chosen_style:
            return "standard"
        return "tab"

    def _selected_vram(self) -> str:
        chosen = self.seg_vram.get()
        return next((key for key, profile in VRAM_PROFILES.items() if profile["label"] == chosen),
                    self.default_vram)

    def _start_pipeline_thread(self):
        if self.is_running:
            return

        input_audio = self.entry_input.get().strip()
        output_dir = self.entry_output.get().strip()

        if not input_audio or (not is_url(input_audio) and not os.path.exists(input_audio)):
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

        # Read widget state here, on the UI thread; the worker only gets plain values.
        parts = self._selected_parts()
        style = self._selected_style()
        vram = self._selected_vram()

        thread = threading.Thread(target=self._run_pipeline, args=(input_audio, output_dir, parts, style, vram),
                                  daemon=True)
        thread.start()
        self.after(100, self._drain_ui_queue)

    def _run_pipeline(self, input_audio: str, output_dir: str, parts: list, style: str, vram: str):
        """Worker thread: never touches Tk widgets, only posts messages to the UI queue."""
        post = self._ui_queue.put
        try:
            result = run_pipeline(
                input_audio, output_dir, parts=parts, style=style, vram=vram,
                log=lambda text: post(("log", text)),
                progress=lambda text, fraction: post(("status", text, fraction))
            )
            post(("done", result))
        except Exception as e:
            post(("error", e, traceback.format_exc()))

    def _drain_ui_queue(self):
        """Applies worker messages on the UI thread, polling until the run ends."""
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._log(msg[1])
                elif kind == "status":
                    self._set_status(msg[1], msg[2])
                elif kind == "done":
                    self._on_pipeline_done(msg[1])
                elif kind == "error":
                    self._on_pipeline_error(msg[1], msg[2])
        except queue.Empty:
            pass
        if self.is_running:
            self.after(100, self._drain_ui_queue)

    def _on_pipeline_done(self, result: dict):
        self.is_running = False
        self.btn_run.configure(state="normal", text="🚀 악보 및 타브 생성 시작")
        self._set_status(f"🎉 모든 작업 완료! ({result['elapsed']:.1f}초 소요)", 1.0)

        self.last_score_path = result["score_xml"]
        self.last_output_dir = result["song_dir"]
        # Never leave the PDF button pointing at another song's PDF
        self.last_pdf_path = result["pdf_path"]
        if self.last_pdf_path:
            self.btn_open_pdf.configure(state="normal")

        self.lbl_active_score.configure(
            text=f"🎼 선택된 악보: {os.path.basename(self.last_score_path)}",
            text_color="#34C759"
        )
        self.btn_open_folder.configure(state="normal")
        self.btn_open_score.configure(state="normal")

    def _on_pipeline_error(self, error: Exception, tb_text: str):
        self.is_running = False
        self.btn_run.configure(state="normal", text="🚀 악보 및 타브 생성 시작")
        self._set_status("❌ 오류가 발생했습니다. 로그를 확인해 주세요.", 0.0)
        self._log(f"\n[!] 오류 발생: {error}")
        self._log(tb_text)
        messagebox.showerror("변환 오류", f"처리 중 오류가 발생했습니다:\n{error}")

    def _restore_recent_scores(self):
        """
        Auto-detect the most recent MusicXML and PDF scores under output/ on app startup
        so that buttons are immediately ready to use even after restarting the application.
        Scores live in output/<song>/scores/ (older versions wrote output/scores/).
        """
        output_dir = OUTPUT_ROOT

        # 1. Output folder button is enabled whenever output directory exists
        if os.path.exists(output_dir):
            self.last_output_dir = output_dir
            self.btn_open_folder.configure(state="normal")

        # 2. Check for existing MusicXML scores
        xml_files = []
        for scores_dir in (os.path.join(output_dir, "*", "scores"), os.path.join(output_dir, "scores")):
            for ext in ("*.musicxml", "*.xml"):
                xml_files.extend(glob.glob(os.path.join(scores_dir, ext)))
        if not xml_files:
            return

        latest_xml = max(xml_files, key=os.path.getmtime)
        scores_dir = os.path.dirname(latest_xml)
        self.last_score_path = latest_xml
        self.btn_open_score.configure(state="normal")
        if os.path.basename(scores_dir) == "scores" and os.path.dirname(scores_dir) != output_dir:
            self.last_output_dir = os.path.dirname(scores_dir)

        # Check for corresponding PDF
        base_no_ext = os.path.splitext(latest_xml)[0]
        candidates = [
            f"{base_no_ext}_TAB.pdf",
            f"{base_no_ext}_TAB_Score.pdf",
            f"{base_no_ext}.pdf",
        ]
        latest_pdf = next((c for c in candidates if os.path.exists(c)), None)
        if not latest_pdf:
            pdf_files = glob.glob(os.path.join(scores_dir, "*.pdf"))
            if pdf_files:
                latest_pdf = max(pdf_files, key=os.path.getmtime)

        if latest_pdf:
            self.last_pdf_path = latest_pdf
            self.btn_open_pdf.configure(state="normal")

        self.lbl_active_score.configure(
            text=f"🎼 최근 악보: {os.path.basename(latest_xml)}",
            text_color="#34C759"
        )

    def _open_folder(self):
        target_dir = self.last_output_dir or OUTPUT_ROOT
        if os.path.exists(target_dir):
            os.startfile(target_dir)
        else:
            messagebox.showinfo("알림", "아직 생성된 결과 폴더가 없습니다.")

    def _open_score(self):
        score_path = self.last_score_path
        default_scores = self.last_output_dir or OUTPUT_ROOT

        # If no score selected yet, prompt user to choose one
        if not score_path or not os.path.exists(score_path):
            chosen = filedialog.askopenfilename(
                title="열람할 악보(MusicXML) 파일을 선택하세요",
                initialdir=default_scores if os.path.exists(default_scores) else PROJECT_DIR,
                filetypes=[("MusicXML 악보 (*.musicxml, *.xml)", "*.musicxml *.xml"), ("모든 파일 (*.*)", "*.*")]
            )
            if chosen and os.path.exists(chosen):
                score_path = chosen
                self.last_score_path = chosen
                self.btn_open_score.configure(state="normal")
                self.lbl_active_score.configure(
                    text=f"🎼 선택된 악보: {os.path.basename(chosen)}",
                    text_color="#34C759"
                )
            else:
                return

        try:
            os.startfile(score_path)
        except Exception as e:
            messagebox.showinfo("알림", f"파일을 열 수 없습니다:\n{e}\n\nMuseScore 4 또는 TuxGuitar를 먼저 설치해 주세요.")

    def _open_pdf(self):
        pdf_path = self.last_pdf_path
        default_scores = self.last_output_dir or OUTPUT_ROOT

        # If no PDF selected yet, prompt user to choose one
        if not pdf_path or not os.path.exists(pdf_path):
            chosen = filedialog.askopenfilename(
                title="열람할 PDF 악보 파일을 선택하세요",
                initialdir=default_scores if os.path.exists(default_scores) else PROJECT_DIR,
                filetypes=[("PDF 파일 (*.pdf)", "*.pdf"), ("모든 파일 (*.*)", "*.*")]
            )
            if chosen and os.path.exists(chosen):
                pdf_path = chosen
                self.last_pdf_path = chosen
                self.btn_open_pdf.configure(state="normal")
            else:
                return

        try:
            os.startfile(pdf_path)
        except Exception as e:
            messagebox.showinfo("알림", f"PDF 파일을 열 수 없습니다:\n{e}")

    def _open_fret_editor(self):
        score_path = self.last_score_path
        default_scores = self.last_output_dir or OUTPUT_ROOT
        
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
        self.btn_open_score.configure(state="normal")

        # Also link matching PDF if available
        base_no_ext = os.path.splitext(score_path)[0]
        for c in [f"{base_no_ext}_TAB.pdf", f"{base_no_ext}.pdf"]:
            if os.path.exists(c):
                self.last_pdf_path = c
                self.btn_open_pdf.configure(state="normal")
                break

        self.lbl_active_score.configure(
            text=f"🎼 선택된 악보: {os.path.basename(score_path)}",
            text_color="#34C759"
        )
        self._log(f"\n🎸 프렛보드 편집기 실행: {os.path.basename(score_path)}")

        FretboardEditorDialog(self, xml_path=score_path, pdf_callback=self._on_pdf_reexported, style=self._selected_style())

    def _on_pdf_reexported(self, new_pdf_path):
        self.last_pdf_path = new_pdf_path
        self.btn_open_pdf.configure(state="normal")
        self._log(f"📄 프렛보드 편집기를 통해 PDF 악보가 갱신되었습니다: {os.path.basename(new_pdf_path)}")


def main():
    app = BandTranscriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()
