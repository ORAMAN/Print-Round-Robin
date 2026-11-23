# -*- coding: utf-8 -*-
"""Light/dark visual system based on FaceSearch loaders/ui_theme.py."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


THEME_PALETTES = {
    "light": {
        "app": "#F2F5F9", "panel": "#F8FAFD", "white": "#FFFFFF",
        "text": "#202B3C", "muted": "#627187", "border": "#D7E0EB",
        "accent": "#0077FF", "accent_active": "#0066DE",
        "accent_disabled": "#9ABFF0", "accent_text": "#FFFFFF",
        "danger_bg": "#FDECEC", "danger_text": "#B42318", "danger_border": "#F5C5C5",
        "selection": "#C9E1FF", "button": "#E5ECF5", "button_active": "#DCE8F6",
        "button_pressed": "#D5E1EF", "button_disabled": "#E9EEF5",
        "disabled_text": "#99A4B4", "progress_track": "#DFE7F1", "scrollbar": "#D7E0EB",
    },
    "dark": {
        "app": "#1C2026", "panel": "#252A31", "white": "#30363F",
        "text": "#E7EAF0", "muted": "#A8B0BC", "border": "#424A55",
        "accent": "#2D77D4", "accent_active": "#2465BA",
        "accent_disabled": "#3E5876", "accent_text": "#FFFFFF",
        "danger_bg": "#482D32", "danger_text": "#FFB4AB", "danger_border": "#704148",
        "selection": "#3C5879", "button": "#363D47", "button_active": "#454E5B",
        "button_pressed": "#2E343D", "button_disabled": "#2B3037",
        "disabled_text": "#77818E", "progress_track": "#343B45", "scrollbar": "#454E5B",
    },
}


# Status colors are deliberately independent of the accent color, but change
# luminance/background contrast between themes (same approach as FaceSearch).
STATUS_PALETTES = {
    "light": {
        "ready":    {"bg": "#E8F5EC", "border": "#27934D", "text": "#176534", "dot": "#218C4C", "title": "#12522B"},
        "printing": {"bg": "#E7F0FF", "border": "#2878D0", "text": "#1554A0", "dot": "#2878D0", "title": "#123F7C"},
        "transfer": {"bg": "#E5F4FF", "border": "#1686B7", "text": "#075B80", "dot": "#1686B7", "title": "#064A68"},
        "warning":  {"bg": "#FFF5DA", "border": "#D99A15", "text": "#855B00", "dot": "#D99A15", "title": "#704900"},
        "error":    {"bg": "#FDECEC", "border": "#D33A32", "text": "#A51F19", "dot": "#D33A32", "title": "#821811"},
        "offline":  {"bg": "#EDF0F4", "border": "#7C8796", "text": "#526070", "dot": "#7C8796", "title": "#3F4A59"},
        "warmup":   {"bg": "#F0E9FF", "border": "#8B5AD0", "text": "#6236A5", "dot": "#8B5AD0", "title": "#4E288A"},
        "unknown":  {"bg": "#EEF2F7", "border": "#9AA7B7", "text": "#5D6A7B", "dot": "#9AA7B7", "title": "#4A5665"},
    },
    "dark": {
        "ready":    {"bg": "#203B2B", "border": "#49B675", "text": "#A5E8BD", "dot": "#5BD18A", "title": "#C4F5D4"},
        "printing": {"bg": "#203650", "border": "#5B9DE5", "text": "#B7D8FF", "dot": "#69ADFF", "title": "#D2E7FF"},
        "transfer": {"bg": "#1D3B49", "border": "#48B2D4", "text": "#B1E8F5", "dot": "#55C6E4", "title": "#D2F5FC"},
        "warning":  {"bg": "#47391F", "border": "#E5B64D", "text": "#FFE2A0", "dot": "#F2C45D", "title": "#FFEDC3"},
        "error":    {"bg": "#48282D", "border": "#F07676", "text": "#FFC1BD", "dot": "#FF7777", "title": "#FFD5D1"},
        "offline":  {"bg": "#30363E", "border": "#8793A1", "text": "#C4CBD4", "dot": "#9BA6B4", "title": "#E0E5EC"},
        "warmup":   {"bg": "#392D4D", "border": "#B18AE6", "text": "#DDC6FF", "dot": "#B992F2", "title": "#EBDFFF"},
        "unknown":  {"bg": "#30363E", "border": "#687586", "text": "#C0C8D3", "dot": "#8995A5", "title": "#DEE4EC"},
    },
}


class ThemeManager:
    def __init__(self, root: tk.Misc, theme: str = "light"):
        self.root = root
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.current = "light"
        self.palette = dict(THEME_PALETTES["light"])
        self.status = STATUS_PALETTES["light"]
        self.apply(theme)

    def apply(self, theme: str) -> None:
        self.current = "dark" if str(theme).lower() in ("dark", "night", "тёмная", "темная") else "light"
        self.palette = dict(THEME_PALETTES[self.current])
        self.status = STATUS_PALETTES[self.current]
        p = self.palette
        s = self.style
        self.root.configure(background=p["app"])
        s.configure(".", font=("Segoe UI", 10), background=p["panel"], foreground=p["text"])
        s.configure("TFrame", background=p["panel"])
        s.configure("App.TFrame", background=p["app"])
        s.configure(
            "TLabelframe", background=p["panel"], bordercolor=p["border"],
            lightcolor=p["border"], darkcolor=p["border"], relief="solid", borderwidth=1,
        )
        s.configure(
            "TLabelframe.Label", background=p["panel"], foreground=p["accent"],
            font=("Segoe UI", 10, "bold"),
        )
        s.configure("TLabel", background=p["panel"], foreground=p["text"])
        s.configure("App.TLabel", background=p["app"], foreground=p["text"])
        s.configure("Muted.TLabel", background=p["app"], foreground=p["muted"], font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=p["app"], foreground=p["text"], font=("Segoe UI", 17, "bold"))
        s.configure("Metric.TLabel", background=p["panel"], foreground=p["text"], font=("Segoe UI", 19, "bold"))
        s.configure("TButton", background=p["button"], foreground=p["text"], padding=(11, 7), relief="flat")
        s.map("TButton", background=[("disabled", p["button_disabled"]), ("pressed", p["button_pressed"]), ("active", p["button_active"])],
              foreground=[("disabled", p["disabled_text"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_text"],
                    padding=(14, 8), font=("Segoe UI", 10, "bold"), relief="flat")
        s.map("Accent.TButton", background=[("disabled", p["accent_disabled"]), ("pressed", p["accent_active"]), ("active", p["accent_active"])])
        s.configure("Danger.TButton", background=p["danger_bg"], foreground=p["danger_text"],
                    padding=(14, 8), relief="flat")
        s.configure("TEntry", fieldbackground=p["white"], foreground=p["text"], insertcolor=p["text"],
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"], padding=5)
        s.configure("TCombobox", fieldbackground=p["white"], foreground=p["text"],
                    background=p["button"], arrowcolor=p["text"], bordercolor=p["border"])
        s.map("TCombobox", fieldbackground=[("readonly", p["white"])])
        s.configure("TNotebook", background=p["app"], bordercolor=p["border"])
        s.configure("TNotebook.Tab", background=p["button"], foreground=p["text"], padding=(12, 7))
        s.map("TNotebook.Tab", background=[("selected", p["panel"]), ("active", p["button_active"])])
        s.configure("Treeview", background=p["white"], fieldbackground=p["white"], foreground=p["text"],
                    rowheight=27, bordercolor=p["border"])
        s.configure("Treeview.Heading", background=p["button"], foreground=p["text"],
                    font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", p["selection"])], foreground=[("selected", p["text"])])
        s.configure("TCheckbutton", background=p["panel"], foreground=p["text"])
        s.configure("TProgressbar", troughcolor=p["progress_track"], background=p["accent"],
                    bordercolor=p["progress_track"])
        s.configure("TScrollbar", background=p["scrollbar"], troughcolor=p["app"], bordercolor=p["app"])
        self._recolor_native(self.root)

    def _recolor_native(self, widget: tk.Misc) -> None:
        p = self.palette
        try:
            if isinstance(widget, tk.Text):
                widget.configure(bg=p["white"], fg=p["text"], insertbackground=p["text"],
                                 selectbackground=p["selection"], highlightbackground=p["border"])
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=p["panel"])
            for child in widget.winfo_children():
                self._recolor_native(child)
        except (tk.TclError, AttributeError):
            pass

    def toggle(self) -> str:
        self.apply("dark" if self.current == "light" else "light")
        return self.current
