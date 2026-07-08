"""
connectors/base.py — Shared utilities and the ConnectorEnvelope type alias.
All connectors import from here.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict


def make_source_id(path: str) -> str:
    """Stable source ID = hex of the absolute path + mtime."""
    p = Path(path)
    mtime = str(p.stat().st_mtime) if p.exists() else ""
    raw = f"{os.path.abspath(path)}:{mtime}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def envelope(
    *,
    source_type: str,
    source_id: str,
    raw_path: str,
    raw_metadata: Dict[str, Any],
    modality: str,
) -> Dict[str, Any]:
    """
    Build a validated connector envelope dict.
    modality must be one of: text | table | audio | image
    """
    assert modality in ("text", "table", "audio", "image"), (
        f"Invalid modality: {modality!r}"
    )
    return {
        "source_type": source_type,
        "source_id": source_id,
        "raw_path": str(Path(raw_path).resolve()),
        "raw_metadata": raw_metadata,
        "modality": modality,
    }
