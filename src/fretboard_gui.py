"""
Interactive Fretboard TAB Editor Dialog
CustomTkinter graphical fretboard editor for adjusting guitar & bass tablature fingerings.
Visually highlights all enharmonic string positions (동음이현) on a 22-fret virtual fretboard.
"""

import os
import math
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

from src.fretboard_editor import ScoreFretEditor, find_alternative_positions, pitch_to_name


class FretboardEditorDialog(ctk.CTkToplevel):
    """
    Interactive Toplevel Window for editing tablature string and fret assignments.
    """
    def __init__(self, parent, xml_path: str, pdf_callback=None, style: str = "tab"):
        super().__init__(parent)

        self.parent = parent
        self.xml_path = xml_path
        self.pdf_callback = pdf_callback
        self.style = style

        self.title(f"🎸 타브 운지 수정 - [{os.path.basename(self.xml_path)}]")
        self.geometry("1020x780")
        self.minsize(740, 600)

        # Bring to front
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))

        self.editor = ScoreFretEditor(self.xml_path)
        
        # State
        self.current_part_id = None
        self.current_measure = 1
        self.total_measures = 1
        self.measure_notes = []
        self.selected_note_idx = 0
        self.unsaved_changes = 0

        # Fretboard canvas clickable hitboxes: [(x, y, radius, string, fret), ...]
        self.fretboard_hitboxes = []

        self._init_ui()
        self._load_initial_part()

    def _init_ui(self):
        # 1. Top Header & Part / Measure Navigation
        top_frame = ctk.CTkFrame(self, corner_radius=10)
        top_frame.pack(fill="x", padx=16, pady=(14, 8))

        title_lbl = ctk.CTkLabel(
            top_frame,
            text="🎸 기타/베이스 프렛보드 운지 편집기",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        title_lbl.pack(anchor="w", padx=16, pady=(10, 2))

        desc_lbl = ctk.CTkLabel(
            top_frame,
            text="특정 음표를 선택하면 동일한 음정이 나는 지판 위의 모든 대안 자리(동음이현)를 안내합니다. 원하는 자리를 클릭하여 운지를 변경하세요.",
            font=ctk.CTkFont(size=12),
            text_color="gray75"
        )
        desc_lbl.pack(anchor="w", padx=16, pady=(0, 8))

        # File info banner & switcher row
        file_frame = ctk.CTkFrame(top_frame, corner_radius=6, fg_color=("#DCE4EE", "#1E222A"))
        file_frame.pack(fill="x", padx=16, pady=(0, 10))

        lbl_f_icon = ctk.CTkLabel(
            file_frame,
            text="📁 현재 편집 중인 악보:",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        lbl_f_icon.pack(side="left", padx=(12, 4), pady=6)

        self.lbl_file_name = ctk.CTkLabel(
            file_frame,
            text=os.path.basename(self.xml_path),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#34C759"
        )
        self.lbl_file_name.pack(side="left", padx=4, pady=6)

        self.lbl_file_path = ctk.CTkLabel(
            file_frame,
            text=f"({self.xml_path})",
            font=ctk.CTkFont(size=11),
            text_color="gray60"
        )
        self.lbl_file_path.pack(side="left", padx=(4, 12), pady=6)

        btn_switch_file = ctk.CTkButton(
            file_frame,
            text="📂 다른 악보 파일 열기...",
            width=150,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2B7DE9",
            hover_color="#1F5FC2",
            command=self._change_xml_file
        )
        btn_switch_file.pack(side="right", padx=10, pady=6)

        nav_row = ctk.CTkFrame(top_frame, fg_color="transparent")
        nav_row.pack(fill="x", padx=16, pady=(0, 10))

        # Part selector
        lbl_p = ctk.CTkLabel(nav_row, text="악기 파트:", font=ctk.CTkFont(weight="bold"))
        lbl_p.pack(side="left", padx=(0, 8))

        part_names = [f"{p['name']} ({p['id']})" for p in self.editor.parts]
        self.combo_parts = ctk.CTkOptionMenu(
            nav_row,
            values=part_names,
            width=220,
            command=self._on_part_changed
        )
        self.combo_parts.pack(side="left", padx=(0, 25))

        # Measure selector
        lbl_m = ctk.CTkLabel(nav_row, text="마디 이동:", font=ctk.CTkFont(weight="bold"))
        lbl_m.pack(side="left", padx=(0, 8))

        self.btn_prev_m = ctk.CTkButton(nav_row, text="◀ 이전", width=60, command=self._prev_measure)
        self.btn_prev_m.pack(side="left", padx=3)

        self.entry_measure = ctk.CTkEntry(nav_row, width=60, justify="center")
        self.entry_measure.insert(0, "1")
        self.entry_measure.bind("<Return>", lambda e: self._jump_measure())
        self.entry_measure.pack(side="left", padx=4)

        self.lbl_total_m = ctk.CTkLabel(nav_row, text="/ 1 마디")
        self.lbl_total_m.pack(side="left", padx=(2, 8))

        self.btn_next_m = ctk.CTkButton(nav_row, text="다음 ▶", width=60, command=self._next_measure)
        self.btn_next_m.pack(side="left", padx=3)

        btn_go = ctk.CTkButton(nav_row, text="이동", width=50, fg_color="gray35", command=self._jump_measure)
        btn_go.pack(side="left", padx=8)

        # 2. Bottom Action Bar - ALWAYS PACK WITH side="bottom" FIRST so it is NEVER cut off!
        bottom_bar = ctk.CTkFrame(self, corner_radius=10)
        bottom_bar.pack(side="bottom", fill="x", padx=16, pady=(6, 14))

        self.lbl_save_status = ctk.CTkLabel(
            bottom_bar,
            text="준비됨. 음표를 선택하여 운지를 확인하고 수정하세요.",
            font=ctk.CTkFont(size=12)
        )
        self.lbl_save_status.pack(side="left", padx=16, pady=10)

        self.btn_save_xml = ctk.CTkButton(
            bottom_bar,
            text="💾 변경사항 저장 (XML)",
            width=150,
            command=self._save_changes
        )
        self.btn_save_xml.pack(side="right", padx=10, pady=10)

        self.btn_reexport_pdf = ctk.CTkButton(
            bottom_bar,
            text="📄 PDF 즉시 갱신 및 열기",
            width=180,
            fg_color="#107C41",
            hover_color="#0B5C30",
            command=self._save_and_reexport_pdf
        )
        self.btn_reexport_pdf.pack(side="right", padx=8, pady=10)

        # 3. Main Content Split: Left (Measure Notes List), Right (Fretboard & Alternatives)
        main_split = ctk.CTkFrame(self, fg_color="transparent")
        main_split.pack(side="top", fill="both", expand=True, padx=16, pady=4)

        # Left Column: Measure Notes List
        left_frame = ctk.CTkFrame(main_split, width=320, corner_radius=10)
        left_frame.pack(side="left", fill="both", padx=(0, 8), pady=0)
        left_frame.pack_propagate(False)

        lbl_notes_title = ctk.CTkLabel(
            left_frame,
            text="🎼 마디 내 음표 목록",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        lbl_notes_title.pack(anchor="w", padx=14, pady=(10, 4))

        self.lbl_measure_info = ctk.CTkLabel(
            left_frame,
            text="마디 1 (0개 음표)",
            font=ctk.CTkFont(size=11),
            text_color="gray70"
        )
        self.lbl_measure_info.pack(anchor="w", padx=14, pady=(0, 6))

        self.scroll_notes = ctk.CTkScrollableFrame(left_frame, corner_radius=6)
        self.scroll_notes.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Right Column: Visual Fretboard & Alternative Positions
        right_frame = ctk.CTkFrame(main_split, corner_radius=10)
        right_frame.pack(side="right", fill="both", expand=True, padx=(8, 0), pady=0)

        # Info card for currently selected note
        self.card_note_info = ctk.CTkFrame(right_frame, corner_radius=8, fg_color=("#E5E9F0", "#22252B"))
        self.card_note_info.pack(fill="x", padx=14, pady=(12, 8))

        self.lbl_sel_note = ctk.CTkLabel(
            self.card_note_info,
            text="선택된 음표: 없음",
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.lbl_sel_note.pack(anchor="w", padx=14, pady=(8, 2))

        self.lbl_sel_fret = ctk.CTkLabel(
            self.card_note_info,
            text="현재 운지: 없음",
            font=ctk.CTkFont(size=13),
            text_color="gray75"
        )
        self.lbl_sel_fret.pack(anchor="w", padx=14, pady=(0, 8))

        # Fretboard Canvas Container
        fretboard_box = ctk.CTkFrame(right_frame, corner_radius=8)
        fretboard_box.pack(fill="x", padx=14, pady=4)

        fret_header = ctk.CTkLabel(
            fretboard_box,
            text="🎸 프렛보드 지판 뷰어 (초록색 원을 클릭하면 즉시 운지가 변경됩니다)",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        fret_header.pack(anchor="w", padx=12, pady=(8, 4))

        # Tkinter Canvas for high-precision fretboard rendering
        self.canvas = tk.Canvas(
            fretboard_box,
            height=190,
            bg="#18181C",
            highlightthickness=0
        )
        self.canvas.pack(fill="x", padx=12, pady=(0, 10))
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Configure>", lambda e: self._draw_fretboard())

        # Alternative positions section
        alts_box = ctk.CTkFrame(right_frame, corner_radius=8)
        alts_box.pack(fill="both", expand=True, padx=14, pady=(8, 12))

        lbl_alts_head = ctk.CTkLabel(
            alts_box,
            text="💡 동음이현(同音異絃) 대안 운지 목록 (클릭하여 변경):",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        lbl_alts_head.pack(anchor="w", padx=14, pady=(10, 6))

        self.scroll_alts = ctk.CTkScrollableFrame(alts_box, height=120, corner_radius=6)
        self.scroll_alts.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    def _change_xml_file(self):
        from tkinter import filedialog
        chosen = filedialog.askopenfilename(
            title="수정할 MusicXML 악보 파일 선택",
            filetypes=[("MusicXML 악보 (*.musicxml, *.xml)", "*.musicxml *.xml"), ("모든 파일 (*.*)", "*.*")]
        )
        if chosen and os.path.exists(chosen):
            self.load_new_file(chosen)

    def load_new_file(self, new_xml_path: str):
        self.xml_path = os.path.abspath(new_xml_path)
        self.title(f"🎸 타브 운지 수정 - [{os.path.basename(self.xml_path)}]")
        self.lbl_file_name.configure(text=os.path.basename(self.xml_path))
        self.lbl_file_path.configure(text=f"({self.xml_path})")
        self.editor = ScoreFretEditor(self.xml_path)
        
        # Update combo parts
        part_names = [f"{p['name']} ({p['id']})" for p in self.editor.parts]
        self.combo_parts.configure(values=part_names)
        if part_names:
            self.combo_parts.set(part_names[0])
        self._load_initial_part()
        self.lbl_save_status.configure(
            text=f"새 파일 로드 완료: {os.path.basename(self.xml_path)}",
            text_color="#34C759"
        )

    def _load_initial_part(self):
        if not self.editor.parts:
            messagebox.showwarning("알림", "악보에서 기타/베이스 파트를 찾을 수 없습니다.")
            return

        # Prefer Lead Guitar or Guitar part as default
        lead = next((p for p in self.editor.parts if "lead" in p["name"].lower()), self.editor.parts[0])
        self.current_part_id = lead["id"]
        
        # Set option menu
        for val in self.combo_parts._values:
            if f"({self.current_part_id})" in val:
                self.combo_parts.set(val)
                break

        self.total_measures = lead["num_measures"]
        self.lbl_total_m.configure(text=f"/ {self.total_measures} 마디")
        self.current_measure = 1
        self._load_measure(1)

    def _on_part_changed(self, choice: str):
        # Extract part id from "Lead Guitar (P4)"
        if "(" in choice and ")" in choice:
            pid = choice.split("(")[-1].replace(")", "").strip()
            self.current_part_id = pid
            part = next((p for p in self.editor.parts if p["id"] == pid), None)
            if part:
                self.total_measures = part["num_measures"]
                self.lbl_total_m.configure(text=f"/ {self.total_measures} 마디")
                self.current_measure = min(self.current_measure, self.total_measures)
                self._load_measure(self.current_measure)

    def _prev_measure(self):
        if self.current_measure > 1:
            self._load_measure(self.current_measure - 1)

    def _next_measure(self):
        if self.current_measure < self.total_measures:
            self._load_measure(self.current_measure + 1)

    def _jump_measure(self):
        try:
            val = int(self.entry_measure.get().strip())
            if 1 <= val <= self.total_measures:
                self._load_measure(val)
            else:
                self.entry_measure.delete(0, "end")
                self.entry_measure.insert(0, str(self.current_measure))
        except ValueError:
            pass

    def _load_measure(self, measure_num: int):
        self.current_measure = measure_num
        self.entry_measure.delete(0, "end")
        self.entry_measure.insert(0, str(measure_num))

        self.measure_notes = self.editor.get_measure_notes(self.current_part_id, measure_num)
        self.lbl_measure_info.configure(text=f"마디 {measure_num} ({len(self.measure_notes)}개 음표)")

        # Clear notes list
        for child in self.scroll_notes.winfo_children():
            child.destroy()

        if not self.measure_notes:
            lbl_empty = ctk.CTkLabel(
                self.scroll_notes,
                text="해당 마디에 연주 음표가 없습니다.\n(쉼표 구간)",
                text_color="gray60"
            )
            lbl_empty.pack(padx=10, pady=25)
            self.selected_note_idx = -1
            self._update_note_display()
            return

        self.selected_note_idx = 0

        # Build note items
        for idx, n in enumerate(self.measure_notes):
            chord_tag = " [화음]" if n["is_chord"] else ""
            fret_desc = f"{n['current_string']}번 줄 {n['current_fret']}프렛" if n['current_string'] else "운지 미지정"

            item_btn = ctk.CTkButton(
                self.scroll_notes,
                text=f"#{idx + 1}  {n['pitch_name']}{chord_tag}\n{fret_desc}",
                font=ctk.CTkFont(size=12),
                height=42,
                anchor="w",
                fg_color=("#3B8ED0", "#1F538D") if idx == 0 else ("gray85", "gray20"),
                text_color="white" if idx == 0 else ("gray15", "gray85"),
                command=lambda i=idx: self._select_note(i)
            )
            item_btn.pack(fill="x", padx=4, pady=3)

        self._update_note_display()

    def _select_note(self, idx: int):
        self.selected_note_idx = idx

        # Update button colors in notes list
        for i, child in enumerate(self.scroll_notes.winfo_children()):
            if isinstance(child, ctk.CTkButton):
                if i == idx:
                    child.configure(fg_color=("#3B8ED0", "#1F538D"), text_color="white")
                else:
                    child.configure(fg_color=("gray85", "gray20"), text_color=("gray15", "gray85"))

        self._update_note_display()

    def _update_note_display(self):
        if self.selected_note_idx < 0 or not self.measure_notes:
            self.lbl_sel_note.configure(text="선택된 음표: 없음")
            self.lbl_sel_fret.configure(text="현재 운지: 없음")
            self._draw_fretboard()
            self._render_alternatives([])
            return

        note = self.measure_notes[self.selected_note_idx]
        pname = note["pitch_name"]
        midi_p = note["midi_pitch"]
        c_str = note["current_string"]
        c_fret = note["current_fret"]

        self.lbl_sel_note.configure(text=f"선택된 음표: {pname} (MIDI: {midi_p})")
        if c_str is not None and c_fret is not None:
            self.lbl_sel_fret.configure(
                text=f"현재 운지: {c_str}번 줄 {c_fret}프렛  |  동음이현 대안: {len(note['alternatives'])}개 자리 가능"
            )
        else:
            self.lbl_sel_fret.configure(text="현재 운지: 미지정  |  대안 자리 선택 가능")

        self._draw_fretboard()
        self._render_alternatives(note["alternatives"])

    def _render_alternatives(self, alts: list):
        for child in self.scroll_alts.winfo_children():
            child.destroy()

        if not alts:
            return

        for alt in alts:
            s_num = alt["string"]
            f_num = alt["fret"]
            is_cur = alt["is_current"]

            prefix = "★ [현재] " if is_cur else "👉 [선택] "
            span_text = f" (거리: {alt['span_distance']:.0f}프렛)" if not is_cur and alt['span_distance'] > 0 else ""
            if f_num == 0:
                span_text = " (개방현 - 0프렛)"

            btn = ctk.CTkButton(
                self.scroll_alts,
                text=f"{prefix}{s_num}번 줄 {f_num}프렛{span_text}",
                font=ctk.CTkFont(size=12, weight="bold" if is_cur else "normal"),
                fg_color="#107C41" if not is_cur else "gray40",
                hover_color="#0B5C30" if not is_cur else "gray50",
                height=32,
                anchor="w",
                command=lambda s=s_num, f=f_num: self._apply_alternative(s, f)
            )
            btn.pack(fill="x", padx=6, pady=2)

    def _apply_alternative(self, new_string: int, new_fret: int):
        if self.selected_note_idx < 0 or not self.measure_notes:
            return

        note = self.measure_notes[self.selected_note_idx]
        ok = self.editor.update_note_position(
            part_id=self.current_part_id,
            measure_number=self.current_measure,
            note_index=self.selected_note_idx,
            new_string=new_string,
            new_fret=new_fret
        )

        if ok:
            self.unsaved_changes += 1
            # Refresh local measure data
            self.measure_notes = self.editor.get_measure_notes(self.current_part_id, self.current_measure)
            
            # Update button text in notes list
            for i, child in enumerate(self.scroll_notes.winfo_children()):
                if i == self.selected_note_idx and isinstance(child, ctk.CTkButton):
                    n = self.measure_notes[self.selected_note_idx]
                    chord_tag = " [화음]" if n["is_chord"] else ""
                    fret_desc = f"{n['current_string']}번 줄 {n['current_fret']}프렛"
                    child.configure(text=f"#{i + 1}  {n['pitch_name']}{chord_tag}\n{fret_desc}")

            self._update_note_display()
            self.lbl_save_status.configure(
                text=f"✓ 마디 {self.current_measure} #{self.selected_note_idx + 1} 음표가 [{new_string}번 줄 {new_fret}프렛]으로 변경되었습니다! (저장 필요)",
                text_color="#34C759"
            )

    def _draw_fretboard(self):
        """Renders the graphical 22-fret neck on the tk.Canvas."""
        self.canvas.delete("all")
        self.fretboard_hitboxes = []

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 10 or h <= 10:
            w, h = 660, 190

        part = next((p for p in self.editor.parts if p["id"] == self.current_part_id), None)
        is_bass = (part and "bass" in part["instrument_type"])
        num_strings = 4 if is_bass else 6
        num_frets = 22

        pad_left = 45
        pad_right = 20
        pad_top = 22
        pad_bottom = 28

        neck_w = w - pad_left - pad_right
        neck_h = h - pad_top - pad_bottom

        # Fret X coordinates (with natural semi-exponential compression towards high frets)
        fret_x = [pad_left]  # Nut is index 0
        scale_len = neck_w / (1.0 - (0.5 ** (num_frets / 17.817)))
        for f in range(1, num_frets + 1):
            x_val = pad_left + scale_len * (1.0 - (0.5 ** (f / 17.817)))
            fret_x.append(x_val)

        # Draw Rosewood/Ebony Fretboard Body
        self.canvas.create_rectangle(
            pad_left, pad_top, fret_x[-1], pad_top + neck_h,
            fill="#262220", outline="#3C3632", width=1
        )

        # Draw Nut (Fret 0)
        self.canvas.create_rectangle(
            pad_left - 5, pad_top - 2, pad_left, pad_top + neck_h + 2,
            fill="#EDEAE1", outline="#C2BEB2", width=1
        )

        # Inlay Dots (Pearloid dots at frets 3, 5, 7, 9, 15, 17, 19, 21 and double dot at 12)
        inlay_frets = [3, 5, 7, 9, 15, 17, 19, 21]
        mid_y = pad_top + neck_h / 2.0
        for f in inlay_frets:
            if f <= num_frets:
                dot_x = (fret_x[f - 1] + fret_x[f]) / 2.0
                self.canvas.create_oval(
                    dot_x - 4.5, mid_y - 4.5, dot_x + 4.5, mid_y + 4.5,
                    fill="#4A4540", outline="#5A544D"
                )

        # Fret 12 double dots
        if 12 <= num_frets:
            dot_x12 = (fret_x[11] + fret_x[12]) / 2.0
            offset_y = neck_h * 0.22
            self.canvas.create_oval(
                dot_x12 - 4.5, mid_y - offset_y - 4.5, dot_x12 + 4.5, mid_y - offset_y + 4.5,
                fill="#4A4540", outline="#5A544D"
            )
            self.canvas.create_oval(
                dot_x12 - 4.5, mid_y + offset_y - 4.5, dot_x12 + 4.5, mid_y + offset_y + 4.5,
                fill="#4A4540", outline="#5A544D"
            )

        # Draw Fret Wires (Vertical silver lines)
        for f in range(1, num_frets + 1):
            x = fret_x[f]
            self.canvas.create_line(x, pad_top, x, pad_top + neck_h, fill="#7A8089", width=2)
            # Fret numbers along bottom
            if f in [1, 3, 5, 7, 9, 12, 15, 17, 19, 21]:
                self.canvas.create_text(
                    (fret_x[f - 1] + x) / 2.0, pad_top + neck_h + 14,
                    text=str(f), fill="gray60", font=("Consolas", 10, "bold")
                )

        # Fret 0 (Nut) label
        self.canvas.create_text(
            pad_left / 2.0, pad_top + neck_h + 14,
            text="0 (Nut)", fill="gray50", font=("Consolas", 9)
        )

        # Draw Strings (Horizontal lines: String 1 is highest pitch at the top)
        string_y = {}
        str_step = neck_h / (num_strings - 1) if num_strings > 1 else neck_h
        for s in range(1, num_strings + 1):
            # String 1 at top (pad_top), String 6 (or 4) at bottom
            sy = pad_top + (s - 1) * str_step
            string_y[s] = sy

            # Gauge thickness (String 1 thin, String 6 thick)
            gauge = 1.0 + (s - 1) * (2.8 / (num_strings - 1))
            self.canvas.create_line(
                pad_left - 10, sy, fret_x[-1], sy,
                fill="#B8B09F", width=int(gauge)
            )

            # String label on left
            t_name = {
                (False, 1): "1 (E)", (False, 2): "2 (B)", (False, 3): "3 (G)",
                (False, 4): "4 (D)", (False, 5): "5 (A)", (False, 6): "6 (E)",
                (True, 1): "1 (G)", (True, 2): "2 (D)", (True, 3): "3 (A)", (True, 4): "4 (E)"
            }.get((is_bass, s), f"{s}")
            self.canvas.create_text(
                pad_left - 18, sy, text=t_name, fill="gray75", font=("Arial", 9, "bold")
            )

        # Plot Note Markers
        if not self.measure_notes or self.selected_note_idx < 0:
            return

        active_note = self.measure_notes[self.selected_note_idx]
        cur_s = active_note["current_string"]
        cur_f = active_note["current_fret"]
        alts = active_note["alternatives"]

        def get_pos_coords(s, f):
            y_pos = string_y.get(s, pad_top)
            if f == 0:
                x_pos = pad_left - 5  # At the nut
            else:
                x_pos = (fret_x[f - 1] + fret_x[f]) / 2.0
            return x_pos, y_pos

        # 1. Plot Alternative Candidates (Glowing Green Circles)
        for alt in alts:
            s_cand = alt["string"]
            f_cand = alt["fret"]
            is_cur = alt["is_current"]

            if is_cur or s_cand not in string_y:
                continue

            cx, cy = get_pos_coords(s_cand, f_cand)
            r = 10

            # Store hitbox for click selection
            self.fretboard_hitboxes.append((cx, cy, r + 4, s_cand, f_cand))

            # Draw green candidate dot
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill="#107C41", outline="#34C759", width=2
            )
            self.canvas.create_text(
                cx, cy, text=str(f_cand),
                fill="white", font=("Arial", 9, "bold")
            )

        # 2. Plot Current Active Note (Bold Blue/Cyan Circle)
        if cur_s is not None and cur_f is not None and cur_s in string_y:
            cx, cy = get_pos_coords(cur_s, cur_f)
            r = 12

            # Glow ring
            self.canvas.create_oval(
                cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3,
                outline="#00B4D8", width=2
            )
            # Center circle
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill="#0077B6", outline="#90E0EF", width=2
            )
            self.canvas.create_text(
                cx, cy, text=str(cur_f),
                fill="white", font=("Arial", 10, "bold")
            )

    def _on_canvas_click(self, event):
        """Detects if user clicked on any alternative candidate circle on the fretboard."""
        click_x, click_y = event.x, event.y
        for (cx, cy, r, s_cand, f_cand) in self.fretboard_hitboxes:
            dist = math.hypot(click_x - cx, click_y - cy)
            if dist <= r:
                self._apply_alternative(s_cand, f_cand)
                break

    def _save_changes(self):
        """Saves current modifications back to MusicXML."""
        saved_path = self.editor.save_xml()
        self.unsaved_changes = 0
        self.lbl_save_status.configure(
            text=f"💾 악보 저장 완료: {os.path.basename(saved_path)}",
            text_color="#34C759"
        )
        messagebox.showinfo("저장 완료", f"수정된 운지가 MusicXML 악보에 성공적으로 반영되었습니다:\n{saved_path}")

    def _save_and_reexport_pdf(self):
        """Saves MusicXML and re-compiles TAB PDF via LilyPond."""
        self._save_changes()
        self.lbl_save_status.configure(text="⏳ PDF 악보 재출력 중 (LilyPond)...", text_color="#FF9500")
        self.update()

        try:
            pdf_path = self.editor.reexport_pdf(style=self.style)
            if pdf_path and os.path.exists(pdf_path):
                self.lbl_save_status.configure(
                    text=f"🎉 PDF 악보 갱신 완료: {os.path.basename(pdf_path)}",
                    text_color="#34C759"
                )
                if self.pdf_callback:
                    self.pdf_callback(pdf_path)

                ans = messagebox.askyesno(
                    "PDF 갱신 완료",
                    f"수정된 운지가 반영된 새 PDF 악보가 생성되었습니다!\n\n지금 바로 열어보시겠습니까?"
                )
                if ans:
                    os.startfile(pdf_path)
            else:
                self.lbl_save_status.configure(text="❌ PDF 생성에 실패했습니다.", text_color="#E05252")
        except Exception as e:
            messagebox.showerror("오류", f"PDF 재출력 중 오류가 발생했습니다:\n{str(e)}")


if __name__ == "__main__":
    # Test runner for standalone debugging
    ctk.set_appearance_mode("Dark")
    root = ctk.CTk()
    root.title("Main Window")
    root.geometry("400x300")

    xml_test = os.path.abspath("output/scores/guitar_lead_rhythm_bass_score.musicxml")
    btn = ctk.CTkButton(
        root, text="Open Fretboard Editor",
        command=lambda: FretboardEditorDialog(root, xml_test)
    )
    btn.pack(expand=True)
    root.mainloop()
