"""
tests/test_chunker.py
Phase 3 verification: source-aware chunking strategies,
citation_meta inheritance, and chunk count correctness.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rag_pipeline.core.normalizer import Normalizer
from rag_pipeline.core.chunker import Chunker, Chunk

normalizer = Normalizer()
chunker = Chunker()  # default config


def assert_valid_chunks(chunks: list, label: str, min_count: int = 1) -> None:
    assert len(chunks) >= min_count, f"{label}: expected >= {min_count} chunks, got {len(chunks)}"
    for i, ch in enumerate(chunks):
        assert isinstance(ch, Chunk), f"{label}[{i}]: not a Chunk"
        assert ch.chunk_id, f"{label}[{i}]: chunk_id missing"
        assert ch.doc_id, f"{label}[{i}]: doc_id missing"
        assert ch.text.strip(), f"{label}[{i}]: text is empty"
        assert ch.citation_meta, f"{label}[{i}]: citation_meta missing"
        assert ch.citation_meta["file_name"], f"{label}[{i}]: citation_meta.file_name missing"
        assert ch.citation_meta["location_label"], f"{label}[{i}]: citation_meta.location_label missing"
        assert ch.chunk_index == i, f"{label}[{i}]: chunk_index mismatch"
        assert ch.total_chunks == len(chunks), f"{label}[{i}]: total_chunks mismatch"
    print(f"  [PASS] {label}: {len(chunks)} chunk(s), citation={chunks[0].citation_meta['location_label']!r}")


def _make_doc(source_type: str, text: str, **kwargs):
    env = {
        "source_type": source_type,
        "source_id": f"test_{source_type}",
        "raw_path": f"/test/{source_type}.file",
        "modality": "text",
        "raw_metadata": {
            "file_name": f"{source_type}.file",
            "full_text": text,
            **kwargs,
        },
    }
    return normalizer.normalize(env)


def test_chunk_pdf_single_page():
    """Short PDF page → 1 chunk."""
    doc = _make_doc("pdf", "Revenue grew 15% year-over-year in Q4 2024.",
                    page_number=1, total_pages=5, title="Annual Report")
    chunks = chunker.chunk(doc)
    assert_valid_chunks(chunks, "PDF (short, 1 chunk)", min_count=1)
    assert len(chunks) == 1
    assert "page 1" in chunks[0].citation_meta["location_label"]


def test_chunk_pdf_long_page():
    """Long PDF page → multiple chunks, all referencing same page."""
    long_text = "Financial performance analysis. " * 100  # ~3200 chars
    doc = _make_doc("pdf", long_text, page_number=3, total_pages=10, title="Report")
    chunks = chunker.chunk(doc)
    assert_valid_chunks(chunks, "PDF (long, multiple chunks)", min_count=2)
    for ch in chunks:
        assert "page 3" in ch.citation_meta["location_label"]


def test_chunk_docx():
    """DOCX → text split; citation says 'full document'."""
    long_text = "Contract clause content. " * 80
    doc = _make_doc("docx", long_text, title="Service Agreement")
    chunks = chunker.chunk(doc)
    assert_valid_chunks(chunks, "DOCX", min_count=1)
    for ch in chunks:
        assert "full document" in ch.citation_meta["location_label"]


def test_chunk_invoice():
    """Invoice → always exactly 1 chunk, never split."""
    env = {
        "source_type": "invoice",
        "source_id": "inv_1",
        "raw_path": "/invoices/inv.pdf",
        "modality": "table",
        "raw_metadata": {
            "file_name": "inv.pdf", "vendor": "Acme Corp",
            "invoice_number": "INV-001", "invoice_date": "2024-01-15",
            "due_date": "2024-02-15", "total_amount": "5000.00",
            "currency": "USD", "bill_to": "Beta Ltd",
            "line_items": [{"description": "Software", "amount": "5000"}],
            "full_text": "Invoice INV-001 from Acme Corp. Total: $5,000. " * 50,
            "structured_fields": {},
        },
    }
    doc = normalizer.normalize(env)
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1, f"Invoice must be exactly 1 chunk, got {len(chunks)}"
    assert_valid_chunks(chunks, "Invoice (single chunk, never split)")


def test_chunk_whatsapp():
    """WhatsApp messages → 1 chunk per message (already atomic)."""
    doc = _make_doc("whatsapp", "Found it. The total is $5,000 due end of month.",
                    message_index=2, datetime="1/15/2024 10:02 AM",
                    sender="Bob", participants=["Alice", "Bob"],
                    total_messages=5)
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1, f"Chat message must be 1 chunk, got {len(chunks)}"
    assert_valid_chunks(chunks, "WhatsApp (single message)")
    assert chunks[0].citation_meta["sender"] == "Bob"


def test_chunk_audio():
    """Audio turn → 1 chunk per turn with timestamp range in citation."""
    doc = _make_doc("audio",
                    "We agreed to net 30 payment terms on the Acme contract.",
                    turn_index=3, speaker="SPEAKER_01",
                    speakers=["SPEAKER_00", "SPEAKER_01"],
                    timestamp_start=145.2, timestamp_end=178.9,
                    timestamp_range="00:02:25\u201300:02:58",
                    total_duration_seconds=600.0,
                    whisper_model="base", diarization_enabled=True)
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1
    assert_valid_chunks(chunks, "Audio (speaker turn)")
    assert "00:02:25" in chunks[0].citation_meta["location_label"]


def test_chunk_excel_tabular():
    """Tabular Excel sheet → 1 chunk (structured data, don't split)."""
    env = {
        "source_type": "excel", "source_id": "xl_pay", "raw_path": "/data/ledger.xlsx",
        "modality": "table",
        "raw_metadata": {
            "file_name": "ledger.xlsx", "sheet_name": "Payments",
            "sheet_classification": "tabular", "row_count": 100, "col_count": 5,
            "columns": ["Vendor", "Amount", "Date", "Invoice_No", "Status"],
            "records": [{"Vendor": "Acme", "Amount": 5000}],
            "full_text": "Columns: Vendor, Amount, Date\n" + "Acme Corp | 5000 | 2024-01-15\n" * 100,
        },
    }
    doc = normalizer.normalize(env)
    chunks = chunker.chunk(doc)
    assert len(chunks) == 1, f"Tabular Excel must be 1 chunk, got {len(chunks)}"
    assert_valid_chunks(chunks, "Excel (tabular, single chunk)")
    assert chunks[0].metadata.get("has_structured_data") is True


def test_chunk_citation_meta_inherited():
    """Verify citation_meta fields are faithfully inherited on every chunk."""
    long_text = "Important financial analysis data. " * 100
    doc = _make_doc("pdf", long_text,
                    page_number=7, total_pages=20, title="Big Report", author="CFO")
    chunks = chunker.chunk(doc)
    assert len(chunks) >= 2, "Need multiple chunks to test inheritance"
    for ch in chunks:
        assert ch.citation_meta["file_name"] == "pdf.file"
        assert "page 7" in ch.citation_meta["location_label"]
        assert "CFO" in (ch.citation_meta.get("sender") or "")


def test_all():
    tests = [
        test_chunk_pdf_single_page,
        test_chunk_pdf_long_page,
        test_chunk_docx,
        test_chunk_invoice,
        test_chunk_whatsapp,
        test_chunk_audio,
        test_chunk_excel_tabular,
        test_chunk_citation_meta_inherited,
    ]
    print("\n=== Phase 3: Chunker Verification ===\n")
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"  [FAIL] {fn.__name__}: {exc}")
            failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = test_all()
    sys.exit(0 if ok else 1)
