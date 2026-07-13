"""
connectors/__init__.py
Registry of all connectors. Each entry maps a file extension (or source type)
to the connector module's extract() function.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Generator


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
}


def extract_file(
    file_path: str,
    source_type: Optional[str] = None,
    **kwargs: Any,
) -> Generator[Dict[str, Any], None, None]:
    """
    Auto-route a file to the correct connector.
    source_type can be specified explicitly; otherwise inferred from extension.
    """
    from typing import Optional  # noqa: F811
    p = Path(file_path)
    ext = p.suffix.lower()

    if source_type is None:
        source_type = EXTENSION_MAP.get(ext)
        if source_type is None:
            raise ValueError(
                f"Cannot infer source_type for extension {ext!r}. "
                f"Specify source_type explicitly or add to EXTENSION_MAP."
            )

    connector = _get_connector(source_type)
    yield from connector.extract(file_path, **kwargs)


from typing import Optional
