"""
UI Theme Module
Colours shared by the desktop GUI and its editors. Each is a (light, dark) pair:
CustomTkinter picks the one for the current appearance, which follows the Windows
light / dark setting (set_appearance_mode("System")).
"""

BG = ("#F3F4F6", "#15171C")              # window
CARD = ("#FFFFFF", "#1E2128")            # section cards
BORDER = ("#E2E5EA", "#2B2F38")
INPUT = ("#F9FAFB", "#16181D")           # entries, log
TEXT = ("#111827", "#E6E8EB")
MUTED = ("#6B7280", "#9AA1AC")

ACCENT = ("#2563EB", "#3B82F6")          # the one colour for primary actions
ACCENT_HOVER = ("#1D4ED8", "#2563EB")
SELECTED = ("#BFDBFE", "#2563EB")        # chosen segment (dark text in light mode)
SELECTED_HOVER = ("#93C5FD", "#1D4ED8")
SECONDARY = ("#E5E7EB", "#2B2F38")       # secondary buttons, unselected segments
SECONDARY_HOVER = ("#D1D5DB", "#363B46")

SUCCESS = ("#15803D", "#4ADE80")
WARNING = ("#B45309", "#FBBF24")
DANGER = ("#DC2626", "#F87171")
DANGER_FILL = ("#DC2626", "#B91C1C")     # cancel button
DANGER_HOVER = ("#B91C1C", "#991B1B")
