from print_round_robin.windows import write_client_installer


def test_client_installer_uses_unc_and_crlf(tmp_path):
    destination = tmp_path / "Connect_Kyocera.bat"

    write_client_installer(destination, "printserver", "KyoceraRoundRobin")

    raw = destination.read_bytes()
    text = raw.decode("utf-8-sig")
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert r"PRINTER_PATH=\\printserver\KyoceraRoundRobin" in text
    assert "Add-Printer -ConnectionName $path" in text
    assert "Start-Process" in text
