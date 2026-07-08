"""
connectors/teams_connector.py
Handles: Microsoft Teams export (.json)

Teams exports can come in multiple formats depending on the tool used
to extract them (Microsoft's eDiscovery export, TeamExplorer, etc.).
We handle the two most common:

Format A — Microsoft compliance export:
{
  "TeamName": "...",
  "ChannelName": "...",
  "Messages": [
    {
      "Id": str,
      "CreatedDateTime": ISO-8601,
      "From": {"User": {"DisplayName": str}},
      "Body": {"Content": str, "ContentType": "html"|"text"},
      "Attachments": [...],
      "Replies": [...]
    }
  ]
}

Format B — Flat array (simpler exports):
[
  {
    "id": str,
    "createdDateTime": ISO-8601,
    "from": {"user": {"displayName": str}},
    "body": {"content": str, "contentType": str},
    ...
  }
]

We auto-detect the format.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """Strip HTML tags for Teams messages with contentType=html."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(body: Optional[Dict[str, Any]]) -> str:
    if not body:
        return ""
    content = body.get("Content") or body.get("content") or ""
    content_type = (body.get("ContentType") or body.get("contentType") or "text").lower()
    if content_type == "html":
        return _strip_html(content)
    return content.strip()


def _get_sender(msg: Dict[str, Any]) -> str:
    """Extract sender display name, handling both format variants."""
    # Format A
    frm = msg.get("From") or msg.get("from") or {}
    user = frm.get("User") or frm.get("user") or {}
    return user.get("DisplayName") or user.get("displayName") or "Unknown"


def _get_datetime(msg: Dict[str, Any]) -> str:
    return (
        msg.get("CreatedDateTime")
        or msg.get("createdDateTime")
        or msg.get("LastModifiedDateTime")
        or ""
    )


def _get_id(msg: Dict[str, Any]) -> str:
    return str(msg.get("Id") or msg.get("id") or "")


def _yield_message(
    msg: Dict[str, Any],
    source_id: str,
    file_path: str,
    channel_name: str,
    team_name: str,
    total: int,
    idx: int,
) -> Optional[Dict[str, Any]]:
    text = _extract_body(msg.get("Body") or msg.get("body"))
    if not text:
        return None

    return envelope(
        source_type="teams",
        source_id=f"{source_id}_msg{idx}",
        raw_path=file_path,
        raw_metadata={
            "file_name": Path(file_path).name,
            "message_id": _get_id(msg),
            "team_name": team_name,
            "channel_name": channel_name,
            "sender": _get_sender(msg),
            "datetime": _get_datetime(msg),
            "total_messages": total,
            "full_text": text,
        },
        modality="text",
    )


def extract_teams(file_path: str) -> Generator[Dict[str, Any], None, None]:
    path = Path(file_path)
    source_id = make_source_id(file_path)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    # Detect format
    if isinstance(data, dict) and "Messages" in data:
        # Format A — Microsoft compliance export
        team_name = data.get("TeamName", path.stem)
        channel_name = data.get("ChannelName", "")
        messages = data.get("Messages", [])
        total = len(messages)
        for idx, msg in enumerate(messages):
            env = _yield_message(msg, source_id, file_path, channel_name, team_name, total, idx)
            if env:
                yield env
            # Also yield replies (threaded)
            for ridx, reply in enumerate(msg.get("Replies", [])):
                renv = _yield_message(
                    reply, source_id, file_path, channel_name, team_name, total,
                    idx * 1000 + ridx,
                )
                if renv:
                    yield renv

    elif isinstance(data, list):
        # Format B — flat array
        team_name = path.stem
        channel_name = path.parent.name
        total = len(data)
        for idx, msg in enumerate(data):
            env = _yield_message(msg, source_id, file_path, channel_name, team_name, total, idx)
            if env:
                yield env

    else:
        logger.warning("Unrecognized Teams JSON format in %s", path.name)


SUPPORTED_EXTENSIONS = {".json"}


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    yield from extract_teams(file_path)
