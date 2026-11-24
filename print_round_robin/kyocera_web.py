# -*- coding: utf-8 -*-
"""Read-only Kyocera Command Center RX and WSD endpoint monitor.

The web monitor is deliberately independent from SNMP and RAW printing. It
uses only standard-library networking and never submits configuration changes.
"""
from __future__ import annotations

import html
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


WSD_PORTS = (5357, 5358)
WEB_TIMEOUT = 1.5
MAX_PAGE_BYTES = 1024 * 1024


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._hidden += 1
            return
        if self._hidden:
            return
        if tag in ("br", "p", "div", "tr", "td", "th", "li", "section"):
            self.parts.append("\n")
        for key in ("alt", "title", "aria-label"):
            value = attrs.get(key)
            if value:
                self.parts.append(str(value) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._hidden:
            self._hidden -= 1
        elif not self._hidden and tag in ("p", "div", "tr", "li", "section"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden and data.strip():
            self.parts.append(data.strip() + " ")


def _read_home_page(host: str, timeout: float) -> tuple[str, str] | tuple[None, None]:
    context = ssl._create_unverified_context()
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    for scheme in ("http", "https"):
        url = f"{scheme}://{host}/"
        request = Request(url, headers={"User-Agent": "PrintRoundRobin/1.0", "Accept": "text/html,*/*"})
        try:
            response = opener.open(request, timeout=timeout)
            with response:
                body = response.read(MAX_PAGE_BYTES + 1)
                if len(body) > MAX_PAGE_BYTES:
                    body = body[:MAX_PAGE_BYTES]
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    source = body.decode(charset, errors="replace")
                except LookupError:
                    source = body.decode("utf-8", errors="replace")
                return response.geturl(), source
        except HTTPError as exc:
            # A login page is still useful to identify the web server, but it
            # does not expose unauthenticated status data.
            if exc.code in (401, 403):
                return url, ""
        except (OSError, URLError, TimeoutError, ssl.SSLError, ValueError):
            continue
    return None, None


def _visible_text(source: str) -> str:
    parser = _VisibleText()
    try:
        parser.feed(source)
        parser.close()
        return html.unescape(" ".join(parser.parts))
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", source))


def _toner_percent(source: str, visible: str) -> int | None:
    candidates = (visible, html.unescape(source))
    patterns = (
        r"(?:toner|тонер)[^%]{0,220}?(\d{1,3})\s*%",
        r"(\d{1,3})\s*%[^%]{0,100}?(?:toner|тонер)",
    )
    for text in candidates:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                value = int(match.group(1))
                if 0 <= value <= 100:
                    return value
    return None


def _classify_status(visible: str) -> tuple[str, str, list[dict[str, str]]]:
    text = re.sub(r"\s+", " ", visible).strip()
    lower = text.casefold()
    errors: list[dict[str, str]] = []
    rules = (
        (("paper jam", "jammed", "замят", "застрев"), "Замятие бумаги", "critical"),
        (("cover open", "door open", "крышка открыта", "дверца открыта"), "Открыта крышка", "critical"),
        (("no paper", "out of paper", "paper empty", "нет бумаги", "бумага отсутствует"), "Нет бумаги", "critical"),
        (("toner empty", "replace toner", "тонер закончился", "замените тонер"), "Требуется замена тонера", "critical"),
        (("low toner", "toner low", "мало тонера", "низкий уровень тонера"), "Мало тонера", "warning"),
        (("low paper", "paper low", "мало бумаги"), "Мало бумаги", "warning"),
    )
    for needles, message, severity in rules:
        if any(needle in lower for needle in needles):
            errors.append({"text": message, "severity": severity})

    status = "unknown"
    status_message = ""
    if errors:
        status = "error" if any(item["severity"] == "critical" for item in errors) else "warning"
        status_message = errors[0]["text"]
    elif any(word in lower for word in ("printing", "печатает", "печать выполняется")):
        status, status_message = "printing", "Печатает"
    elif any(word in lower for word in ("ready to print", "ready", "готов к печати", "готов", "ожидает")):
        status, status_message = "ready", "Готов"
    elif any(word in lower for word in ("warming up", "warm-up", "прогрев")):
        status, status_message = "warmup", "Прогрев"

    # Prefer a short phrase next to the operation-panel/status label where
    # present; otherwise the recognized state above is the useful summary.
    for label in ("operation panel", "device status", "printer status", "состояние устройства", "панель управления"):
        pos = lower.find(label)
        if pos >= 0:
            excerpt = text[pos + len(label):pos + len(label) + 180].strip(" :|-")
            excerpt = re.split(r"\s{2,}|(?:paper|toner|media input)\b", excerpt, maxsplit=1, flags=re.IGNORECASE)[0]
            if excerpt and len(excerpt) <= 100:
                status_message = excerpt
                break
    return status, status_message, errors


def poll_kyocera_web(host: str, timeout: float = WEB_TIMEOUT) -> dict:
    """Probe standard WSD HTTP endpoints and read public Command Center RX status."""
    wsd_port = None
    for port in WSD_PORTS:
        try:
            with socket.create_connection((host, port), timeout=min(timeout, 0.7)):
                wsd_port = port
                break
        except OSError:
            continue

    url, source = _read_home_page(host, timeout)
    if source is None:
        return {
            "wsd_available": wsd_port is not None,
            "wsd_port": wsd_port,
            "web_available": False,
            "web_url": None,
            "supplies": [],
            "errors": [],
            "state": "unknown",
            "status_message": "",
            "polled_at": time.time(),
            "source": "Kyocera WSD / Command Center RX",
        }

    visible = _visible_text(source)
    percent = _toner_percent(source, visible)
    state, status_message, errors = _classify_status(visible)
    supplies = ([{"name": "Тонер", "percent": percent, "source": "Command Center RX"}]
                if percent is not None else [])
    return {
        "wsd_available": wsd_port is not None,
        "wsd_port": wsd_port,
        "web_available": bool(source),
        "web_url": url,
        "supplies": supplies,
        "errors": errors,
        "state": state,
        "status_message": status_message,
        "polled_at": time.time(),
        "source": "Kyocera Command Center RX",
    }
