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
        "if($null -eq $p){'null'}else{$p | Select-Object Name,DriverName,PortName,PrinterStatus,Shared,ShareName "
        "| ConvertTo-Json -Compress}"
    )
    value = _parse_json_output(run_powershell(script), None)
    return value if isinstance(value, dict) else None


def get_printer_port_info(port_name: str) -> dict | None:
    script = (
        "$ErrorActionPreference='Stop'; Import-Module PrintManagement; "
        f"$p=Get-PrinterPort -Name {_ps_quote(port_name)} -ErrorAction SilentlyContinue; "
        "if($null -eq $p){'null'}else{$p | Select-Object Name,PrinterHostAddress,PortNumber | ConvertTo-Json -Compress}"
    )
    value = _parse_json_output(run_powershell(script), None)
    return value if isinstance(value, dict) else None


def inspect_virtual_printer(config: AppConfig) -> dict:
    """Compare the installed queue, TCP/IP port and optional share with saved settings."""
    _require_windows()
    config.validate()
    queue = get_queue_info(config.queue_name)
    port = get_printer_port_info(config.port_name)
    issues: list[str] = []

    if queue is None:
        issues.append(f"Очередь «{config.queue_name}» отсутствует.")
    else:
        if str(queue.get("DriverName", "")).casefold() != config.driver_name.casefold():
            issues.append("У виртуальной очереди выбран другой драйвер.")
        if str(queue.get("PortName", "")).casefold() != config.port_name.casefold():
            issues.append("Виртуальная очередь привязана к другому порту.")

    if port is None:
        issues.append(f"TCP/IP-порт «{config.port_name}» отсутствует.")
    else:
        actual_host = str(port.get("PrinterHostAddress", "")).strip()
        try:
            port_matches = int(port.get("PortNumber")) == config.listener_port
        except (TypeError, ValueError):
            port_matches = False
        if actual_host.casefold() != config.listener_host.casefold() or not port_matches:
            issues.append(
                f"Порт «{config.port_name}» направлен не на {config.listener_host}:{config.listener_port}."
            )

    if queue is not None:
        shared = str(queue.get("Shared", "")).strip().casefold() in ("true", "1", "yes")
        actual_share = str(queue.get("ShareName", "") or "").strip()
        if config.share_enabled and (not shared or actual_share.casefold() != config.share_name.casefold()):
            issues.append("Сетевая публикация очереди отсутствует или использует другое имя.")
        elif not config.share_enabled and shared:
            issues.append("Очередь опубликована, хотя публикация отключена в настройках.")

    return {"healthy": not issues, "queue": queue, "port": port, "issues": issues}


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
    if config.share_enabled:
        share_setup = (
            f"if($null -eq $printer){{Add-Printer -Name {q} -DriverName {d} -PortName {p} "
            f"-Shared -ShareName {_ps_quote(config.share_name)}}} "
            f"else{{Set-Printer -Name {q} -DriverName {d} -PortName {p} "
            f"-Shared $true -ShareName {_ps_quote(config.share_name)}}}"
        )
    else:
        share_setup = (
            f"if($null -eq $printer){{Add-Printer -Name {q} -DriverName {d} -PortName {p}}} "
            f"else{{Set-Printer -Name {q} -DriverName {d} -PortName {p} -Shared $false}}"
        )
    ps = (
        "$ErrorActionPreference='Stop'; Import-Module PrintManagement; "
        f"$printer=Get-Printer -Name {q} -ErrorAction SilentlyContinue; "
        f"{share_setup}; "
        f"Get-Printer -Name {q} | Select-Object Name,DriverName,PortName,Shared,ShareName | ConvertTo-Json -Compress"
    )
    queue_result = _parse_json_output(run_powershell(ps, timeout=60), {})
    if not isinstance(queue_result, dict):
        raise WindowsIntegrationError("Windows не вернула сведения о созданной очереди печати.")
    return (
        f"Виртуальный принтер настроен.\n"
        f"Очередь: {queue_result.get('Name', config.queue_name)}\n"
        f"Драйвер: {queue_result.get('DriverName', config.driver_name)}\n"
        f"Порт: {queue_result.get('PortName', config.port_name)} → "
        f"{config.listener_host}:{config.listener_port}\n"
        + (
            f"Сетевой доступ: включён (\\\\{config.share_server or os.environ.get('COMPUTERNAME', 'ИМЯ_СЕРВЕРА')}\\{config.share_name})\n"
            if config.share_enabled else "Сетевой доступ: выключен\n"
        )
        + "\nПроверьте в свойствах принтера вкладку «Порты»: снимите флажок "
        "«Разрешить двусторонний обмен данными», если он включён."
    )


def write_client_installer(destination: Path, server_name: str, share_name: str) -> Path:
    """Write a self-elevating CRLF BAT installer for a shared printer connection."""
    destination = Path(destination)
    server_name = str(server_name).strip()
    share_name = str(share_name).strip()
    if not server_name or any(not (ch.isalnum() or ch in ".-_") for ch in server_name):
        raise WindowsIntegrationError("Имя сервера должно содержать только буквы, цифры, точку, дефис или подчёркивание.")
    if (not share_name or len(share_name) > 31
            or any(not (ch.isalnum() or ch in "_-.") for ch in share_name)
            or share_name.endswith(".")):
        raise WindowsIntegrationError("Имя сетевого ресурса должно быть до 31 символа без пробелов и спецсимволов.")
    printer_path = f"\\\\{server_name}\\{share_name}"
    lines = [
        "@echo off",
        "setlocal",
        f'set "PRINTER_PATH={printer_path}"',
        'set "THIS_SCRIPT=%~f0"',
        "net session >nul 2>&1",
        "if errorlevel 1 (",
        "  echo Administrator permissions are required. Opening UAC prompt...",
        "  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"Start-Process -FilePath $env:ComSpec -Verb RunAs -ArgumentList ('/c ' + [char]34 + $env:THIS_SCRIPT + [char]34)\"",
        "  exit /b",
        ")",
        "powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command \"$ErrorActionPreference='Stop'; $path=$env:PRINTER_PATH; if (-not (Get-Printer -Name $path -ErrorAction SilentlyContinue)) { Add-Printer -ConnectionName $path }; Write-Host ('Connected: ' + $path)\"",
        "if errorlevel 1 (",
        "  echo Failed to connect. Check server connectivity, printer sharing and Point and Print policy.",
        "  pause",
        "  exit /b 1",
        ")",
        "echo Printer connection is ready.",
        "pause",
        "endlocal",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write("\r\n".join(lines) + "\r\n")
    return destination


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


def register_startup(enabled: bool, minimized: bool = False) -> None:
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
