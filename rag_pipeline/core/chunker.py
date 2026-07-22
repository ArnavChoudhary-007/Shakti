"""
core/chunker.py
Source-aware chunking strategies. Every chunk inherits and refines
citation_meta from its parent NormalizedDocument.

Strategies:
  pdf / docx / pptx / email → recursive character splitter
  whatsapp / telegram / slack / teams / audio → conversation window
  invoice / excel (tabular) → single chunk (never split)
  csv / excel (narrative) → recursive character splitter
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    _HAS_SPLITTER = True
except ImportError:
    _HAS_SPLITTER = False
    logger.warning("langchain-text-splitters not installed — using simple splitter.")


# ── Data types ────────────────────────────────────────────────

@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    source_type: str
    text: str
    citation_meta: Dict[str, Any]   # inherited + refined from parent doc
    chunk_index: int
    total_chunks: int
    metadata: Dict[str, Any] = field(default_factory=dict)  # extra for vector store


# ── Simple fallback splitter (no langchain) ───────────────────

def _simple_split(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    Naive character-level splitter used when langchain is not available.
    Splits on paragraph boundaries (\n\n) where possible.
    """
    if len(text) <= chunk_size:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # Try to split at a paragraph boundary
        if end < len(text):
            para_break = text.rfind("\n\n", start, end)
            if para_break > start:
                end = para_break + 2
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if c.strip()]


def _split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    if _HAS_SPLITTER:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        return [c for c in splitter.split_text(text) if c.strip()]
    return _simple_split(text, chunk_size, overlap)


# ── Citation meta helpers ─────────────────────────────────────

def _refine_citation_meta(
    base_meta: Dict[str, Any],
    chunk_index: int,
    total_chunks: int,
    doc: Any,
    chunk_text: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Clone the parent's citation_meta and compute a precise location label.
    """
    refined = dict(base_meta)
    
    st = doc.source_type
    loc = base_meta.get("location_label", "")
    
    # Calculate precise location label
    if st == "pdf":
        # Usually loc is "page 3 of 20"
        if loc.startswith("page "):
            page_num = loc.split(" ")[1]
            refined["location_label"] = f"page {page_num}"
        else:
            refined["location_label"] = loc
    elif st in ("docx", "csv", "email", "mbox", "eml", "pst") or (st == "excel" and doc.structured_data is None) or st not in _SINGLE_CHUNK_TYPES | _CHAT_TYPES | _EXCEL_TYPES | _TEXT_SPLIT_TYPES:
        # Text splits: calculate line numbers
        start_char = doc.text.find(chunk_text)
        if start_char != -1:
            start_line = doc.text.count("\\n", 0, start_char) + 1
            end_line = start_line + chunk_text.count("\\n")
            if st == "excel":
                # Fallback narrative excel
                sheet = loc.replace("sheet: ", "") if "sheet: " in loc else loc
                refined["location_label"] = f"Sheet '{sheet}', Rows {start_line}–{end_line}"
            elif st in ("email", "mbox", "eml", "pst"):
                sender = base_meta.get("sender") or "Unknown"
                date_str = doc.created_at[:10] if doc.created_at else "Unknown date"
                refined["location_label"] = f"Email from {sender}, {date_str}"
            else:
                if loc and "full document" in loc:
                    refined["location_label"] = f"{loc}, Lines {start_line}–{end_line}"
                else:
                    refined["location_label"] = f"Lines {start_line}–{end_line}"
        sheet = loc.replace("sheet: ", "") if "sheet: " in loc else loc
        refined["location_label"] = f"Sheet '{sheet}'"
    elif st in ("whatsapp", "telegram", "slack", "teams"):
        sender = base_meta.get("sender") or "Unknown"
        # Extract timestamp from location string (e.g. "msg 1 at 2024-01-01 10:00" or "#channel at ts 1234")
        ts = "Unknown time"
        if " at ts " in loc:
            ts = loc.split(" at ts ")[1]
        elif " at " in loc:
            ts = loc.split(" at ")[1]
        refined["location_label"] = f"{sender}, {ts}"
    elif st == "audio":
        # loc is HH:MM:SS-HH:MM:SS
        parts = loc.split("-")
        if len(parts) == 2:
            def trim_hours(t):
                return t[3:] if t.startswith("00:") else t
            refined["location_label"] = f"{trim_hours(parts[0])}–{trim_hours(parts[1])}"
        else:
            refined["location_label"] = loc

    if total_chunks > 1:
        refined["chunk_info"] = f"chunk {chunk_index + 1}/{total_chunks}"
    if extra:
        refined.update(extra)
    return refined


def _citation_meta_dict(doc: Any) -> Dict[str, Any]:
    """Extract citation_meta as a plain dict from a NormalizedDocument."""
    cm = doc.citation_meta
    return {
        "file_name": cm.file_name,
        "file_path": cm.file_path,
        "location_label": cm.location_label,
        "sender": cm.sender,
    }


# ── Chunking strategies ───────────────────────────────────────

# Source types that are NEVER split (single-chunk)
_SINGLE_CHUNK_TYPES = {"invoice"}

# Source types using conversation-window grouping
_CHAT_TYPES = {"whatsapp", "telegram", "slack", "teams", "audio"}

# Source types using recursive text splitting
_TEXT_SPLIT_TYPES = {"pdf", "docx", "pptx", "email", "mbox", "eml", "pst", "csv"}

# Excel: tabular sheets → single chunk; narrative → text split
_EXCEL_TYPES = {"excel"}


class Chunker:
    """
    Splits NormalizedDocuments into Chunks using source-aware strategies.

    Usage:
        chunker = Chunker(config)
        chunks = chunker.chunk(normalized_doc)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        chunk_cfg = cfg.get("chunking", {})
        self.default_chunk_size = chunk_cfg.get("default_chunk_size", 800)
        self.default_overlap = chunk_cfg.get("default_chunk_overlap", 150)
        self.chat_window = chunk_cfg.get("chat", {}).get("window_messages", 10)
        self._per_source = chunk_cfg  # holds per-source overrides

    def _get_sizes(self, source_type: str):
        src_cfg = self._per_source.get(source_type, {})
        size = src_cfg.get("chunk_size", self.default_chunk_size)
        overlap = src_cfg.get("chunk_overlap", self.default_overlap)
        return int(size), int(overlap)

    def chunk(self, doc: Any) -> List[Chunk]:
        """
        Main dispatch method.
        doc must be a NormalizedDocument (or any object with the same attrs).
        """
        st = doc.source_type

        if st in _SINGLE_CHUNK_TYPES:
            return self._single_chunk(doc)

        if st in _CHAT_TYPES:
            return self._single_chunk(doc)  # individual messages → already atomic

        if st in _EXCEL_TYPES:
            # Tabular sheets get a single chunk; narrative sheets are text-split
            if doc.structured_data is not None:
                return self._single_chunk(doc)
            return self._text_split_chunk(doc)

        if st in _TEXT_SPLIT_TYPES or st in ("pdf", "docx", "pptx"):
            return self._text_split_chunk(doc)

        # Fallback: text split
        logger.warning("Using default text-split for unknown source_type=%r", st)
        return self._text_split_chunk(doc)

    # ── Single-chunk ─────────────────────────────────────────

    def _single_chunk(self, doc: Any) -> List[Chunk]:
        cm = _citation_meta_dict(doc)
        return [Chunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=doc.doc_id,
            source_type=doc.source_type,
            text=doc.text,
            citation_meta=_refine_citation_meta(cm, 0, 1, doc, doc.text),
            chunk_index=0,
            total_chunks=1,
            metadata={
                "source_type": doc.source_type,
                "file_name": cm["file_name"],
                "file_path": cm["file_path"],
                "location_label": cm["location_label"],
                "sender": cm.get("sender"),
                "title": doc.title,
                "has_structured_data": doc.structured_data is not None,
            },
        )]

    # ── Text-split ────────────────────────────────────────────

    def _text_split_chunk(self, doc: Any) -> List[Chunk]:
        size, overlap = self._get_sizes(doc.source_type)
        text_chunks = _split_text(doc.text, size, overlap)

        if not text_chunks:
            logger.warning("No text chunks produced for doc %s (%s)", doc.doc_id, doc.source_type)
            return []

        total = len(text_chunks)
        base_cm = _citation_meta_dict(doc)
        chunks: List[Chunk] = []

        for i, text in enumerate(text_chunks):
            cm = _refine_citation_meta(base_cm, i, total, doc, text)
            chunks.append(Chunk(
                chunk_id=str(uuid.uuid4()),
                doc_id=doc.doc_id,
                source_type=doc.source_type,
                text=text,
                citation_meta=cm,
                chunk_index=i,
                total_chunks=total,
                metadata={
                    "source_type": doc.source_type,
                    "file_name": base_cm["file_name"],
                    "file_path": base_cm["file_path"],
                    "location_label": base_cm["location_label"],
                    "sender": base_cm.get("sender"),
                    "title": doc.title,
                    "chunk_index": i,
                    "total_chunks": total,
                    "has_structured_data": doc.structured_data is not None,
                },
            ))
        return chunks


def chunk_document(doc: Any, config: Optional[Dict[str, Any]] = None) -> List[Chunk]:
    """Module-level convenience wrapper."""
    return Chunker(config).chunk(doc)
