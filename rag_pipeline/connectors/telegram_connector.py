"""
connectors/telegram_connector.py
Handles: Telegram chat export (.json)

Telegram Desktop exports a JSON file with this structure:
{
  "name": "Chat Name",
  "type": "personal_chat" | "private_group" | "private_supergroup" | "public_supergroup" | "public_channel",
  "messages": [
    {
      "id": int,
      "type": "message" | "service",
      "date": "ISO-8601",
      "from": str,
      "from_id": str,
      "text": str | list[str|dict],   # can be a rich list with entities
      ...
    }
  ]
}

Yields one envelope per message.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Union

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)


def _flatten_text(text: Union[str, List[Any]]) -> str:
    """
    Telegram text fields can be a plain string or a list of mixed
    string/dict entities. Flatten to plain text.
    """
    if isinstance(text, str):
        return text
    parts: List[str] = []
    for item in text:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            # {"type": "bold", "text": "..."} etc.
            parts.append(item.get("text", ""))
    return "".join(parts)


def extract_telegram(file_path: str) -> Generator[Dict[str, Any], None, None]:
    path = Path(file_path)
    source_id = make_source_id(file_path)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    chat_name = data.get("name", path.stem)
    chat_type = data.get("type", "unknown")
    messages = data.get("messages", [])
    total = len(messages)

    participants = sorted(
        {m.get("from", "") for m in messages if m.get("from")}
    )

    for msg in messages:
        if msg.get("type") != "message":
            continue  # skip service events (pinned, joined, etc.)

        raw_text = msg.get("text", "")
        text = _flatten_text(raw_text).strip()
        if not text:
            continue

        sender = msg.get("from", "Unknown")
        msg_id = msg.get("id", 0)
        date_str = msg.get("date", "")

        # Forwarded-from info
        fwd_from = msg.get("forwarded_from", None)
        reply_to = msg.get("reply_to_message_id", None)

        yield envelope(
            source_type="telegram",
            source_id=f"{source_id}_msg{msg_id}",
            raw_path=file_path,
            raw_metadata={
                "file_name": path.name,
                "message_id": msg_id,
                "chat_name": chat_name,
                "chat_type": chat_type,
                "sender": sender,
                "date": date_str,
                "forwarded_from": fwd_from,
                "reply_to_message_id": reply_to,
                "participants": participants,
                "total_messages": total,
                "full_text": text,
            },
            modality="text",
        )


SUPPORTED_EXTENSIONS = {".json"}


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    yield from extract_telegram(file_path)
