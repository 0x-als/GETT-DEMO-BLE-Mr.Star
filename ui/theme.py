import sys

import customtkinter as ctk

from config import Config


class Theme:
    BG = "#0c0d10"
    CARD = "#121318"
    CARD_2 = "#181a21"
    BORDER = "#23252e"
    TRACK = "#1d1f27"

    TEXT = "#eceef3"
    MUTED = "#6f7385"

    OK = "#34d399"
    WARN = "#fbbf24"
    ERR = "#f87171"

    BLUE = "#4c8dff"
    BLUE_HOVER = "#3b7bec"

    PRIMARY = "#eceef3"
    PRIMARY_HOVER = "#ffffff"
    PRIMARY_TEXT = "#0c0d10"

    RADIUS = 22
    ROW_RADIUS = 14

    FAMILY = {"win32": "Segoe UI", "darwin": "Helvetica Neue"}.get(sys.platform, "Noto Sans")
    MONO = {"win32": "Consolas", "darwin": "Menlo"}.get(sys.platform, "DejaVu Sans Mono")

    @classmethod
    def font(cls, size, weight="normal", family=None):
        return ctk.CTkFont(family=family or cls.FAMILY, size=size, weight=weight)

    @staticmethod
    def scale_of(widget):
        if not Config.IS_WIN:
            return 1.0
        try:
            return max(1.0, round(widget.winfo_fpixels("1i") / 96, 2))
        except Exception:
            return 1.0
