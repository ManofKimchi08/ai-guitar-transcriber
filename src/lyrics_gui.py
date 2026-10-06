"""
Lyrics Editor Dialog
CustomTkinter window for correcting recognized lyrics note by note, then re-exporting the PDF.
"""

import os
from tkinter import messagebox
import customtkinter as ctk

from src.lyrics_editor import ScoreLyricsEditor


class LyricsEditorDialog(ctk.CTkToplevel):
    """
    Shows one measure of a vocal part at a time: each sung note with an entry for its syllable.
    Edits are applied when moving to another measure, saving, or re-exporting.
    """
    def __init__(self, parent, xml_path: str, pdf_callback=None, style: str = "tab"):
        super().__init__(parent)

        self.xml_path = os.path.abspath(xml_path)
        self.pdf_callback = pdf_callback
        self.style = style

        self.title(f"🎤 가사 수정 - [{os.path.basename(self.xml_path)}]")
        self.geometry("760x640")
        self.minsize(560, 480)

        # Bring to front
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))

        self.editor = ScoreLyricsEditor(self.xml_path)
        self.current_part_id = self.editor.parts[0]["id"] if self.editor.parts else None
        self.current_measure = 1
        self.total_measures = self.editor.parts[0]["num_measures"] if self.editor.parts else 0
        self.entries = []
        self.unsaved_changes = False

        self._init_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self.editor.parts:
            messagebox.showwarning(
                "알림",
                "이 악보에는 보컬(가사) 파트가 없습니다.\n'보컬 멜로디 + 가사' 파트를 선택해 악보를 다시 생성해 주세요.",
                parent=self
            )
            return
        self._load_measure(1)

    def _init_ui(self):
        top = ctk.CTkFrame(self, corner_radius=10)
        top.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(top, text="🎤 가사 편집기", font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=16, pady=(10, 2))
        ctk.CTkLabel(
            top,
            text="음표마다 한 음절씩 입력합니다. 단어가 다음 음표로 이어지면 끝에 '-'를 붙이세요 "
                 "(예: beau-, ti-, ful). 칸을 비우면 그 음표의 가사가 지워집니다.",
            font=ctk.CTkFont(size=12), text_color="gray75", wraplength=700, justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 8))

        nav = ctk.CTkFrame(top, fg_color="transparent")
        nav.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(nav, text="파트:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 6))
        part_names = [f"{p['name']} ({p['id']})" for p in self.editor.parts] or ["(가사 파트 없음)"]
        self.combo_parts = ctk.CTkOptionMenu(nav, values=part_names, width=180, command=self._on_part_changed)
        self.combo_parts.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(nav, text="마디:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 6))
        ctk.CTkButton(nav, text="◀ 이전", width=60, command=lambda: self._go_to(self.current_measure - 1)).pack(
            side="left", padx=3)
        self.entry_measure = ctk.CTkEntry(nav, width=56, justify="center")
        self.entry_measure.bind("<Return>", lambda e: self._jump())
        self.entry_measure.pack(side="left", padx=4)
        self.lbl_total = ctk.CTkLabel(nav, text=f"/ {self.total_measures}")
        self.lbl_total.pack(side="left", padx=(2, 8))
        ctk.CTkButton(nav, text="다음 ▶", width=60, command=lambda: self._go_to(self.current_measure + 1)).pack(
            side="left", padx=3)

        # Bottom bar first so it is never pushed off-screen
        bottom = ctk.CTkFrame(self, corner_radius=10)
        bottom.pack(side="bottom", fill="x", padx=16, pady=(6, 14))
        self.lbl_status = ctk.CTkLabel(bottom, text="준비됨.", font=ctk.CTkFont(size=12))
        self.lbl_status.pack(side="left", padx=16, pady=10)
        ctk.CTkButton(bottom, text="💾 저장 (XML)", width=120, command=self._save).pack(side="right", padx=10, pady=10)
        ctk.CTkButton(bottom, text="📄 PDF 갱신 및 열기", width=150, fg_color="#107C41", hover_color="#0B5C30",
                      command=self._save_and_reexport_pdf).pack(side="right", padx=8, pady=10)

        overview = ctk.CTkFrame(self, corner_radius=10)
        overview.pack(side="bottom", fill="x", padx=16, pady=4)
        ctk.CTkLabel(overview, text="📜 전체 가사", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=14, pady=(8, 2))
        self.txt_overview = ctk.CTkTextbox(overview, height=90, wrap="word")
        self.txt_overview.pack(fill="x", padx=12, pady=(0, 10))

        self.scroll_notes = ctk.CTkScrollableFrame(self, corner_radius=10)
        self.scroll_notes.pack(side="top", fill="both", expand=True, padx=16, pady=4)

    # ----------------------------------------------------------------- navigation
    def _on_part_changed(self, choice: str):
        self._apply_current()
        pid = choice.split("(")[-1].rstrip(")").strip()
        part = next((p for p in self.editor.parts if p["id"] == pid), None)
        if part:
            self.current_part_id = pid
            self.total_measures = part["num_measures"]
            self.lbl_total.configure(text=f"/ {self.total_measures}")
            self._load_measure(1)

    def _go_to(self, measure: int):
        if self.current_part_id and 1 <= measure <= self.total_measures:
            self._apply_current()
            self._load_measure(measure)

    def _jump(self):
        try:
            self._go_to(int(self.entry_measure.get().strip()))
        except ValueError:
            pass
        self.entry_measure.delete(0, "end")
        self.entry_measure.insert(0, str(self.current_measure))

    def _load_measure(self, measure: int):
        self.current_measure = measure
        self.entry_measure.delete(0, "end")
        self.entry_measure.insert(0, str(measure))

        for child in self.scroll_notes.winfo_children():
            child.destroy()
        self.entries = []

        notes = self.editor.get_measure_lyrics(self.current_part_id, measure)
        if not notes:
            ctk.CTkLabel(self.scroll_notes, text="이 마디에는 부르는 음표가 없습니다 (쉼표).",
                         text_color="gray60").pack(padx=10, pady=25)
        for idx, note in enumerate(notes):
            row = ctk.CTkFrame(self.scroll_notes, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=3)
            ctk.CTkLabel(row, text=f"#{idx + 1}  {note['pitch_name']}", width=90, anchor="w",
                         font=ctk.CTkFont(family="Consolas", size=13)).pack(side="left", padx=(4, 8))
            entry = ctk.CTkEntry(row, font=ctk.CTkFont(size=14))
            entry.insert(0, note["text"])
            entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self.entries.append((entry, note["text"]))
        self._refresh_overview()

    # ----------------------------------------------------------------- editing
    def _apply_current(self) -> bool:
        """Writes this measure's entries into the score if any changed."""
        if not self.current_part_id or not self.entries:
            return False
        texts = [entry.get() for entry, _ in self.entries]
        if texts == [original for _, original in self.entries]:
            return False
        self.editor.set_measure_lyrics(self.current_part_id, self.current_measure, texts)
        self.entries = [(entry, entry.get()) for entry, _ in self.entries]
        self.unsaved_changes = True
        self.lbl_status.configure(text=f"✓ 마디 {self.current_measure} 가사 반영됨 (저장 필요)", text_color="#34C759")
        self._refresh_overview()
        return True

    def _refresh_overview(self):
        self.txt_overview.configure(state="normal")
        self.txt_overview.delete("1.0", "end")
        if self.current_part_id:
            self.txt_overview.insert("1.0", self.editor.full_text(self.current_part_id))
        self.txt_overview.configure(state="disabled")

    def _save(self, show_dialog: bool = True) -> str:
        self._apply_current()
        saved = self.editor.save_xml()
        self.unsaved_changes = False
        self.lbl_status.configure(text=f"💾 저장 완료: {os.path.basename(saved)}", text_color="#34C759")
        if show_dialog:
            messagebox.showinfo("저장 완료", f"가사가 악보에 저장되었습니다:\n{saved}", parent=self)
        return saved

    def _save_and_reexport_pdf(self):
        self._save(show_dialog=False)
        self.lbl_status.configure(text="⏳ PDF 악보 재출력 중 (LilyPond)...", text_color="#FF9500")
        self.update()
        pdf_path = self.editor.reexport_pdf(style=self.style)
        if not pdf_path or not os.path.exists(pdf_path):
            self.lbl_status.configure(text="❌ PDF 생성에 실패했습니다.", text_color="#E05252")
            return
        self.lbl_status.configure(text=f"🎉 PDF 갱신 완료: {os.path.basename(pdf_path)}", text_color="#34C759")
        if self.pdf_callback:
            self.pdf_callback(pdf_path)
        if hasattr(os, "startfile") and messagebox.askyesno("PDF 갱신 완료", "수정된 가사가 반영된 PDF를 열어볼까요?", parent=self):
            os.startfile(pdf_path)

    def _on_close(self):
        self._apply_current()
        if self.unsaved_changes:
            answer = messagebox.askyesnocancel("저장하지 않은 변경", "수정한 가사를 저장할까요?", parent=self)
            if answer is None:
                return
            if answer:
                self._save(show_dialog=False)
        self.destroy()
