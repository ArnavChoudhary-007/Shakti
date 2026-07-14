"""
core/normalizer.py
Converts ConnectorEnvelope dicts → NormalizedDocument (Pydantic model).

Rules:
  - Every document MUST have citation_meta populated — no exceptions.
  - source-specific logic lives in per-type _normalize_* functions.
  - Unrecognised source types fall through to _normalize_generic().
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────

class CitationMeta(BaseModel):
    """Mandatory citation anchor — every document and chunk carries this."""
    file_name: str
    file_path: str
    location_label: str          # "page 3" | "slide 5" | "00:01:23–00:02:10" | "msg 4–13"
    sender: Optional[str] = None    # for emails / chats


class NormalizedDocument(BaseModel):
    doc_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_type: str
    title: str
    text: str
    structured_data: Optional[Dict[str, Any]] = None
    speakers: Optional[List[str]] = None
    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    citation_meta: CitationMeta


class ConnectorEnvelope(BaseModel):
    """Schema mirror of the dict returned by every connector."""
    source_type: str
    source_id: str
    raw_path: str
    raw_metadata: Dict[str, Any]
    modality: str


# ── Normalizer ────────────────────────────────────────────────

class Normalizer:
    """
    Converts a connector envelope dict into a NormalizedDocument.
    Call normalizer.normalize(envelope_dict) → NormalizedDocument.
    """

    def normalize(self, envelope_dict: Dict[str, Any]) -> NormalizedDocument:
        env = ConnectorEnvelope(**envelope_dict)
        dispatch = {
            "pdf":       self._normalize_pdf,
            "docx":      self._normalize_docx,
            "pptx":      self._normalize_pptx,
            "excel":     self._normalize_excel,
            "csv":       self._normalize_excel,   # same logic
            "email":     self._normalize_email,
            "mbox":      self._normalize_email,
            "eml":       self._normalize_email,
            "pst":       self._normalize_email,
            "whatsapp":  self._normalize_chat,
            "telegram":  self._normalize_chat,
            "slack":     self._normalize_chat,
            "teams":     self._normalize_chat,
            "invoice":   self._normalize_invoice,
            "audio":     self._normalize_audio,
        }
        handler = dispatch.get(env.source_type, self._normalize_generic)
        return handler(env)

    # ── PDF ──────────────────────────────────────────────────

    def _normalize_pdf(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        page = meta.get("page_number", 1)
        total = meta.get("total_pages", "?")
        title = meta.get("title") or meta.get("file_name", "")

        return NormalizedDocument(
            source_type="pdf",
            title=f"{title} — page {page}",
            text=meta.get("full_text", ""),
            created_at=_parse_date(meta.get("created", "")),
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label=f"page {page} of {total}",
            ),
        )

    # ── DOCX ─────────────────────────────────────────────────

    def _normalize_docx(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        title = meta.get("title") or meta.get("file_name", "")

        return NormalizedDocument(
            source_type="docx",
            title=title,
            text=meta.get("full_text", ""),
            created_at=_parse_date(meta.get("created", "")),
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label="full document",
            ),
        )

    # ── PPTX ─────────────────────────────────────────────────

    def _normalize_pptx(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        slide = meta.get("slide_number", 1)
        total = meta.get("total_slides", "?")
        slide_title = meta.get("slide_title", "")
        pres_title = meta.get("presentation_title") or meta.get("file_name", "")
        title = f"{pres_title} — slide {slide}"
        if slide_title:
            title += f": {slide_title}"

        return NormalizedDocument(
            source_type="pptx",
            title=title,
            text=meta.get("full_text", ""),
            created_at=_parse_date(meta.get("created", "")),
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label=f"slide {slide} of {total}",
            ),
        )

    # ── Excel / CSV ──────────────────────────────────────────

    def _normalize_excel(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        file_name = meta["file_name"]
        sheet = meta.get("sheet_name", "")
        classification = meta.get("sheet_classification", "narrative")
        title = f"{file_name} — {sheet}" if sheet else file_name

        # For tabular sheets, expose records as structured_data
        structured_data: Optional[Dict[str, Any]] = None
        if classification == "tabular" and "records" in meta:
            structured_data = {
                "records": meta["records"],
                "columns": meta.get("columns", []),
                "sheet_name": sheet,
                "row_count": meta.get("row_count", 0),
            }

        return NormalizedDocument(
            source_type=env.source_type,
            title=title,
            text=meta.get("full_text", ""),
            structured_data=structured_data,
            citation_meta=CitationMeta(
                file_name=file_name,
                file_path=env.raw_path,
                location_label=f"sheet: {sheet}" if sheet else "full file",
            ),
        )

    # ── Email ────────────────────────────────────────────────

    def _normalize_email(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        subject = meta.get("subject", "(no subject)")
        sender = meta.get("sender", "Unknown")
        date_str = meta.get("date", "")
        msg_idx = meta.get("message_index", 0)

        return NormalizedDocument(
            source_type="email",
            title=f"Email: {subject}",
            text=meta.get("full_text", ""),
            created_at=_parse_date(date_str),
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label=f"message {msg_idx + 1} ({date_str})",
                sender=sender,
            ),
        )

    # ── Chats (WhatsApp / Telegram / Slack / Teams) ──────────

    def _normalize_chat(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        source_type = env.source_type
        sender = meta.get("sender", "Unknown")
        msg_idx = meta.get("message_index", 0)

        # Build a readable location string per platform
        if source_type == "whatsapp":
            location = f"msg {msg_idx + 1} at {meta.get('datetime', '')}"
            title = f"WhatsApp: {meta['file_name']}"
        elif source_type == "telegram":
            chat = meta.get("chat_name", meta["file_name"])
            location = f"msg {meta.get('message_id', msg_idx)} at {meta.get('date', '')}"
            title = f"Telegram: {chat}"
        elif source_type == "slack":
            channel = meta.get("channel_name", "")
            ts = meta.get("timestamp", "")
            location = f"#{channel} at ts {ts}"
            title = f"Slack #{channel}"
        elif source_type == "teams":
            team = meta.get("team_name", "")
            channel = meta.get("channel_name", "")
            location = f"{team}/{channel} at {meta.get('datetime', '')}"
            title = f"Teams: {team}/{channel}"
        else:
            location = f"msg {msg_idx + 1}"
            title = meta["file_name"]

        participants = meta.get("participants") or []
        speakers = participants if participants else ([sender] if sender else None)

        return NormalizedDocument(
            source_type=source_type,
            title=title,
            text=meta.get("full_text", ""),
            speakers=speakers if speakers else None,
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label=location,
                sender=sender,
            ),
        )

    # ── Invoice ──────────────────────────────────────────────

    def _normalize_invoice(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        inv_no = meta.get("invoice_number", "")
        vendor = meta.get("vendor", "")
        inv_date = meta.get("invoice_date", "")

        title_parts = ["Invoice"]
        if vendor:
            title_parts.append(f"from {vendor}")
        if inv_no:
            title_parts.append(f"#{inv_no}")
        if inv_date:
            title_parts.append(f"({inv_date})")

        # Expose all structured fields for Phase 4 SQLite loading
        structured_fields = meta.get("structured_fields", {}) or {}
        structured_data: Dict[str, Any] = {
            "vendor": meta.get("vendor"),
            "invoice_number": meta.get("invoice_number"),
            "invoice_date": meta.get("invoice_date"),
            "due_date": meta.get("due_date"),
            "total_amount": meta.get("total_amount"),
            "currency": meta.get("currency"),
            "bill_to": meta.get("bill_to"),
            "line_items": meta.get("line_items", []),
        }
        # Merge any extra fields from LLM extraction
        for k, v in structured_fields.items():
            if k not in structured_data:
                structured_data[k] = v

        text = meta.get("full_text", "") or str(structured_data)

        return NormalizedDocument(
            source_type="invoice",
            title=" ".join(title_parts),
            text=text,
            structured_data=structured_data,
            created_at=_parse_date(inv_date),
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label=f"invoice {inv_no}" if inv_no else "full document",
                sender=vendor or None,
            ),
        )

    # ── Audio ────────────────────────────────────────────────

    def _normalize_audio(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        speaker = meta.get("speaker", "UNKNOWN")
        ts_range = meta.get("timestamp_range", "")
        turn_idx = meta.get("turn_index", 0)
        speakers = meta.get("speakers") or [speaker]

        return NormalizedDocument(
            source_type="audio",
            title=f"{meta['file_name']} — {speaker} at {ts_range}",
            text=meta.get("full_text", ""),
            speakers=speakers,
            citation_meta=CitationMeta(
                file_name=meta["file_name"],
                file_path=env.raw_path,
                location_label=ts_range or f"turn {turn_idx + 1}",
                sender=speaker,
            ),
        )

    # ── Fallback ─────────────────────────────────────────────

    def _normalize_generic(self, env: ConnectorEnvelope) -> NormalizedDocument:
        meta = env.raw_metadata
        logger.warning("Using generic normalizer for source_type=%r", env.source_type)
        return NormalizedDocument(
            source_type=env.source_type,
            title=meta.get("title") or meta.get("file_name", env.source_id),
            text=meta.get("full_text", str(meta)),
            citation_meta=CitationMeta(
                file_name=meta.get("file_name", env.raw_path.split("/")[-1]),
                file_path=env.raw_path,
                location_label="full document",
                sender=meta.get("sender") or meta.get("author") or None,
            ),
        )


# ── Utilities ─────────────────────────────────────────────────

def _parse_date(raw: str) -> str:
    """
    Try to parse a date string into ISO-8601 UTC.
    Falls back to current time if unparseable.
    """
    if not raw:
        return datetime.utcnow().isoformat() + "Z"
    try:
        from dateutil import parser as dateparser
        dt = dateparser.parse(raw, fuzzy=True)
        if dt:
            return dt.isoformat()
    except Exception:
        pass
    return raw  # return raw string if we can't parse it


def normalize_envelope(envelope_dict: Dict[str, Any]) -> NormalizedDocument:
    """Module-level convenience wrapper."""
    return Normalizer().normalize(envelope_dict)
