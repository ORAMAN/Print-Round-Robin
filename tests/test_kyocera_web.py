from print_round_robin import kyocera_web


def test_command_center_rx_extracts_toner_and_ready_status():
    source = """
    <html><body>
      <table>
        <tr><td>Device Status</td><td>Ready to print</td></tr>
        <tr><td>Toner</td><td><img alt="Toner level 78%"></td></tr>
      </table>
    </body></html>
    """
    visible = kyocera_web._visible_text(source)
    assert kyocera_web._toner_percent(source, visible) == 78
    state, message, errors, notices = kyocera_web._classify_status(visible)
    assert state == "ready"
    assert message
    assert errors == []
    assert notices == []


def test_command_center_rx_detects_paper_jam():
    state, message, errors, notices = kyocera_web._classify_status(
        "Device Status Paper jam. Open rear cover."
    )
    assert state == "error"
    assert message == "Замятие бумаги"
    assert errors == [{"text": "Замятие бумаги", "severity": "critical"}]
    assert notices == []


def test_web_poll_returns_status_even_when_wsd_is_disabled(monkeypatch):
    monkeypatch.setattr(kyocera_web, "_read_home_page", lambda host, timeout: (
        "http://192.0.2.1/", "<html><body>Device Status: Ready to print. Toner 65%</body></html>"
    ))
    monkeypatch.setattr(kyocera_web.socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("closed")))

    result = kyocera_web.poll_kyocera_web("192.0.2.1")

    assert result["web_available"] is True
    assert result["wsd_available"] is False
    assert result["supplies"] == [
        {"name": "Тонер", "kind": "toner", "percent": 65, "source": "Command Center RX"}
    ]
    assert result["state"] == "ready"
    assert result["notices"] == []


def test_web_poll_reports_unavailable_without_failing():
    # A non-routable test address returns cleanly when both services are absent.
    result = kyocera_web.poll_kyocera_web("192.0.2.254", timeout=0.1)
    assert result["web_available"] is False
    assert result["wsd_available"] is False
    assert result["supplies"] == []



def test_command_center_does_not_show_waste_toner_label_without_status():
    source = "<html><body>Device Status: Ready to print. Waste Toner Box</body></html>"
    monkeypatch_result = kyocera_web._visible_text(source)
    state, message, errors, notices = kyocera_web._classify_status(monkeypatch_result)
    assert state == "ready"
    assert errors == []
    assert notices == []

    # A bare component label is not an actionable supply reading.
    from unittest.mock import patch
    with patch.object(kyocera_web, "_read_home_page", return_value=("http://192.0.2.1/", source)), \
         patch.object(kyocera_web.socket, "create_connection", side_effect=OSError("closed")):
        result = kyocera_web.poll_kyocera_web("192.0.2.1")
    assert result["supplies"] == []
    assert result["state"] == "ready"
    assert result["errors"] == []
    assert result["notices"] == []


def test_command_center_detects_full_waste_toner_box():
    state, message, errors, notices = kyocera_web._classify_status(
        "Device Status: Waste Toner Box Full. Replace Waste Toner Box."
    )
    assert state == "error"
    assert errors[0]["text"] == "Бункер отработанного тонера заполнен — требуется замена"



def test_low_toner_and_low_paper_do_not_override_printing_state():
    state, message, errors, notices = kyocera_web._classify_status(
        "Device Status: Printing. Low toner. Paper low."
    )

    assert state == "printing"
    assert message == "Печатает"
    assert errors == []
    assert notices == [
        {"text": "Мало тонера", "severity": "info"},
        {"text": "Мало бумаги", "severity": "info"},
    ]


def test_low_toner_and_low_paper_do_not_override_ready_state():
    state, message, errors, notices = kyocera_web._classify_status(
        "Device Status: Ready to print. Low toner. Low paper."
    )

    assert state == "ready"
    assert message == "Готов"
    assert errors == []
    assert [item["text"] for item in notices] == ["Мало тонера", "Мало бумаги"]
