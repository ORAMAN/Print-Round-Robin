# -*- coding: utf-8 -*-
"""Windows Print Spooler integration. PowerShell is invoked inline; no .ps1 files."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .config import AppConfig, app_data_dir


class WindowsIntegrationError(RuntimeError):
    pass


def _require_windows() -> None:
    if os.name != "nt":
        raise WindowsIntegrationError("Управление принтерами доступно только в Windows.")


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_powershell(script: str, timeout: float = 30.0) -> str:
    _require_windows()
    command = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-Command", script,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, creationflags=creationflags)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "PowerShell вернул ошибку без текста.").strip()
        raise WindowsIntegrationError(message)
    return result.stdout.strip()


def _parse_json_output(output: str, default: Any):
    if not output:
        return default
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise WindowsIntegrationError(f"Не удалось разобрать ответ Windows PowerShell: {output[:500]}") from exc


def list_installed_drivers() -> list[str]:
    script = (
        "$ErrorActionPreference='Stop'; Import-Module PrintManagement; "
        "Get-PrinterDriver | Sort-Object -Property Name | "
        "Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"
    )
    output = run_powershell(script)
    values = _parse_json_output(output, [])
    if isinstance(values, str):
        values = [values]
    return [str(v) for v in values if str(v).strip()]


def get_queue_info(queue_name: str) -> dict | None:
    script = (
        "$ErrorActionPreference='Stop'; Import-Module PrintManagement; "
        f"$p=Get-Printer -Name {_ps_quote(queue_name)} -ErrorAction SilentlyContinue; "
        "if($null -eq $p){'null'}else{$p | Select-Object Name,DriverName,PortName,PrinterStatus,Shared "
        "| ConvertTo-Json -Compress}"
    )
    value = _parse_json_output(run_powershell(script), None)
    return value if isinstance(value, dict) else None


def list_printer_ports() -> list[dict]:
    script = (
        "$ErrorActionPreference='Stop'; Import-Module PrintManagement; "
        "Get-PrinterPort | Select-Object Name,PrinterHostAddress,PortNumber | ConvertTo-Json -Compress"
    )
    value = _parse_json_output(run_powershell(script), [])
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def locate_prnport_vbs() -> Path:
    _require_windows()
    root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "Printing_Admin_Scripts"
    if root.exists():
        for candidate in root.rglob("prnport.vbs"):
            if candidate.is_file():
                return candidate
    raise WindowsIntegrationError(
        "Не найден prnport.vbs (Windows Printing Administration Scripts). "
        "Проверьте установку компонентов управления печатью Windows."
    )


def setup_virtual_printer(config: AppConfig) -> str:
    """Create/update the loopback RAW port and the logical Windows queue."""
    _require_windows()
    config.validate()
    if not config.driver_name:
        raise WindowsIntegrationError("Выберите установленный драйвер Kyocera.")
    drivers = list_installed_drivers()
    if config.driver_name not in drivers:
        raise WindowsIntegrationError(f"Драйвер «{config.driver_name}» не найден среди установленных.")

    ports = list_printer_ports()
    port_exists = any(str(p.get("Name", "")).casefold() == config.port_name.casefold() for p in ports)
    prnport = locate_prnport_vbs()
    operation = "-t" if port_exists else "-a"
    args = [
        "cscript.exe", "//NoLogo", str(prnport), operation,
        "-r", config.port_name,
        "-h", config.listener_host,
        "-o", "raw",
        "-n", str(config.listener_port),
        "-md",
    ]
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise WindowsIntegrationError(
            f"Не удалось создать/обновить TCP/IP-порт {config.port_name}.\n"
            f"{(result.stderr or result.stdout).strip()}"
        )

    q = _ps_quote(config.queue_name)
    d = _ps_quote(config.driver_name)
    p = _ps_quote(config.port_name)
    ps = (
        "$ErrorActionPreference='Stop'; Import-Module PrintManagement; "
        f"$printer=Get-Printer -Name {q} -ErrorAction SilentlyContinue; "
        f"if($null -eq $printer){{Add-Printer -Name {q} -DriverName {d} -PortName {p}}} "
        f"else{{Set-Printer -Name {q} -DriverName {d} -PortName {p}}}; "
        f"Get-Printer -Name {q} | Select-Object Name,DriverName,PortName | ConvertTo-Json -Compress"
    )
    queue_result = _parse_json_output(run_powershell(ps, timeout=60), {})
    if not isinstance(queue_result, dict):
        raise WindowsIntegrationError("Windows не вернула сведения о созданной очереди печати.")
    return (
        f"Виртуальный принтер настроен.\n"
        f"Очередь: {queue_result.get('Name', config.queue_name)}\n"
        f"Драйвер: {queue_result.get('DriverName', config.driver_name)}\n"
        f"Порт: {queue_result.get('PortName', config.port_name)} → "
        f"{config.listener_host}:{config.listener_port}\n\n"
        "Проверьте в свойствах принтера вкладку «Порты»: снимите флажок "
        "«Разрешить двусторонний обмен данными», если он включён."
    )


def stop_legacy_scheduled_task(task_name: str = "Kyocera Round Robin Relay", remove: bool = False) -> str:
    _require_windows()
    t = _ps_quote(task_name)
    if remove:
        script = (
            "$ErrorActionPreference='Stop'; "
            f"$task=Get-ScheduledTask -TaskName {t} -ErrorAction SilentlyContinue; "
            "if($null -eq $task){'Задача не найдена.'}else{"
            f"Stop-ScheduledTask -TaskName {t} -ErrorAction SilentlyContinue; "
            f"Unregister-ScheduledTask -TaskName {t} -Confirm:$false; 'Старая задача остановлена и удалена.'}}"
        )
    else:
        script = (
            "$ErrorActionPreference='Stop'; "
            f"$task=Get-ScheduledTask -TaskName {t} -ErrorAction SilentlyContinue; "
            "if($null -eq $task){'Задача не найдена.'}else{"
            f"Stop-ScheduledTask -TaskName {t} -ErrorAction SilentlyContinue; 'Старая задача остановлена.'}}"
        )
    return run_powershell(script, timeout=30)


def register_startup(enabled: bool, minimized: bool = True) -> None:
    _require_windows()
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    value_name = "PrintRoundRobin"
    executable = sys.executable
    if not getattr(sys, "frozen", False) and Path(executable).name.casefold() == "python.exe":
        pythonw = Path(executable).with_name("pythonw.exe")
        if pythonw.exists():
            executable = str(pythonw)
    if getattr(sys, "frozen", False):
        command_parts = [executable]
    else:
        entry = Path(sys.argv[0]).resolve()
        command_parts = [executable, str(entry)]
    if enabled and minimized:
        command_parts.append("--minimized")
    command = subprocess.list2cmdline(command_parts)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass


def request_elevated_action(action: dict) -> Path:
    """Start this application with UAC and return a path to the result JSON."""
    _require_windows()
    app_dir = app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)
    action_id = uuid.uuid4().hex
    action_path = app_dir / f"admin_action_{action_id}.json"
    result_path = app_dir / f"admin_result_{action_id}.json"
    data = dict(action)
    data["result_path"] = str(result_path)
    action_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if getattr(sys, "frozen", False):
        executable = sys.executable
        parameters = f'--admin-action-file "{action_path}"'
    else:
        executable = sys.executable
        entry = Path(sys.argv[0]).resolve()
        parameters = f'"{entry}" --admin-action-file "{action_path}"'
    shell32 = ctypes.windll.shell32
    result = shell32.ShellExecuteW(None, "runas", executable, parameters, None, 0)
    if result <= 32:
        raise WindowsIntegrationError(f"Не удалось запустить действие с правами администратора (код {result}).")
    return result_path


def execute_admin_action_file(action_file: Path) -> int:
    """Entry point for the elevated helper process; no second PS script is used."""
    action_file = Path(action_file)
    data: dict = {}
    try:
        data = json.loads(action_file.read_text(encoding="utf-8-sig"))
        action = data.get("action")
        if action == "setup_queue":
            config = AppConfig.from_dict(data["config"])
            message = setup_virtual_printer(config)
        elif action == "stop_legacy_task":
            message = stop_legacy_scheduled_task(remove=bool(data.get("remove", False)))
        else:
            raise WindowsIntegrationError(f"Неизвестное административное действие: {action}")
        result = {"success": True, "message": message}
        code = 0
    except Exception as exc:
        result = {"success": False, "message": str(exc)}
        code = 1
    try:
        result_path = Path(data.get("result_path", action_file.with_suffix(".result.json")))
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        action_file.unlink(missing_ok=True)
    except OSError:
        pass
    return code


def read_admin_result(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
