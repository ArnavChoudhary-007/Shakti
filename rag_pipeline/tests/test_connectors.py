"""
tests/test_connectors.py
Phase 1 verification: tests every connector against a real sample file.
Run: python -m pytest tests/test_connectors.py -v
Or:  python tests/test_connectors.py   (no pytest needed)
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Make rag_pipeline importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

SAMPLES_DIR = Path(__file__).parent / "sample_files"

# Required envelope fields
ENVELOPE_REQUIRED_KEYS = {"source_type", "source_id", "raw_path", "raw_metadata", "modality"}
VALID_MODALITIES = {"text", "table", "audio", "image"}


def assert_valid_envelope(env: dict, test_name: str) -> None:
    missing = ENVELOPE_REQUIRED_KEYS - set(env.keys())
    assert not missing, f"{test_name}: Missing envelope keys: {missing}"
    assert env["modality"] in VALID_MODALITIES, (
        f"{test_name}: Invalid modality {env['modality']!r}"
    )
    meta = env["raw_metadata"]
    assert isinstance(meta, dict), f"{test_name}: raw_metadata must be a dict"
    assert "file_name" in meta, f"{test_name}: raw_metadata must have 'file_name'"
    assert "full_text" in meta or "structured_fields" in meta, (
        f"{test_name}: raw_metadata must have 'full_text' or 'structured_fields'"
    )
    if "full_text" in meta:
        assert isinstance(meta["full_text"], str) and meta["full_text"].strip(), (
            f"{test_name}: full_text must be non-empty string"
        )
    print(f"  [PASS] {test_name}")


# ── Individual tests ──────────────────────────────────────────

def test_pdf_connector():
    from rag_pipeline.connectors.pdf_connector import extract
    f = SAMPLES_DIR / "sample.pdf"
    assert f.exists(), "sample.pdf not found — run tests/create_samples.py first"
    envelopes = list(extract(str(f)))
    assert len(envelopes) >= 1, "PDF should yield at least 1 envelope"
    for i, env in enumerate(envelopes):
        assert_valid_envelope(env, f"PDF page {i+1}")
        assert env["raw_metadata"]["page_number"] == i + 1
    print(f"    Pages: {len(envelopes)}, source_type={envelopes[0]['source_type']}")


def test_docx_connector():
    from rag_pipeline.connectors.pdf_connector import extract
    f = SAMPLES_DIR / "sample.docx"
    assert f.exists(), "sample.docx not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) == 1, "DOCX should yield 1 envelope"
    assert_valid_envelope(envelopes[0], "DOCX")
    assert envelopes[0]["source_type"] == "docx"


def test_pptx_connector():
    from rag_pipeline.connectors.pdf_connector import extract
    f = SAMPLES_DIR / "sample.pptx"
    assert f.exists(), "sample.pptx not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) >= 1, "PPTX should yield at least 1 envelope"
    for i, env in enumerate(envelopes):
        assert_valid_envelope(env, f"PPTX slide {i+1}")
        assert env["raw_metadata"]["slide_number"] == i + 1


def test_excel_connector():
    from rag_pipeline.connectors.excel_connector import extract
    f = SAMPLES_DIR / "sample.xlsx"
    assert f.exists(), "sample.xlsx not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) == 2, "Excel should yield 2 envelopes (one per sheet)"
    # Payments sheet = tabular, Notes sheet = narrative
    classifications = {env["raw_metadata"]["sheet_name"]: env["raw_metadata"]["sheet_classification"]
                       for env in envelopes}
    assert classifications.get("Payments") == "tabular", f"Payments sheet should be tabular, got: {classifications}"
    assert classifications.get("Notes") == "narrative", f"Notes sheet should be narrative, got: {classifications}"
    for env in envelopes:
        assert_valid_envelope(env, f"Excel sheet '{env['raw_metadata']['sheet_name']}'")


def test_csv_connector():
    from rag_pipeline.connectors.excel_connector import extract
    f = SAMPLES_DIR / "sample.csv"
    assert f.exists(), "sample.csv not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) == 1, "CSV should yield 1 envelope"
    assert_valid_envelope(envelopes[0], "CSV")
    assert envelopes[0]["source_type"] == "csv"


def test_mbox_connector():
    from rag_pipeline.connectors.email_connector import extract
    f = SAMPLES_DIR / "sample.mbox"
    assert f.exists(), "sample.mbox not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) >= 1, "mbox should yield at least 1 email"
    assert_valid_envelope(envelopes[0], "Email (mbox)")
    meta = envelopes[0]["raw_metadata"]
    assert "sender" in meta and meta["sender"]
    assert "subject" in meta


def test_whatsapp_connector():
    from rag_pipeline.connectors.whatsapp_connector import extract
    f = SAMPLES_DIR / "sample_whatsapp.txt"
    assert f.exists(), "sample_whatsapp.txt not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) >= 3, f"WhatsApp should yield at least 3 messages, got {len(envelopes)}"
    for env in envelopes:
        assert_valid_envelope(env, f"WhatsApp msg {env['raw_metadata']['message_index']}")
        assert env["raw_metadata"]["sender"] in ("Alice", "Bob")


def test_telegram_connector():
    from rag_pipeline.connectors.telegram_connector import extract
    f = SAMPLES_DIR / "sample_telegram.json"
    assert f.exists(), "sample_telegram.json not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) == 2, f"Telegram: expected 2 messages (service skipped), got {len(envelopes)}"
    assert_valid_envelope(envelopes[0], "Telegram msg 1")
    # Check rich text was flattened
    assert "INV-001" in envelopes[1]["raw_metadata"]["full_text"]


def test_slack_connector():
    from rag_pipeline.connectors.slack_connector import extract
    slack_dir = SAMPLES_DIR / "slack_export"
    assert slack_dir.exists(), "slack_export/ directory not found"
    envelopes = list(extract(str(slack_dir)))
    assert len(envelopes) == 2, f"Slack: expected 2 messages (join skipped), got {len(envelopes)}"
    for env in envelopes:
        assert_valid_envelope(env, f"Slack msg {env['raw_metadata']['timestamp']}")
    # Check mention was resolved
    any_mention = any("@bob" in env["raw_metadata"]["full_text"] for env in envelopes)
    assert any_mention, "Slack @mentions should be resolved to display names"


def test_teams_connector():
    from rag_pipeline.connectors.teams_connector import extract
    f = SAMPLES_DIR / "sample_teams.json"
    assert f.exists(), "sample_teams.json not found"
    envelopes = list(extract(str(f)))
    assert len(envelopes) == 2, f"Teams: expected 2 (msg + reply), got {len(envelopes)}"
    for env in envelopes:
        assert_valid_envelope(env, f"Teams msg {env['raw_metadata']['message_id']}")
    # Check HTML was stripped from reply
    assert "<p>" not in envelopes[1]["raw_metadata"]["full_text"]


def test_invoice_connector():
    from rag_pipeline.connectors.invoice_connector import extract_invoice
    f = SAMPLES_DIR / "sample_invoice.pdf"
    assert f.exists(), "sample_invoice.pdf not found"
    # Use regex only (no Ollama required for unit test)
    envelopes = list(extract_invoice(str(f), use_llm_fallback=False))
    assert len(envelopes) == 1, "Invoice should yield 1 envelope"
    assert_valid_envelope(envelopes[0], "Invoice")
    assert envelopes[0]["source_type"] == "invoice"
    meta = envelopes[0]["raw_metadata"]
    # At least some fields extracted
    found = [k for k in ("invoice_number", "total_amount", "vendor", "invoice_date") if meta.get(k)]
    assert len(found) >= 2, f"Invoice regex extraction found only {found} — check patterns"
    print(f"    Extracted fields: {found}")


def test_envelope_schema_uniformity():
    """All connectors must produce envelopes with the same required keys."""
    all_tests = [
        test_pdf_connector,
        test_docx_connector,
        test_pptx_connector,
        test_excel_connector,
        test_csv_connector,
        test_mbox_connector,
        test_whatsapp_connector,
        test_telegram_connector,
        test_slack_connector,
        test_teams_connector,
        test_invoice_connector,
    ]
    print("\n=== Phase 1: Connector Verification ===\n")
    passed = 0
    failed = 0
    for test_fn in all_tests:
        try:
            test_fn()
            passed += 1
        except Exception as exc:
            print(f"  [FAIL] {test_fn.__name__}: {exc}")
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = test_envelope_schema_uniformity()
    sys.exit(0 if ok else 1)
