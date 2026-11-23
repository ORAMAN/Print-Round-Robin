import pytest

from print_round_robin.config import AppConfig, ConfigStore, PrinterConfig, reorder_printers


def test_default_config_has_ten_printers_and_is_valid():
    config = AppConfig()
    config.validate()
    assert len(config.printers) == 10
    assert config.printers[0].host == "192.168.20.121"
    assert config.printers[-1].host == "192.168.20.130"


def test_settings_round_trip(tmp_path):
    store = ConfigStore(tmp_path / "settings.json")
    config = AppConfig(queue_name="RR Test", printers=[PrinterConfig("P1", "192.0.2.10")])
    store.save(config)
    restored = store.load()
    assert restored.queue_name == "RR Test"
    assert restored.printers[0].host == "192.0.2.10"


def test_invalid_duplicate_printer_is_rejected():
    config = AppConfig(printers=[
        PrinterConfig("A", "192.0.2.10"),
        PrinterConfig("B", "192.0.2.10"),
    ])
    with pytest.raises(ValueError, match="повторно"):
        config.validate()


def test_invalid_listener_must_not_bind_external_interfaces():
    config = AppConfig(listener_host="0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        config.validate()


def test_invalid_config_file_is_backed_up(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ invalid json", encoding="utf-8")
    store = ConfigStore(path)
    with pytest.raises(ValueError):
        store.load()
    assert path.with_suffix(".json.broken").exists()


def test_selected_printers_move_up_together_and_keep_relative_order():
    printers = [
        PrinterConfig("A", "192.0.2.1"),
        PrinterConfig("B", "192.0.2.2"),
        PrinterConfig("C", "192.0.2.3"),
        PrinterConfig("D", "192.0.2.4"),
        PrinterConfig("E", "192.0.2.5"),
    ]

    reordered = reorder_printers(printers, {"192.0.2.2", "192.0.2.4"}, -1)

    assert [printer.name for printer in reordered] == ["B", "A", "D", "C", "E"]
    assert [printer.name for printer in printers] == ["A", "B", "C", "D", "E"]


def test_selected_printers_move_down_together_and_keep_relative_order():
    printers = [
        PrinterConfig("A", "192.0.2.1"),
        PrinterConfig("B", "192.0.2.2"),
        PrinterConfig("C", "192.0.2.3"),
        PrinterConfig("D", "192.0.2.4"),
        PrinterConfig("E", "192.0.2.5"),
    ]

    reordered = reorder_printers(printers, {"192.0.2.2", "192.0.2.4"}, 1)

    assert [printer.name for printer in reordered] == ["A", "C", "B", "E", "D"]


def test_selected_contiguous_printer_group_moves_as_a_block():
    printers = [
        PrinterConfig("A", "192.0.2.1"),
        PrinterConfig("B", "192.0.2.2"),
        PrinterConfig("C", "192.0.2.3"),
        PrinterConfig("D", "192.0.2.4"),
    ]

    reordered = reorder_printers(printers, {"192.0.2.2", "192.0.2.3"}, -1)

    assert [printer.name for printer in reordered] == ["B", "C", "A", "D"]


def test_reorder_rejects_invalid_direction():
    with pytest.raises(ValueError, match="Направление"):
        reorder_printers([PrinterConfig("A", "192.0.2.1")], {"192.0.2.1"}, 0)



def test_default_config_uses_face_search_dark_theme():
    config = AppConfig()
    config.validate()
    assert config.theme == "dark"
