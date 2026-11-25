from types import SimpleNamespace

from print_round_robin.gui import (
    PrintRoundRobinApp,
    _format_supply_line,
    _format_tray_line,
    _partition_printer_messages,
)


def test_toner_card_line_includes_cartridge_model():
    assert _format_supply_line({
        "name": "Тонер",
        "kind": "toner",
        "model": "TK-3190",
        "percent": 76,
        "percent_semantics": "remaining",
    }) == "Тонер (TK-3190): 76%"


def test_waste_toner_supply_line_is_hidden_when_not_reported():
    assert _format_supply_line({
        "name": "Бункер отработанного тонера",
        "kind": "waste_toner",
        "status_text": "Заполнение не сообщается",
        "percent": None,
    }) == ""



def test_low_toner_and_low_paper_are_informational_even_in_legacy_warning_data():
    operational, notices = _partition_printer_messages([
        {"text": "Мало тонера (12%)", "severity": "warning"},
        {"text": "Мало бумаги", "severity": "warning"},
        {"text": "Замятие бумаги", "severity": "critical"},
    ])

    assert [item["text"] for item in operational] == ["Замятие бумаги"]
    assert [item["text"] for item in notices] == ["Мало тонера (12%)", "Мало бумаги"]


def test_explicit_info_messages_are_separated_and_deduplicated():
    operational, notices = _partition_printer_messages(
        [{"text": "Мало тонера", "severity": "info"}],
        [{"text": "Мало тонера", "severity": "info"}, {"text": "Мало бумаги", "severity": "info"}],
    )

    assert operational == []
    assert [item["text"] for item in notices] == ["Мало тонера", "Мало бумаги"]



def test_theme_refresh_passes_information_slot_to_printer_card_painter():
    app = object.__new__(PrintRoundRobinApp)
    app.theme_manager = SimpleNamespace(palette={"panel": "#ffffff"}, status={})
    app.printer_cards = {"192.0.2.1": object()}
    app._latest_printer_rows = {}
    app._legend_widgets = []
    calls = []
    app._paint_printer_card = lambda *args: calls.append(args)

    app._apply_dashboard_theme()

    assert len(calls) == 1
    assert len(calls[0]) == 7
    assert calls[0][4] == ""
    assert calls[0][5] == "Тонер: —"
    assert calls[0][6] == "SNMP: —"



def test_tray_card_line_shows_current_maximum_percent_and_media():
    assert _format_tray_line({
        "name": "Кассета 2",
        "input_index": 2,
        "current": 180,
        "max_capacity": 500,
        "unit": "листов",
        "capacity_unit": 8,
        "percent": 36,
        "media_name": "A4",
    }) == "Кассета 2: 180/500 листов (36%) • A4"


def test_tray_card_line_formats_printer_mib_sentinel_values():
    assert _format_tray_line({
        "name": "Ручная подача",
        "current": -3,
        "max_capacity": 300,
        "unit": "листов",
        "capacity_unit": 8,
        "percent": None,
    }) == "Ручная подача: не менее 1/300 листов"
