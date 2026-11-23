from print_round_robin.theme import STATUS_PALETTES, THEME_PALETTES


def test_theme_base_colors_match_face_search_server():
    dark = THEME_PALETTES["dark"]
    assert dark["app"] == "#151515"
    assert dark["panel"] == "#242424"
    assert dark["border"] == "#383838"
    assert dark["text"] == "#eeeeee"
    assert dark["accent"] == "#167ac6"

    light = THEME_PALETTES["light"]
    assert light["app"] == "#f4f5f7"
    assert light["panel"] == "#ffffff"
    assert light["border"] == "#c8cfd8"
    assert light["text"] == "#202124"
    assert light["accent"] == "#1479c9"


def test_theme_status_palettes_have_all_dashboard_states():
    expected = {"ready", "printing", "transfer", "warning", "error", "offline", "warmup", "unknown"}
    assert set(STATUS_PALETTES["dark"]) == expected
    assert set(STATUS_PALETTES["light"]) == expected
