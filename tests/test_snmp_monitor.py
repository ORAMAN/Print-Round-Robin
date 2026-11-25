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
    assert [(item["text"], item["severity"]) for item in result] == [
        ("Мало бумаги", "info"),
        ("Мало тонера", "info"),
        ("Замятие бумаги", "critical"),
        ("Входной лоток пуст", "warning"),
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
    assert result["trays"] == []


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



def test_low_toner_and_low_paper_bits_do_not_override_printing_state(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        def __init__(self, host, community, port, timeout, version):
            self.version = 1

        def get(self, oid):
            values = {
                f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1": b"Kyocera ECOSYS PA6000x",
                f"{snmp_monitor.HR_PRINTER_STATUS}.1": 4,
                f"{snmp_monitor.HR_DEVICE_STATUS}.1": 3,
                f"{snmp_monitor.HR_PRINTER_ERRORS}.1": b"\xA0\x00",
            }
            if oid not in values:
                return None
            value = values[oid]
            tag = 0x04 if isinstance(value, bytes) else 0x02
            return snmp_monitor.VarBind(oid, tag, value)

        def walk(self, oid, max_rows=80):
            return []

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.83", version="2c")

    assert result["state"] == "printing"
    assert result["errors"] == []
    assert [item["text"] for item in result["notices"]] == [
        "Мало бумаги",
        "Мало тонера",
    ]


def test_toner_below_five_percent_is_informational_not_critical(monkeypatch):
    from print_round_robin import snmp_monitor

    class FakeClient:
        def __init__(self, host, community, port, timeout, version):
            self.version = 1

        def get(self, oid):
            values = {
                f"{snmp_monitor.HR_DEVICE_DESCRIPTION}.1": b"Kyocera ECOSYS PA6000x",
                f"{snmp_monitor.HR_PRINTER_STATUS}.1": 3,
                f"{snmp_monitor.HR_DEVICE_STATUS}.1": 2,
                f"{snmp_monitor.HR_PRINTER_ERRORS}.1": b"\x00\x00",
            }
            if oid not in values:
                return None
            value = values[oid]
            tag = 0x04 if isinstance(value, bytes) else 0x02
            return snmp_monitor.VarBind(oid, tag, value)

        def walk(self, oid, max_rows=80):
            rows = {
                snmp_monitor.PRT_SUPPLY_TYPE: [
                    snmp_monitor.VarBind(oid + ".1", 0x02, 3)
                ],
                snmp_monitor.PRT_SUPPLY_DESCRIPTION: [
                    snmp_monitor.VarBind(oid + ".1", 0x04, b"TK-3190")
                ],
                snmp_monitor.PRT_SUPPLY_CAPACITY: [
                    snmp_monitor.VarBind(oid + ".1", 0x02, 100)
                ],
                snmp_monitor.PRT_SUPPLY_LEVEL: [
                    snmp_monitor.VarBind(oid + ".1", 0x02, 4)
                ],
            }
            return rows.get(oid, [])

    monkeypatch.setattr(snmp_monitor, "SnmpClient", FakeClient)
    result = snmp_monitor.poll_printer("192.0.2.84", version="2c")

    assert result["state"] == "ready"
    assert result["errors"] == []
    assert result["supplies"][0]["percent"] == 4
    assert result["notices"] == [{
        "text": "Тонер почти закончился (4%)",
        "severity": "info",
    }]


def test_low_consumable_snmp_alerts_are_informational_even_if_agent_says_critical():
    from print_round_robin import snmp_monitor

    class FakeClient:
        def walk(self, oid, max_rows=80):
            if oid == snmp_monitor.PRT_ALERT_DESCRIPTION:
                return [
                    snmp_monitor.VarBind(oid + ".1", 0x04, b"Low toner"),
                    snmp_monitor.VarBind(oid + ".2", 0x04, b"Paper low"),
                ]
            if oid == snmp_monitor.PRT_ALERT_SEVERITY:
                return [
                    snmp_monitor.VarBind(oid + ".1", 0x02, 3),
                    snmp_monitor.VarBind(oid + ".2", 0x02, 3),
                ]
            return []

    assert snmp_monitor._poll_alerts(FakeClient()) == [
        {"text": "Мало тонера", "severity": "info"},
        {"text": "Мало бумаги", "severity": "info"},
    ]



def test_input_tray_table_joins_current_and_maximum_by_both_indexes():
    from print_round_robin import snmp_monitor

    rows = {
        snmp_monitor.PRT_INPUT_CAPACITY_UNIT: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CAPACITY_UNIT + ".1.1", 0x02, 8),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CAPACITY_UNIT + ".1.2", 0x02, 8),
        ],
        snmp_monitor.PRT_INPUT_MAX_CAPACITY: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MAX_CAPACITY + ".1.1", 0x02, 300),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MAX_CAPACITY + ".1.2", 0x02, 500),
        ],
        snmp_monitor.PRT_INPUT_CURRENT_LEVEL: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CURRENT_LEVEL + ".1.1", 0x02, -3),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CURRENT_LEVEL + ".1.2", 0x02, 180),
        ],
        snmp_monitor.PRT_INPUT_NAME: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_NAME + ".1.1", 0x04, b"MP Tray"),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_NAME + ".1.2", 0x04, "Кассета 2".encode("utf-8")),
        ],
        snmp_monitor.PRT_INPUT_MEDIA_NAME: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MEDIA_NAME + ".1.2", 0x04, b"A4"),
        ],
    }

    class FakeClient:
        def walk(self, oid, max_rows=80):
            return rows.get(oid, [])

    result = snmp_monitor._poll_input_trays(FakeClient())

    assert [item["input_index"] for item in result] == [1, 2]
    assert result[0] == {
        "device_index": 1,
        "input_index": 1,
        "name": "MP Tray",
        "media_name": "",
        "current": -3,
        "max_capacity": 300,
        "capacity_unit": 8,
        "unit": "листов",
        "percent": None,
    }
    assert result[1]["name"] == "Кассета 2"
    assert result[1]["media_name"] == "A4"
    assert result[1]["current"] == 180
    assert result[1]["max_capacity"] == 500
    assert result[1]["percent"] == 36


def test_input_tray_unknown_values_are_not_treated_as_negative_sheet_counts():
    from print_round_robin import snmp_monitor

    rows = {
        snmp_monitor.PRT_INPUT_CAPACITY_UNIT: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CAPACITY_UNIT + ".1.3", 0x02, 8),
        ],
        snmp_monitor.PRT_INPUT_MAX_CAPACITY: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MAX_CAPACITY + ".1.3", 0x02, -2),
        ],
        snmp_monitor.PRT_INPUT_CURRENT_LEVEL: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CURRENT_LEVEL + ".1.3", 0x02, -2),
        ],
        snmp_monitor.PRT_INPUT_NAME: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_NAME + ".1.3", 0x04, b"Tray 3"),
        ],
    }

    class FakeClient:
        def walk(self, oid, max_rows=80):
            return rows.get(oid, [])

    result = snmp_monitor._poll_input_trays(FakeClient())

    assert len(result) == 1
    assert result[0]["current"] == -2
    assert result[0]["max_capacity"] == -2
    assert result[0]["percent"] is None



def test_input_tray_status_oid_is_printer_mib_column_11():
    from print_round_robin import snmp_monitor

    assert snmp_monitor.PRT_INPUT_STATUS == "1.3.6.1.2.1.43.8.2.1.11"


def test_closed_or_unavailable_input_tray_is_not_reported_as_empty():
    from print_round_robin import snmp_monitor

    rows = {
        snmp_monitor.PRT_INPUT_CAPACITY_UNIT: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CAPACITY_UNIT + ".1.1", 0x02, 8),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CAPACITY_UNIT + ".1.2", 0x02, 8),
        ],
        snmp_monitor.PRT_INPUT_MAX_CAPACITY: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MAX_CAPACITY + ".1.1", 0x02, 300),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MAX_CAPACITY + ".1.2", 0x02, 500),
        ],
        snmp_monitor.PRT_INPUT_CURRENT_LEVEL: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CURRENT_LEVEL + ".1.1", 0x02, 0),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CURRENT_LEVEL + ".1.2", 0x02, 180),
        ],
        snmp_monitor.PRT_INPUT_STATUS: [
            # unavailableAndOnRequest(1): closed/not currently available
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_STATUS + ".1.1", 0x02, 1),
            # availableAndIdle(0): main cassette available
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_STATUS + ".1.2", 0x02, 0),
        ],
        snmp_monitor.PRT_INPUT_NAME: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_NAME + ".1.1", 0x04, b"MP Tray"),
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_NAME + ".1.2", 0x04, b"Cassette 2"),
        ],
    }

    class FakeClient:
        def walk(self, oid, max_rows=80):
            return rows.get(oid, [])

    result = snmp_monitor._poll_input_trays(FakeClient())

    assert len(result) == 1
    assert result[0]["input_index"] == 2
    assert result[0]["current"] == 180
    assert result[0]["percent"] == 36


def test_broken_input_tray_is_not_reported():
    from print_round_robin import snmp_monitor

    rows = {
        snmp_monitor.PRT_INPUT_CURRENT_LEVEL: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_CURRENT_LEVEL + ".1.1", 0x02, 0),
        ],
        snmp_monitor.PRT_INPUT_MAX_CAPACITY: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_MAX_CAPACITY + ".1.1", 0x02, 300),
        ],
        snmp_monitor.PRT_INPUT_STATUS: [
            snmp_monitor.VarBind(snmp_monitor.PRT_INPUT_STATUS + ".1.1", 0x02, 3),
        ],
    }

    class FakeClient:
        def walk(self, oid, max_rows=80):
            return rows.get(oid, [])

    assert snmp_monitor._poll_input_trays(FakeClient()) == []
