import pytest

from print_round_robin.config import AppConfig, ConfigStore, PrinterConfig


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
