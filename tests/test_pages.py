from print_round_robin.pages import detect_page_count


def test_detects_pdf_page_objects(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(
        b"%PDF-1.7\n"
        b"1 0 obj << /Type /Pages /Count 2 >> endobj\n"
        b"2 0 obj << /Type /Page /Parent 1 0 R >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 1 0 R >> endobj\n"
    )
    assert detect_page_count(path) == 2


def test_pdf_with_compressed_object_stream_is_unknown(tmp_path):
    path = tmp_path / "compressed.pdf"
    path.write_bytes(b"%PDF-1.7\n/Type /ObjStm\n/Type /Page")
    assert detect_page_count(path) is None


def test_detects_postscript_declared_page_count(tmp_path):
    path = tmp_path / "sample.ps"
    path.write_bytes(b"%!PS-Adobe-3.0\n%%Pages: 7\n")
    assert detect_page_count(path) == 7


def test_detects_postscript_pages_at_end(tmp_path):
    path = tmp_path / "sample.ps"
    path.write_bytes(
        b"%!PS-Adobe-3.0\n%%Pages: (atend)\n"
        b"%%Page: 1 1\nshowpage\n%%Page: 2 2\nshowpage\n"
    )
    assert detect_page_count(path) == 2


def test_unknown_raw_payload_returns_none(tmp_path):
    path = tmp_path / "job.prn"
    path.write_bytes(b"\x1bE\x1b&l0H\x0c")
    assert detect_page_count(path) is None
