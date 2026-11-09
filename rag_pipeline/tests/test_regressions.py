"""
tests/test_regressions.py
Regression tests for bugs found in the November 2026 review.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_pipeline.connectors import extract_file
from rag_pipeline.core.chunker import Chunker
from rag_pipeline.core.generator import (
    NOT_FOUND_ANSWER, Generator, _extract_citations, clean_formatting_artifacts, is_not_found,
)
from rag_pipeline.core.normalizer import Normalizer

SAMPLES = Path(__file__).resolve().parents[2] / "sample_data"


def _docx_doc(text: str):
    env = {
        "source_type": "docx", "source_id": "x", "raw_path": "/d/a.docx", "modality": "text",
        "raw_metadata": {"file_name": "a.docx", "title": "T", "full_text": text},
    }
    doc = Normalizer().normalize(env)
    doc.doc_id = "abc"
    return doc


# H5: every DOCX/email/text chunk was labelled "Sheet 'full document'".
def test_docx_chunks_get_line_ranges():
    text = "\n".join(f"line {i} lorem ipsum dolor sit amet" for i in range(40))
    chunks = Chunker({"chunking": {"default_chunk_size": 200, "default_chunk_overlap": 20}}).chunk(_docx_doc(text))
    labels = [c.citation_meta["location_label"] for c in chunks]
    assert labels[0] == "full document, Lines 1–5"
    assert labels[1] == "full document, Lines 6–10"
    assert not any("Sheet" in l for l in labels)


# H4: chunk ids must be stable so re-ingestion can't duplicate.
def test_chunk_ids_are_deterministic():
    text = "\n".join(f"line {i} lorem ipsum" for i in range(30))
    chunker = Chunker({"chunking": {"default_chunk_size": 120, "default_chunk_overlap": 10}})
    first = [c.chunk_id for c in chunker.chunk(_docx_doc(text))]
    second = [c.chunk_id for c in chunker.chunk(_docx_doc(text))]
    assert first == second and first[0] == "abc-0"


# H6: LaTeX cleanup rewrote identifiers.
@pytest.mark.parametrize("text", ["invoice_2024", "file_3.pdf", "INV_2024 total", "my_var_1", "costs $5 and $10"])
def test_cleanup_leaves_identifiers_alone(text):
    assert clean_formatting_artifacts(text) == text


def test_cleanup_still_fixes_chemistry():
    assert clean_formatting_artifacts(r"H_2O and Fe(OH)_2 + O_2 $\rightarrow$ Fe^{3+}") == "H₂O and Fe(OH)₂ + O₂ → Fe³⁺"


# H7: "not found" anywhere in an answer dropped every citation.
def test_not_found_phrase_inside_answer_keeps_citations():
    chunks = [{"text": "a", "metadata": {"file_name": "x.pdf"}}]
    assert len(_extract_citations("The vendor was not found to be late [1].", chunks)) == 1
    assert _extract_citations(NOT_FOUND_ANSWER, chunks) == []
    assert is_not_found("**Not found in documents**")


def test_finalize_normalizes_not_found():
    result = Generator({}).finalize("Not found in the documents.", [{"text": "a", "metadata": {}}], "m")
    assert result.answer == NOT_FOUND_ANSWER and result.citations == []


# H8: every .txt went to the WhatsApp parser and plain notes failed to ingest.
def test_plain_text_and_whatsapp_txt_route_correctly(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("Meeting notes\nWe chose Vendor B because pricing was lower.\n", encoding="utf-8")
    assert {e["source_type"] for e in extract_file(str(notes))} == {"text"}
    assert {e["source_type"] for e in extract_file(str(SAMPLES / "team_chat.txt"))} == {"whatsapp"}


def test_markdown_supported_and_legacy_office_rejected(tmp_path):
    md = tmp_path / "readme.md"
    md.write_bytes("# Title\n\nCaf\xe9".encode("latin-1"))
    envs = list(extract_file(str(md)))
    assert envs[0]["raw_metadata"]["full_text"].endswith("Café")
    with pytest.raises(ValueError, match="docx"):
        list(extract_file(str(tmp_path / "old.doc")))


# The model was left to add up raw rows (and copied "$4,200" from a prompt example).
def test_sql_chunk_carries_exact_totals():
    from rag_pipeline.core.generator import sql_rows_to_chunks
    rows = [
        {"row_data": {"Date": "2026-01-14", "Vendor": "BrightPack Supplies", "Amount": "70166.67"}, "file_name": "ledger.csv", "sheet_name": ""},
        {"row_data": {"Date": "2026-02-14", "Vendor": "BrightPack Supplies", "Amount": 70166.67}, "file_name": "ledger.csv", "sheet_name": ""},
    ]
    chunk = sql_rows_to_chunks(rows)[0]
    assert "Amount: total 140,333.34 across 2 row(s)" in chunk["text"]
    assert "Date: total" not in chunk["text"]
    assert chunk["metadata"]["file_name"] == "ledger.csv"


def test_system_prompt_has_no_example_figures():
    from rag_pipeline.core.generator import _SYSTEM_PROMPT
    assert "$4,200" not in _SYSTEM_PROMPT
