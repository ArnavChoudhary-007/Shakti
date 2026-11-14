"""
connectors/text_connector.py — plain text and Markdown files.

One envelope per file; the chunker splits it like any other prose document.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator

from .base import envelope, make_source_id


def read_text(file_path: str) -> str:
    raw = Path(file_path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        import chardet
        detected = chardet.detect(raw[:100_000]).get("encoding")
    except ImportError:
        detected = None
    return raw.decode(detected or "latin-1", errors="replace")


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    text = read_text(file_path).strip()
    if not text:
        return
    p = Path(file_path)
    yield envelope(
        source_type="text",
        source_id=make_source_id(file_path),
        raw_path=file_path,
        modality="text",
        raw_metadata={
            "file_name": p.name,
            "title": p.stem,
            "full_text": text,
        },
    )
