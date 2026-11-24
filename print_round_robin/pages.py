"""Conservative page-count detection for common print-stream formats.

The relay never modifies the RAW payload. Unsupported or ambiguous formats return
None rather than presenting a guessed page count as exact.
"""
from __future__ import annotations

import mmap
import re
from pathlib import Path


_PDF_PAGE = re.compile(rb"/Type\\s*/Page\\b")
_PDF_OBJECT_STREAM = re.compile(rb"/Type\\s*/ObjStm\\b")
_PS_PAGES = re.compile(rb"(?m)^%%Pages:\\s*(\\d+)\\s*$")
_PS_PAGES_AT_END = re.compile(rb"(?m)^%%Pages:\\s*\\(atend\\)\\s*$")
_PS_PAGE = re.compile(rb"(?m)^%%Page:")


def detect_page_count(path: str | Path) -> int | None:
    """Return a conservative page count for ordinary PDF/PostScript files."""
    try:
        with Path(path).open("rb") as stream:
            if Path(path).stat().st_size == 0:
                return None
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                if data[:5] == b"%PDF-":
                    # Page dictionaries inside compressed object streams cannot be
                    # counted safely without a full PDF object parser.
                    if _PDF_OBJECT_STREAM.search(data):
                        return None
                    count = len(_PDF_PAGE.findall(data))
                    return count if count > 0 else None

                if data[:4] == b"%!PS":
                    declared = _PS_PAGES.search(data)
                    if declared:
                        return int(declared.group(1))
                    if _PS_PAGES_AT_END.search(data):
                        count = len(_PS_PAGE.findall(data))
                        return count if count > 0 else None
    except (OSError, ValueError):
        return None
    return None
