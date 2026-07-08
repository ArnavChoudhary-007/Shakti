"""
connectors/slack_connector.py
Handles: Slack export directory (.json files, channel-based)

Slack exports are structured as a directory:
  export_root/
    channels.json          # list of all channels
    users.json             # user ID → display name mapping
    <channel-name>/
      YYYY-MM-DD.json      # list of message objects per day

Each day file contains messages:
[
  {
    "type": "message",
    "user": "UXXXXXXX",
    "text": "Hello world <@UXXXXXXX>",
    "ts": "1609459200.000100",
    "thread_ts": "...",       # if threaded
    "reply_count": N,
    "replies": [...]
  }
]

User mention tokens (<@UXXXX>) are resolved to display names.
Yields one envelope per message.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)


def _load_user_map(export_root: Path) -> Dict[str, str]:
    """Build user_id → display_name map from users.json."""
    users_file = export_root / "users.json"
    if not users_file.exists():
        return {}
    with open(users_file, "r", encoding="utf-8") as f:
        users = json.load(f)
    return {
        u["id"]: (
            u.get("profile", {}).get("display_name")
            or u.get("real_name")
            or u.get("name", u["id"])
        )
        for u in users
        if "id" in u
    }


def _resolve_mentions(text: str, user_map: Dict[str, str]) -> str:
    """Replace <@UXXXXXXX> tokens with @display_name."""
    def _replace(m: re.Match) -> str:
        uid = m.group(1)
        return f"@{user_map.get(uid, uid)}"
    return re.sub(r"<@([A-Z0-9]+)>", _replace, text)


def _load_channels(export_root: Path) -> List[Dict[str, Any]]:
    channels_file = export_root / "channels.json"
    if not channels_file.exists():
        return []
    with open(channels_file, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_slack_directory(export_root: str) -> Generator[Dict[str, Any], None, None]:
    """
    Walk a Slack export directory and yield one envelope per message.
    export_root can be the top-level export directory.
    """
    root = Path(export_root)
    source_id = make_source_id(export_root)
    user_map = _load_user_map(root)
    channels_meta = {c["name"]: c for c in _load_channels(root)}

    # Discover channel directories (subdirs with at least one .json file)
    channel_dirs = [
        d for d in root.iterdir()
        if d.is_dir() and any(d.glob("*.json"))
    ]

    for channel_dir in sorted(channel_dirs):
        channel_name = channel_dir.name
        channel_info = channels_meta.get(channel_name, {})
        channel_topic = channel_info.get("topic", {}).get("value", "")
        channel_purpose = channel_info.get("purpose", {}).get("value", "")

        day_files = sorted(channel_dir.glob("*.json"))
        for day_file in day_files:
            with open(day_file, "r", encoding="utf-8") as f:
                try:
                    day_messages = json.load(f)
                except json.JSONDecodeError:
                    logger.warning("Could not parse %s", day_file)
                    continue

            for msg in day_messages:
                if msg.get("type") != "message":
                    continue
                if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                    continue

                text = _resolve_mentions(msg.get("text", ""), user_map).strip()
                if not text:
                    continue

                user_id = msg.get("user", "")
                sender = user_map.get(user_id, user_id) if user_id else "Unknown"
                ts = msg.get("ts", "")
                thread_ts = msg.get("thread_ts", None)
                reply_count = msg.get("reply_count", 0)

                yield envelope(
                    source_type="slack",
                    source_id=f"{source_id}_{channel_name}_{ts}",
                    raw_path=str(day_file),
                    raw_metadata={
                        "file_name": day_file.name,
                        "export_root": str(root),
                        "channel_name": channel_name,
                        "channel_topic": channel_topic,
                        "channel_purpose": channel_purpose,
                        "sender": sender,
                        "user_id": user_id,
                        "timestamp": ts,
                        "thread_ts": thread_ts,
                        "reply_count": reply_count,
                        "full_text": text,
                    },
                    modality="text",
                )


def extract_slack_file(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Handle a single Slack day-file (e.g., 2024-01-01.json) directly,
    when the user provides a single file instead of the full export dir.
    """
    path = Path(file_path)
    source_id = make_source_id(file_path)
    channel_name = path.parent.name  # best guess

    with open(file_path, "r", encoding="utf-8") as f:
        messages = json.load(f)

    for msg in messages:
        if msg.get("type") != "message":
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue

        sender = msg.get("user", "Unknown")
        ts = msg.get("ts", "")

        yield envelope(
            source_type="slack",
            source_id=f"{source_id}_{ts}",
            raw_path=file_path,
            raw_metadata={
                "file_name": path.name,
                "channel_name": channel_name,
                "sender": sender,
                "timestamp": ts,
                "full_text": text,
            },
            modality="text",
        )


SUPPORTED_EXTENSIONS = {".json"}


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Auto-detect: if file_path is a directory → full Slack export walk.
    If it's a .json file → single day-file parse.
    """
    p = Path(file_path)
    if p.is_dir():
        yield from extract_slack_directory(file_path)
    else:
        yield from extract_slack_file(file_path)
