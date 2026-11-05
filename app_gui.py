"""
AI Band Transcriber - Modern Desktop GUI
Built with CustomTkinter for Windows.
Provides one-click audio selection, GPU status, progress bar with cancel, real-time logs,
a list of converted songs, and direct opening of results in Windows Explorer / MuseScore.
The window follows the Windows light / dark setting; the last choices are remembered.
"""

import os
import sys
import queue
import threading
import traceback
from datetime import datetime
from tkinter import filedialog, messagebox

import customtkinter as ctk

# Add project root to sys.path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

from src.pipeline import run_pipeline, is_url, VRAM_PROFILES, vram_profile_for
from src.cancellation import PipelineCancelled
from src.fretboard_gui import FretboardEditorDialog
from src.lyrics_gui import LyricsEditorDialog
from src.gui_state import load_settings, save_settings, find_songs, matching_pdf
from src import ui_theme as theme

OUTPUT_ROOT = os.path.join(PROJECT_DIR, "output")
SETTINGS_PATH = os.path.join(PROJECT_DIR, "gui_settings.json")

# Lyrics language choices -> Whisper language code (None = detect)
LYRICS_LANGUAGES = {"자동 감지": None, "한국어": "ko", "English": "en", "日本語": "ja"}
# PDF style key -> segment label
STYLE_LABELS = {"tab": "TAB", "standard": "오선보", "both": "TAB + 오선보"}
# Part key -> checkbox label, in display order
PART_LABELS = {"drums": "드럼", "bass": "베이스 TAB", "lead": "리드 기타 TAB", "rhythm": "리듬 기타 TAB"}
DEFAULT_PARTS = ["drums", "bass", "lead", "rhythm"]
NO_SONGS = "아직 변환한 곡이 없습니다"

# Follow the Windows light / dark setting
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


def _primary(parent, **kw):
    return ctk.CTkButton(parent, fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                         text_color="white", text_color_disabled=("#DBEAFE", "#DBEAFE"), corner_radius=8, **kw)


def _secondary(parent, **kw):
    return ctk.CTkButton(parent, fg_color=theme.SECONDARY, hover_color=theme.SECONDARY_HOVER,
                         text_color=theme.TEXT, text_color_disabled=theme.MUTED, corner_radius=8, **kw)


def _entry(parent, **kw):
    return ctk.CTkEntry(parent, fg_color=theme.INPUT, border_color=theme.BORDER, text_color=theme.TEXT,
                        corner_radius=8, height=34, **kw)


def _segmented(parent, values):
    return ctk.CTkSegmentedButton(parent, values=values, height=32, corner_radius=8,
                                  fg_color=theme.SECONDARY, unselected_color=theme.SECONDARY,
                                  unselected_hover_color=theme.SECONDARY_HOVER,
                                  selected_color=theme.SELECTED, selected_hover_color=theme.SELECTED_HOVER,
                                  text_color=theme.TEXT)


def _option_menu(parent, values, **kw):
    return ctk.CTkOptionMenu(parent, values=values, fg_color=theme.SECONDARY, button_color=theme.SECONDARY_HOVER,
                             button_hover_color=theme.BORDER, text_color=theme.TEXT,
                             text_color_disabled=theme.MUTED, corner_radius=8, dynamic_resizing=False, **kw)


class BandTranscriberApp(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=theme.BG)

        self.title("AI Band Transcriber & TAB Generator")
        self.geometry("880x820")
        self.minsize(680, 660)

        # Check GPU
        self.gpu_name = "CPU 전용"
        self.has_cuda = False
        self.default_vram = "8gb"
        try:
            import torch
            if torch.cuda.is_available():
                self.has_cuda = True
                vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
                self.default_vram = vram_profile_for(vram_gb)
                self.gpu_name = f"NVIDIA {torch.cuda.get_device_name(0)} · VRAM {vram_gb:.1f}GB"
        except Exception:
            pass

        self.is_running = False
        self.last_score_path = None
        self.last_pdf_path = None
        self.last_output_dir = None
        self.songs = []
        self._cancel = threading.Event()
        self._closing = False

        # Worker thread -> UI thread messages (Tk widgets must only be touched by the UI thread)
        self._ui_queue = queue.Queue()

        self.settings = load_settings(SETTINGS_PATH)
        self._build_ui()
        self._apply_settings()
        self._refresh_songs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- layout ----------

    def _card(self, parent, title=None, **pack):
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=theme.CARD, border_width=1, border_color=theme.BORDER)
        card.pack(fill="x", padx=12, pady=6, **pack)
        if title:
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=theme.TEXT).pack(anchor="w", padx=16, pady=(12, 6))
        return card

    def _field_label(self, parent, text):
        return ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=12), text_color=theme.MUTED)

    def _build_ui(self):
        # Results bar, docked at the bottom so it is never pushed off-screen
        results = ctk.CTkFrame(self, corner_radius=12, fg_color=theme.CARD, border_width=1, border_color=theme.BORDER)
        results.pack(side="bottom", fill="x", padx=20, pady=(4, 14))
        for col in range(5):
            results.grid_columnconfigure(col, weight=1, uniform="results")

        song_row = ctk.CTkFrame(results, fg_color="transparent")
        song_row.grid(row=0, column=0, columnspan=5, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(song_row, text="곡", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=theme.TEXT).pack(side="left", padx=(4, 10))
        self.opt_song = _option_menu(song_row, [NO_SONGS], command=self._on_song_chosen, height=32)
        self.opt_song.pack(side="left", fill="x", expand=True)
        _secondary(song_row, text="새로고침", width=84, height=32, command=self._refresh_songs).pack(side="left", padx=(8, 0))

        self.lbl_active_score = ctk.CTkLabel(results, text="", font=ctk.CTkFont(size=12), text_color=theme.MUTED)
        self.lbl_active_score.grid(row=1, column=0, columnspan=5, sticky="w", padx=16, pady=(0, 4))

        self.btn_open_folder = _secondary(results, text="결과 폴더", height=36, state="disabled", command=self._open_folder)
        self.btn_open_score = _secondary(results, text="악보 열기", height=36, state="disabled", command=self._open_score)
        self.btn_open_pdf = _secondary(results, text="PDF 열기", height=36, state="disabled", command=self._open_pdf)
        self.btn_fret_editor = _secondary(results, text="운지 수정", height=36, command=self._open_fret_editor)
        self.btn_lyrics_editor = _secondary(results, text="가사 수정", height=36, command=self._open_lyrics_editor)
        for col, btn in enumerate((self.btn_open_folder, self.btn_open_score, self.btn_open_pdf,
                                   self.btn_fret_editor, self.btn_lyrics_editor)):
            btn.grid(row=2, column=col, sticky="ew", padx=(12 if col == 0 else 4, 12 if col == 4 else 4), pady=(0, 12))

        body = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")
        body.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 0))

        # Header
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(6, 4))
        ctk.CTkLabel(header, text="AI Band Transcriber", font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(header, text="음원을 드럼, 베이스, 리드/리듬 기타, 보컬로 나눠 오선보·TAB 악보와 가사를 만듭니다.",
                     font=ctk.CTkFont(size=12), text_color=theme.MUTED).pack(anchor="w", pady=(2, 4))
        device = f"● {self.gpu_name}" + (" · CUDA 가속" if self.has_cuda else " · GPU를 찾지 못해 느릴 수 있음")
        ctk.CTkLabel(header, text=device, font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.SUCCESS if self.has_cuda else theme.WARNING).pack(anchor="w")

        # Input
        files = self._card(body, "입력")
        files.grid_columnconfigure(1, weight=1)
        inner = ctk.CTkFrame(files, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(0, 14))
        inner.grid_columnconfigure(1, weight=1)
        self._field_label(inner, "음원 / 유튜브 URL").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        self.entry_input = _entry(inner, placeholder_text="MP3·WAV·FLAC 파일을 고르거나 유튜브 링크를 붙여넣기")
        self.entry_input.grid(row=0, column=1, sticky="ew", pady=4)
        _secondary(inner, text="파일 찾기", width=96, height=34, command=self._browse_input).grid(row=0, column=2, padx=(8, 0), pady=4)
        self._field_label(inner, "저장 폴더").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        self.entry_output = _entry(inner)
        self.entry_output.grid(row=1, column=1, sticky="ew", pady=4)
        _secondary(inner, text="폴더 찾기", width=96, height=34, command=self._browse_output).grid(row=1, column=2, padx=(8, 0), pady=4)

        # Options
        opts = self._card(body, "옵션")
        inner = ctk.CTkFrame(opts, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(0, 14))
        inner.grid_columnconfigure(1, weight=1)

        self._field_label(inner, "악기 파트").grid(row=0, column=0, sticky="nw", padx=(0, 12), pady=(6, 4))
        parts = ctk.CTkFrame(inner, fg_color="transparent")
        parts.grid(row=0, column=1, sticky="w", pady=4)
        checkbox = dict(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.MUTED,
                        text_color=theme.TEXT, corner_radius=5, checkbox_width=20, checkbox_height=20)
        self.chk_parts = {}
        for key, label in PART_LABELS.items():
            chk = ctk.CTkCheckBox(parts, text=label, **checkbox)
            chk.pack(side="left", padx=(0, 18))
            self.chk_parts[key] = chk

        self._field_label(inner, "보컬").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        vocal_row = ctk.CTkFrame(inner, fg_color="transparent")
        vocal_row.grid(row=1, column=1, sticky="w", pady=4)
        self.chk_vocals = ctk.CTkCheckBox(vocal_row, text="보컬 멜로디 + 가사", **checkbox)
        self.chk_vocals.pack(side="left", padx=(0, 18))
        ctk.CTkLabel(vocal_row, text="가사 언어", font=ctk.CTkFont(size=12), text_color=theme.MUTED).pack(side="left", padx=(0, 8))
        self.opt_lyrics_lang = _option_menu(vocal_row, list(LYRICS_LANGUAGES), width=120, height=30)
        self.opt_lyrics_lang.pack(side="left")

        self._field_label(inner, "PDF 악보").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=4)
        self.seg_style = _segmented(inner, list(STYLE_LABELS.values()))
        self.seg_style.grid(row=2, column=1, sticky="ew", pady=4)

        self._field_label(inner, "GPU 메모리").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=4)
        self.seg_vram = _segmented(inner, [profile["label"] for profile in VRAM_PROFILES.values()])
        self.seg_vram.grid(row=3, column=1, sticky="ew", pady=4)

        # Run
        run = self._card(body)
        buttons = ctk.CTkFrame(run, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(14, 8))
        self.btn_run = _primary(buttons, text="악보 만들기", height=44, font=ctk.CTkFont(size=16, weight="bold"),
                                command=self._start_pipeline_thread)
        self.btn_run.pack(side="left", fill="x", expand=True)
        self.btn_cancel = ctk.CTkButton(buttons, text="취소", width=96, height=44, corner_radius=8, state="disabled",
                                        fg_color=theme.SECONDARY, hover_color=theme.DANGER_HOVER,
                                        text_color=theme.TEXT, text_color_disabled=theme.MUTED,
                                        command=self._cancel_pipeline)
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.lbl_status = ctk.CTkLabel(run, text="준비 완료. 변환할 음원을 골라 주세요.", font=ctk.CTkFont(size=13),
                                       text_color=theme.TEXT, anchor="w", justify="left")
        self.lbl_status.pack(fill="x", padx=16, pady=(0, 6))
        self.prog_bar = ctk.CTkProgressBar(run, height=8, corner_radius=4, fg_color=theme.SECONDARY,
                                           progress_color=theme.ACCENT)
        self.prog_bar.pack(fill="x", padx=16, pady=(0, 16))
        self.prog_bar.set(0)

        # Log
        log = self._card(body, "처리 로그", expand=True)
        self.txt_log = ctk.CTkTextbox(log, height=140, corner_radius=8, fg_color=theme.INPUT, text_color=theme.TEXT,
                                      border_width=1, border_color=theme.BORDER,
                                      font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_log.pack(fill="both", expand=True, padx=16, pady=(0, 14))

    # ---------- settings ----------

    def _apply_settings(self):
        """Puts the saved choices (or the defaults) into the widgets."""
        s = self.settings
        self.entry_output.insert(0, s.get("output_dir") or OUTPUT_ROOT)
        parts = s.get("parts", DEFAULT_PARTS)
        for key, chk in self.chk_parts.items():
            chk.select() if key in parts else chk.deselect()
        self.chk_vocals.select() if "vocals" in parts else self.chk_vocals.deselect()
        lang = s.get("lyrics_language")
        self.opt_lyrics_lang.set(lang if lang in LYRICS_LANGUAGES else "자동 감지")
        self.seg_style.set(STYLE_LABELS.get(s.get("style"), STYLE_LABELS["tab"]))
        vram = s.get("vram") if s.get("vram") in VRAM_PROFILES else self.default_vram
        self.seg_vram.set(VRAM_PROFILES[vram]["label"])

    def _save_settings(self):
        self.settings.update({
            "output_dir": self.entry_output.get().strip(),
            "parts": self._selected_parts(),
            "lyrics_language": self.opt_lyrics_lang.get(),
            "style": self._selected_style(),
            "vram": self._selected_vram(),
        })
        save_settings(SETTINGS_PATH, self.settings)

    # ---------- songs ----------

    def _songs_root(self) -> str:
        root = self.entry_output.get().strip()
        return root if root and os.path.isdir(root) else OUTPUT_ROOT

    @staticmethod
    def _song_label(song: dict) -> str:
        return f"{song['name']}  ·  {datetime.fromtimestamp(song['mtime']):%m-%d %H:%M}"

    def _refresh_songs(self, select_dir: str = None):
        """Lists the songs under the output folder; selects `select_dir`, else the newest."""
        root = self._songs_root()
        self.songs = find_songs(root)
        if os.path.isdir(root):
            self.last_output_dir = self.last_output_dir or root
            self.btn_open_folder.configure(state="normal")
        if not self.songs:
            self.opt_song.configure(values=[NO_SONGS], state="disabled")
            self.opt_song.set(NO_SONGS)
            return
        self.opt_song.configure(values=[self._song_label(s) for s in self.songs], state="normal")
        chosen = next((s for s in self.songs if select_dir and os.path.samefile(s["dir"], select_dir)), self.songs[0])
        self.opt_song.set(self._song_label(chosen))
        self._select_song(chosen)

    def _on_song_chosen(self, label: str):
        song = next((s for s in self.songs if self._song_label(s) == label), None)
        if song:
            self._select_song(song)

    def _select_song(self, song: dict):
        self.last_output_dir = song["dir"]
        self._set_score(song["score"], song["pdf"])

    def _set_score(self, score_path: str, pdf_path: str = None):
        """Points the result buttons and editors at one score (and its PDF, if any)."""
        self.last_score_path = score_path
        self.last_pdf_path = pdf_path
        self.btn_open_score.configure(state="normal")
        self.btn_open_folder.configure(state="normal")
        self.btn_open_pdf.configure(state="normal" if pdf_path else "disabled")
        self.lbl_active_score.configure(text=f"악보: {os.path.basename(score_path)}"
                                             + ("" if pdf_path else "  (PDF 없음)"))

    # ---------- inputs ----------

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
            self.last_output_dir = None
            self._refresh_songs()

    def _log(self, text: str):
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")

    def _set_status(self, text: str, progress: float, color=theme.TEXT):
        self.lbl_status.configure(text=text, text_color=color)
        self.prog_bar.set(progress)

    def _selected_parts(self) -> list:
        parts = [key for key in ("drums", "bass", "rhythm", "lead") if self.chk_parts[key].get()]
        if self.chk_vocals.get():
            parts.append("vocals")
        return parts

    def _selected_style(self) -> str:
        chosen = self.seg_style.get()
        return next((key for key, label in STYLE_LABELS.items() if label == chosen), "tab")

    def _selected_vram(self) -> str:
        chosen = self.seg_vram.get()
        return next((key for key, profile in VRAM_PROFILES.items() if profile["label"] == chosen),
                    self.default_vram)

    # ---------- run ----------

    def _set_running(self, running: bool):
        self.is_running = running
        self.btn_run.configure(state="disabled" if running else "normal",
                               text="변환 중..." if running else "악보 만들기")
        self.btn_cancel.configure(state="normal" if running else "disabled",
                                  fg_color=theme.DANGER_FILL if running else theme.SECONDARY,
                                  text_color="white" if running else theme.TEXT)

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

        self._save_settings()
        self._cancel.clear()
        self._set_running(True)
        self._set_status("시작하는 중...", 0.0)
        self.txt_log.delete("1.0", "end")

        # Read widget state here, on the UI thread; the worker only gets plain values.
        parts = self._selected_parts()
        style = self._selected_style()
        vram = self._selected_vram()
        lyrics_language = LYRICS_LANGUAGES[self.opt_lyrics_lang.get()]

        thread = threading.Thread(target=self._run_pipeline,
                                  args=(input_audio, output_dir, parts, style, vram, lyrics_language),
                                  daemon=True)
        thread.start()
        self.after(100, self._drain_ui_queue)

    def _cancel_pipeline(self):
        if not self.is_running or self._cancel.is_set():
            return
        self._cancel.set()
        self.btn_cancel.configure(state="disabled", text="취소 중")
        self._set_status("취소하는 중... 진행 중인 작업을 멈추고 있습니다.", self.prog_bar.get(), theme.WARNING)
        self._log("\n[*] 사용자가 취소를 요청했습니다.")

    def _run_pipeline(self, input_audio: str, output_dir: str, parts: list, style: str, vram: str,
                      lyrics_language: str = None):
        """Worker thread: never touches Tk widgets, only posts messages to the UI queue."""
        post = self._ui_queue.put
        try:
            result = run_pipeline(
                input_audio, output_dir, parts=parts, style=style, vram=vram,
                lyrics_language=lyrics_language,
                log=lambda text: post(("log", text)),
                progress=lambda text, fraction: post(("status", text, fraction)),
                cancel=self._cancel
            )
            post(("done", result))
        except PipelineCancelled:
            post(("cancelled",))
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
                    if not self._cancel.is_set():
                        self._set_status(msg[1], msg[2])
                elif kind == "done":
                    self._on_pipeline_done(msg[1])
                elif kind == "cancelled":
                    self._on_pipeline_cancelled()
                elif kind == "error":
                    self._on_pipeline_error(msg[1], msg[2])
        except queue.Empty:
            pass
        if self.is_running:
            self.after(100, self._drain_ui_queue)

    def _finish_run(self):
        self._set_running(False)
        self.btn_cancel.configure(text="취소")
        if self._closing:
            self.destroy()
            return True
        return False

    def _on_pipeline_done(self, result: dict):
        if self._finish_run():
            return
        self._set_status(f"완료 ({result['elapsed']:.1f}초 소요)", 1.0, theme.SUCCESS)
        self.last_output_dir = result["song_dir"]
        self._refresh_songs(select_dir=result["song_dir"])
        # Never leave the PDF button pointing at another song's PDF
        self._set_score(result["score_xml"], result["pdf_path"])

    def _on_pipeline_cancelled(self):
        if self._finish_run():
            return
        self._set_status("변환을 취소했습니다.", 0.0, theme.WARNING)
        self._log("[*] 변환을 취소했습니다.")

    def _on_pipeline_error(self, error: Exception, tb_text: str):
        if self._finish_run():
            return
        self._set_status("오류가 발생했습니다. 로그를 확인해 주세요.", 0.0, theme.DANGER)
        self._log(f"\n[!] 오류 발생: {error}")
        self._log(tb_text)
        messagebox.showerror("변환 오류", f"처리 중 오류가 발생했습니다:\n{error}")

    def _on_close(self):
        self._save_settings()
        if not self.is_running:
            self.destroy()
            return
        if not messagebox.askyesno("변환 중", "변환이 진행 중입니다. 취소하고 종료할까요?"):
            return
        # Stop the run first (a running Demucs is killed), then close; give up waiting after 15 s
        self._closing = True
        self._cancel_pipeline()
        self.after(15000, self.destroy)

    # ---------- results ----------

    def _open_folder(self):
        target_dir = self.last_output_dir or self._songs_root()
        if os.path.exists(target_dir):
            os.startfile(target_dir)
        else:
            messagebox.showinfo("알림", "아직 생성된 결과 폴더가 없습니다.")

    def _choose_file(self, title: str, filetypes: list):
        initial = self.last_output_dir or self._songs_root()
        chosen = filedialog.askopenfilename(title=title, filetypes=filetypes,
                                            initialdir=initial if os.path.exists(initial) else PROJECT_DIR)
        return chosen if chosen and os.path.exists(chosen) else None

    def _open_score(self):
        score_path = self.last_score_path
        if not score_path or not os.path.exists(score_path):
            score_path = self._choose_file("열람할 악보(MusicXML) 파일을 선택하세요", SCORE_FILETYPES)
            if not score_path:
                return
            self._set_score(score_path, matching_pdf(score_path))
        try:
            os.startfile(score_path)
        except Exception as e:
            messagebox.showinfo("알림", f"파일을 열 수 없습니다:\n{e}\n\nMuseScore 4 또는 TuxGuitar를 먼저 설치해 주세요.")

    def _open_pdf(self):
        pdf_path = self.last_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            pdf_path = self._choose_file("열람할 PDF 악보 파일을 선택하세요", [("PDF 파일 (*.pdf)", "*.pdf"), ("모든 파일 (*.*)", "*.*")])
            if not pdf_path:
                return
            self.last_pdf_path = pdf_path
            self.btn_open_pdf.configure(state="normal")
        try:
            os.startfile(pdf_path)
        except Exception as e:
            messagebox.showinfo("알림", f"PDF 파일을 열 수 없습니다:\n{e}")

    def _select_score_for_editing(self, title: str):
        """Returns the score to edit (the selected song's, or one the user picks), or None."""
        score_path = self.last_score_path
        if not score_path or not os.path.exists(score_path):
            score_path = self._choose_file(title, SCORE_FILETYPES)
            if not score_path:
                return None
            self._set_score(score_path, matching_pdf(score_path))
        return score_path

    def _open_fret_editor(self):
        score_path = self._select_score_for_editing("운지를 수정할 타브 악보(MusicXML) 파일을 선택하세요")
        if not score_path:
            return
        self._log(f"\n[*] 프렛보드 편집기 실행: {os.path.basename(score_path)}")
        FretboardEditorDialog(self, xml_path=score_path, pdf_callback=self._on_pdf_reexported, style=self._selected_style())

    def _open_lyrics_editor(self):
        score_path = self._select_score_for_editing("가사를 수정할 악보(MusicXML) 파일을 선택하세요")
        if not score_path:
            return
        self._log(f"\n[*] 가사 편집기 실행: {os.path.basename(score_path)}")
        LyricsEditorDialog(self, xml_path=score_path, pdf_callback=self._on_pdf_reexported, style=self._selected_style())

    def _on_pdf_reexported(self, new_pdf_path):
        self.last_pdf_path = new_pdf_path
        self.btn_open_pdf.configure(state="normal")
        self._log(f"[+] 편집기에서 PDF 악보를 갱신했습니다: {os.path.basename(new_pdf_path)}")


SCORE_FILETYPES = [("MusicXML 악보 (*.musicxml, *.xml)", "*.musicxml *.xml"), ("모든 파일 (*.*)", "*.*")]


def main():
    app = BandTranscriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()
