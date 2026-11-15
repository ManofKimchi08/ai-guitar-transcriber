"""
AI Band Transcriber - Modern Desktop GUI
Built with CustomTkinter for Windows.
Left: the converted songs (search, pick one to open or edit its score). Right: a new
conversion (input, options, run / cancel, stage checklist), the chosen song, and the log.
The window follows the Windows light / dark setting; the last choices are remembered.
"""

import os
import sys
import time
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
# Part key -> chip label, in display order
PART_LABELS = {"drums": "드럼", "bass": "베이스", "lead": "리드 기타", "rhythm": "리듬 기타", "vocals": "보컬 + 가사"}
DEFAULT_PARTS = ["drums", "bass", "lead", "rhythm"]
# Pipeline stage key -> checklist label, in run order
STAGE_LABELS = {"download": "유튜브 음원 받기", "separate": "음원 분리 (Demucs)", "split": "리드 / 리듬 기타 분리",
                "transcribe": "악기별 채보", "lyrics": "가사 인식 (Whisper)", "score": "악보 · PDF 만들기"}
SCORE_FILETYPES = [("MusicXML 악보 (*.musicxml, *.xml)", "*.musicxml *.xml"), ("모든 파일 (*.*)", "*.*")]
NO_SONG_HINT = "변환이 끝나거나 왼쪽 목록에서 곡을 고르면 여기에서 열 수 있습니다."

# Follow the Windows light / dark setting
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


def _primary(parent, **kw):
    return ctk.CTkButton(parent, fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color="white",
                         text_color_disabled=("#DBEAFE", "#DBEAFE"), corner_radius=8, **kw)


def _secondary(parent, **kw):
    return ctk.CTkButton(parent, fg_color=theme.SECONDARY, hover_color=theme.SECONDARY_HOVER,
                         text_color=theme.TEXT, text_color_disabled=theme.MUTED, corner_radius=8, **kw)


def _entry(parent, **kw):
    return ctk.CTkEntry(parent, fg_color=theme.INPUT, border_color=theme.BORDER, text_color=theme.TEXT,
                        corner_radius=8, height=34, **kw)


def _label(parent, text, size=12, weight="normal", color=theme.TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=size, weight=weight), text_color=color, **kw)


def _segmented(parent, values):
    return ctk.CTkSegmentedButton(parent, values=values, height=32, corner_radius=8,
                                  fg_color=theme.SECONDARY, unselected_color=theme.SECONDARY,
                                  unselected_hover_color=theme.SECONDARY_HOVER,
                                  selected_color=theme.SELECTED, selected_hover_color=theme.SELECTED_HOVER,
                                  text_color=theme.TEXT)


def _duration(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes}분 {sec}초" if minutes else f"{sec}초"


class BandTranscriberApp(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=theme.BG)

        self.title("AI Band Transcriber & TAB Generator")
        self.geometry("1160x800")
        self.minsize(1000, 660)

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
        self.selected_dir = None
        self.part_on = {key: False for key in PART_LABELS}
        self.stage_state = {}    # stage key -> "todo" | "now" | "done" | "skipped" | "stopped"
        self._stage_rows = {}    # stage key -> (row, mark, name, note) widgets
        self._stage_started = None
        self._run_started = None
        self._cancel = threading.Event()
        self._closing = False

        # Worker thread -> UI thread messages (Tk widgets must only be touched by the UI thread)
        self._ui_queue = queue.Queue()

        self.settings = load_settings(SETTINGS_PATH)
        self._build_ui()
        self._apply_settings()
        self._show_stages(["separate", "split", "transcribe", "score"])
        self._refresh_songs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- layout ----------

    def _card(self, parent, title=None, **pack):
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=theme.CARD, border_width=1, border_color=theme.BORDER)
        card.pack(fill="x", pady=(0, 12), **pack)
        if title:
            _label(card, title, 15, "bold").pack(anchor="w", padx=18, pady=(14, 8))
        return card

    def _build_ui(self):
        self._build_sidebar()
        ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=theme.BORDER).pack(side="left", fill="y")
        main = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True, padx=(14, 4), pady=(14, 2))
        self._build_convert_card(main)
        self._build_song_card(main)
        log = self._card(main, "처리 로그")
        self.txt_log = ctk.CTkTextbox(log, height=170, corner_radius=8, fg_color=theme.INPUT, text_color=theme.TEXT,
                                      border_width=1, border_color=theme.BORDER,
                                      font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_log.pack(fill="both", expand=True, padx=18, pady=(0, 14))

    def _build_sidebar(self):
        side = ctk.CTkFrame(self, width=300, corner_radius=0, fg_color=theme.CARD)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        _label(side, "AI Band Transcriber", 18, "bold").pack(anchor="w", padx=20, pady=(22, 0))
        device = f"● {self.gpu_name}" if self.has_cuda else "● CPU 전용 · GPU를 찾지 못해 느릴 수 있음"
        _label(side, device, 11, "bold", theme.SUCCESS if self.has_cuda else theme.WARNING,
               wraplength=260, justify="left").pack(anchor="w", padx=20, pady=(2, 16))
        _primary(side, text="+  새 변환", height=40, font=ctk.CTkFont(size=13, weight="bold"),
                 command=self._new_conversion).pack(fill="x", padx=16)

        self.lbl_song_count = _label(side, "내 악보", 12, "bold", theme.MUTED)
        self.lbl_song_count.pack(anchor="w", padx=20, pady=(22, 6))
        self.entry_search = _entry(side, placeholder_text="곡 검색")
        self.entry_search.pack(fill="x", padx=16, pady=(0, 6))
        self.entry_search.bind("<KeyRelease>", lambda event: self._render_songs())

        self.btn_open_root = _secondary(side, text="결과 폴더 전체 열기", height=34, command=self._open_root)
        self.btn_open_root.pack(side="bottom", fill="x", padx=16, pady=16)
        self.song_list = ctk.CTkScrollableFrame(side, corner_radius=0, fg_color="transparent")
        self.song_list.pack(fill="both", expand=True, padx=4)

    def _build_convert_card(self, parent):
        card = self._card(parent, "새 변환")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=18)
        grid.grid_columnconfigure(1, weight=1)

        def field(row, text):
            _label(grid, text, color=theme.MUTED).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)

        field(0, "음원 / 유튜브 URL")
        self.entry_input = _entry(grid, placeholder_text="MP3·WAV·FLAC 파일을 고르거나 유튜브 링크를 붙여넣기")
        self.entry_input.grid(row=0, column=1, sticky="ew", pady=4)
        _secondary(grid, text="파일 찾기", width=96, height=34, command=self._browse_input).grid(row=0, column=2, padx=(8, 0))
        field(1, "저장 폴더")
        self.entry_output = _entry(grid)
        self.entry_output.grid(row=1, column=1, sticky="ew", pady=4)
        _secondary(grid, text="폴더 찾기", width=96, height=34, command=self._browse_output).grid(row=1, column=2, padx=(8, 0))

        field(2, "악기 파트")
        chips = ctk.CTkFrame(grid, fg_color="transparent")
        chips.grid(row=2, column=1, columnspan=2, sticky="w", pady=6)
        self.chips = {}
        for key in PART_LABELS:
            chip = ctk.CTkButton(chips, width=60, height=32, corner_radius=16, border_width=1,
                                 command=lambda k=key: self._toggle_part(k))
            chip.pack(side="left", padx=(0, 8))
            self.chips[key] = chip

        choices = ctk.CTkFrame(card, fg_color="transparent")
        choices.pack(fill="x", padx=18, pady=(4, 0))
        choices.grid_columnconfigure((0, 1), weight=1, uniform="choices")
        _label(choices, "PDF 악보", color=theme.MUTED).grid(row=0, column=0, sticky="w")
        self.seg_style = _segmented(choices, list(STYLE_LABELS.values()))
        self.seg_style.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(2, 0))
        _label(choices, "GPU 메모리", color=theme.MUTED).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.seg_vram = _segmented(choices, [profile["label"] for profile in VRAM_PROFILES.values()])
        self.seg_vram.grid(row=1, column=1, sticky="ew", padx=6, pady=(2, 0))
        _label(choices, "가사 언어", color=theme.MUTED).grid(row=0, column=2, sticky="w", padx=(6, 0))
        self.opt_lyrics_lang = ctk.CTkOptionMenu(
            choices, values=list(LYRICS_LANGUAGES), width=120, height=32, corner_radius=8, dynamic_resizing=False,
            fg_color=theme.SECONDARY, button_color=theme.SECONDARY_HOVER, button_hover_color=theme.BORDER,
            text_color=theme.TEXT, text_color_disabled=theme.MUTED)
        self.opt_lyrics_lang.grid(row=1, column=2, sticky="e", padx=(6, 0), pady=(2, 0))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(16, 8))
        self.btn_run = _primary(buttons, text="악보 만들기", height=44, font=ctk.CTkFont(size=16, weight="bold"),
                                command=self._start_pipeline_thread)
        self.btn_run.pack(side="left", fill="x", expand=True)
        self.btn_cancel = ctk.CTkButton(buttons, text="취소", width=96, height=44, corner_radius=8, state="disabled",
                                        fg_color=theme.SECONDARY, hover_color=theme.DANGER_HOVER,
                                        text_color=theme.TEXT, text_color_disabled=theme.MUTED,
                                        command=self._cancel_pipeline)
        self.btn_cancel.pack(side="left", padx=(8, 0))

        status = ctk.CTkFrame(card, fg_color="transparent")
        status.pack(fill="x", padx=18)
        self.lbl_status = _label(status, "준비 완료. 변환할 음원을 골라 주세요.", 13, anchor="w", justify="left")
        self.lbl_status.pack(side="left")
        self.lbl_elapsed = _label(status, "", 12, color=theme.MUTED)
        self.lbl_elapsed.pack(side="right")
        self.prog_bar = ctk.CTkProgressBar(card, height=8, corner_radius=4, fg_color=theme.SECONDARY,
                                           progress_color=theme.ACCENT)
        self.prog_bar.pack(fill="x", padx=18, pady=(6, 8))
        self.prog_bar.set(0)
        self.stage_box = ctk.CTkFrame(card, fg_color="transparent")
        self.stage_box.pack(fill="x", padx=18, pady=(0, 14))

    def _build_song_card(self, parent):
        card = self._card(parent)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 0))
        self.lbl_song_title = _label(head, "선택한 곡 없음", 15, "bold")
        self.lbl_song_title.pack(side="left")
        self.lbl_song_date = _label(head, "", 11, color=theme.MUTED)
        self.lbl_song_date.pack(side="left", padx=10)
        self.lbl_active_score = _label(card, NO_SONG_HINT, 11, color=theme.MUTED, anchor="w", justify="left")
        self.lbl_active_score.pack(fill="x", padx=18, pady=(2, 0))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(10, 14))
        buttons.grid_columnconfigure((0, 1, 2, 3, 4), weight=1, uniform="results")
        self.btn_open_pdf = _primary(buttons, text="PDF 열기", height=36, state="disabled", command=self._open_pdf)
        self.btn_open_score = _secondary(buttons, text="악보 열기", height=36, state="disabled", command=self._open_score)
        self.btn_fret_editor = _secondary(buttons, text="운지 수정", height=36, command=self._open_fret_editor)
        self.btn_lyrics_editor = _secondary(buttons, text="가사 수정", height=36, command=self._open_lyrics_editor)
        self.btn_open_folder = _secondary(buttons, text="결과 폴더", height=36, state="disabled", command=self._open_folder)
        for col, btn in enumerate((self.btn_open_pdf, self.btn_open_score, self.btn_fret_editor,
                                   self.btn_lyrics_editor, self.btn_open_folder)):
            btn.grid(row=0, column=col, sticky="ew", padx=4)
        self._style_pdf_button()

    def _style_pdf_button(self):
        """The PDF button is the accent one only while there is a PDF to open."""
        ready = bool(self.last_pdf_path)
        self.btn_open_pdf.configure(state="normal" if ready else "disabled",
                                    fg_color=theme.ACCENT if ready else theme.SECONDARY,
                                    text_color_disabled=theme.MUTED)

    # ---------- parts ----------

    def _set_part(self, key: str, on: bool):
        self.part_on[key] = on
        self.chips[key].configure(
            text=("✓ " if on else "") + PART_LABELS[key],
            font=ctk.CTkFont(size=12, weight="bold" if on else "normal"),
            fg_color=theme.ACCENT if on else "transparent",
            hover_color=theme.ACCENT_HOVER if on else theme.SECONDARY,
            border_color=theme.ACCENT if on else theme.BORDER,
            text_color="white" if on else theme.MUTED)
        if key == "vocals":
            self.opt_lyrics_lang.configure(state="normal" if on else "disabled")

    def _toggle_part(self, key: str):
        self._set_part(key, not self.part_on[key])

    # ---------- settings ----------

    def _apply_settings(self):
        """Puts the saved choices (or the defaults) into the widgets."""
        s = self.settings
        self.entry_output.insert(0, s.get("output_dir") or OUTPUT_ROOT)
        parts = s.get("parts", DEFAULT_PARTS)
        lang = s.get("lyrics_language")
        self.opt_lyrics_lang.set(lang if lang in LYRICS_LANGUAGES else "자동 감지")
        for key in PART_LABELS:
            self._set_part(key, key in parts)
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

    def _refresh_songs(self, select_dir: str = None):
        """Lists the songs under the output folder; selects `select_dir`, else keeps the
        current choice, else takes the newest."""
        root = self._songs_root()
        self.songs = find_songs(root)
        self.btn_open_root.configure(state="normal" if os.path.isdir(root) else "disabled")
        wanted = select_dir or self.selected_dir
        chosen = next((s for s in self.songs if wanted and os.path.normcase(s["dir"]) == os.path.normcase(wanted)),
                      self.songs[0] if self.songs else None)
        if chosen:
            self._select_song(chosen)
        else:
            self._render_songs()

    def _render_songs(self):
        """Redraws the song list (filtered by the search box), marking the chosen song."""
        for child in self.song_list.winfo_children():
            child.destroy()
        query = self.entry_search.get().strip().lower()
        shown = [s for s in self.songs if query in s["name"].lower()]
        self.lbl_song_count.configure(text=f"내 악보  {len(self.songs)}")
        if not shown:
            _label(self.song_list, "검색 결과가 없습니다." if self.songs else "아직 변환한 곡이 없습니다.",
                   12, color=theme.MUTED).pack(anchor="w", padx=14, pady=10)
            return
        for song in shown:
            selected = bool(self.selected_dir) and os.path.normcase(song["dir"]) == os.path.normcase(self.selected_dir)
            row = ctk.CTkFrame(self.song_list, corner_radius=8, fg_color=theme.ROW_SELECTED if selected else "transparent")
            row.pack(fill="x", padx=4, pady=2)
            bar = ctk.CTkFrame(row, width=3, height=34, corner_radius=2, fg_color=theme.ACCENT if selected else "transparent")
            bar.pack(side="left", padx=(6, 0))
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, padx=(8, 6), pady=8)
            name = _label(text, song["name"], 13, "bold", anchor="w", justify="left", wraplength=220)
            name.pack(fill="x")
            meta = ctk.CTkFrame(text, fg_color="transparent")
            meta.pack(fill="x", pady=(2, 0))
            widgets = [row, bar, text, name, meta]
            when = _label(meta, f"{datetime.fromtimestamp(song['mtime']):%m-%d %H:%M}", 11, color=theme.MUTED)
            when.pack(side="left", padx=(0, 8))
            widgets.append(when)
            for tag in song["tags"]:
                pill = ctk.CTkLabel(meta, text=tag, font=ctk.CTkFont(size=10, weight="bold"), text_color=theme.MUTED,
                                    fg_color=theme.SECONDARY, corner_radius=6, height=20, width=10, padx=8)
                pill.pack(side="left", padx=(0, 4))
                widgets.append(pill)
            for widget in widgets:  # the whole row is the click target
                widget.bind("<Button-1>", lambda event, s=song: self._select_song(s))

    def _select_song(self, song: dict):
        self.selected_dir = song["dir"]
        self.last_output_dir = song["dir"]
        self._set_score(song["score"], song["pdf"], title=song["name"], mtime=song["mtime"])
        self._render_songs()

    def _set_score(self, score_path: str, pdf_path: str = None, title: str = None, mtime: float = None):
        """Points the result buttons and editors at one score (and its PDF, if any)."""
        self.last_score_path = score_path
        self.last_pdf_path = pdf_path
        self.btn_open_score.configure(state="normal")
        self.btn_open_folder.configure(state="normal")
        self._style_pdf_button()
        self.lbl_song_title.configure(text=title or os.path.splitext(os.path.basename(score_path))[0])
        self.lbl_song_date.configure(text=f"{datetime.fromtimestamp(mtime):%m-%d %H:%M} 변환" if mtime else "")
        self.lbl_active_score.configure(text=os.path.basename(score_path) + "  ·  "
                                             + (os.path.basename(pdf_path) if pdf_path else "PDF 없음"))

    # ---------- inputs ----------

    def _new_conversion(self):
        """Sidebar button: pick the next file to convert."""
        if not self.is_running:
            self._browse_input()
        self.entry_input.focus_set()

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
            self.selected_dir = None
            self._refresh_songs()

    def _log(self, text: str):
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")

    def _set_status(self, text: str, progress: float, color=theme.TEXT):
        self.lbl_status.configure(text=text, text_color=color)
        self.prog_bar.set(progress)

    def _selected_parts(self) -> list:
        return [key for key in ("drums", "bass", "rhythm", "lead", "vocals") if self.part_on[key]]

    def _selected_style(self) -> str:
        chosen = self.seg_style.get()
        return next((key for key, label in STYLE_LABELS.items() if label == chosen), "tab")

    def _selected_vram(self) -> str:
        chosen = self.seg_vram.get()
        return next((key for key, profile in VRAM_PROFILES.items() if profile["label"] == chosen),
                    self.default_vram)

    # ---------- stages ----------

    def _show_stages(self, keys: list):
        """Lays out the checklist for one run, every stage still to do."""
        for child in self.stage_box.winfo_children():
            child.destroy()
        self.stage_state, self._stage_rows, self._stage_started = {}, {}, None
        for key in keys:
            row = ctk.CTkFrame(self.stage_box, corner_radius=8, fg_color="transparent")
            row.pack(fill="x", pady=1)
            mark = _label(row, "○", 13, "bold", theme.MUTED, width=26)
            mark.pack(side="left", padx=(8, 4), pady=2)
            name = _label(row, STAGE_LABELS[key], 12, color=theme.MUTED)
            name.pack(side="left")
            note = _label(row, "", 11, color=theme.MUTED)
            note.pack(side="right", padx=12)
            self._stage_rows[key] = (row, mark, name, note)
            self.stage_state[key] = "todo"

    def _mark_stage(self, key: str, state: str, note: str = ""):
        look = {"todo": ("○", theme.MUTED, theme.MUTED), "now": ("●", theme.ACCENT, theme.TEXT),
                "done": ("✓", theme.SUCCESS, theme.TEXT), "skipped": ("–", theme.MUTED, theme.MUTED),
                "stopped": ("!", theme.WARNING, theme.TEXT)}[state]
        row, mark, name, note_label = self._stage_rows[key]
        self.stage_state[key] = state
        row.configure(fg_color=theme.ROW_SELECTED if state == "now" else "transparent")
        mark.configure(text=look[0], text_color=look[1])
        name.configure(text_color=look[2], font=ctk.CTkFont(size=12, weight="bold" if state == "now" else "normal"))
        note_label.configure(text=note)

    def _current_stage(self):
        return next((key for key, state in self.stage_state.items() if state == "now"), None)

    def _close_stage(self, state: str = "done", note: str = None):
        """Ends the running stage, noting how long it took."""
        current = self._current_stage()
        if current:
            took = _duration(time.time() - self._stage_started)
            self._mark_stage(current, state, f"{note} · {took}" if note else took)

    def _on_stage(self, key: str):
        """A stage started: the one before it is done, any stage passed over did not run."""
        if key not in self.stage_state:
            return
        self._close_stage()
        for earlier in list(self.stage_state)[:list(self.stage_state).index(key)]:
            if self.stage_state[earlier] == "todo":
                self._mark_stage(earlier, "skipped", "건너뜀")
        self._stage_started = time.time()
        self._mark_stage(key, "now", "진행 중")

    def _tick(self):
        """While a run is on, shows the time spent in total and in the current stage."""
        if not self.is_running:
            return
        now = time.time()
        self.lbl_elapsed.configure(text=f"경과 {_duration(now - self._run_started)}")
        current = self._current_stage()
        if current:
            self._stage_rows[current][3].configure(text=f"진행 중 · {_duration(now - self._stage_started)}")
        self.after(500, self._tick)

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

        # Read widget state here, on the UI thread; the worker only gets plain values.
        parts = self._selected_parts()
        style = self._selected_style()
        vram = self._selected_vram()
        lyrics_language = LYRICS_LANGUAGES[self.opt_lyrics_lang.get()]

        self._save_settings()
        self._cancel.clear()
        self._set_running(True)
        self._set_status("시작하는 중...", 0.0)
        self.txt_log.delete("1.0", "end")
        self._show_stages((["download"] if is_url(input_audio) else []) + ["separate", "split", "transcribe"]
                          + (["lyrics"] if "vocals" in parts else []) + ["score"])
        self._run_started = time.time()
        self._tick()

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
                stage=lambda key: post(("stage", key)),
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
                elif kind == "stage":
                    if not self._cancel.is_set():
                        self._on_stage(msg[1])
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

    def _finish_run(self) -> bool:
        """Common end of a run; True when the window is closing and nothing more should be drawn."""
        self._set_running(False)
        self.btn_cancel.configure(text="취소")
        if self._closing:
            self.destroy()
            return True
        self.lbl_elapsed.configure(text=f"총 {_duration(time.time() - self._run_started)}")
        return False

    def _on_pipeline_done(self, result: dict):
        if self._finish_run():
            return
        self._close_stage()
        for key, state in self.stage_state.items():
            if state == "todo":
                self._mark_stage(key, "skipped", "건너뜀")
        self._set_status("완료했습니다. 아래에서 악보를 열 수 있습니다.", 1.0, theme.SUCCESS)
        self._refresh_songs(select_dir=result["song_dir"])
        # Never leave the PDF button pointing at another song's PDF
        self.last_output_dir = result["song_dir"]
        self._set_score(result["score_xml"], result["pdf_path"], title=result.get("song_title"), mtime=time.time())

    def _on_pipeline_cancelled(self):
        if self._finish_run():
            return
        self._close_stage("stopped", "취소됨")
        self._set_status("변환을 취소했습니다.", 0.0, theme.WARNING)
        self._log("[*] 변환을 취소했습니다.")

    def _on_pipeline_error(self, error: Exception, tb_text: str):
        if self._finish_run():
            return
        self._close_stage("stopped", "오류")
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

    def _open_root(self):
        root = self._songs_root()
        if os.path.exists(root):
            os.startfile(root)
        else:
            messagebox.showinfo("알림", "아직 생성된 결과 폴더가 없습니다.")

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

    def _pick_score(self, title: str):
        """A score chosen by hand (not from the song list) becomes the current one."""
        score_path = self._choose_file(title, SCORE_FILETYPES)
        if score_path:
            self.selected_dir = None
            self.last_output_dir = os.path.dirname(score_path)
            self._set_score(score_path, matching_pdf(score_path))
            self._render_songs()
        return score_path

    def _open_score(self):
        score_path = self.last_score_path
        if not score_path or not os.path.exists(score_path):
            score_path = self._pick_score("열람할 악보(MusicXML) 파일을 선택하세요")
            if not score_path:
                return
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
            self._style_pdf_button()
        try:
            os.startfile(pdf_path)
        except Exception as e:
            messagebox.showinfo("알림", f"PDF 파일을 열 수 없습니다:\n{e}")

    def _select_score_for_editing(self, title: str):
        """Returns the score to edit (the selected song's, or one the user picks), or None."""
        score_path = self.last_score_path
        if not score_path or not os.path.exists(score_path):
            score_path = self._pick_score(title)
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
        self._style_pdf_button()
        self._log(f"[+] 편집기에서 PDF 악보를 갱신했습니다: {os.path.basename(new_pdf_path)}")


def main():
    app = BandTranscriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()
