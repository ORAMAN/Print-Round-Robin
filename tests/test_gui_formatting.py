from print_round_robin.gui import _format_supply_line


def test_toner_card_line_includes_cartridge_model():
    assert _format_supply_line({
        "name": "Тонер",
        "kind": "toner",
        "model": "TK-3190",
        "percent": 76,
        "percent_semantics": "remaining",
    }) == "Тонер (TK-3190): 76%"


def test_waste_toner_line_uses_fill_status_text():
    assert _format_supply_line({
        "name": "Бункер отработанного тонера",
        "kind": "waste_toner",
        "status_text": "Заполнение не сообщается",
        "percent": None,
    }) == "Бункер отработанного тонера: Заполнение не сообщается"
