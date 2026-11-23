# -*- coding: utf-8 -*-
"""Light/dark palettes matched to the FaceSearch HTTP server theme."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


# Values follow the CSS variables in FaceSearch/server_http.py:
# dark:  #151515 / #242424 / #383838 / #eeeeee / #167ac6
# light: #f4f5f7 / #ffffff / #c8cfd8 / #202124 / #1479c9
THEME_PALETTES = {
    "light": {
        "app": "#f4f5f7", "panel": "#ffffff", "white": "#ffffff",
        "text": "#202124", "muted": "#62666b", "border": "#c8cfd8",
        "accent": "#1479c9", "accent_active": "#1068ad",
        "accent_disabled": "#9fc5e4", "accent_text": "#ffffff",
        "danger_bg": "#fcebea", "danger_text": "#9b2c2c", "danger_border": "#e4aaaa",
        "selection": "#d5e8f8", "button": "#e5e7eb", "button_active": "#d1d5db",
        "button_pressed": "#c8cdd4", "button_disabled": "#eceef1",
        "disabled_text": "#92979e", "progress_track": "#e9eaec", "scrollbar": "#c8cfd8",
    },
    "dark": {
        "app": "#151515", "panel": "#242424", "white": "#1c1c1c",
        "text": "#eeeeee", "muted": "#aaaaaa", "border": "#383838",
        "accent": "#167ac6", "accent_active": "#1268a9",
        "accent_disabled": "#496b87", "accent_text": "#ffffff",
        "danger_bg": "#3e2323", "danger_text": "#ffc1bd", "danger_border": "#704040",
        "selection": "#244765", "button": "#333333", "button_active": "#444444",
        "button_pressed": "#292929", "button_disabled": "#252525",
        "disabled_text": "#777777", "progress_track": "#333333", "scrollbar": "#383838",
    },
}


# Status cards use the same neutral surfaces and muted accent family as the
# FaceSearch server, with semantic colors retained for operational states.
STATUS_PALETTES = {
    "light": {
        "ready":    {"bg": "#eaf5ee", "border": "#359b67", "text": "#155b35", "dot": "#218c52", "title": "#174d31"},
        "printing": {"bg": "#eaf3fb", "border": "#1479c9", "text": "#0b568f", "dot": "#1479c9", "title": "#104c7b"},
        "transfer": {"bg": "#e7f4fb", "border": "#1685a8", "text": "#075b75", "dot": "#1685a8", "title": "#07506a"},
        "warning":  {"bg": "#fff3dc", "border": "#d49a32", "text": "#805700", "dot": "#d49a32", "title": "#805700"},
        "error":    {"bg": "#fcebea", "border": "#c83e3e", "text": "#9b2c2c", "dot": "#c83e3e", "title": "#872929"},
        "offline":  {"bg": "#e9eaec", "border": "#aab4c0", "text": "#62666b", "dot": "#8b929a", "title": "#45494e"},
        "warmup":   {"bg": "#f0eafa", "border": "#8b5cf6", "text": "#5b3a91", "dot": "#8b5cf6", "title": "#4e327d"},
        "unknown":  {"bg": "#ffffff", "border": "#aab4c0", "text": "#62666b", "dot": "#94a3b8", "title": "#202124"},
    },
    "dark": {
        "ready":    {"bg": "#1b3025", "border": "#359b67", "text": "#a8dfbd", "dot": "#4ab47b", "title": "#d0f0dc"},
        "printing": {"bg": "#1b3044", "border": "#167ac6", "text": "#b7d8ff", "dot": "#5ba7e6", "title": "#d2e7ff"},
        "transfer": {"bg": "#1a3038", "border": "#1685a8", "text": "#b1e8f5", "dot": "#38b6d7", "title": "#d2f5fc"},
        "warning":  {"bg": "#3b301d", "border": "#d49a32", "text": "#ffe0a1", "dot": "#e5b04d", "title": "#ffedc3"},
        "error":    {"bg": "#3e2323", "border": "#c83e3e", "text": "#ffb8b8", "dot": "#e05a5a", "title": "#ffd5d1"},
        "offline":  {"bg": "#242424", "border": "#626b76", "text": "#aaaaaa", "dot": "#94a3b8", "title": "#eeeeee"},
        "warmup":   {"bg": "#30253d", "border": "#8b5cf6", "text": "#dbc5ff", "dot": "#a78bfa", "title": "#eee1ff"},
        "unknown":  {"bg": "#242424", "border": "#626b76", "text": "#eeeeee", "dot": "#94a3b8", "title": "#eeeeee"},
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
        s.map(
            "TButton",
            background=[("disabled", p["button_disabled"]), ("pressed", p["button_pressed"]), ("active", p["button_active"])],
            foreground=[("disabled", p["disabled_text"])],
        )
        s.configure(
            "Accent.TButton", background=p["accent"], foreground=p["accent_text"],
            padding=(14, 8), font=("Segoe UI", 10, "bold"), relief="flat",
        )
        s.map("Accent.TButton", background=[("disabled", p["accent_disabled"]), ("pressed", p["accent_active"]), ("active", p["accent_active"])])
        s.configure(
            "Danger.TButton", background=p["danger_bg"], foreground=p["danger_text"],
            padding=(14, 8), relief="flat",
        )
        s.map("Danger.TButton", background=[("active", p["danger_border"])])
        s.configure(
            "TEntry", fieldbackground=p["white"], foreground=p["text"], insertcolor=p["text"],
            bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"], padding=5,
        )
        s.configure(
            "TCombobox", fieldbackground=p["white"], foreground=p["text"],
            background=p["button"], arrowcolor=p["text"], bordercolor=p["border"],
        )
        s.map("TCombobox", fieldbackground=[("readonly", p["white"])], foreground=[("readonly", p["text"])])
        s.configure("TNotebook", background=p["app"], bordercolor=p["border"])
        s.configure("TNotebook.Tab", background=p["button"], foreground=p["text"], padding=(12, 7))
        s.map("TNotebook.Tab", background=[("selected", p["panel"]), ("active", p["button_active"])])
        s.configure(
            "Treeview", background=p["white"], fieldbackground=p["white"], foreground=p["text"],
            rowheight=27, bordercolor=p["border"],
        )
        s.configure("Treeview.Heading", background=p["button"], foreground=p["text"], font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", p["selection"])], foreground=[("selected", p["text"])])
        s.configure("TCheckbutton", background=p["panel"], foreground=p["text"])
        s.map("TCheckbutton", foreground=[("disabled", p["disabled_text"])])
        s.configure("TProgressbar", troughcolor=p["progress_track"], background=p["accent"], bordercolor=p["progress_track"])
        s.configure("TScrollbar", background=p["scrollbar"], troughcolor=p["app"], bordercolor=p["app"])
        self._recolor_native(self.root)

    def _recolor_native(self, widget: tk.Misc) -> None:
        p = self.palette
        try:
            if isinstance(widget, tk.Text):
                widget.configure(
                    bg=p["white"], fg=p["text"], insertbackground=p["text"],
                    selectbackground=p["selection"], highlightbackground=p["border"],
                )
            elif isinstance(widget, tk.Label):
                widget.configure(bg=p["panel"], fg=p["text"])
            elif isinstance(widget, tk.Frame):
                widget.configure(bg=p["panel"])
            for child in widget.winfo_children():
                self._recolor_native(child)
        except (tk.TclError, AttributeError):
            pass

    def toggle(self) -> str:
        self.apply("dark" if self.current == "light" else "light")
        return self.current
