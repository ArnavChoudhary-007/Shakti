"""
connectors/__init__.py
Registry of all connectors. Each entry maps a file extension (or source type)
to the connector module's extract() function.
"""
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Optional


# ── Lazy imports (so missing optional deps don't break import) ──
def _get_connector(source_type: str) -> Any:
    if source_type in ("pdf", "docx", "pptx"):
        from . import pdf_connector
        return pdf_connector
    elif source_type in ("excel", "csv"):
        from . import excel_connector
        return excel_connector
    elif source_type in ("email", "mbox", "eml", "pst"):
        from . import email_connector
        return email_connector
    elif source_type == "whatsapp":
        from . import whatsapp_connector
        return whatsapp_connector
    elif source_type == "telegram":
        from . import telegram_connector
        return telegram_connector
    elif source_type == "slack":
        from . import slack_connector
        return slack_connector
    elif source_type == "teams":
        from . import teams_connector
        return teams_connector
    elif source_type == "invoice":
        from . import invoice_connector
        return invoice_connector
    elif source_type == "audio":
        from . import audio_connector
        return audio_connector
    elif source_type == "json":
        from . import json_connector
        return json_connector
    raise ValueError(f"Unknown source type: {source_type!r}")


# Extension → source_type mapping
EXTENSION_MAP: Dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".pptx": "pptx",
    ".ppt": "pptx",
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "csv",
    ".mbox": "mbox",
    ".eml": "eml",
    ".pst": "pst",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".opus": "audio",
    ".aac": "audio",
    ".mp4": "audio",
    ".png": "invoice",
    ".jpg": "invoice",
    ".jpeg": "invoice",
    ".tiff": "invoice",
    ".tif": "invoice",
    ".bmp": "invoice",
    ".json": "json",
    ".txt": "whatsapp",
}


def _sniff_json_source_type(file_path: str) -> str:
    """
    Telegram, Slack, and Teams exports are all '.json' — the extension alone
    can't tell them apart, so peek at the top-level shape. Falls back to
    'json' (generic connector) if parsing fails or nothing matches, which
    preserves prior behavior for genuinely generic JSON files.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = _json.load(f)
    except Exception:
        return "json"

    if isinstance(data, dict):
        if isinstance(data.get("messages"), list):
            return "telegram"
        if isinstance(data.get("Messages"), list):
            return "teams"
        return "json"

    if isinstance(data, list) and data and isinstance(data[0], dict):
        first = data[0]
        if "ts" in first and "text" in first:
            return "slack"
        if ("Body" in first or "body" in first) and ("From" in first or "from" in first):
            return "teams"

    return "json"


def extract_file(
    file_path: str,
    source_type: Optional[str] = None,
    **kwargs: Any,
) -> Generator[Dict[str, Any], None, None]:
    """
    Auto-route a file to the correct connector.
    source_type can be specified explicitly; otherwise inferred from extension.
    """
    p = Path(file_path)
    ext = p.suffix.lower()

    if source_type is None:
        source_type = EXTENSION_MAP.get(ext)
        if source_type is None:
            raise ValueError(
                f"Cannot infer source_type for extension {ext!r}. "
                f"Specify source_type explicitly or add to EXTENSION_MAP."
            )
        if source_type == "json":
            source_type = _sniff_json_source_type(file_path)

    connector = _get_connector(source_type)
    yield from connector.extract(file_path, **kwargs)
