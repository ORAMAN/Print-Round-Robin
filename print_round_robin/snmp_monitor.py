# -*- coding: utf-8 -*-
"""Small read-only SNMP v1/v2c client for standard printer MIB objects.

No third-party packages are required. The module polls Host Resources MIB and
Printer-MIB (RFC 2790 / RFC 3805). It intentionally performs read-only GET and
GETNEXT operations only.
"""
from __future__ import annotations

import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any


class SnmpError(RuntimeError):
    pass


def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(payload)) + payload


def _encode_integer(value: int) -> bytes:
    if value == 0:
        raw = b"\x00"
    else:
        size = max(1, (value.bit_length() + (8 if value >= 0 else 7)) // 8)
        raw = value.to_bytes(size, "big", signed=True)
        while len(raw) > 1 and (
            (raw[0] == 0 and raw[1] < 0x80) or
            (raw[0] == 0xFF and raw[1] >= 0x80)
        ):
            raw = raw[1:]
    return _tlv(0x02, raw)


def _base128(value: int) -> bytes:
    if value < 0:
        raise ValueError("OID arcs cannot be negative")
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def encode_oid(oid: str | tuple[int, ...]) -> bytes:
    arcs = tuple(int(x) for x in oid.strip(".").split(".")) if isinstance(oid, str) else tuple(oid)
    if len(arcs) < 2 or arcs[0] not in (0, 1, 2) or arcs[1] < 0:
        raise ValueError(f"Invalid OID: {oid}")
    if arcs[0] < 2 and arcs[1] > 39:
        raise ValueError(f"Invalid OID: {oid}")
    body = _base128(40 * arcs[0] + arcs[1])
    for arc in arcs[2:]:
        body += _base128(arc)
    return _tlv(0x06, body)


def _read_tlv(data: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    if offset + 2 > len(data):
        raise SnmpError("Некорректный BER: заголовок обрезан.")
    tag = data[offset]
    first_length = data[offset + 1]
    cursor = offset + 2
    if first_length & 0x80:
        count = first_length & 0x7F
        if count == 0 or count > 4 or cursor + count > len(data):
            raise SnmpError("Некорректная длина BER.")
        length = int.from_bytes(data[cursor:cursor + count], "big")
        cursor += count
    else:
        length = first_length
    end = cursor + length
    if end > len(data):
        raise SnmpError("BER-значение обрезано.")
    return tag, data[cursor:end], end


def decode_oid(data: bytes) -> str:
    if not data:
        raise SnmpError("Пустой OID.")
    subids: list[int] = []
    current = 0
    for byte in data:
        current = (current << 7) | (byte & 0x7F)
        if not byte & 0x80:
            subids.append(current)
            current = 0
    if current:
        raise SnmpError("Обрезанный OID.")
    first = subids.pop(0)
    if first < 40:
        arcs = [0, first]
    elif first < 80:
        arcs = [1, first - 40]
    else:
        arcs = [2, first - 80]
    arcs.extend(subids)
    return ".".join(str(x) for x in arcs)


def _decode_integer(data: bytes) -> int:
    return int.from_bytes(data or b"\x00", "big", signed=True)


@dataclass
class VarBind:
    oid: str
    tag: int
    value: Any


def _decode_value(tag: int, value: bytes) -> Any:
    if tag == 0x02:
        return _decode_integer(value)
    if tag == 0x04:
        return value
    if tag == 0x05:
        return None
    if tag == 0x06:
        return decode_oid(value)
    if tag in (0x41, 0x42, 0x43, 0x46):
        return int.from_bytes(value or b"\x00", "big", signed=False)
    if tag == 0x40:
        return ".".join(str(x) for x in value)
    if tag in (0x80, 0x81, 0x82):
        return None
    return value


def decode_message(packet: bytes) -> tuple[int, int, list[VarBind]]:
    tag, outer, end = _read_tlv(packet, 0)
    if tag != 0x30 or end != len(packet):
        raise SnmpError("Ответ SNMP не является BER SEQUENCE.")
    offset = 0
    _version_tag, version_raw, offset = _read_tlv(outer, offset)
    version = _decode_integer(version_raw)
    _community_tag, _community, offset = _read_tlv(outer, offset)
    pdu_tag, pdu, offset = _read_tlv(outer, offset)
    if pdu_tag != 0xA2:
        raise SnmpError(f"Ожидался SNMP GetResponse, получен тег {pdu_tag:#x}.")
    p = 0
    _request_tag, request_raw, p = _read_tlv(pdu, p)
    _error_tag, error_raw, p = _read_tlv(pdu, p)
    _index_tag, index_raw, p = _read_tlv(pdu, p)
    _list_tag, bindings_raw, p = _read_tlv(pdu, p)
    if p != len(pdu):
        raise SnmpError("Лишние данные в SNMP PDU.")
    request_id = _decode_integer(request_raw)
    error_status = _decode_integer(error_raw)
    _error_index = _decode_integer(index_raw)
    # bindings_raw is already the content of the VarBindList SEQUENCE.
    cursor = 0
    bindings: list[VarBind] = []
    while cursor < len(bindings_raw):
        item_tag, item, cursor = _read_tlv(bindings_raw, cursor)
        if item_tag != 0x30:
            raise SnmpError("Некорректная запись VarBind.")
        pos = 0
        _oid_tag, oid_raw, pos = _read_tlv(item, pos)
        value_tag, value_raw, pos = _read_tlv(item, pos)
        bindings.append(VarBind(decode_oid(oid_raw), value_tag, _decode_value(value_tag, value_raw)))
    return request_id, error_status, bindings


class SnmpClient:
    def __init__(self, host: str, community: str = "public", port: int = 161,
                 timeout: float = 0.8, version: str = "2c"):
        self.host = host
        self.community = community.encode("utf-8")
        self.port = int(port)
        self.timeout = float(timeout)
        self.version = 0 if str(version).lower() in ("1", "v1") else 1

    def _request(self, oid: str, next_value: bool = False) -> VarBind | None:
        request_id = secrets.randbelow(0x7FFFFFFE) + 1
        varbind = _tlv(0x30, encode_oid(oid) + _tlv(0x05, b""))
        varlist = _tlv(0x30, varbind)
        pdu_tag = 0xA1 if next_value else 0xA0
        pdu = _tlv(
            pdu_tag,
            _encode_integer(request_id) + _encode_integer(0) +
            _encode_integer(0) + varlist,
        )
        packet = _tlv(
            0x30,
            _encode_integer(self.version) + _tlv(0x04, self.community) + pdu,
        )
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self.timeout)
                sock.sendto(packet, (self.host, self.port))
                response, _address = sock.recvfrom(65535)
        except OSError as exc:
            raise SnmpError(str(exc)) from exc
        response_id, error_status, bindings = decode_message(response)
        if response_id != request_id:
            raise SnmpError("SNMP ответ содержит другой request-id.")
        if error_status:
            if error_status in (2, 5):  # noSuchName / genErr
                return None
            raise SnmpError(f"SNMP error-status={error_status}.")
        if not bindings:
            return None
        binding = bindings[0]
        if binding.tag in (0x80, 0x81, 0x82):
            return None
        return binding

    def get(self, oid: str) -> VarBind | None:
        result = self._request(oid)
        if result is not None and result.oid != oid:
            raise SnmpError("SNMP GET вернул неожиданный OID.")
        return result

    def walk(self, root_oid: str, max_rows: int = 80) -> list[VarBind]:
        root = root_oid.strip(".")
        current = root
        found: list[VarBind] = []
        for _ in range(max_rows):
            value = self._request(current, next_value=True)
            if value is None:
                break
            if not value.oid.startswith(root + "."):
                break
            if value.oid == current:
                break
            found.append(value)
            current = value.oid
        return found


# Host Resources MIB, RFC 2790.
HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1"
HR_PRINTER_ERRORS = "1.3.6.1.2.1.25.3.5.1.2"
# Printer-MIB, RFC 3805.
PRT_SUPPLY_DESCRIPTION = "1.3.6.1.2.1.43.11.1.1.6"
PRT_SUPPLY_CAPACITY = "1.3.6.1.2.1.43.11.1.1.8"
PRT_SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"
PRT_ALERT_SEVERITY = "1.3.6.1.2.1.43.18.1.1.2"
PRT_ALERT_DESCRIPTION = "1.3.6.1.2.1.43.18.1.1.8"


_ERROR_BITS = (
    (0, "Мало бумаги", "warning"),
    (1, "Нет бумаги", "critical"),
    (2, "Мало тонера", "warning"),
    (3, "Тонер закончился", "critical"),
    (4, "Открыта крышка", "critical"),
    (5, "Замятие бумаги", "critical"),
    (6, "Принтер в автономном режиме", "critical"),
    (7, "Требуется обслуживание", "warning"),
    (8, "Не установлен входной лоток", "critical"),
    (9, "Не установлен выходной лоток", "warning"),
    (10, "Отсутствует расходный материал", "critical"),
    (11, "Выходной лоток почти заполнен", "warning"),
    (12, "Выходной лоток заполнен", "critical"),
    (13, "Входной лоток пуст", "warning"),
    (14, "Требуется регламентное обслуживание", "warning"),
)


def decode_error_bits(value: bytes) -> list[dict[str, str]]:
    """Decode hrPrinterDetectedErrorState; bit 0 is the MSB of byte 0."""
    result = []
    for bit, label, severity in _ERROR_BITS:
        byte_index, bit_in_byte = divmod(bit, 8)
        if byte_index < len(value) and (value[byte_index] & (1 << (7 - bit_in_byte))):
            result.append({"text": label, "severity": severity})
    return result


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00 ").strip()
    return str(value)


def _index_from_oid(oid: str, base: str) -> str:
    return oid[len(base):].lstrip(".")


def _poll_supply_data(client: SnmpClient) -> list[dict]:
    descriptions = client.walk(PRT_SUPPLY_DESCRIPTION, max_rows=40)
    capacities = { _index_from_oid(v.oid, PRT_SUPPLY_CAPACITY): v.value
                   for v in client.walk(PRT_SUPPLY_CAPACITY, max_rows=40) }
    levels = { _index_from_oid(v.oid, PRT_SUPPLY_LEVEL): v.value
               for v in client.walk(PRT_SUPPLY_LEVEL, max_rows=40) }
    supplies = []
    for item in descriptions:
        suffix = _index_from_oid(item.oid, PRT_SUPPLY_DESCRIPTION)
        description = _text(item.value)
        level = levels.get(suffix)
        capacity = capacities.get(suffix)
        percent = None
        if isinstance(level, int) and isinstance(capacity, int) and level >= 0 and capacity > 0:
            percent = max(0, min(100, round(level * 100 / capacity)))
        if description:
            supplies.append({"name": description, "level": level, "capacity": capacity, "percent": percent})
    return supplies


def _poll_alerts(client: SnmpClient) -> list[dict]:
    descriptions = client.walk(PRT_ALERT_DESCRIPTION, max_rows=60)
    severities = { _index_from_oid(v.oid, PRT_ALERT_SEVERITY): v.value
                   for v in client.walk(PRT_ALERT_SEVERITY, max_rows=60) }
    alerts = []
    for item in descriptions:
        description = _text(item.value)
        if not description:
            continue
        suffix = _index_from_oid(item.oid, PRT_ALERT_DESCRIPTION)
        severity = severities.get(suffix)
        # RFC 3805: critical(3), warning(4), warningBinaryChangeEvent(5), other(1).
        level = "critical" if severity == 3 else "warning" if severity in (4, 5) else "info"
        alerts.append({"text": description, "severity": level})
    return alerts


def poll_printer(host: str, community: str = "public", port: int = 161,
                 timeout: float = 0.8, version: str = "2c") -> dict:
    """Return a normalized printer status or raise SnmpError if SNMP is unavailable."""
    client = SnmpClient(host, community, port, timeout, version)
    sys_name = client.get("1.3.6.1.2.1.1.5.0")
    if sys_name is None:
        raise SnmpError("SNMP-агент не вернул system.sysName.")

    status_rows = client.walk(HR_PRINTER_STATUS, max_rows=20)
    printer_index = None
    printer_status = None
    for row in status_rows:
        suffix = _index_from_oid(row.oid, HR_PRINTER_STATUS)
        if suffix.isdigit():
            printer_index = suffix
            printer_status = row.value
            break

    errors: list[dict[str, str]] = []
    if printer_index:
        error = client.get(f"{HR_PRINTER_ERRORS}.{printer_index}")
        if error and isinstance(error.value, bytes):
            errors = decode_error_bits(error.value)

    alerts = _poll_alerts(client)
    # Alerts provide detailed device-specific wording; de-duplicate against Host MIB bits.
    seen = {x["text"].casefold() for x in errors}
    for alert in alerts:
        if alert["text"].casefold() not in seen:
            errors.append(alert)
            seen.add(alert["text"].casefold())

    supplies = _poll_supply_data(client)
    supply_warning = []
    for supply in supplies:
        name = supply["name"].casefold()
        if supply["percent"] is not None and any(word in name for word in ("toner", "тонер")):
            if supply["percent"] <= 5:
                supply_warning.append({"text": f"Тонер почти закончился ({supply['percent']}%)", "severity": "critical"})
            elif supply["percent"] <= 15:
                supply_warning.append({"text": f"Мало тонера ({supply['percent']}%)", "severity": "warning"})
    for item in supply_warning:
        if item["text"] not in seen:
            errors.append(item)

    critical = any(item["severity"] == "critical" for item in errors)
    warning = any(item["severity"] == "warning" for item in errors)
    state_names = {1: "Другое", 2: "Неизвестно", 3: "Ожидает", 4: "Печатает", 5: "Прогрев"}
    if critical:
        state = "error"
    elif warning:
        state = "warning"
    elif printer_status == 4:
        state = "printing"
    elif printer_status == 5:
        state = "warmup"
    elif printer_status == 3:
        state = "ready"
    else:
        state = "unknown"

    toner_values = [
        f"{s['name']}: {s['percent']}%" for s in supplies
        if s["percent"] is not None and any(k in s["name"].casefold() for k in ("toner", "тонер"))
    ]
    return {
        "reachable": True,
        "name": _text(sys_name.value),
        "state": state,
        "state_text": state_names.get(printer_status, "Состояние неизвестно"),
        "printer_status": printer_status,
        "printer_index": printer_index,
        "errors": errors,
        "alerts": alerts,
        "supplies": supplies,
        "toner_text": ", ".join(toner_values) if toner_values else "Уровень тонера не сообщается",
        "polled_at": time.time(),
        "source": "SNMP Printer-MIB / Host Resources MIB",
    }


def classify_error_bits(value: bytes) -> str:
    items = decode_error_bits(value)
    return " / ".join(item["text"] for item in items) if items else "Нет активных ошибок"
