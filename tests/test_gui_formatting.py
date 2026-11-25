from types import SimpleNamespace

from print_round_robin.gui import (
    PrintRoundRobinApp,
    _build_printer_meter_items,
    _format_supply_line,
    _format_tray_line,
    _level_color,
    _packed_sibling,
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
    assert calls[0][5] == ""
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


def test_tray_card_line_hides_unavailable_or_unknown_values():
    for current, maximum in ((-3, 300), (-2, 300), (180, -2), (None, 300), (180, None)):
        assert _format_tray_line({
            "name": "Ручная подача",
            "current": current,
            "max_capacity": maximum,
            "unit": "листов",
            "capacity_unit": 8,
            "percent": None,
        }) == ""


def test_color_scale_distinguishes_normal_low_and_critical_levels():
    assert _level_color(76) == "#26965b"
    assert _level_color(35) == "#c79a20"
    assert _level_color(15) == "#df792b"
    assert _level_color(4) == "#d64c4c"


def test_meter_data_contains_only_real_numeric_toner_and_tray_readings():
    items = _build_printer_meter_items(
        [
            {"name": "Тонер", "kind": "toner", "model": "TK-3190", "percent": 76},
            {"name": "Тонер", "kind": "toner", "model": "TK-3190", "percent": None},
            {"name": "Бункер отработанного тонера", "kind": "waste_toner", "percent": 90},
        ],
        [
            {
                "name": "Кассета 2", "media_name": "A4", "current": 180,
                "max_capacity": 500, "capacity_unit": 8, "unit": "листов", "percent": 36,
            },
            {
                "name": "Ручная подача", "current": -3, "max_capacity": 300,
                "capacity_unit": 8, "unit": "листов", "percent": None,
            },
        ],
    )

    assert items == [
        {"label": "Тонер (TK-3190)", "value": "76%", "percent": 76, "kind": "toner"},
        {
            "label": "Кассета 2 · A4", "value": "180/500 листов · 36%",
            "percent": 36, "kind": "tray",
        },
    ]


def test_percent_only_tray_reading_is_supported_when_mib_unit_is_percent():
    assert _build_printer_meter_items([], [{
        "name": "Лоток", "current": 28, "max_capacity": -1,
        "capacity_unit": 19, "unit": "%", "percent": 28,
    }]) == [{
        "label": "Лоток", "value": "28%", "percent": 28, "kind": "tray",
    }]



def test_toner_label_uses_meter_frame_only_when_it_is_packed():
    class FakeWidget:
        def __init__(self, manager):
            self.manager = manager

        def winfo_manager(self):
            return self.manager

    meter_frame = FakeWidget("")

    footer = FakeWidget("pack")
    assert _packed_sibling(
        {"meter_frame": meter_frame, "footer": footer},
        "meter_frame", "footer",
    ) is footer

    meter_frame.manager = "pack"
    assert _packed_sibling(
        {"meter_frame": meter_frame, "footer": footer},
        "meter_frame", "footer",
    ) is meter_frame


def test_printer_update_does_not_abort_other_cards_after_one_render_error():
    app = object.__new__(PrintRoundRobinApp)
    app.printer_cards = {"192.0.2.1": {}, "192.0.2.2": {}}
    app.theme_manager = SimpleNamespace(
        status={"unknown": {"bg": "#fff", "text": "#000"}},
    )
    app.logger = SimpleNamespace(exception=lambda *args, **kwargs: None)
    rendered = []

    def render(card, row):
        if row["host"] == "192.0.2.1":
            raise RuntimeError("broken meter widget")
        rendered.append(row["host"])

    app._render_printer_card = render
    app._paint_printer_card = lambda *args: None
    app._update_printer_meters = lambda *args: None

    app._update_printer_status([
        {"host": "192.0.2.1"},
        {"host": "192.0.2.2"},
    ])

    assert rendered == ["192.0.2.2"]



def test_event_pump_continues_after_ui_callback_exception_and_reschedules():
    import queue

    app = object.__new__(PrintRoundRobinApp)
    app.events = queue.Queue()
    app.events.put({"type": "printers", "printers": []})
    app.events.put({"type": "engine", "running": True})
    app._closing = False
    app.logger = SimpleNamespace(exception=lambda *args, **kwargs: None)
    scheduled = []
    processed = []
    app.root = SimpleNamespace(after=lambda delay, callback: scheduled.append((delay, callback)))

    def broken_printer_update(_printers):
        raise RuntimeError("simulated Tk widget failure")

    app._update_printer_status = broken_printer_update
    app._set_engine_state = lambda running: processed.append(running)

    app._poll_events()

    assert processed == [True]
    assert app.events.unfinished_tasks == 0
    assert len(scheduled) == 1
    assert scheduled[0][0] == 150
    assert callable(scheduled[0][1])
