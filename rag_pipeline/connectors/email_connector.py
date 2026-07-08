"""
connectors/email_connector.py
Handles: mbox files (stdlib mailbox) and individual .eml files.
PST support requires libpff-python (optional, flagged if not available).

Each email → one envelope.
citation_meta will reference: file_name, message_id, date, sender.
"""
from __future__ import annotations

import email
import email.policy
import logging
import mailbox
import quopri
import re
from email.header import decode_header
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)

try:
    import chardet
    _HAS_CHARDET = True
except ImportError:
    _HAS_CHARDET = False


# ── Header decoding ──────────────────────────────────────────

def _decode_header_value(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded_parts: List[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode("latin-1", errors="replace"))
        else:
            decoded_parts.append(str(part))
    return " ".join(decoded_parts).strip()


# ── Body extraction ──────────────────────────────────────────

def _extract_text_from_message(msg: email.message.Message) -> str:
    """
    Walk a message and extract plain text parts.
    Falls back to HTML-stripped content if no text/plain found.
    """
    text_parts: List[str] = []

    for part in msg.walk():
        ct = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))

        if "attachment" in disposition:
            continue

        if ct == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    text_parts.append(payload.decode(charset, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    text_parts.append(payload.decode("latin-1", errors="replace"))

        elif ct == "text/html" and not text_parts:
            # Fallback: strip HTML tags
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    html = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    html = payload.decode("latin-1", errors="replace")
                # Simple tag strip
                text_parts.append(re.sub(r"<[^>]+>", " ", html))

    return "\n".join(text_parts).strip()


# ── Attachment list ──────────────────────────────────────────

def _list_attachments(msg: email.message.Message) -> List[str]:
    attachments: List[str] = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in disposition:
            filename = part.get_filename() or "unnamed"
            attachments.append(_decode_header_value(filename))
    return attachments


# ── Single email → envelope ──────────────────────────────────

def _message_to_envelope(
    msg: email.message.Message,
    file_path: str,
    message_index: int,
) -> Optional[Dict[str, Any]]:
    text = _extract_text_from_message(msg)
    if not text:
        return None

    subject = _decode_header_value(msg.get("Subject", ""))
    sender = _decode_header_value(msg.get("From", ""))
    recipients = _decode_header_value(msg.get("To", ""))
    date_str = msg.get("Date", "")
    message_id = msg.get("Message-ID", f"msg_{message_index}").strip("<>")
    path = Path(file_path)

    return envelope(
        source_type="email",
        source_id=f"{make_source_id(file_path)}_{message_index}",
        raw_path=file_path,
        raw_metadata={
            "file_name": path.name,
            "message_index": message_index,
            "message_id": message_id,
            "subject": subject,
            "sender": sender,
            "recipients": recipients,
            "date": date_str,
            "attachments": _list_attachments(msg),
            "full_text": text,
        },
        modality="text",
    )


# ── mbox ────────────────────────────────────────────────────

def extract_mbox(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """Yield one envelope per email in an mbox file."""
    mbox = mailbox.mbox(file_path)
    for idx, msg in enumerate(mbox):
        try:
            env = _message_to_envelope(msg, file_path, idx)
            if env:
                yield env
        except Exception as exc:
            logger.warning("Failed to parse mbox message %d: %s", idx, exc)
    mbox.close()


# ── .eml ────────────────────────────────────────────────────

def extract_eml(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """Yield one envelope for a single .eml file."""
    with open(file_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    env = _message_to_envelope(msg, file_path, 0)
    if env:
        yield env


# ── PST (optional) ───────────────────────────────────────────

def extract_pst(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """
    Yield envelopes from a PST file.
    Requires: pip install libpff-python
    """
    try:
        import pypff  # libpff-python
    except ImportError:
        raise RuntimeError(
            "PST support requires libpff-python.\n"
            "Install with: pip install libpff-python\n"
            "(May require Visual C++ build tools on Windows.)"
        )

    pst = pypff.file()
    pst.open(file_path)
    root = pst.get_root_folder()

    def _walk_folder(folder: Any, idx_counter: List[int]) -> Generator[Dict[str, Any], None, None]:
        for i in range(folder.get_number_of_sub_messages()):
            msg_pff = folder.get_sub_message(i)
            body = msg_pff.get_plain_text_body() or b""
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")
            if not body.strip():
                continue
            meta = {
                "file_name": Path(file_path).name,
                "message_index": idx_counter[0],
                "subject": msg_pff.get_subject() or "",
                "sender": msg_pff.get_sender_name() or "",
                "date": str(msg_pff.get_delivery_time()),
                "full_text": body,
            }
            yield envelope(
                source_type="email",
                source_id=f"{make_source_id(file_path)}_{idx_counter[0]}",
                raw_path=file_path,
                raw_metadata=meta,
                modality="text",
            )
            idx_counter[0] += 1

        for j in range(folder.get_number_of_sub_folders()):
            yield from _walk_folder(folder.get_sub_folder(j), idx_counter)

    yield from _walk_folder(root, [0])
    pst.close()


# ── Unified entry point ──────────────────────────────────────

SUPPORTED_EXTENSIONS = {".mbox", ".eml", ".pst"}


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    ext = Path(file_path).suffix.lower()
    if ext == ".mbox":
        yield from extract_mbox(file_path)
    elif ext == ".eml":
        yield from extract_eml(file_path)
    elif ext == ".pst":
        yield from extract_pst(file_path)
    else:
        raise ValueError(f"Unsupported extension {ext!r} for email_connector.")
