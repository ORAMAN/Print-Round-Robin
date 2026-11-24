from print_round_robin.snmp_monitor import (
    SnmpClient,
    _encode_integer,
    _read_tlv,
    _tlv,
    decode_error_bits,
    decode_message,
    decode_oid,
    encode_oid,
)


def test_oid_encoder_round_trips_standard_printer_mib_oid():
    encoded = encode_oid("1.3.6.1.2.1.43.11.1.1.9")
    tag, payload, end = _read_tlv(encoded)
    assert tag == 0x06
    assert end == len(encoded)
    assert decode_oid(payload) == "1.3.6.1.2.1.43.11.1.1.9"


def test_hr_printer_error_bits_are_decoded_most_significant_first():
    # Bit 0 = lowPaper, bit 2 = lowToner, bit 5 = jammed, bit 13 = inputTrayEmpty.
    result = decode_error_bits(bytes((0b10100100, 0b00000100)))
    assert [item["text"] for item in result] == [
        "Мало бумаги",
        "Мало тонера",
        "Замятие бумаги",
        "Входной лоток пуст",
    ]


def test_decode_snmp_get_response_with_one_integer_varbind():
    request_id = 12345
    oid = "1.3.6.1.2.1.25.3.5.1.1.1"
    varbind = _tlv(0x30, encode_oid(oid) + _tlv(0x02, b"\x04"))
    varlist = _tlv(0x30, varbind)
    pdu = _tlv(
        0xA2,
        _encode_integer(request_id) + _encode_integer(0) + _encode_integer(0) + varlist,
    )
    packet = _tlv(
        0x30,
        _encode_integer(1) + _tlv(0x04, b"public") + pdu,
    )
    returned_id, error_status, bindings = decode_message(packet)
    assert returned_id == request_id
    assert error_status == 0
    assert bindings[0].oid == oid
    assert bindings[0].value == 4


def test_alert_severity_and_toner_level_are_standard_mib_paths():
    assert SnmpClient("192.0.2.1").port == 161



def test_poll_printer_uses_vba_hr_device_description_and_reads_vba_fields(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        def __init__(self, host, community, port, timeout, version):
            self.version = 0 if version == "1" else 1

        def get(self, oid):
            values = {
                f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1": b"Canon TS700 series 1.040",
                f"{snmp_monitor.HR_PRINTER_STATUS}.1": 3,
                f"{snmp_monitor.HR_DEVICE_STATUS}.1": 2,
                f"{snmp_monitor.PRT_CONSOLE_DISPLAY_TEXT}.1.1": b"Ready to print.",
                f"{snmp_monitor.PRT_GENERAL_SERIAL_NUMBER}.1": b"AELV22855",
                f"{snmp_monitor.HR_PRINTER_ERRORS}.1": b"\x00\x00",
            }
            if oid not in values:
                return None
            tag = 0x04 if isinstance(values[oid], bytes) else 0x02
            return snmp_monitor.VarBind(oid, tag, values[oid])

        def walk(self, oid, max_rows=80):
            return []

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.80", version="2c")

    assert result["reachable"] is True
    assert result["name"] == "Canon TS700 series 1.040\nAELV22855"
    assert result["identity_oid"] == f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1"
    assert result["printer_status"] == 3
    assert result["device_status"] == 2
    assert result["status_message"] == "Ready to print."
    assert result["snmp_version_used"] == "2c"


def test_poll_printer_falls_back_to_snmp_v1_when_v2c_does_not_answer(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        def __init__(self, host, community, port, timeout, version):
            self.version = 0 if version == "1" else 1
            self.protocol = version

        def get(self, oid):
            if self.protocol == "2c":
                raise snmp_monitor.SnmpError("timeout")
            if oid == f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1":
                return snmp_monitor.VarBind(oid, 0x04, b"Canon TS700")
            return None

        def walk(self, oid, max_rows=80):
            return []

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.81", version="2c")

    assert result["reachable"] is True
    assert result["snmp_version_used"] == "1"
    assert result["name"] == "Canon TS700"


def test_poll_printer_remains_available_when_optional_mib_tables_time_out(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        def __init__(self, host, community, port, timeout, version):
            self.version = 1

        def get(self, oid):
            if oid == f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1":
                return snmp_monitor.VarBind(oid, 0x04, b"Printer model")
            return None

        def walk(self, oid, max_rows=80):
            raise snmp_monitor.SnmpError("unsupported MIB table")

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.82", version="2c")

    assert result["reachable"] is True
    assert result["name"] == "Printer model"
    assert result["supplies"] == []
    assert result["alerts"] == []



def test_waste_toner_supply_is_not_displayed_even_if_mib_has_a_level():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            rows = {
                snmp_monitor.PRT_SUPPLY_TYPE: [snmp_monitor.VarBind(oid + ".1", 0x02, 4)],
                snmp_monitor.PRT_SUPPLY_DESCRIPTION: [snmp_monitor.VarBind(oid + ".1", 0x04, b"Waste Toner Box")],
                snmp_monitor.PRT_SUPPLY_CAPACITY: [snmp_monitor.VarBind(oid + ".1", 0x02, 100)],
                snmp_monitor.PRT_SUPPLY_LEVEL: [snmp_monitor.VarBind(oid + ".1", 0x02, 90)],
            }
            return rows.get(oid, [])

    result = snmp_monitor._poll_supply_data(FakeClient())
    assert result == []


def test_toner_supply_keeps_original_cartridge_model_description():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            rows = {
                snmp_monitor.PRT_SUPPLY_TYPE: [snmp_monitor.VarBind(oid + ".1", 0x02, 3)],
                snmp_monitor.PRT_SUPPLY_DESCRIPTION: [snmp_monitor.VarBind(oid + ".1", 0x04, b"TK-3190")],
                snmp_monitor.PRT_SUPPLY_CAPACITY: [snmp_monitor.VarBind(oid + ".1", 0x02, 100)],
                snmp_monitor.PRT_SUPPLY_LEVEL: [snmp_monitor.VarBind(oid + ".1", 0x02, 76)],
            }
            return rows.get(oid, [])

    result = snmp_monitor._poll_supply_data(FakeClient())
    assert len(result) == 1
    assert result[0]["name"] == "Тонер"
    assert result[0]["kind"] == "toner"
    assert result[0]["model"] == "TK-3190"
    assert result[0]["percent"] == 76


def test_waste_toner_box_unknown_level_is_not_reported_as_empty():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            rows = {
                snmp_monitor.PRT_SUPPLY_TYPE: [snmp_monitor.VarBind(oid + ".1", 0x02, 4)],
                snmp_monitor.PRT_SUPPLY_DESCRIPTION: [snmp_monitor.VarBind(oid + ".1", 0x04, b"Waste Toner Box")],
                snmp_monitor.PRT_SUPPLY_CAPACITY: [snmp_monitor.VarBind(oid + ".1", 0x02, 100)],
                snmp_monitor.PRT_SUPPLY_LEVEL: [snmp_monitor.VarBind(oid + ".1", 0x02, -1)],
            }
            return rows.get(oid, [])

    result = snmp_monitor._poll_supply_data(FakeClient())
    assert result == []


def test_waste_toner_label_without_actionable_alert_is_ignored():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            if oid == snmp_monitor.PRT_ALERT_DESCRIPTION:
                return [snmp_monitor.VarBind(oid + ".1", 0x04, b"Waste Toner Box")]
            if oid == snmp_monitor.PRT_ALERT_SEVERITY:
                return [snmp_monitor.VarBind(oid + ".1", 0x02, 1)]
            return []

    assert snmp_monitor._poll_alerts(FakeClient()) == []


def test_waste_toner_device_warning_remains_visible_as_real_alert():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            if oid == snmp_monitor.PRT_ALERT_DESCRIPTION:
                return [snmp_monitor.VarBind(oid + ".1", 0x04, b"Waste Toner Box near full")]
            if oid == snmp_monitor.PRT_ALERT_SEVERITY:
                return [snmp_monitor.VarBind(oid + ".1", 0x02, 4)]
            return []

    result = snmp_monitor._poll_alerts(FakeClient())
    assert result == [{
        "text": "Бункер отработанного тонера требует внимания",
        "severity": "warning",
    }]


def test_explicit_waste_toner_full_text_is_kept_even_with_generic_severity():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            if oid == snmp_monitor.PRT_ALERT_DESCRIPTION:
                return [snmp_monitor.VarBind(oid + ".1", 0x04, b"Waste Toner Box Full")]
            if oid == snmp_monitor.PRT_ALERT_SEVERITY:
                return [snmp_monitor.VarBind(oid + ".1", 0x02, 1)]
            return []

    result = snmp_monitor._poll_alerts(FakeClient())
    assert result == [{
        "text": "Бункер отработанного тонера заполнен — требуется замена",
        "severity": "critical",
    }]


def test_low_toner_error_bit_is_an_advisory_not_an_operational_fault(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        version = 1

        def __init__(self, host, community, port, timeout, version):
            self.version = 1

        def get(self, oid):
            values = {
                f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1": b"Kyocera ECOSYS PA6000x",
                f"{snmp_monitor.HR_PRINTER_STATUS}.1": 3,
                f"{snmp_monitor.HR_DEVICE_STATUS}.1": 3,
                f"{snmp_monitor.HR_PRINTER_ERRORS}.1": b"\\x20\\x00",
                f"{snmp_monitor.PRT_CONSOLE_DISPLAY_TEXT}.1.1": b"Ready to print",
            }
            if oid not in values:
                return None
            value = values[oid]
            return snmp_monitor.VarBind(oid, 0x04 if isinstance(value, bytes) else 0x02, value)

        def walk(self, oid, max_rows=80):
            return []

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.83", version="2c")
    assert result["state"] == "ready"
    assert result["errors"] == []
    assert result["advisories"] == [{"text": "Мало тонера", "severity": "info"}]


def test_paper_jam_remains_critical_when_low_toner_is_also_reported(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        def __init__(self, host, community, port, timeout, version):
            self.version = 1

        def get(self, oid):
            values = {
                f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1": b"Kyocera ECOSYS PA6000x",
                f"{snmp_monitor.HR_PRINTER_STATUS}.1": 3,
                f"{snmp_monitor.HR_DEVICE_STATUS}.1": 3,
                f"{snmp_monitor.HR_PRINTER_ERRORS}.1": b"\\x24\\x00",
                f"{snmp_monitor.PRT_CONSOLE_DISPLAY_TEXT}.1.1": b"Paper jam",
            }
            if oid not in values:
                return None
            value = values[oid]
            return snmp_monitor.VarBind(oid, 0x04 if isinstance(value, bytes) else 0x02, value)

        def walk(self, oid, max_rows=80):
            return []

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.84", version="2c")
    assert result["state"] == "error"
    assert any(item["text"] == "Замятие бумаги" for item in result["errors"])
    assert any(item["text"] == "Мало тонера" for item in result["advisories"])
