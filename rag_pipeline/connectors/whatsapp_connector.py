"""
connectors/whatsapp_connector.py
Handles: WhatsApp chat export (.txt)

WhatsApp exports two main format variants:
  Android 12-hr: "12/31/23, 2:35 PM - Sender: message"
  Android 24-hr: "31/12/2023, 14:35 - Sender: message"
  iOS:           "[31/12/2023, 14:35:00] Sender: message"

Each message → one envelope (chunker will group them into windows).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)

# ── Regex patterns for known WhatsApp export formats ────────
# Covers Android (both 12h/24h) and iOS bracket format

_ANDROID_PATTERN = re.compile(
    r"^(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),?\s+"  # date
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)"  # time
    r"\s[-\u2013]\s"                                   # separator (- or –)
    r"([^:]+):\s"                                      # sender:
    r"(.+)$",                                          # message body
    re.DOTALL,
)

_IOS_PATTERN = re.compile(
    r"^\[(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),\s+"   # [date,
    r"(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?)"  # time]
    r"\]\s"
    r"([^:]+):\s"                                       # sender:
    r"(.+)$",                                           # message body
    re.DOTALL,
)

_SYSTEM_KEYWORDS = {
    "Messages and calls are end-to-end encrypted",
    "changed the subject",
    "added ",
    "removed ",
    "left",
    "joined using this group's invite link",
    "created group",
}


def _is_system_message(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in _SYSTEM_KEYWORDS)


def _parse_message_line(line: str) -> Optional[Tuple[str, str, str]]:
    """
    Returns (datetime_str, sender, text) or None if line doesn't match.
    """
    for pattern in (_ANDROID_PATTERN, _IOS_PATTERN):
        m = pattern.match(line.strip())
        if m:
            date, time, sender, text = m.group(1), m.group(2), m.group(3), m.group(4)
            return f"{date} {time}", sender.strip(), text.strip()
    return None


def extract_whatsapp(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Parse a WhatsApp .txt export and yield one envelope per message.
    Multi-line messages are folded into the preceding message.
    System notifications are skipped.
    """
    path = Path(file_path)
    source_id = make_source_id(file_path)

    # Detect encoding
    encoding = "utf-8"
    try:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read(65536)
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"
    except ImportError:
        pass

    with open(file_path, "r", encoding=encoding, errors="replace") as f:
        lines = f.readlines()

    # Multi-line fold: continuation lines (not matching a timestamp) are
    # appended to the previous message.
    messages: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None

    for line in lines:
        line = line.rstrip("\n")
        parsed = _parse_message_line(line)
        if parsed:
            if current:
                messages.append(current)
            dt, sender, text = parsed
            current = {"datetime": dt, "sender": sender, "text": text}
        elif current:
            # Continuation of previous message
            current["text"] += "\n" + line

    if current:
        messages.append(current)

    # Collect unique participants
    participants = sorted({m["sender"] for m in messages})

    for idx, msg in enumerate(messages):
        text = msg["text"].strip()
        if not text or _is_system_message(text):
            continue

        yield envelope(
            source_type="whatsapp",
            source_id=f"{source_id}_msg{idx}",
            raw_path=file_path,
            raw_metadata={
                "file_name": path.name,
                "message_index": idx,
                "datetime": msg["datetime"],
                "sender": msg["sender"],
                "participants": participants,
                "total_messages": len(messages),
                "full_text": text,
            },
            modality="text",
        )


SUPPORTED_EXTENSIONS = {".txt"}


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    yield from extract_whatsapp(file_path)
