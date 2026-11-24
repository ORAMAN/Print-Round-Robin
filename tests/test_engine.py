import socket
import threading
import time

from print_round_robin.config import AppConfig, PrinterConfig
from print_round_robin.engine import JobInfo, RoundRobinEngine


class RawPrinter:
    def __init__(self, host="127.0.0.1"):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, 0))
        self.server.listen(5)
        self.server.settimeout(0.2)
        self.port = self.server.getsockname()[1]
        self.payloads = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            chunks = []
            with client:
                while True:
                    data = client.recv(65536)
                    if not data:
                        break
                    chunks.append(data)
            payload = b"".join(chunks)
            if payload:
                self.payloads.append(payload)

    def close(self):
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=1)


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_one_job_is_staged_and_forwarded_to_raw_printer(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    printer = RawPrinter()
    listener_port = _free_port()
    config = AppConfig(
        listener_port=listener_port,
        printers=[PrinterConfig("Loopback test", "127.0.0.1", printer.port)],
        max_workers=1,
        health_interval=60,
        connect_timeout=0.5,
        send_timeout=5,
    )
    engine = RoundRobinEngine(config)
    payload = b"%PDF-1.7\nprint-round-robin-test\n" + bytes(range(256))
    try:
        engine.start()
        with socket.create_connection(("127.0.0.1", listener_port), timeout=2) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
        deadline = time.time() + 5
        while time.time() < deadline and not printer.payloads:
            time.sleep(0.05)
        assert printer.payloads == [payload]
        snapshot = engine.snapshot()
        assert snapshot["completed"] == 1
        assert snapshot["failed"] == 0
    finally:
        engine.stop()
        printer.close()


def test_two_jobs_are_dispatched_to_different_printers_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    hosts = ["127.0.0.2", "127.0.0.3"]
    printers = [RawPrinter(host) for host in hosts]
    listener_port = _free_port()
    config = AppConfig(
        listener_port=listener_port,
        printers=[
            PrinterConfig("Loopback A", hosts[0], printers[0].port),
            PrinterConfig("Loopback B", hosts[1], printers[1].port),
        ],
        max_workers=2,
        health_interval=60,
        connect_timeout=0.5,
        send_timeout=5,
    )
    engine = RoundRobinEngine(config)
    payloads = [b"first-independent-document", b"second-independent-document"]
    try:
        engine.start()

        def submit(payload):
            with socket.create_connection(("127.0.0.1", listener_port), timeout=2) as client:
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)

        senders = [threading.Thread(target=submit, args=(payload,)) for payload in payloads]
        for sender in senders:
            sender.start()
        for sender in senders:
            sender.join(timeout=2)

        deadline = time.time() + 6
        while time.time() < deadline and not all(printer.payloads for printer in printers):
            time.sleep(0.05)

        assert sorted(printer.payloads[0] for printer in printers) == sorted(payloads)
        snapshot = engine.snapshot()
        assert snapshot["completed"] == 2
        assert snapshot["failed"] == 0
    finally:
        engine.stop()
        for printer in printers:
            printer.close()



def test_snapshot_limits_jobs_and_prioritizes_active_then_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = AppConfig(printers=[])
    engine = RoundRobinEngine(config)
    with engine._jobs_lock:
        for index in range(125):
            job = JobInfo(
                job_id=f"job-{index:03d}",
                filename=f"job-{index:03d}.prn",
                status="Передано",
                received_at=float(index),
            )
            engine._jobs[job.job_id] = job
        # Старое активное задание должно остаться в видимой выборке.
        engine._jobs["active-old"] = JobInfo(
            job_id="active-old",
            filename="active-old.prn",
            status="Печатается",
            received_at=1.0,
        )

    jobs = engine.snapshot()["jobs"]
    assert len(jobs) == 100
    assert jobs[-1]["job_id"] == "active-old"
    assert jobs[0]["job_id"] == "job-025"
    assert jobs[-1]["status"] == "Печатается"
