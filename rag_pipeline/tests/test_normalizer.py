"""
tests/test_normalizer.py
Phase 2 verification: every connector envelope type normalizes correctly,
and citation_meta is always fully populated.
Run: python rag_pipeline/tests/test_normalizer.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rag_pipeline.core.normalizer import Normalizer, NormalizedDocument, CitationMeta

normalizer = Normalizer()


def assert_valid_doc(doc: NormalizedDocument, label: str) -> None:
    assert isinstance(doc, NormalizedDocument), f"{label}: not a NormalizedDocument"
    assert doc.doc_id, f"{label}: doc_id missing"
    assert doc.source_type, f"{label}: source_type missing"
    assert doc.title, f"{label}: title missing"
    assert doc.text.strip(), f"{label}: text is empty"
    assert doc.citation_meta, f"{label}: citation_meta missing"
    cm = doc.citation_meta
    assert cm.file_name, f"{label}: citation_meta.file_name missing"
    assert cm.file_path, f"{label}: citation_meta.file_path missing"
    assert cm.page_or_timestamp, f"{label}: citation_meta.page_or_timestamp missing"
    print(f"  [PASS] {label}")
    print(f"         title={doc.title!r}")
    print(f"         citation={cm.page_or_timestamp!r}, sender={cm.sender!r}")


def test_normalize_pdf():
    env = {
        "source_type": "pdf", "source_id": "abc_p1", "raw_path": "/docs/report.pdf",
        "modality": "text",
        "raw_metadata": {
            "file_name": "report.pdf", "page_number": 3, "total_pages": 10,
            "title": "Annual Report 2024", "author": "Finance Dept",
            "created": "2024-01-01", "used_ocr": False, "image_count": 2,
            "full_text": "Revenue grew 15% year-over-year in Q4 2024.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "PDF")
    assert "page 3" in doc.citation_meta.page_or_timestamp
    assert doc.citation_meta.sender == "Finance Dept"


def test_normalize_docx():
    env = {
        "source_type": "docx", "source_id": "xyz", "raw_path": "/docs/contract.docx",
        "modality": "text",
        "raw_metadata": {
            "file_name": "contract.docx", "title": "Service Agreement",
            "author": "Legal Team", "created": "2024-03-15",
            "paragraph_count": 20, "table_count": 2,
            "full_text": "This agreement is entered into between Party A and Party B.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "DOCX")
    assert doc.title == "Service Agreement"
    assert doc.citation_meta.page_or_timestamp == "full document"


def test_normalize_pptx():
    env = {
        "source_type": "pptx", "source_id": "ppt_s2", "raw_path": "/decks/q4.pptx",
        "modality": "text",
        "raw_metadata": {
            "file_name": "q4.pptx", "slide_number": 2, "total_slides": 15,
            "slide_title": "Revenue Breakdown", "presentation_title": "Q4 Review",
            "author": "CEO", "created": "2024-01-10",
            "full_text": "Software: $4M, Hardware: $2M, Services: $1.5M",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "PPTX")
    assert "slide 2" in doc.citation_meta.page_or_timestamp
    assert "Revenue Breakdown" in doc.title


def test_normalize_excel_tabular():
    env = {
        "source_type": "excel", "source_id": "xl_Payments", "raw_path": "/data/ledger.xlsx",
        "modality": "table",
        "raw_metadata": {
            "file_name": "ledger.xlsx", "sheet_name": "Payments",
            "sheet_classification": "tabular", "row_count": 3, "col_count": 5,
            "columns": ["Vendor", "Amount", "Date", "Invoice_No", "Status"],
            "records": [{"Vendor": "Acme", "Amount": 5000, "Date": "2024-01-15"}],
            "full_text": "Columns: Vendor, Amount, Date\nAcme Corp | 5000 | 2024-01-15",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Excel (tabular)")
    assert doc.structured_data is not None
    assert "records" in doc.structured_data


def test_normalize_excel_narrative():
    env = {
        "source_type": "excel", "source_id": "xl_Notes", "raw_path": "/data/ledger.xlsx",
        "modality": "text",
        "raw_metadata": {
            "file_name": "ledger.xlsx", "sheet_name": "Notes",
            "sheet_classification": "narrative", "row_count": 3, "col_count": 1,
            "columns": ["Note"],
            "full_text": "Q1 vendor meeting: discussed payment terms",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Excel (narrative)")
    assert doc.structured_data is None


def test_normalize_email():
    env = {
        "source_type": "email", "source_id": "em_0", "raw_path": "/mail/inbox.mbox",
        "modality": "text",
        "raw_metadata": {
            "file_name": "inbox.mbox", "message_index": 0,
            "message_id": "msg-001@example.com",
            "subject": "Invoice from Acme Corp", "sender": "billing@acme.com",
            "recipients": "accounts@company.com", "date": "Mon, 15 Jan 2024 10:00:00 +0000",
            "attachments": [], "full_text": "Please find the attached invoice for $5,000.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Email")
    assert "Invoice from Acme Corp" in doc.title
    assert doc.citation_meta.sender == "billing@acme.com"
    assert "message 1" in doc.citation_meta.page_or_timestamp


def test_normalize_whatsapp():
    env = {
        "source_type": "whatsapp", "source_id": "wa_2", "raw_path": "/exports/chat.txt",
        "modality": "text",
        "raw_metadata": {
            "file_name": "chat.txt", "message_index": 2,
            "datetime": "1/15/2024 10:02 AM", "sender": "Bob",
            "participants": ["Alice", "Bob"], "total_messages": 5,
            "full_text": "Found it. The total is $5,000 due by end of month.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "WhatsApp")
    assert doc.citation_meta.sender == "Bob"
    assert doc.speakers is not None and "Alice" in doc.speakers


def test_normalize_telegram():
    env = {
        "source_type": "telegram", "source_id": "tg_2", "raw_path": "/exports/tg.json",
        "modality": "text",
        "raw_metadata": {
            "file_name": "tg.json", "message_id": 2, "chat_name": "Finance Chat",
            "chat_type": "private_group", "sender": "Bob", "date": "2024-01-15T10:01:00",
            "forwarded_from": None, "reply_to_message_id": 1,
            "participants": ["Alice", "Bob"], "total_messages": 3,
            "full_text": "Yes, INV-001 is $5,000 due Jan 31.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Telegram")
    assert "Finance Chat" in doc.title


def test_normalize_slack():
    env = {
        "source_type": "slack", "source_id": "sl_ts123", "raw_path": "/slack/general/2024-01-15.json",
        "modality": "text",
        "raw_metadata": {
            "file_name": "2024-01-15.json", "export_root": "/slack",
            "channel_name": "general", "channel_topic": "Team updates",
            "channel_purpose": "General", "sender": "alice", "user_id": "U001",
            "timestamp": "1705312800.000100", "thread_ts": None, "reply_count": 0,
            "full_text": "Hey team, the invoice from Acme has been approved.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Slack")
    assert "#general" in doc.title
    assert "general" in doc.citation_meta.page_or_timestamp


def test_normalize_teams():
    env = {
        "source_type": "teams", "source_id": "tm_0", "raw_path": "/teams/export.json",
        "modality": "text",
        "raw_metadata": {
            "file_name": "export.json", "message_id": "msg-001",
            "team_name": "Finance Team", "channel_name": "General",
            "sender": "Alice Smith", "datetime": "2024-01-15T10:00:00Z",
            "total_messages": 2,
            "full_text": "The Q4 vendor payments have been reconciled.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Teams")
    assert "Finance Team" in doc.title


def test_normalize_invoice():
    env = {
        "source_type": "invoice", "source_id": "inv_abc", "raw_path": "/invoices/inv001.pdf",
        "modality": "table",
        "raw_metadata": {
            "file_name": "inv001.pdf", "vendor": "Acme Corporation",
            "invoice_number": "INV-2024-0042", "invoice_date": "January 15, 2024",
            "due_date": "February 15, 2024", "total_amount": "5000.00",
            "currency": "USD", "bill_to": "Beta Technologies",
            "line_items": [{"description": "Software License", "amount": "4000"}],
            "full_text": "INVOICE\nInvoice No: INV-2024-0042\nVendor: Acme Corporation\nTotal: $5,000.00",
            "structured_fields": {},
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Invoice")
    assert "INV-2024-0042" in doc.title
    assert doc.structured_data is not None
    assert doc.structured_data["vendor"] == "Acme Corporation"
    assert doc.citation_meta.sender == "Acme Corporation"


def test_normalize_audio():
    env = {
        "source_type": "audio", "source_id": "au_turn3", "raw_path": "/calls/call.mp3",
        "modality": "text",
        "raw_metadata": {
            "file_name": "call.mp3", "turn_index": 3, "speaker": "SPEAKER_01",
            "speakers": ["SPEAKER_00", "SPEAKER_01"],
            "timestamp_start": 145.2, "timestamp_end": 178.9,
            "timestamp_range": "00:02:25\u201300:02:58",
            "total_duration_seconds": 600.0,
            "whisper_model": "base", "diarization_enabled": True,
            "full_text": "We agreed to net 30 payment terms on the Acme contract.",
        },
    }
    doc = normalizer.normalize(env)
    assert_valid_doc(doc, "Audio")
    assert "SPEAKER_01" in doc.title
    assert "00:02:25" in doc.citation_meta.page_or_timestamp
    assert doc.speakers == ["SPEAKER_00", "SPEAKER_01"]


def test_all():
    tests = [
        test_normalize_pdf,
        test_normalize_docx,
        test_normalize_pptx,
        test_normalize_excel_tabular,
        test_normalize_excel_narrative,
        test_normalize_email,
        test_normalize_whatsapp,
        test_normalize_telegram,
        test_normalize_slack,
        test_normalize_teams,
        test_normalize_invoice,
        test_normalize_audio,
    ]
    print("\n=== Phase 2: Normalizer Verification ===\n")
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
