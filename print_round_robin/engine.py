# -*- coding: utf-8 -*-
"""Concurrent disk-spooled RAW TCP print relay."""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import socket
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import AppConfig, PrinterConfig, LEGACY_DIR, runtime_dir, load_legacy_next_index

LOG = logging.getLogger("PrintRoundRobin")


@dataclass
class JobInfo:
    job_id: str
    filename: str
    status: str
    size: int = 0
    received_at: float = field(default_factory=time.time)
    printer: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status,
            "size": self.size,
            "received_at": self.received_at,
            "printer": self.printer,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass
class PrinterRuntime:
    config: PrinterConfig
    busy: bool = False
    available: bool | None = None
    checked_at: float | None = None
    jobs_sent: int = 0
    current_job: str = ""
    last_error: str = ""


class RoundRobinEngine:
    """Receives each Windows RAW job to disk, then forwards jobs concurrently.

    A physical printer is protected by its own lock, so two independent jobs
    can never be interleaved on one printer. Different printers work in
    parallel. A complete job is the distribution unit (not individual pages).
    """

    def __init__(self, config: AppConfig, event_callback: Callable[[dict], None] | None = None):
        self.config = config
        self.event_callback = event_callback or (lambda _event: None)
        self.base_dir = runtime_dir()
        self.spool_dir = self.base_dir / "spool"
        self.failed_dir = self.base_dir / "failed"
        self.state_file = self.base_dir / "state.json"

        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._receiver_slots = threading.BoundedSemaphore(config.max_receivers)
        self._worker_threads: list[threading.Thread] = []
        self._workers_queue: queue.Queue[tuple[Path, str]] = queue.Queue()
        self._receiver_lock = threading.Lock()
        self._receivers: set[threading.Thread] = set()
        self._receiver_clients: set[socket.socket] = set()

        self._scheduler_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._jobs_lock = threading.Lock()
        self._next_index = self._load_next_index()
        self._printer_runtime = {
            p.host: PrinterRuntime(p) for p in config.printers if p.enabled
        }
        self._printer_locks = {p.host: threading.Lock() for p in config.printers if p.enabled}
        self._jobs: OrderedDict[str, JobInfo] = OrderedDict()
        self._counters = {"received": 0, "completed": 0, "failed": 0}
        self._started = False
        self._started_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._started and not self._stop.is_set()

    def _emit(self, event_type: str, **payload) -> None:
        try:
            self.event_callback({"type": event_type, "time": time.time(), **payload})
        except Exception:
            LOG.exception("Ошибка обработчика события интерфейса")

    def _load_next_index(self) -> int:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            return int(raw.get("next_index", 0)) % max(1, len([p for p in self.config.printers if p.enabled]))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return load_legacy_next_index() % max(1, len([p for p in self.config.printers if p.enabled]))

    def _save_next_index(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump({"next_index": self._next_index}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.state_file)

    def start(self) -> None:
        with self._started_lock:
            if self._started and not self._stop.is_set():
                return
            self.config.validate()
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.spool_dir.mkdir(parents=True, exist_ok=True)
            self.failed_dir.mkdir(parents=True, exist_ok=True)
            self._stop.clear()
            self._workers_queue = queue.Queue()
            self._receiver_slots = threading.BoundedSemaphore(self.config.max_receivers)
            self._worker_threads = []

            # Recover completed staged jobs from a previous clean/unclean exit.
            self._migrate_legacy_spool()
            self._recover_spool()

            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.settimeout(0.5)
            try:
                server.bind((self.config.listener_host, self.config.listener_port))
                server.listen(self.config.max_receivers)
            except OSError:
                server.close()
                raise
            self._server = server
            for index in range(min(self.config.max_workers, len(self._printer_runtime))):
                worker = threading.Thread(target=self._worker_loop, name=f"PrintWorker-{index + 1}", daemon=True)
                worker.start()
                self._worker_threads.append(worker)
            self._started = True
            self._accept_thread = threading.Thread(target=self._accept_loop, name="PrintRelay-Accept", daemon=True)
            self._health_thread = threading.Thread(target=self._health_loop, name="PrintRelay-Health", daemon=True)
            self._accept_thread.start()
            self._health_thread.start()
            LOG.info(
                "Ретранслятор запущен: %s:%d; принтеров: %d; рабочих потоков: %d",
                self.config.listener_host, self.config.listener_port,
                len(self._printer_runtime), len(self._worker_threads),
            )
            self._emit("engine", running=True)

    def stop(self, wait_seconds: float = 2.0) -> None:
        with self._started_lock:
            if not self._started:
                return
            self._stop.set()
            server, self._server = self._server, None
            if server:
                try:
                    server.close()
                except OSError:
                    pass
            with self._receiver_lock:
                receivers = list(self._receivers)
                clients = list(self._receiver_clients)
            for client in clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    client.close()
                except OSError:
                    pass
            for thread in [self._accept_thread, self._health_thread, *receivers, *self._worker_threads]:
                if thread and thread.is_alive():
                    thread.join(timeout=wait_seconds)
            self._started = False
            LOG.info("Ретранслятор остановлен. Незавершённые задания оставлены в локальной очереди.")
            self._emit("engine", running=False)

    def _accept_loop(self) -> None:
        server = self._server
        if server is None:
            return
        while not self._stop.is_set():
            try:
                client, address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    LOG.exception("Ошибка приёма TCP-подключения")
                break
            if not self._receiver_slots.acquire(blocking=False):
                LOG.warning("Превышен лимит входящих подключений; соединение %s:%s отклонено", *address)
                client.close()
                continue
            thread = threading.Thread(
                target=self._receive_job,
                args=(client, address),
                name=f"PrintReceiver-{address[0]}",
                daemon=True,
            )
            with self._receiver_lock:
                self._receivers.add(thread)
                self._receiver_clients.add(client)
            thread.start()

    def _receive_job(self, client: socket.socket, address: tuple) -> None:
        job_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        partial_path = self.spool_dir / f"{job_id}.part"
        staged_path = self.spool_dir / f"{job_id}.prn"
        size = 0
        reached_eof = False
        try:
            with client:
                with partial_path.open("wb") as output:
                    while True:
                        chunk = client.recv(self.config.receive_buffer_size)
                        if not chunk:
                            reached_eof = True
                            break
                        output.write(chunk)
                        size += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if size == 0:
                partial_path.unlink(missing_ok=True)
                return  # Windows port-monitor probe; not a print job.
            if not reached_eof:
                target = self.failed_dir / partial_path.name
                os.replace(partial_path, target)
                LOG.warning("Неполное задание %s перемещено для ручной проверки", job_id)
                return
            os.replace(partial_path, staged_path)
            info = JobInfo(job_id, staged_path.name, "В очереди", size=size)
            with self._jobs_lock:
                self._jobs[job_id] = info
                self._counters["received"] += 1
                self._trim_jobs()
            LOG.info("Задание %s принято от %s:%s; %.0f байт", job_id, *address, size)
            self._emit("job", job=info.as_dict())
            if not self._stop.is_set():
                self._workers_queue.put((staged_path, job_id))
        except OSError as exc:
            if partial_path.exists():
                try:
                    if partial_path.stat().st_size:
                        target = self.failed_dir / partial_path.name
                        os.replace(partial_path, target)
                    else:
                        partial_path.unlink(missing_ok=True)
                except OSError:
                    LOG.exception("Не удалось обработать частичный файл %s", partial_path)
            if not self._stop.is_set():
                LOG.exception("Ошибка приёма задания от %s:%s: %s", *address, exc)
        finally:
            try:
                client.close()
            except OSError:
                pass
            self._receiver_slots.release()
            current = threading.current_thread()
            with self._receiver_lock:
                self._receivers.discard(current)
                self._receiver_clients.discard(client)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                path, job_id = self._workers_queue.get(timeout=0.4)
            except queue.Empty:
                continue
            try:
                if self._stop.is_set():
                    continue
                self._forward_job(path, job_id)
            finally:
                self._workers_queue.task_done()

    def _reserve_printer(self) -> tuple[PrinterConfig, socket.socket, threading.Lock] | None:
        printers = [p for p in self.config.printers if p.enabled]
        if not printers:
            return None
        while not self._stop.is_set():
            connection_failures = 0
            acquired_busy = False
            with self._scheduler_lock:
                count = len(printers)
                start = self._next_index % count
                for offset in range(count):
                    index = (start + offset) % count
                    printer = printers[index]
                    lock = self._printer_locks[printer.host]
                    if not lock.acquire(blocking=False):
                        acquired_busy = True
                        continue
                    try:
                        sock = socket.create_connection(
                            (printer.host, printer.port), timeout=self.config.connect_timeout
                        )
                        sock.settimeout(self.config.send_timeout)
                    except OSError as exc:
                        connection_failures += 1
                        runtime = self._printer_runtime[printer.host]
                        runtime.available = False
                        runtime.checked_at = time.time()
                        runtime.last_error = str(exc)
                        lock.release()
                        continue
                    self._next_index = (index + 1) % count
                    try:
                        with self._state_lock:
                            self._save_next_index()
                    except OSError:
                        LOG.exception("Не удалось сохранить позицию Round Robin")
                    runtime = self._printer_runtime[printer.host]
                    runtime.available = True
                    runtime.checked_at = time.time()
                    runtime.last_error = ""
                    runtime.busy = True
                    self._emit_printer_status()
                    return printer, sock, lock
            if self._stop.wait(self.config.busy_retry_delay if acquired_busy and not connection_failures
                               else self.config.retry_delay):
                return None
        return None

    def _forward_job(self, source_path: Path, job_id: str) -> None:
        printer: PrinterConfig | None = None
        printer_socket: socket.socket | None = None
        printer_lock: threading.Lock | None = None
        staged_sending = source_path.with_suffix(".sending")
        bytes_sent = 0
        transmission_started = False
        started = time.monotonic()
        try:
            printer, printer_socket, printer_lock = self._reserve_printer() or (None, None, None)
            if printer is None or printer_socket is None:
                return
            os.replace(source_path, staged_sending)
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if job:
                    job.status = "Печатается"
                    job.printer = printer.host
                    job.started_at = time.time()
            self._printer_runtime[printer.host].current_job = job_id
            self._emit("job", job=self._jobs[job_id].as_dict() if job_id in self._jobs else {})

            with printer_socket:
                with staged_sending.open("rb") as source:
                    while True:
                        if self._stop.is_set():
                            raise OSError("Ретранслятор остановлен во время передачи")
                        chunk = source.read(self.config.receive_buffer_size)
                        if not chunk:
                            break
                        transmission_started = True
                        printer_socket.sendall(chunk)
                        bytes_sent += len(chunk)
                try:
                    printer_socket.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            elapsed = time.monotonic() - started
            self._printer_runtime[printer.host].jobs_sent += 1
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if job:
                    job.status = "Передано"
                    job.finished_at = time.time()
                self._counters["completed"] += 1
            staged_sending.unlink(missing_ok=True)
            LOG.info("Задание %s передано на %s:%d; %.0f байт; %.2f с",
                     job_id, printer.host, printer.port, bytes_sent, elapsed)
            self._emit("job_done", job_id=job_id, printer=printer.host, bytes=bytes_sent, elapsed=elapsed)
        except OSError as exc:
            if transmission_started:
                self._archive_failed(staged_sending, job_id, f"Передача на {printer.host if printer else '?'} прервана: {exc}")
            elif staged_sending.exists():
                # No bytes reached the printer: this staged job can safely be retried.
                try:
                    os.replace(staged_sending, source_path)
                    with self._jobs_lock:
                        job = self._jobs.get(job_id)
                        if job:
                            job.status = "В очереди"
                            job.printer = ""
                    self._emit("job", job=self._jobs[job_id].as_dict() if job_id in self._jobs else {})
                except OSError:
                    self._archive_failed(staged_sending, job_id, f"Не удалось вернуть задание в очередь: {exc}")
            if not self._stop.is_set():
                LOG.error("Ошибка задания %s: %s", job_id, exc)
        except Exception as exc:
            self._archive_failed(staged_sending, job_id, f"Непредвиденная ошибка: {exc}")
            LOG.exception("Непредвиденная ошибка обработки задания %s", job_id)
        finally:
            if printer_socket:
                try:
                    printer_socket.close()
                except OSError:
                    pass
            if printer_lock:
                printer_lock.release()
            if printer:
                runtime = self._printer_runtime[printer.host]
                runtime.busy = False
                runtime.current_job = ""
                self._emit_printer_status()

    def _archive_failed(self, path: Path, job_id: str, reason: str) -> None:
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        destination = self.failed_dir / path.name
        try:
            if path.exists():
                os.replace(path, destination)
        except OSError:
            destination = path
            LOG.exception("Не удалось переместить проблемное задание %s", path)
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "Ошибка — проверка"
                job.error = reason
                job.finished_at = time.time()
            self._counters["failed"] += 1
        LOG.error("Задание %s помещено в каталог ручной проверки: %s (%s)", job_id, destination, reason)
        self._emit("job_failed", job_id=job_id, reason=reason, path=str(destination))

    def _recover_spool(self) -> None:
        for path in sorted(self.spool_dir.glob("*.prn")):
            job_id = path.stem
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            info = JobInfo(job_id, path.name, "В очереди после перезапуска", size=size)
            with self._jobs_lock:
                self._jobs[job_id] = info
            self._workers_queue.put((path, job_id))
            LOG.warning("Восстановлено ожидающее задание %s", path.name)
        # A .sending file means a transfer may have reached the printer before
        # the previous process exited. Never retry it automatically.
        for path in self.spool_dir.glob("*.sending"):
            target = self.failed_dir / path.name
            try:
                os.replace(path, target)
                LOG.warning("Найдено задание с неизвестным результатом передачи: %s", target)
            except OSError:
                LOG.exception("Не удалось переместить незавершённое задание %s", path)
        for path in self.spool_dir.glob("*.part"):
            target = self.failed_dir / path.name
            try:
                os.replace(path, target)
            except OSError:
                LOG.exception("Не удалось переместить частичное задание %s", path)

    def _migrate_legacy_spool(self) -> None:
        old_spool = LEGACY_DIR / "spool"
        if not old_spool.exists() or old_spool.resolve() == self.spool_dir.resolve():
            return
        migrated_dir = old_spool / "migrated_by_print_round_robin"
        for old_file in old_spool.glob("*.prn"):
            target = self.spool_dir / old_file.name
            try:
                migrated_dir.mkdir(parents=True, exist_ok=True)
                archived_source = migrated_dir / old_file.name
                if archived_source.exists():
                    archived_source = migrated_dir / f"{uuid.uuid4().hex[:8]}_{old_file.name}"
                # Move the old source aside first so a later launch cannot import
                # the same document a second time after the new copy is printed.
                os.replace(old_file, archived_source)
                try:
                    if not target.exists():
                        shutil.copy2(archived_source, target)
                    LOG.warning("Перенесено задание прежней версии: %s", old_file.name)
                except OSError:
                    os.replace(archived_source, old_file)
                    raise
            except OSError:
                LOG.exception("Не удалось перенести прежнее задание %s", old_file)

    def _health_loop(self) -> None:
        while not self._stop.is_set():
            for printer in [p for p in self.config.printers if p.enabled]:
                if self._stop.is_set():
                    return
                runtime = self._printer_runtime[printer.host]
                if runtime.busy:
                    continue
                try:
                    with socket.create_connection((printer.host, printer.port), timeout=self.config.connect_timeout):
                        runtime.available = True
                        runtime.last_error = ""
                except OSError as exc:
                    runtime.available = False
                    runtime.last_error = str(exc)
                runtime.checked_at = time.time()
            self._emit_printer_status()
            self._stop.wait(self.config.health_interval)

    def _emit_printer_status(self) -> None:
        data = []
        for host, runtime in self._printer_runtime.items():
            if runtime.busy:
                status = "Занят"
            elif runtime.available is True:
                status = "Доступен"
            elif runtime.available is False:
                status = "Недоступен"
            else:
                status = "Проверка..."
            data.append({
                "name": runtime.config.name,
                "host": host,
                "port": runtime.config.port,
                "status": status,
                "busy": runtime.busy,
                "available": runtime.available,
                "jobs_sent": runtime.jobs_sent,
                "current_job": runtime.current_job,
                "last_error": runtime.last_error,
                "checked_at": runtime.checked_at,
            })
        self._emit("printers", printers=data)

    def _trim_jobs(self) -> None:
        while len(self._jobs) > 200:
            key, job = next(iter(self._jobs.items()))
            if job.status in ("В очереди", "В очереди после перезапуска", "Печатается"):
                break
            self._jobs.pop(key, None)

    def snapshot(self) -> dict:
        with self._jobs_lock:
            jobs = [job.as_dict() for job in list(self._jobs.values())[-100:]]
            counters = dict(self._counters)
        queued = sum(1 for job in jobs if job["status"].startswith("В очереди"))
        active = sum(1 for job in jobs if job["status"] == "Печатается")
        return {
            "running": self.running,
            "received": counters["received"],
            "completed": counters["completed"],
            "failed": counters["failed"],
            "queued": queued,
            "active": active,
            "jobs": jobs,
            "printers": [
                {
                    "name": r.config.name, "host": host, "port": r.config.port,
                    "status": "Занят" if r.busy else ("Доступен" if r.available is True else
                             "Недоступен" if r.available is False else "Проверка..."),
                    "jobs_sent": r.jobs_sent, "current_job": r.current_job,
                    "last_error": r.last_error,
                }
                for host, r in self._printer_runtime.items()
            ],
        }
