"""
connectors/json_connector.py

Generic JSON file connector.
Detects shape (Telegram / Teams / Slack) and routes accordingly.
Falls back to a generic readable-text extractor for unknown JSON shapes.

Fixes applied vs. previous version:
  1. extract() is now a plain function returning a generator (not a generator
     itself), so _load_and_parse() raises immediately at call time, not lazily
     during iteration.
  2. JSONL fallback is smarter: only attempts line-by-line parsing when the
     file actually looks line-delimited, skips individual bad lines with a
     warning rather than failing the whole file, and distinguishes the two
     failure modes clearly in error messages.
  3. _is_slack() tightened: requires 'user' and 'text' in addition to 'ts'
     and 'type' to avoid misidentifying generic event/API logs as Slack exports.
  4. _is_teams() tightened: checks that 'body' is a dict with 'contentType'
     rather than any key named 'body', avoiding misidentification of calendar
     or event-API responses.
  5. Generic JSON handler now converts JSON to readable natural-language-style
     text (key: value lines) rather than raw JSON syntax, so embeddings reflect
     content rather than syntax noise.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Generator, List

from .base import envelope, make_source_id
from .telegram_connector import extract_telegram
from .teams_connector import extract_teams
from .slack_connector import extract_slack_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shape-detection helpers
# ---------------------------------------------------------------------------

def _is_telegram(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and "messages" in data
        and "name" in data
        and "type" in data
    )


def _is_teams(data: Any) -> bool:
    # Shape 1: dict with top-level "Messages" key
    if isinstance(data, dict) and "Messages" in data:
        return True
    # Shape 2: list of message objects — tightened to require body.contentType
    # to avoid false-positives on calendar/event-API JSON that also has
    # createdDateTime + a generic "body" key.
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if (
            isinstance(first, dict)
            and "createdDateTime" in first
            and isinstance(first.get("body"), dict)
            and "contentType" in first["body"]
        ):
            return True
    return False


def _is_slack(data: Any) -> bool:
    # Require ts + type + user + text — the four fields always present on a
    # real Slack message object. Checking only ts + type is too loose and
    # misidentifies generic event/API logs.
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if (
            isinstance(first, dict)
            and all(k in first for k in ("ts", "type", "user", "text"))
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# JSON-to-readable-text converter (used by generic handler)
# ---------------------------------------------------------------------------

def _flatten_json(obj: Any, prefix: str = "") -> List[str]:
    """Recursively convert a JSON object into 'key: value' text lines."""
    lines: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            child_prefix = f"{prefix} > {k}" if prefix else k
            lines.extend(_flatten_json(v, prefix=child_prefix))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            lines.extend(_flatten_json(v, prefix=f"{prefix}[{i}]" if prefix else f"[{i}]"))
    else:
        lines.append(f"{prefix}: {obj}" if prefix else str(obj))
    return lines


def _to_readable_text(obj: Any) -> str:
    return "\n".join(_flatten_json(obj))


# ---------------------------------------------------------------------------
# File loading with JSONL fallback
# ---------------------------------------------------------------------------

def _load_and_parse(file_path: str):
    """
    Plain (non-generator) function — raises immediately on failure so callers
    get the error at call time, not lazily during iteration.

    Attempts standard JSON first, then JSON Lines as a fallback only when the
    file genuinely looks line-delimited.
    """
    source_id = make_source_id(file_path)
    path = Path(file_path)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Primary: standard JSON
    try:
        data = json.loads(content)
        return data, source_id, path
    except json.JSONDecodeError:
        pass

    # Fallback: JSON Lines — only if every non-empty line looks like a JSON object/array
    lines = [l for l in content.splitlines() if l.strip()]
    if lines and all(l.lstrip().startswith(("{", "[")) for l in lines):
        logger.info(f"{path.name}: standard JSON failed, attempting JSON Lines parse")
        parsed = []
        for i, line in enumerate(lines):
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping malformed JSONL line {i + 1} in {path.name}: {e}")
        if parsed:
            logger.info(f"{path.name}: parsed {len(parsed)} JSON Lines records ({len(lines) - len(parsed)} skipped)")
            return parsed, source_id, path
        else:
            logger.error(f"No valid JSON Lines records found in {path.name}")
            raise ValueError(f"No valid JSON Lines records found in {file_path}")

    # Both attempts failed
    logger.error(f"Malformed JSON in {path.name} — could not parse as JSON or JSON Lines")
    raise ValueError(f"Malformed JSON file (not valid JSON or JSON Lines): {file_path}")


# ---------------------------------------------------------------------------
# Routing and envelope generation
# ---------------------------------------------------------------------------

def _route_and_extract(
    data: Any, source_id: str, path: Path, file_path: str
) -> Generator[Dict[str, Any], None, None]:
    """Generator: routes to the appropriate parser based on detected shape."""

    if _is_telegram(data):
        logger.info(f"Routed {path.name} to Telegram parser")
        yield from extract_telegram(file_path)
        return

    if _is_teams(data):
        logger.info(f"Routed {path.name} to Teams parser")
        yield from extract_teams(file_path)
        return

    if _is_slack(data):
        logger.info(f"Routed {path.name} to Slack parser")
        yield from extract_slack_file(file_path)
        return

    # Generic JSON — convert to readable text rather than raw JSON syntax
    logger.info(f"Routed {path.name} to Generic JSON parser")

    if isinstance(data, list):
        for i, item in enumerate(data):
            text = _to_readable_text(item)
            yield envelope(
                source_type="json",
                source_id=f"{source_id}_item_{i}",
                raw_path=file_path,
                raw_metadata={
                    "file_name": path.name,
                    "index": i,
                    "full_text": text,
                },
                modality="text",
            )
    else:
        text = _to_readable_text(data)
        yield envelope(
            source_type="json",
            source_id=source_id,
            raw_path=file_path,
            raw_metadata={
                "file_name": path.name,
                "full_text": text,
            },
            modality="text",
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Public connector entry point.

    This is a plain function that returns a generator — NOT a generator
    function itself. This means _load_and_parse() runs immediately when
    extract() is called, so any file/parse errors raise at call time rather
    than lazily during iteration.
    """
    data, source_id, path = _load_and_parse(file_path)  # raises immediately if file is bad
    return _route_and_extract(data, source_id, path, file_path)
