# -*- coding: utf-8 -*-
"""Small read-only SNMP v1/v2c client for standard printer MIB objects.

No third-party packages are required. The module polls Host Resources MIB and
Printer-MIB (RFC 2790 / RFC 3805). It intentionally performs read-only GET and
GETNEXT operations only.
"""
from __future__ import annotations

import secrets
import socket
import threading
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
HR_DEVICE_DESCRIPTION = "1.3.6.1.2.1.25.3.2.1.3"
HR_DEVICE_STATUS = "1.3.6.1.2.1.25.3.2.1.5"
HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1"
HR_PRINTER_ERRORS = "1.3.6.1.2.1.25.3.5.1.2"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
_VERSION_CACHE: dict[tuple[str, str, int, str], str] = {}
_VERSION_CACHE_LOCK = threading.Lock()
# Printer-MIB fields also queried by the user's working OLEPRN/VBA code.
PRT_GENERAL_SERIAL_NUMBER = "1.3.6.1.2.1.43.5.1.1.17"
PRT_CONSOLE_DISPLAY_TEXT = "1.3.6.1.2.1.43.16.5.1.2"
# Printer-MIB, RFC 3805.
PRT_SUPPLY_TYPE = "1.3.6.1.2.1.43.11.1.1.5"
PRT_SUPPLY_DESCRIPTION = "1.3.6.1.2.1.43.11.1.1.6"
PRT_SUPPLY_CAPACITY = "1.3.6.1.2.1.43.11.1.1.8"
PRT_SUPPLY_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"
# Printer-MIB input tray table (indexed by hrDeviceIndex.prtInputIndex).
PRT_INPUT_CAPACITY_UNIT = "1.3.6.1.2.1.43.8.2.1.8"
PRT_INPUT_MAX_CAPACITY = "1.3.6.1.2.1.43.8.2.1.9"
PRT_INPUT_CURRENT_LEVEL = "1.3.6.1.2.1.43.8.2.1.10"
PRT_INPUT_STATUS = "1.3.6.1.2.1.43.8.2.1.11"
PRT_INPUT_MEDIA_NAME = "1.3.6.1.2.1.43.8.2.1.12"
PRT_INPUT_NAME = "1.3.6.1.2.1.43.8.2.1.13"
PRT_INPUT_MODEL = "1.3.6.1.2.1.43.8.2.1.15"
PRT_INPUT_DESCRIPTION = "1.3.6.1.2.1.43.8.2.1.18"
PRT_ALERT_SEVERITY = "1.3.6.1.2.1.43.18.1.1.2"
PRT_ALERT_DESCRIPTION = "1.3.6.1.2.1.43.18.1.1.8"


_ERROR_BITS = (
    # Low-paper / low-toner are supply notices. They do not prevent printing.
    (0, "Мало бумаги", "info"),
    (1, "Нет бумаги", "critical"),
    (2, "Мало тонера", "info"),
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
    """Read Printer-MIB supplies, preserving type and interpreting waste-toner fill."""
    types = {
        _index_from_oid(v.oid, PRT_SUPPLY_TYPE): v.value
        for v in _safe_walk(client, PRT_SUPPLY_TYPE, max_rows=40)
    }
    descriptions = _safe_walk(client, PRT_SUPPLY_DESCRIPTION, max_rows=40)
    capacities = {
        _index_from_oid(v.oid, PRT_SUPPLY_CAPACITY): v.value
        for v in _safe_walk(client, PRT_SUPPLY_CAPACITY, max_rows=40)
    }
    levels = {
        _index_from_oid(v.oid, PRT_SUPPLY_LEVEL): v.value
        for v in _safe_walk(client, PRT_SUPPLY_LEVEL, max_rows=40)
    }
    supplies = []
    for item in descriptions:
        suffix = _index_from_oid(item.oid, PRT_SUPPLY_DESCRIPTION)
        supply_type = types.get(suffix)
        description = _text(item.value)
        # The device may expose a generic waste-toner component name without
        # publishing its actual fill/status. Do not present that as a supply
        # reading; real warning/critical events are handled by _poll_alerts.
        is_waste_toner = supply_type == 4 or "waste toner" in description.casefold() or "отработанн" in description.casefold()
        if is_waste_toner:
            continue
        if supply_type == 3 or any(word in description.casefold() for word in ("toner", "тонер")):
            name = "Тонер"
            kind = "toner"
        else:
            name = description
            kind = "other"

        level = levels.get(suffix)
        capacity = capacities.get(suffix)
        percent = None
        # Negative Printer-MIB values mean unavailable/other/unknown, not 0%.
        if isinstance(level, int) and isinstance(capacity, int) and level >= 0 and capacity > 0:
            percent = max(0, min(100, round(level * 100 / capacity)))

        if description:
            supply = {
                "name": name,
                "kind": kind,
                "snmp_type": supply_type,
                "level": level,
                "capacity": capacity,
                "percent": percent,
                "status": "unknown",
                "status_text": "",
                "percent_semantics": "remaining",
            }
            # Keep the device's original Printer-MIB description. On Kyocera
            # models this may contain the actual cartridge model (for example,
            # TK-xxx); the normalized display name alone loses that detail.
            if kind == "toner" and description.casefold() not in {"toner", "тонер"}:
                supply["model"] = description
            supplies.append(supply)
    return supplies


_INPUT_CAPACITY_UNITS = {
    1: "ед.",
    2: "ед.",
    3: "0,0001 дюйма",
    4: "мкм",
    8: "листов",
    16: "футов",
    17: "м",
    18: "шт.",
    19: "%",
}


def _poll_input_trays(client: SnmpClient) -> list[dict]:
    """Read input tray names and current/max paper load from Printer-MIB."""
    columns = {
        "unit": PRT_INPUT_CAPACITY_UNIT,
        "max_capacity": PRT_INPUT_MAX_CAPACITY,
        "current": PRT_INPUT_CURRENT_LEVEL,
        "status": PRT_INPUT_STATUS,
        "media_name": PRT_INPUT_MEDIA_NAME,
        "name": PRT_INPUT_NAME,
        "model": PRT_INPUT_MODEL,
        "description": PRT_INPUT_DESCRIPTION,
    }
    values: dict[str, dict[str, Any]] = {}
    suffixes: set[str] = set()
    for field, oid in columns.items():
        rows = _safe_walk(client, oid, max_rows=80)
        column = {_index_from_oid(row.oid, oid): row.value for row in rows}
        values[field] = column
        suffixes.update(column)

    trays = []
    for suffix in suffixes:
        indexes = suffix.split(".")
        if len(indexes) != 2 or not all(part.isdigit() for part in indexes):
            continue
        device_index, input_index = (int(part) for part in indexes)
        current = values["current"].get(suffix)
        maximum = values["max_capacity"].get(suffix)
        status = values["status"].get(suffix)
        if not isinstance(current, int) and not isinstance(maximum, int):
            continue

        # RFC 3805 PrtSubUnitStatusTC: availability is encoded in the low
        # three bits. 1 = unavailable/onRequest, 3 = unavailable/broken.
        # PA6000x reports a closed MP Tray as 19 (0x13): low bits = 3.
        # Its open state is 0, while the installed cassette reports 4.
        # Hide unavailable inputs instead of presenting the closed MP Tray as empty.
        availability = status & 0x07 if isinstance(status, int) and status >= 0 else None
        if availability in (1, 3):
            continue

        capacity_unit = values["unit"].get(suffix)
        unit_text = _INPUT_CAPACITY_UNITS.get(capacity_unit, "ед.")
        name_value = values["name"].get(suffix)
        model_value = values["model"].get(suffix)
        description_value = values["description"].get(suffix)
        media_value = values["media_name"].get(suffix)
        name = _text(name_value) if name_value is not None else ""
        model = _text(model_value) if model_value is not None else ""
        description = _text(description_value) if description_value is not None else ""
        media_name = _text(media_value) if media_value is not None else ""
        if not name:
            # Some Kyocera firmware leaves prtInputName empty but reports the
            # physical feed name in prtInputDescription/prtInputModel.
            name = description or model or f"Лоток {input_index}"

        percent = None
        if isinstance(current, int) and current >= 0 and isinstance(maximum, int) and maximum > 0:
            percent = max(0, min(100, round(current * 100 / maximum)))
        elif capacity_unit == 19 and isinstance(current, int) and 0 <= current <= 100:
            percent = current

        trays.append({
            "device_index": device_index,
            "input_index": input_index,
            "name": name,
            "model": model,
            "description": description,
            "media_name": media_name,
            "current": current,
            "max_capacity": maximum,
            "status": status,
            "availability": availability,
            "capacity_unit": capacity_unit,
            "unit": unit_text,
            "percent": percent,
        })

    trays.sort(key=lambda item: (item["device_index"], item["input_index"]))
    return trays


def _poll_alerts(client: SnmpClient) -> list[dict]:
    descriptions = _safe_walk(client, PRT_ALERT_DESCRIPTION, max_rows=60)
    severities = { _index_from_oid(v.oid, PRT_ALERT_SEVERITY): v.value
                   for v in _safe_walk(client, PRT_ALERT_SEVERITY, max_rows=60) }
    alerts = []
    for item in descriptions:
        description = _text(item.value)
        if not description:
            continue
        suffix = _index_from_oid(item.oid, PRT_ALERT_DESCRIPTION)
        severity = severities.get(suffix)
        # RFC 3805: critical(3), warning(4), warningBinaryChangeEvent(5), other(1).
        level = "critical" if severity == 3 else "warning" if severity in (4, 5) else "info"
        lower_description = description.casefold()
        if any(token in lower_description for token in (
            "low toner", "toner low", "мало тонера", "низкий уровень тонера",
        )):
            alerts.append({"text": "Мало тонера", "severity": "info"})
            continue
        if any(token in lower_description for token in (
            "low paper", "paper low", "paper is low", "мало бумаги", "низкий уровень бумаги",
        )):
            alerts.append({"text": "Мало бумаги", "severity": "info"})
            continue
        if any(token in lower_description for token in (
            "waste toner box", "waste toner container",
            "контейнер отработанного тонера", "бункер отработанного тонера",
        )):
            lower = description.casefold()
            explicit_full = any(token in lower for token in (
                "full", "replace", "заполнен", "замен",
            ))
            explicit_warning = any(token in lower for token in (
                "near full", "almost full", "почти заполнен", "требует внимания",
            ))
            # Ignore a bare component label. Preserve actionable text even if
            # the device reports an unhelpful generic severity code.
            if severity == 3 or (explicit_full and not explicit_warning):
                description = "Бункер отработанного тонера заполнен — требуется замена"
                level = "critical"
            elif severity in (4, 5) or explicit_warning:
                description = "Бункер отработанного тонера требует внимания"
                level = "warning"
            else:
                continue
        alerts.append({"text": description, "severity": level})
    return alerts


def _safe_get(client: SnmpClient, oid: str) -> VarBind | None:
    """An unsupported optional MIB object must not make the whole printer unavailable."""
    try:
        return client.get(oid)
    except SnmpError:
        return None


def _safe_walk(client: SnmpClient, oid: str, max_rows: int) -> list[VarBind]:
    try:
        return client.walk(oid, max_rows=max_rows)
    except SnmpError:
        return []


def _discover_printer_identity(client: SnmpClient) -> tuple[VarBind | None, str]:
    """Use the same hrDeviceDescr OID as OLEPRN VBA, then standard sysName as fallback."""
    candidates = (f"{HR_DEVICE_DESCRIPTION}.1", SYS_NAME)
    for oid in candidates:
        try:
            item = client.get(oid)
        except SnmpError:
            item = None
        if item is not None and _text(item.value):
            return item, oid

    # Some devices expose hrDeviceDescr but use an index other than 1.
    for item in _safe_walk(client, HR_DEVICE_DESCRIPTION, max_rows=20):
        if _text(item.value):
            return item, item.oid
    return None, ""


def _make_client_for_printer(host: str, community: str, port: int,
                             timeout: float, version: str) -> tuple[SnmpClient, VarBind, str]:
    """Try the configured SNMP version first, then the other community-based version."""
    preferred = "1" if str(version).lower() in ("1", "v1") else "2c"
    other = "2c" if preferred == "1" else "1"
    cache_key = (str(host), str(community), int(port), preferred)
    with _VERSION_CACHE_LOCK:
        cached_version = _VERSION_CACHE.get(cache_key)
    candidates = (
        (cached_version, other) if cached_version in ("1", "2c")
        else (preferred, other)
    )
    errors: list[str] = []
    for candidate_version in candidates:
        client = SnmpClient(host, community, port, timeout, candidate_version)
        identity, identity_oid = _discover_printer_identity(client)
        if identity is not None:
            with _VERSION_CACHE_LOCK:
                _VERSION_CACHE[cache_key] = candidate_version
            return client, identity, identity_oid
        errors.append(f"SNMPv{candidate_version}: не получен ответ на OID hrDeviceDescr/sysName")
    raise SnmpError(
        "Нет ответа на базовые SNMP-запросы hrDeviceDescr и sysName. " + "; ".join(errors)
    )


def poll_printer(host: str, community: str = "public", port: int = 161,
                 timeout: float = 0.8, version: str = "2c") -> dict:
    """Poll a printer; tolerate missing MIB objects and negotiate v1/v2c for its agent."""
    client, identity, identity_oid = _make_client_for_printer(
        host, community, port, timeout, version
    )

    # Independent SNMP requests are sent concurrently. A device that silently
    # drops an optional MIB request therefore cannot delay every other field.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=7, thread_name_prefix="SNMPFields") as pool:
        status_future = pool.submit(_safe_get, client, f"{HR_PRINTER_STATUS}.1")
        device_future = pool.submit(_safe_get, client, f"{HR_DEVICE_STATUS}.1")
        console_future = pool.submit(_safe_get, client, f"{PRT_CONSOLE_DISPLAY_TEXT}.1.1")
        serial_future = pool.submit(_safe_get, client, f"{PRT_GENERAL_SERIAL_NUMBER}.1")
        alerts_future = pool.submit(_poll_alerts, client)
        supplies_future = pool.submit(_poll_supply_data, client)
        trays_future = pool.submit(_poll_input_trays, client)

        status = status_future.result()
        device_status_item = device_future.result()
        console_item = console_future.result()
        serial_item = serial_future.result()
        alerts = alerts_future.result()
        supplies = supplies_future.result()
        trays = trays_future.result()

    printer_index = None
    printer_status = None
    if status is not None and isinstance(status.value, int):
        printer_index = "1"
        printer_status = status.value
    else:
        for row in _safe_walk(client, HR_PRINTER_STATUS, max_rows=20):
            suffix = _index_from_oid(row.oid, HR_PRINTER_STATUS)
            if suffix.isdigit():
                printer_index = suffix
                printer_status = row.value
                break

    errors: list[dict[str, str]] = []
    if printer_index:
        error = _safe_get(client, f"{HR_PRINTER_ERRORS}.{printer_index}")
        if error and isinstance(error.value, bytes):
            errors = decode_error_bits(error.value)

    # hrDeviceStatus is the warning/status value read by the VBA workbook.
    device_status = device_status_item.value if device_status_item else None
    if device_status == 3:
        errors.append({"text": "Устройство сообщает состояние WARNING (hrDeviceStatus=3)", "severity": "warning"})
    elif device_status == 5:
        errors.append({"text": "Устройство сообщает состояние DOWN (hrDeviceStatus=5)", "severity": "critical"})

    console_message = _text(console_item.value) if console_item and console_item.value is not None else ""
    serial_number = _text(serial_item.value) if serial_item and serial_item.value is not None else ""

    # Alerts provide detailed device-specific wording; de-duplicate against Host MIB bits.
    seen = {x["text"].casefold() for x in errors}
    for alert in alerts:
        if alert["text"].casefold() not in seen:
            errors.append(alert)
            seen.add(alert["text"].casefold())
    supply_warning = []
    for supply in supplies:
        name = supply["name"].casefold()
        if supply["percent"] is not None and any(word in name for word in ("toner", "тонер")):
            if supply["percent"] <= 5:
                supply_warning.append({"text": f"Тонер почти закончился ({supply['percent']}%)", "severity": "info"})
            elif supply["percent"] <= 15:
                supply_warning.append({"text": f"Мало тонера ({supply['percent']}%)", "severity": "info"})
    for item in supply_warning:
        if item["text"] not in seen:
            errors.append(item)
            seen.add(item["text"])

    # Keep consumable notices separate from operational warnings and faults.
    notices = [item for item in errors if item.get("severity") == "info"]
    errors = [item for item in errors if item.get("severity") != "info"]

    # Some printers raise the generic hrDeviceStatus=warning flag for low supplies.
    # When a specific consumable notice exists and the printer is operational,
    # do not let that generic host-resource flag mask Ready / Printing.
    if notices and printer_status in (3, 4, 5):
        errors = [
            item for item in errors
            if item.get("text") != "Устройство сообщает состояние WARNING (hrDeviceStatus=3)"
        ]

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
    name = _text(identity.value)
    if serial_number and serial_number.casefold() not in name.casefold():
        name = f"{name}\n{serial_number}"
    return {
        "reachable": True,
        "name": name,
        "identity_oid": identity_oid,
        "state": state,
        "state_text": state_names.get(printer_status, "Состояние неизвестно"),
        "printer_status": printer_status,
        "device_status": device_status,
        "status_message": console_message,
        "serial_number": serial_number,
        "printer_index": printer_index,
        "errors": errors,
        "notices": notices,
        "alerts": alerts,
        "supplies": supplies,
        "trays": trays,
        "toner_text": ", ".join(toner_values) if toner_values else "Уровень тонера не сообщается",
        "snmp_version_used": "1" if client.version == 0 else "2c",
        "polled_at": time.time(),
        "source": "SNMP Printer-MIB / Host Resources MIB",
    }


def classify_error_bits(value: bytes) -> str:
    items = decode_error_bits(value)
    return " / ".join(item["text"] for item in items) if items else "Нет активных ошибок"
