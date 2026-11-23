# -*- coding: utf-8 -*-
"""Tkinter desktop interface for Print Round Robin."""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import windows
from .config import AppConfig, ConfigStore, PrinterConfig, app_data_dir
from .engine import RoundRobinEngine
from .theme import ThemeManager


class QueueLogHandler(logging.Handler):
    def __init__(self, target: queue.Queue):
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)


def configure_logging(target: queue.Queue) -> logging.Logger:
    app_data_dir().mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("PrintRoundRobin")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        file_handler = logging.handlers.RotatingFileHandler(
            app_data_dir() / "print_round_robin.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        gui_handler = QueueLogHandler(target)
        gui_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(gui_handler)
    return logger


class PrinterDialog(tk.Toplevel):
    def __init__(self, parent, title: str, printer: PrinterConfig | None = None,
                 default_port: int = 9100):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: PrinterConfig | None = None
        source = printer or PrinterConfig("Kyocera", "192.168.20.121", default_port)
        self.name_var = tk.StringVar(value=source.name)
        self.host_var = tk.StringVar(value=source.host)
        self.port_var = tk.StringVar(value=str(source.port))
        self.enabled_var = tk.BooleanVar(value=source.enabled)

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        for row, (label, variable) in enumerate((
            ("Название", self.name_var),
            ("IP-адрес", self.host_var),
            ("RAW TCP-порт", self.port_var),
        )):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(body, textvariable=variable, width=34).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(
            body, text="Участвует в распределении", variable=self.enabled_var
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 12))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right")
        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    def _save(self):
        try:
            result = PrinterConfig(
                name=self.name_var.get(),
                host=self.host_var.get(),
                port=int(self.port_var.get()),
                enabled=self.enabled_var.get(),
            )
            result.validate()
            self.result = result
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Некорректные данные", str(exc), parent=self)


class PrintRoundRobinApp:
    SETTINGS_FIELDS = (
        ("queue_name", "Имя виртуального принтера", str),
        ("port_name", "Имя локального TCP/IP-порта", str),
        ("listener_host", "Локальный адрес приёма", str),
        ("listener_port", "Локальный порт", int),
        ("default_printer_port", "RAW-порт по умолчанию для новых принтеров", int),
        ("connect_timeout", "Таймаут подключения, с", float),
        ("send_timeout", "Таймаут передачи, с", float),
        ("retry_delay", "Повтор при недоступности, с", float),
        ("busy_retry_delay", "Пауза, если принтеры заняты, с", float),
        ("health_interval", "Интервал проверки доступности, с", float),
        ("receive_buffer_size", "Буфер приёма/передачи, байт", int),
        ("max_receivers", "Максимум принимаемых заданий", int),
        ("max_workers", "Параллельные рабочие потоки", int),
    )

    def __init__(self, root: tk.Tk, minimized: bool = False):
        self.root = root
        self.root.title("Print Round Robin — управление печатью")
        self.root.geometry("1600x900")
        self.root.minsize(1280, 800)
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass
        self.events: queue.Queue = queue.Queue()
        self.log_events: queue.Queue = queue.Queue()
        self.logger = configure_logging(self.log_events)
        self.store = ConfigStore()
        try:
            self.config = self.store.load()
        except Exception as exc:
            messagebox.showerror(
                "Настройки",
                f"Не удалось загрузить сохранённые настройки. Будут созданы значения по умолчанию.\n\n{exc}",
                parent=root,
            )
            self.config = AppConfig()
            self.store.save(self.config)

        self.theme_manager = ThemeManager(self.root, self.config.theme)
        self.printer_cards: dict[str, dict] = {}
        self.engine: RoundRobinEngine | None = None
        self.engine_starting = False
        self.admin_result_path: Path | None = None
        self.admin_action_kind = ""
        self.admin_started_at = 0.0
        self.driver_names: list[str] = []
        self._closing = False

        self._build_style()
        self._build_ui()
        self._load_config_to_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._poll_events)
        self.root.after(250, self._poll_logs)
        self.root.after(1000, self._poll_admin_result)
        self.root.after(1200, self._refresh_dashboard)
        self.root.after(300, self._refresh_queue_info)
        self.root.after(400, self.refresh_drivers)
        if minimized:
            self.root.iconify()
        if self.config.auto_start_engine:
            self.root.after(1000, self.start_engine)

    def _build_style(self):
        self.theme_manager.apply(self.config.theme)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="PRINT ROUND ROBIN", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header, text="Windows 10/11  •  Tkinter  •  RAW TCP",
            foreground="#666666",
        ).pack(side="left", padx=16)
        self.engine_badge = ttk.Label(
            header, text="● ОСТАНОВЛЕН", foreground="#a33",
            font=("Segoe UI", 10, "bold"),
        )
        self.engine_badge.pack(side="right")

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(
            toolbar, text="▶ Запустить", style="Main.TButton", command=self.start_engine
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            toolbar, text="■ Остановить", command=self.stop_engine, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(6, 14))
        ttk.Button(toolbar, text="Сохранить настройки", command=self.save_settings).pack(side="left")
        ttk.Button(toolbar, text="Импорт JSON", command=self.import_settings).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Экспорт JSON", command=self.export_settings).pack(side="left")
        self.theme_button = ttk.Button(toolbar, text="", command=self.toggle_theme)
        self.theme_button.pack(side="right")
        self._update_theme_button()

        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.monitor_tab = ttk.Frame(self.tabs, padding=10)
        self.printers_tab = ttk.Frame(self.tabs, padding=10)
        self.settings_tab = ttk.Frame(self.tabs, padding=10)
        self.windows_tab = ttk.Frame(self.tabs, padding=10)
        self.logs_tab = ttk.Frame(self.tabs, padding=10)
        for tab, title in (
            (self.monitor_tab, "Мониторинг"),
            (self.printers_tab, "Принтеры"),
            (self.settings_tab, "Параметры"),
            (self.windows_tab, "Драйвер и очередь"),
            (self.logs_tab, "Журнал"),
        ):
            self.tabs.add(tab, text=title)

        self._build_monitor()
        self._build_printers()
        self._build_settings()
        self._build_windows_tab()
        self._build_logs()
        self.footer_var = tk.StringVar(value=str(self.store.path))
        ttk.Label(outer, textvariable=self.footer_var, foreground="#666666", anchor="w").pack(fill="x", pady=(7, 0))

    def _tree(self, parent, columns, titles, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for col, title, width in zip(columns, titles, widths):
            tree.heading(col, text=title)
            tree.column(col, width=width, minwidth=75, anchor="w")
        tree.tag_configure("ok", foreground="#18733a")
        tree.tag_configure("busy", foreground="#986000")
        tree.tag_configure("error", foreground="#b3261e")
        return tree

    def _build_monitor(self):
        metrics = ttk.Frame(self.monitor_tab)
        metrics.pack(fill="x", pady=(0, 7))
        self.metric_vars = {}
        labels = (
            ("received", "Принято"),
            ("queued", "Ожидает"),
            ("active", "Передаётся"),
            ("completed", "Передано"),
            ("failed", "На проверке"),
        )
        for col, (key, title) in enumerate(labels):
            card = ttk.LabelFrame(metrics, text=title, padding=8)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0))
            metrics.columnconfigure(col, weight=1)
            var = tk.StringVar(value="0")
            self.metric_vars[key] = var
            ttk.Label(card, textvariable=var, style="Metric.TLabel").pack()

        legend = ttk.Frame(self.monitor_tab)
        legend.pack(fill="x", pady=(0, 5))
        self.legend_items = [
            ("ready", "ГОТОВ / ОЖИДАЕТ"),
            ("printing", "ПЕЧАТАЕТ"),
            ("transfer", "ПЕРЕДАЧА"),
            ("warning", "ПРЕДУПРЕЖДЕНИЕ"),
            ("error", "ОШИБКА"),
            ("offline", "НЕТ СВЯЗИ"),
            ("warmup", "ПРОГРЕВ"),
        ]
        for key, caption in self.legend_items:
            item = tk.Frame(legend, bd=0)
            item.pack(side="left", padx=(0, 14))
            dot = tk.Label(item, text="●", font=("Segoe UI", 10, "bold"))
            dot.pack(side="left")
            text = ttk.Label(item, text=caption, style="Muted.TLabel")
            text.pack(side="left", padx=(4, 0))
            self._legend_widgets = getattr(self, "_legend_widgets", [])
            self._legend_widgets.append((key, item, dot))

        split = ttk.Panedwindow(self.monitor_tab, orient="vertical")
        split.pack(fill="both", expand=True)
        printer_box = ttk.LabelFrame(split, text="Состояние физических принтеров  •  10 устройств", padding=5)
        job_box = ttk.LabelFrame(split, text="Задания печати", padding=6)
        split.add(printer_box, weight=2)
        split.add(job_box, weight=1)

        self.printer_grid = tk.Frame(
            printer_box, bg=self.theme_manager.palette["panel"], bd=0, highlightthickness=0
        )
        self.printer_grid.pack(fill="both", expand=True)
        for col in range(5):
            self.printer_grid.grid_columnconfigure(col, weight=1, uniform="printer-card")
        for row in range(2):
            self.printer_grid.grid_rowconfigure(row, weight=1, uniform="printer-row")

        self.printer_cards = {}
        for index, printer in enumerate(self.config.printers[:10]):
            self._create_printer_card(printer, index)

        self.job_tree = self._tree(
            job_box,
            ("id", "status", "size", "printer", "received", "progress"),
            ("ID задания", "Состояние", "Размер", "Принтер", "Принято", "Передача / ошибка"),
            (205, 150, 100, 140, 145, 310),
        )

    def _create_printer_card(self, printer: PrinterConfig, index: int) -> None:
        """Create one large status card; all ten cards remain visible in a 5x2 grid."""
        palette = self.theme_manager.palette
        state_palette = self.theme_manager.status[self.theme_manager.current]["unknown"]
        frame = tk.Frame(
            self.printer_grid,
            bg=state_palette["bg"],
            highlightbackground=state_palette["border"],
            highlightcolor=state_palette["border"],
            highlightthickness=2,
            bd=0,
            padx=9,
            pady=7,
        )
        frame.grid(
            row=index // 5, column=index % 5, sticky="nsew",
            padx=4, pady=4,
        )
        header = tk.Frame(frame, bg=state_palette["bg"])
        header.pack(fill="x")
        name_label = tk.Label(
            header, text=printer.name, bg=state_palette["bg"], fg=state_palette["title"],
            font=("Segoe UI", 10, "bold"), anchor="w",
        )
        name_label.pack(side="left", fill="x", expand=True)
        dot_label = tk.Label(
            header, text="●", bg=state_palette["bg"], fg=state_palette["dot"],
            font=("Segoe UI", 15, "bold"),
        )
        dot_label.pack(side="right")
        ip_label = tk.Label(
            frame, text=f"{printer.host}:{printer.port}", bg=state_palette["bg"],
            fg=palette["muted"], font=("Segoe UI", 9), anchor="w",
        )
        ip_label.pack(fill="x", pady=(0, 3))
        state_label = tk.Label(
            frame, text="ОЖИДАНИЕ ЗАПУСКА", bg=state_palette["bg"],
            fg=state_palette["text"], font=("Segoe UI", 13, "bold"), anchor="w",
        )
        state_label.pack(fill="x", pady=(1, 2))
        detail_label = tk.Label(
            frame, text="Состояние устройства пока не опрашивалось",
            bg=state_palette["bg"], fg=state_palette["text"],
            font=("Segoe UI", 9, "bold"), anchor="w", justify="left",
            wraplength=250, height=2,
        )
        detail_label.pack(fill="x")
        toner_label = tk.Label(
            frame, text="Тонер: —", bg=state_palette["bg"],
            fg=state_palette["text"], font=("Segoe UI", 9), anchor="w",
        )
        toner_label.pack(fill="x", pady=(3, 0))
        footer_label = tk.Label(
            frame, text="Сеть: проверка  •  SNMP: —  •  Заданий: 0",
            bg=state_palette["bg"], fg=palette["muted"],
            font=("Segoe UI", 8), anchor="w",
        )
        footer_label.pack(fill="x", side="bottom", pady=(5, 0))

        card = {
            "frame": frame, "header": header, "name": name_label, "dot": dot_label,
            "ip": ip_label, "state": state_label, "detail": detail_label,
            "toner": toner_label, "footer": footer_label,
            "printer": printer,
        }
        self.printer_cards[printer.host] = card
        for widget in (frame, header, name_label, dot_label, ip_label, state_label,
                       detail_label, toner_label, footer_label):
            widget.bind("<Double-Button-1>", lambda _event, host=printer.host: self.show_printer_details(host))

    def _build_printers(self):
        toolbar = ttk.Frame(self.printers_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Добавить", command=self.add_printer).pack(side="left")
        ttk.Button(toolbar, text="Изменить", command=self.edit_printer).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Удалить", command=self.delete_printer).pack(side="left")
        ttk.Button(toolbar, text="Включить / отключить", command=self.toggle_printer).pack(side="left", padx=6)
        ttk.Label(
            toolbar, text="Изменения вступят в силу после перезапуска ретранслятора.",
            foreground="#666666",
        ).pack(side="right")
        self.printer_config_tree = self._tree(
            self.printers_tab,
            ("enabled", "name", "host", "port"),
            ("Участвует", "Название", "IP-адрес", "RAW TCP-порт"),
            (100, 250, 190, 130),
        )
        self.printer_config_tree.bind("<Double-1>", lambda _event: self.edit_printer())

    def _build_settings(self):
        frame = ttk.Frame(self.settings_tab)
        frame.pack(anchor="nw", fill="both", expand=True)
        self.setting_vars = {}
        for row, (key, title, _kind) in enumerate(self.SETTINGS_FIELDS):
            ttk.Label(frame, text=title).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=5)
            value = tk.StringVar()
            self.setting_vars[key] = value
            ttk.Entry(frame, textvariable=value, width=36).grid(row=row, column=1, sticky="w", pady=5)
        row = len(self.SETTINGS_FIELDS)
        self.auto_start_var = tk.BooleanVar()
        self.windows_startup_var = tk.BooleanVar()
        ttk.Checkbutton(
            frame, text="Автоматически запускать ретранслятор при открытии программы",
            variable=self.auto_start_var,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 4))
        ttk.Checkbutton(
            frame, text="Запускать программу при входе в Windows (сворачивать в панель задач)",
            variable=self.windows_startup_var,
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(
            frame,
            text="Программа должна оставаться запущенной, пока требуется печать через виртуальную очередь.",
            foreground="#666666", wraplength=850,
        ).grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_windows_tab(self):
        queue_box = ttk.LabelFrame(self.windows_tab, text="Виртуальная очередь Windows", padding=12)
        queue_box.pack(fill="x", pady=(0, 12))
        self.queue_info_var = tk.StringVar(value="Получение сведений о принтере...")
        ttk.Label(queue_box, textvariable=self.queue_info_var, justify="left").pack(anchor="w")
        self.windows_status_var = tk.StringVar()
        ttk.Label(queue_box, textvariable=self.windows_status_var, foreground="#666666").pack(anchor="w", pady=(6, 0))

        driver_box = ttk.LabelFrame(self.windows_tab, text="Установленный драйвер", padding=12)
        driver_box.pack(fill="x", pady=(0, 12))
        line = ttk.Frame(driver_box)
        line.pack(fill="x")
        ttk.Label(line, text="Драйвер печати:").pack(side="left", padx=(0, 10))
        self.driver_var = tk.StringVar(value=self.config.driver_name)
        self.driver_combo = ttk.Combobox(line, textvariable=self.driver_var, state="readonly", width=70)
        self.driver_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(line, text="Обновить список", command=self.refresh_drivers).pack(side="left", padx=(8, 0))
        ttk.Label(
            driver_box,
            text="Отображаются драйверы, уже установленные в диспетчере печати Windows.",
            foreground="#666666",
        ).pack(anchor="w", pady=(8, 0))
        actions = ttk.Frame(self.windows_tab)
        actions.pack(fill="x", pady=4)
        ttk.Button(
            actions, text="Настроить виртуальную очередь (UAC)",
            style="Main.TButton", command=self.setup_queue,
        ).pack(side="left")
        ttk.Button(
            actions, text="Остановить и удалить старый ретранслятор (UAC)",
            command=self.remove_legacy_task,
        ).pack(side="left", padx=8)
        ttk.Label(
            self.windows_tab,
            text=("Настройка очереди требует подтверждения UAC. Отдельные PowerShell-файлы не используются: "
                  "команды Windows PrintManagement запускаются непосредственно из приложения."),
            foreground="#666666", wraplength=900,
        ).pack(anchor="w", pady=(12, 0))

    def _build_logs(self):
        toolbar = ttk.Frame(self.logs_tab)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Очистить окно", command=self._clear_log_view).pack(side="left")
        ttk.Button(toolbar, text="Открыть файл журнала", command=self.open_log_file).pack(side="left", padx=6)
        self.log_text = tk.Text(self.logs_tab, wrap="none", font=("Consolas", 9), state="disabled")
        ybar = ttk.Scrollbar(self.logs_tab, orient="vertical", command=self.log_text.yview)
        xbar = ttk.Scrollbar(self.logs_tab, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        ybar.pack(side="right", fill="y")
        xbar.pack(side="bottom", fill="x")

    def _load_config_to_controls(self):
        for key, _title, _kind in self.SETTINGS_FIELDS:
            self.setting_vars[key].set(str(getattr(self.config, key)))
        self.auto_start_var.set(self.config.auto_start_engine)
        self.windows_startup_var.set(self.config.start_with_windows)
        self.driver_var.set(self.config.driver_name)
        self._refresh_printer_config_tree()
        self.footer_var.set(f"Настройки: {self.store.path}")

    def _config_from_controls(self) -> AppConfig:
        values = self.config.to_dict()
        values["printers"] = [p.__dict__.copy() for p in self.config.printers]
        for key, _title, kind in self.SETTINGS_FIELDS:
            raw = self.setting_vars[key].get().strip()
            if kind is str:
                values[key] = raw
            elif kind is int:
                values[key] = int(raw)
            else:
                values[key] = float(raw.replace(",", "."))
        values["driver_name"] = self.driver_var.get().strip()
        values["auto_start_engine"] = bool(self.auto_start_var.get())
        values["start_with_windows"] = bool(self.windows_startup_var.get())
        return AppConfig.from_dict(values)

    def save_settings(self, silent: bool = False) -> bool:
        try:
            candidate = self._config_from_controls()
            if self.engine and self.engine.running and candidate.to_dict() != self.config.to_dict():
                if not silent:
                    messagebox.showwarning(
                        "Требуется остановка",
                        "Сначала остановите ретранслятор. Сетевые параметры и список принтеров "
                        "применяются после перезапуска.",
                        parent=self.root,
                    )
                return False
            was_startup = self.config.start_with_windows
            self.store.save(candidate)
            self.config = candidate
            if os.name == "nt" and was_startup != candidate.start_with_windows:
                try:
                    windows.register_startup(candidate.start_with_windows, minimized=True)
                except Exception as exc:
                    messagebox.showwarning("Автозапуск", str(exc), parent=self.root)
            self._refresh_printer_config_tree()
            self.footer_var.set(f"Настройки сохранены: {self.store.path}")
            if not silent:
                messagebox.showinfo("Настройки", "Настройки сохранены.", parent=self.root)
            return True
        except Exception as exc:
            if not silent:
                messagebox.showerror("Ошибка настроек", str(exc), parent=self.root)
            return False

    def import_settings(self):
        if self.engine and self.engine.running:
            messagebox.showwarning("Импорт", "Сначала остановите ретранслятор.", parent=self.root)
            return
        filename = filedialog.askopenfilename(
            parent=self.root, title="Импорт настроек",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
        )
        if not filename:
            return
        try:
            candidate = ConfigStore.import_from(Path(filename))
            was_startup = self.config.start_with_windows
            self.config = candidate
            self.store.save(candidate)
            if os.name == "nt" and was_startup != candidate.start_with_windows:
                windows.register_startup(candidate.start_with_windows, minimized=True)
            self._load_config_to_controls()
            messagebox.showinfo("Импорт", "Настройки импортированы и сохранены.", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Импорт", str(exc), parent=self.root)

    def export_settings(self):
        if not self.save_settings(silent=True):
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root, title="Экспорт настроек", defaultextension=".json",
            initialfile="PrintRoundRobin_settings.json",
            filetypes=[("JSON", "*.json")],
        )
        if not filename:
            return
        try:
            self.store.export_to(Path(filename), self.config)
            messagebox.showinfo("Экспорт", f"Настройки сохранены:\n{filename}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Экспорт", str(exc), parent=self.root)

    def _refresh_printer_config_tree(self):
        if not hasattr(self, "printer_config_tree"):
            return
        for item in self.printer_config_tree.get_children():
            self.printer_config_tree.delete(item)
        for index, printer in enumerate(self.config.printers):
            self.printer_config_tree.insert(
                "", "end", iid=str(index),
                values=("Да" if printer.enabled else "Нет", printer.name, printer.host, printer.port),
            )

    def _selected_printer_index(self) -> int | None:
        selected = self.printer_config_tree.selection()
        if not selected:
            messagebox.showwarning("Принтеры", "Сначала выберите принтер в таблице.", parent=self.root)
            return None
        return int(selected[0])

    def _ensure_engine_stopped(self) -> bool:
        if self.engine and self.engine.running:
            messagebox.showwarning("Изменение конфигурации", "Сначала остановите ретранслятор.", parent=self.root)
            return False
        return True

    def add_printer(self):
        if not self._ensure_engine_stopped():
            return
        dialog = PrinterDialog(self.root, "Добавить принтер", default_port=self.config.default_printer_port)
        self.root.wait_window(dialog)
        if dialog.result:
            if any(p.host == dialog.result.host for p in self.config.printers):
                messagebox.showerror("Принтеры", "Такой IP-адрес уже добавлен.", parent=self.root)
                return
            self.config.printers.append(dialog.result)
            self._refresh_printer_config_tree()

    def edit_printer(self):
        if not self._ensure_engine_stopped():
            return
        index = self._selected_printer_index()
        if index is None:
            return
        dialog = PrinterDialog(self.root, "Изменить принтер", self.config.printers[index])
        self.root.wait_window(dialog)
        if dialog.result:
            if any(i != index and p.host == dialog.result.host for i, p in enumerate(self.config.printers)):
                messagebox.showerror("Принтеры", "Такой IP-адрес уже добавлен.", parent=self.root)
                return
            self.config.printers[index] = dialog.result
            self._refresh_printer_config_tree()

    def delete_printer(self):
        if not self._ensure_engine_stopped():
            return
        index = self._selected_printer_index()
        if index is None:
            return
        if len(self.config.printers) <= 1:
            messagebox.showwarning("Принтеры", "Нельзя удалить последний принтер.", parent=self.root)
            return
        printer = self.config.printers[index]
        if messagebox.askyesno("Удаление", f"Удалить {printer.name} ({printer.host})?", parent=self.root):
            self.config.printers.pop(index)
            self._refresh_printer_config_tree()

    def toggle_printer(self):
        if not self._ensure_engine_stopped():
            return
        index = self._selected_printer_index()
        if index is None:
            return
        item = self.config.printers[index]
        item.enabled = not item.enabled
        if not any(p.enabled for p in self.config.printers):
            item.enabled = True
            messagebox.showwarning("Принтеры", "Должен быть включён хотя бы один принтер.", parent=self.root)
        self._refresh_printer_config_tree()

    def refresh_drivers(self):
        self.windows_status_var.set("Получение списка драйверов Windows...")
        def worker():
            try:
                names = windows.list_installed_drivers()
                self.events.put({"type": "drivers", "names": names})
            except Exception as exc:
                self.events.put({"type": "drivers_error", "message": str(exc)})
        threading.Thread(target=worker, name="DriverEnumerator", daemon=True).start()

    def _refresh_queue_info(self):
        def worker():
            try:
                info = windows.get_queue_info(self.config.queue_name)
                self.events.put({"type": "queue_info", "info": info})
            except Exception as exc:
                self.events.put({"type": "queue_info_error", "message": str(exc)})
        threading.Thread(target=worker, name="QueueInspector", daemon=True).start()

    def setup_queue(self):
        if not self._ensure_engine_stopped() or not self.save_settings(silent=True):
            return
        if not self.driver_var.get().strip():
            messagebox.showwarning("Драйвер", "Выберите установленный драйвер Kyocera.", parent=self.root)
            return
        try:
            self.admin_result_path = windows.request_elevated_action({
                "action": "setup_queue", "config": self.config.to_dict()
            })
            self.admin_action_kind = "setup"
            self.admin_started_at = time.monotonic()
            self.windows_status_var.set("Ожидание подтверждения UAC и настройки виртуальной очереди...")
        except Exception as exc:
            messagebox.showerror("Настройка очереди", str(exc), parent=self.root)

    def remove_legacy_task(self):
        if not self._ensure_engine_stopped():
            return
        answer = messagebox.askyesno(
            "Старая версия",
            "Будет остановлена и удалена задача планировщика «Kyocera Round Robin Relay», "
            "созданная прежней версией программы. Убедитесь, что старая очередь пуста. Продолжить?",
            parent=self.root,
        )
        if not answer:
            return
        try:
            self.admin_result_path = windows.request_elevated_action({
                "action": "stop_legacy_task", "remove": True
            })
            self.admin_action_kind = "legacy"
            self.admin_started_at = time.monotonic()
            self.windows_status_var.set("Ожидание подтверждения UAC...")
        except Exception as exc:
            messagebox.showerror("Старая версия", str(exc), parent=self.root)

    def _poll_admin_result(self):
        if self.admin_result_path:
            result = windows.read_admin_result(self.admin_result_path)
            if result is not None:
                kind = self.admin_action_kind
                path = self.admin_result_path
                self.admin_result_path = None
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                message = result.get("message", "")
                if result.get("success"):
                    self.windows_status_var.set(message.replace("\n", " • "))
                    messagebox.showinfo("Готово", message, parent=self.root)
                    if kind == "setup":
                        self._refresh_queue_info()
                else:
                    self.windows_status_var.set("Ошибка административного действия.")
                    messagebox.showerror("Ошибка", message, parent=self.root)
            elif time.monotonic() - self.admin_started_at > 180:
                self.admin_result_path = None
                self.windows_status_var.set("Действие не завершено: возможно, запрос UAC был отменён.")
        self.root.after(1000, self._poll_admin_result)

    def start_engine(self):
        if self.engine and self.engine.running:
            return
        if self.engine_starting:
            return
        if not self.save_settings(silent=True):
            return
        try:
            config = self._config_from_controls()
            config.validate()
        except Exception as exc:
            messagebox.showerror("Параметры", str(exc), parent=self.root)
            return
        self.config = config
        self.engine_starting = True
        self.start_button.configure(state="disabled")
        self.engine_badge.configure(text="● ЗАПУСК...", foreground="#996600")

        def worker():
            try:
                engine = RoundRobinEngine(config, self.events.put)
                self.engine = engine
                engine.start()
                self.events.put({"type": "engine_started"})
            except Exception as exc:
                self.engine = None
                self.events.put({"type": "engine_start_error", "message": str(exc)})
        threading.Thread(target=worker, name="EngineStarter", daemon=True).start()

    def stop_engine(self):
        if not self.engine or not self.engine.running:
            return
        self.stop_button.configure(state="disabled")
        def worker():
            try:
                self.engine.stop()
                self.events.put({"type": "engine_stopped"})
            except Exception as exc:
                self.events.put({"type": "engine_stop_error", "message": str(exc)})
        threading.Thread(target=worker, name="EngineStopper", daemon=True).start()

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event.get("type")
                if kind == "engine":
                    self._set_engine_state(bool(event.get("running")))
                elif kind == "engine_started":
                    self.engine_starting = False
                    self._set_engine_state(True)
                elif kind == "engine_start_error":
                    self.engine_starting = False
                    self.start_button.configure(state="normal")
                    self._set_engine_state(False)
                    messagebox.showerror(
                        "Не удалось запустить ретранслятор",
                        event.get("message", "Неизвестная ошибка") +
                        "\n\nПроверьте, не занят ли локальный TCP-порт предыдущим ретранслятором.",
                        parent=self.root,
                    )
                elif kind == "engine_stopped":
                    self._set_engine_state(False)
                elif kind == "engine_stop_error":
                    self.stop_button.configure(state="normal")
                    messagebox.showerror("Остановка", event.get("message", "Ошибка"), parent=self.root)
                elif kind == "printers":
                    self._update_printer_status(event.get("printers", []))
                elif kind in ("job", "job_progress", "job_done", "job_failed"):
                    self._update_job_event(event)
                elif kind == "drivers":
                    self.driver_names = event.get("names", [])
                    self.driver_combo["values"] = self.driver_names
                    if self.config.driver_name in self.driver_names:
                        self.driver_var.set(self.config.driver_name)
                    elif self.driver_names and self.driver_var.get() not in self.driver_names:
                        kyocera = next((n for n in self.driver_names if "kyocera" in n.casefold()), "")
                        self.driver_var.set(kyocera or self.driver_names[0])
                    self.windows_status_var.set(f"Установлено драйверов: {len(self.driver_names)}")
                elif kind == "drivers_error":
                    self.windows_status_var.set("Не удалось получить драйверы Windows.")
                    self.logger.error("Список драйверов: %s", event.get("message"))
                elif kind == "queue_info":
                    self._show_queue_info(event.get("info"))
                elif kind == "queue_info_error":
                    self.queue_info_var.set("Не удалось прочитать сведения об очереди.")
                    self.windows_status_var.set(event.get("message", ""))
                self.events.task_done()
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(150, self._poll_events)

    def _set_engine_state(self, running: bool):
        self.engine_starting = False
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.engine_badge.configure(
            text="● РАБОТАЕТ" if running else "● ОСТАНОВЛЕН",
            foreground="#18733a" if running else "#a33",
        )
        self.footer_var.set(
            ("Ретранслятор активен" if running else "Ретранслятор остановлен") +
            f"  •  {self.store.path}"
        )

    def _refresh_dashboard(self):
        if self.engine:
            try:
                snapshot = self.engine.snapshot()
                for key in ("received", "queued", "active", "completed", "failed"):
                    self.metric_vars[key].set(str(snapshot.get(key, 0)))
                self._update_printer_status(snapshot.get("printers", []))
                for job in snapshot.get("jobs", []):
                    self._upsert_job(job)
            except Exception:
                self.logger.exception("Не удалось обновить мониторинг")
        if not self._closing:
            self.root.after(1200, self._refresh_dashboard)

    def _update_printer_status(self, printers: list[dict]):
        valid = set()
        for row in printers:
            host = row.get("host", "")
            valid.add(host)
            status = row.get("status", "")
            tag = "ok" if status == "Доступен" else "busy" if status == "Занят" else "error" if status == "Недоступен" else ""
            values = (
                row.get("name", ""), host, status, row.get("jobs_sent", 0),
                row.get("current_job", "") or "—", row.get("last_error", ""),
            )
            if self.printer_status_tree.exists(host):
                self.printer_status_tree.item(host, values=values, tags=(tag,) if tag else ())
            else:
                self.printer_status_tree.insert("", "end", iid=host, values=values, tags=(tag,) if tag else ())
        for item in self.printer_status_tree.get_children():
            if item not in valid:
                self.printer_status_tree.delete(item)

    def _update_job_event(self, event: dict):
        if event.get("type") in ("job", "job_progress") and event.get("job"):
            self._upsert_job(event["job"])
        elif event.get("type") == "job_done":
            self.logger.info(
                "Задание %s передано на %s: %s байт, %.2f с",
                event.get("job_id"), event.get("printer"), event.get("bytes", 0), event.get("elapsed", 0),
            )
        elif event.get("type") == "job_failed":
            self.logger.error(
                "Задание %s требует проверки: %s", event.get("job_id"), event.get("reason")
            )

    def _upsert_job(self, job: dict):
        job_id = job.get("job_id", "")
        if not job_id:
            return
        size = int(job.get("size", 0))
        sent = int(job.get("bytes_sent", 0))
        size_label = f"{size / 1024:.1f} КБ" if size < 1024 * 1024 else f"{size / 1048576:.2f} МБ"
        received = time.strftime("%d.%m %H:%M:%S", time.localtime(job.get("received_at", time.time())))
        detail = (
            f"Отправлено {sent / 1024:.1f} КБ из {size_label}"
            if sent and job.get("status") == "Печатается" else job.get("error", "")
        )
        values = (
            job_id, job.get("status", ""), size_label,
            job.get("printer", "") or "—", received, detail,
        )
        status = job.get("status", "")
        tag = "ok" if status == "Передано" else "busy" if status == "Печатается" or status.startswith("В очереди") else "error"
        if self.job_tree.exists(job_id):
            self.job_tree.item(job_id, values=values, tags=(tag,))
        else:
            self.job_tree.insert("", "end", iid=job_id, values=values, tags=(tag,))
        children = self.job_tree.get_children()
        if len(children) > 100:
            for item in children[:-100]:
                self.job_tree.delete(item)

    def _show_queue_info(self, info: dict | None):
        if not info:
            self.queue_info_var.set(f"Очередь «{self.config.queue_name}» не найдена.")
            return
        self.queue_info_var.set(
            f"Имя: {info.get('Name', '')}\n"
            f"Драйвер: {info.get('DriverName', '')}\n"
            f"Порт: {info.get('PortName', '')}\n"
            f"Состояние Windows: {info.get('PrinterStatus', '—')}"
        )

    def _poll_logs(self):
        try:
            while True:
                line = self.log_events.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                lines = int(self.log_text.index("end-1c").split(".")[0])
                if lines > 1500:
                    self.log_text.delete("1.0", f"{lines - 1200}.0")
                self.log_text.configure(state="disabled")
                self.log_events.task_done()
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(300, self._poll_logs)

    def _clear_log_view(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_log_file(self):
        path = app_data_dir() / "print_round_robin.log"
        try:
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("Журнал", f"Не удалось открыть {path}:\n{exc}", parent=self.root)

    def _on_close(self):
        if self.engine and self.engine.running:
            if not messagebox.askyesno(
                "Выход",
                "Ретранслятор работает. При выходе он будет остановлен. Принятые, но ещё не отправленные "
                "задания останутся в локальной очереди и будут восстановлены при следующем запуске. Выйти?",
                parent=self.root,
            ):
                return
            self.engine.stop(wait_seconds=1.0)
        self.save_settings(silent=True)
        self._closing = True
        self.root.destroy()
