# -*- coding: utf-8 -*-
"""Application configuration and persistent paths."""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "PrintRoundRobin"
LEGACY_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "KyoceraRoundRobin"


def app_data_dir() -> Path:
    root = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return root / APP_NAME


def runtime_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return root / APP_NAME


@dataclass
class PrinterConfig:
    name: str
    host: str
    port: int = 9100
    enabled: bool = True

    def validate(self) -> None:
        self.name = str(self.name).strip() or self.host
        self.host = str(self.host).strip()
        ipaddress.ip_address(self.host)
        self.port = int(self.port)
        if not 1 <= self.port <= 65535:
            raise ValueError(f"Недопустимый TCP-порт у {self.host}: {self.port}")
        self.enabled = bool(self.enabled)


@dataclass
class AppConfig:
    config_version: int = 1
    queue_name: str = "Kyocera Round Robin"
    port_name: str = "KYOCERA_RR_LOCAL"
    driver_name: str = ""
    auto_manage_queue: bool = True
    share_enabled: bool = False
    share_name: str = "KyoceraRoundRobin"
    share_server: str = ""
    listener_host: str = "127.0.0.1"
    listener_port: int = 9100
    default_printer_port: int = 9100
    connect_timeout: float = 0.8
    send_timeout: float = 300.0
    retry_delay: float = 3.0
    busy_retry_delay: float = 0.25
    health_interval: float = 1.0
    receive_buffer_size: int = 1024 * 1024
    max_receivers: int = 20
    max_workers: int = 10
    auto_start_engine: bool = False
    start_with_windows: bool = False
    theme: str = "dark"
    snmp_enabled: bool = True
    snmp_version: str = "2c"
    snmp_community: str = "public"
    snmp_port: int = 161
    snmp_timeout: float = 0.8
    snmp_interval: float = 5.0
    printers: list[PrinterConfig] = field(default_factory=lambda: [
        PrinterConfig(name=f"Kyocera {index}", host=f"192.168.20.{index + 120}")
        for index in range(1, 11)
    ])

    def validate(self) -> None:
        self.queue_name = str(self.queue_name).strip()
        self.port_name = str(self.port_name).strip()
        self.driver_name = str(self.driver_name).strip()
        self.auto_manage_queue = bool(self.auto_manage_queue)
        self.share_enabled = bool(self.share_enabled)
        self.share_name = str(self.share_name).strip()
        self.share_server = str(self.share_server).strip()
        if len(self.share_name) > 31 or any(
            not (ch.isalnum() or ch in "_-.") for ch in self.share_name
        ):
            raise ValueError("Имя сетевого ресурса принтера должно содержать не более 31 символа: буквы, цифры, точка, дефис или подчёркивание.")
        if self.share_name.endswith((".", " ")):
            raise ValueError("Имя сетевого ресурса принтера не должно заканчиваться точкой.")
        if self.share_enabled and not self.share_name:
            raise ValueError("Укажите имя сетевого ресурса для публикации принтера.")
        if any(not (ch.isalnum() or ch in ".-_") for ch in self.share_server):
            raise ValueError("Имя сервера должно содержать только буквы, цифры, точку, дефис или подчёркивание.")
        if not self.queue_name:
            raise ValueError("Не задано имя виртуального принтера.")
        if not self.port_name:
            raise ValueError("Не задано имя локального TCP/IP-порта.")
        self.listener_host = str(self.listener_host).strip() or "127.0.0.1"
        # Keep the listener local: the virtual Windows printer is on this PC.
        if self.listener_host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Локальный ретранслятор должен слушать только loopback (127.0.0.1).")
        self.listener_port = int(self.listener_port)
        self.default_printer_port = int(self.default_printer_port)
        for value, label in ((self.listener_port, "Порт локального ретранслятора"),
                             (self.default_printer_port, "Порт физических принтеров")):
            if not 1 <= value <= 65535:
                raise ValueError(f"{label}: допустим диапазон 1–65535.")
        self.connect_timeout = _bounded_float(self.connect_timeout, "Таймаут подключения", 0.1, 30.0)
        self.send_timeout = _bounded_float(self.send_timeout, "Таймаут передачи", 1.0, 3600.0)
        self.retry_delay = _bounded_float(self.retry_delay, "Интервал повтора", 0.1, 120.0)
        self.busy_retry_delay = _bounded_float(self.busy_retry_delay, "Пауза при занятых принтерах", 0.05, 10.0)
        self.health_interval = _bounded_float(self.health_interval, "Интервал проверки", 1.0, 300.0)
        self.receive_buffer_size = int(self.receive_buffer_size)
        if not 4096 <= self.receive_buffer_size <= 8 * 1024 * 1024:
            raise ValueError("Размер буфера должен быть от 4 КБ до 8 МБ.")
        self.max_receivers = int(self.max_receivers)
        self.max_workers = int(self.max_workers)
        self.theme = "dark" if str(self.theme).lower() in ("dark", "night", "тёмная", "темная") else "light"
        self.snmp_enabled = bool(self.snmp_enabled)
        self.snmp_version = "1" if str(self.snmp_version).lower() in ("1", "v1") else "2c"
        self.snmp_community = str(self.snmp_community).strip() or "public"
        self.snmp_port = int(self.snmp_port)
        if not 1 <= self.snmp_port <= 65535:
            raise ValueError("SNMP-порт должен находиться в диапазоне 1–65535.")
        self.snmp_timeout = _bounded_float(self.snmp_timeout, "SNMP-таймаут", 0.1, 10.0)
        self.snmp_interval = _bounded_float(self.snmp_interval, "Интервал SNMP-опроса", 2.0, 300.0)
        if not 1 <= self.max_receivers <= 100:
            raise ValueError("Количество входящих потоков должно быть от 1 до 100.")
        if not 1 <= self.max_workers <= 50:
            raise ValueError("Количество рабочих потоков должно быть от 1 до 50.")
        if not self.printers:
            raise ValueError("Добавьте хотя бы один физический принтер.")
        seen: set[tuple[str, int]] = set()
        seen_hosts: set[str] = set()
        for printer in self.printers:
            if not isinstance(printer, PrinterConfig):
                raise ValueError("Некорректная запись принтера в конфигурации.")
            printer.validate()
            key = (printer.host, printer.port)
            if key in seen or printer.host in seen_hosts:
                raise ValueError(f"Принтер {printer.host}:{printer.port} добавлен повторно.")
            seen.add(key)
            seen_hosts.add(printer.host)
        if not any(p.enabled for p in self.printers):
            raise ValueError("Должен быть включён хотя бы один принтер.")
        self.max_workers = min(self.max_workers, len([p for p in self.printers if p.enabled]))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        values = dict(raw)
        raw_printers = values.pop("printers", None)
        if raw_printers is not None:
            values["printers"] = [
                p if isinstance(p, PrinterConfig) else PrinterConfig(**p)
                for p in raw_printers
            ]
        config = cls(**values)
        config.validate()
        return config

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_float(value: Any, label: str, minimum: float, maximum: float) -> float:
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}: допустимый диапазон {minimum:g}–{maximum:g}.")
    return number


def reorder_printers(
    printers: list[PrinterConfig], selected_hosts: set[str], direction: int
) -> list[PrinterConfig]:
    """Move selected printers one position up (-1) or down (+1), preserving order."""
    if direction not in (-1, 1):
        raise ValueError("Направление перемещения должно быть -1 или 1.")
    ordered = list(printers)
    selected = set(selected_hosts)
    if not selected:
        return ordered

    if direction < 0:
        for index in range(1, len(ordered)):
            if ordered[index].host in selected and ordered[index - 1].host not in selected:
                ordered[index - 1], ordered[index] = ordered[index], ordered[index - 1]
    else:
        for index in range(len(ordered) - 2, -1, -1):
            if ordered[index].host in selected and ordered[index + 1].host not in selected:
                ordered[index], ordered[index + 1] = ordered[index + 1], ordered[index]
    return ordered


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (app_data_dir() / "settings.json")

    def load(self) -> AppConfig:
        if not self.path.exists():
            config = AppConfig()
            self.save(config)
            return config
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("Корень файла настроек должен быть JSON-объектом.")
            return AppConfig.from_dict(raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            backup = self.path.with_suffix(self.path.suffix + ".broken")
            try:
                shutil.copy2(self.path, backup)
            except OSError:
                pass
            raise ValueError(f"Не удалось прочитать настройки {self.path}: {exc}") from exc

    def save(self, config: AppConfig) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)
        fd, temp_name = tempfile.mkstemp(prefix="settings_", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def export_to(self, destination: Path, config: AppConfig) -> None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def import_from(source: Path) -> AppConfig:
        raw = json.loads(Path(source).read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("Файл настроек должен содержать JSON-объект.")
        return AppConfig.from_dict(raw)


def load_legacy_next_index() -> int:
    """Read the previous relay's saved round-robin position, if present."""
    candidates = [runtime_dir() / "state.json", LEGACY_DIR / "state.json"]
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return max(0, int(raw.get("next_index", 0)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return 0
