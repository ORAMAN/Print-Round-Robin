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
    result = decode_error_bits(bytes((0b10000100, 0b00100000)))
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
