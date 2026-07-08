"""
connectors/invoice_connector.py
Handles: Invoices — both digital PDFs and scanned images.

Strategy:
  1. Digital PDF  → extract text with pymupdf, then parse structured fields
                    with regex + LLM fallback (Ollama) for ambiguous formats.
  2. Scanned PDF  → OCR each page with pytesseract, then same field extraction.
  3. Image files  → send to Ollama vision model for structured field extraction.

Structured fields extracted:
  - vendor (supplier name)
  - invoice_number
  - invoice_date
  - due_date
  - total_amount
  - currency
  - line_items: list[{description, quantity, unit_price, amount}]
  - bill_to (client/buyer)

The envelope modality is "table" so Phase 4 loads it into SQLite.
The full_text field always contains the raw extracted text for vector search.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)

try:
    import fitz
    _HAS_FITZ = True
except ImportError:
    _HAS_FITZ = False

try:
    from PIL import Image
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}


# ── Regex-based field extraction ─────────────────────────────

_PATTERNS: Dict[str, re.Pattern] = {
    "invoice_number": re.compile(
        r"invoice\s*(?:no|number|#|num)[:\s]*([A-Z0-9\-\/]+)", re.IGNORECASE
    ),
    "invoice_date": re.compile(
        r"(?:invoice\s*date|date\s*of\s*invoice|date\s*issued)[:\s]*([\d]{1,2}[\-\/\.]\d{1,2}[\-\/\.]\d{2,4}|\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    "due_date": re.compile(
        r"(?:due\s*date|payment\s*due|pay\s*by)[:\s]*([\d]{1,2}[\-\/\.]\d{1,2}[\-\/\.]\d{2,4}|\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    "total_amount": re.compile(
        r"(?:total\s*(?:amount|due|payable)?|amount\s*due|grand\s*total)[:\s]*(?:[\$£€₹]?\s*)([0-9,]+\.?\d*)",
        re.IGNORECASE,
    ),
    "vendor": re.compile(
        r"(?:from|vendor|supplier|billed?\s*(?:by|from))[:\s]*([^\n]{3,60})",
        re.IGNORECASE,
    ),
    "bill_to": re.compile(
        r"(?:bill\s*to|to|client|customer|sold\s*to)[:\s]*([^\n]{3,60})",
        re.IGNORECASE,
    ),
    "currency": re.compile(r"(USD|EUR|GBP|INR|CAD|AUD|JPY|[\$£€₹])", re.IGNORECASE),
}


def _extract_fields_regex(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for field, pattern in _PATTERNS.items():
        m = pattern.search(text)
        if m:
            fields[field] = m.group(1).strip()
    return fields


# ── LLM extraction fallback (Ollama) ─────────────────────────

def _extract_fields_llm(text: str, ollama_host: str, model: str) -> Dict[str, Any]:
    """
    Ask Ollama to extract invoice fields from text.
    Returns parsed JSON dict or empty dict on failure.
    """
    import httpx

    prompt = f"""Extract the following fields from this invoice text and return ONLY a valid JSON object with these keys:
vendor, invoice_number, invoice_date, due_date, total_amount, currency, bill_to, line_items.

For line_items, return a list of objects with: description, quantity, unit_price, amount.
If a field is not found, set it to null.

Invoice text:
---
{text[:4000]}
---

Return ONLY the JSON, no explanation:"""

    try:
        resp = httpx.post(
            f"{ollama_host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        # Extract JSON block if wrapped in markdown
        json_match = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
        json_str = json_match.group(1) if json_match else raw
        return json.loads(json_str.strip())
    except Exception as exc:
        logger.warning("LLM invoice extraction failed: %s", exc)
        return {}


# ── Vision model extraction (scanned images / image PDFs) ────

def _extract_fields_vision(
    image_path: str, ollama_host: str, vision_model: str
) -> Dict[str, Any]:
    """
    Send image to Ollama vision model and extract invoice fields.
    Ollama vision API accepts base64-encoded images.
    """
    import base64
    import httpx

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = """This is an invoice image. Extract the following fields and return ONLY a valid JSON object:
vendor, invoice_number, invoice_date, due_date, total_amount, currency, bill_to, line_items.
For line_items: list of {description, quantity, unit_price, amount}.
If a field is not found, set it to null. Return ONLY JSON."""

    try:
        resp = httpx.post(
            f"{ollama_host}/api/generate",
            json={
                "model": vision_model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        json_match = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
        json_str = json_match.group(1) if json_match else raw
        return json.loads(json_str.strip())
    except Exception as exc:
        logger.warning("Vision invoice extraction failed: %s", exc)
        return {}


# ── PDF text extraction ───────────────────────────────────────

def _extract_pdf_text(file_path: str) -> str:
    """Extract all text from a PDF, with OCR fallback per page."""
    if not _HAS_FITZ:
        raise RuntimeError("pymupdf required for PDF invoice extraction.")
    doc = fitz.open(file_path)
    parts: List[str] = []
    for page in doc:
        text = page.get_text("text").strip()
        if not text and _HAS_OCR:
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img).strip()
        if text:
            parts.append(text)
    doc.close()
    return "\n\n".join(parts)


# ── Main extractor ────────────────────────────────────────────

def extract_invoice(
    file_path: str,
    ollama_host: str = "http://localhost:11434",
    text_model: str = "llama3.2",
    vision_model: str = "llava",
    use_llm_fallback: bool = True,
) -> Generator[Dict[str, Any], None, None]:
    path = Path(file_path)
    source_id = make_source_id(file_path)
    ext = path.suffix.lower()

    # ── Image-only invoices ──────────────────────────────────
    if ext in _IMAGE_EXTENSIONS:
        fields = _extract_fields_vision(file_path, ollama_host, vision_model)
        full_text = str(fields)  # best we can do without OCR text
        structured = fields

    # ── PDF invoices ─────────────────────────────────────────
    elif ext == ".pdf":
        full_text = _extract_pdf_text(file_path)

        # Try regex first
        fields = _extract_fields_regex(full_text)

        # If regex found fewer than 3 fields, use LLM
        non_null = {k: v for k, v in fields.items() if v}
        if len(non_null) < 3 and use_llm_fallback:
            logger.info("Regex only found %d fields for %s; using LLM fallback.", len(non_null), path.name)
            llm_fields = _extract_fields_llm(full_text, ollama_host, text_model)
            # Merge: LLM fills in what regex missed
            for k, v in llm_fields.items():
                if v and not fields.get(k):
                    fields[k] = v

        structured = fields

    else:
        raise ValueError(f"Unsupported invoice format: {ext!r}")

    yield envelope(
        source_type="invoice",
        source_id=source_id,
        raw_path=file_path,
        raw_metadata={
            "file_name": path.name,
            "vendor": structured.get("vendor", ""),
            "invoice_number": structured.get("invoice_number", ""),
            "invoice_date": structured.get("invoice_date", ""),
            "due_date": structured.get("due_date", ""),
            "total_amount": structured.get("total_amount", ""),
            "currency": structured.get("currency", ""),
            "bill_to": structured.get("bill_to", ""),
            "line_items": structured.get("line_items", []),
            "full_text": full_text if ext == ".pdf" else str(structured),
            "structured_fields": structured,
        },
        modality="table",   # → Phase 4 loads into SQLite invoices table
    )


SUPPORTED_EXTENSIONS = {".pdf"} | _IMAGE_EXTENSIONS


def extract(
    file_path: str,
    ollama_host: str = "http://localhost:11434",
    text_model: str = "llama3.2",
    vision_model: str = "llava",
) -> Generator[Dict[str, Any], None, None]:
    yield from extract_invoice(
        file_path,
        ollama_host=ollama_host,
        text_model=text_model,
        vision_model=vision_model,
    )
