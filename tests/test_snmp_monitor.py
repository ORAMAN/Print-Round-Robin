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
