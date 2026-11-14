import asyncio
from types import SimpleNamespace

import pytest

import rag_pipeline.api.main as api_main
from rag_pipeline.core.generator import sql_rows_to_chunks, summarize_numeric_columns
from rag_pipeline.structured_db.db import StructuredDB


@pytest.fixture()
def database(tmp_path):
    return StructuredDB(str(tmp_path / "totals.db"))


def test_ledger_totals_include_all_600_rows(database, monkeypatch):
    database.upsert_ledger_records(
        "ledger", "ledger.csv", "Payments", [{"Amount": 1, "Vendor": "BrightPack"}] * 600,
        workspace_id="finance",
    )
    database.upsert_ledger_records(
        "private", "private.csv", "Payments", [{"Amount": 10000}], workspace_id="other",
    )
    monkeypatch.setattr(api_main, "_get_struct_db", lambda: database)
    route = SimpleNamespace(use_sql=True, query_type="ledger", sql_params={}, reason="ledger")
    monkeypatch.setattr(api_main, "_get_router", lambda: SimpleNamespace(route=lambda query: route))
    request = api_main.QueryRequest(query="What is the total expense?", workspace_id="finance")
    chunks, used_sql, result_route = asyncio.run(api_main._gather_context(request, request.query))
    assert used_sql and result_route is route
    text = chunks[0]["text"]
    assert "Amount: total 600.00 across 600 row(s)" in text
    assert "Only the first 50 of 600 matching rows are shown" in text
    assert text.count('"Amount": 1') == 50
    assert "private.csv" not in text
    assert chunks[0]["metadata"]["location_label"] == "600 matching row(s); showing 50"


def test_vendor_filter_applies_before_limit_and_never_falls_back_to_all_rows(database):
    rows = [{"Vendor": "Other", "Amount": 100}] * 550
    rows += [{"Vendor": "BrightPack", "Amount": 2}] * 60
    database.upsert_ledger_records("ledger", "ledger.csv", "Payments", rows, workspace_id="finance")
    context = database.query_answer_context("ledger", {"vendor": "brightpack"}, "finance")
    assert context.total_count == 60 and len(context.rows) == 50
    assert "Amount: total 120.00 across 60 row(s)" in context.numeric_summary[0]
    assert all(row["row_data"]["Vendor"] == "BrightPack" for row in context.rows)
    missing = database.query_answer_context("ledger", {"vendor": "Missing"}, "finance")
    assert missing.total_count == 0 and missing.rows == [] and missing.numeric_summary == []


def test_sheet_filter_and_all_source_file_names(database):
    database.upsert_ledger_records("first", "a.csv", "Payments", [{"Amount": 1}] * 60)
    database.upsert_ledger_records("second", "b.csv", "Payments", [{"Amount": 2}] * 60)
    database.upsert_ledger_records("third", "c.csv", "Stock", [{"Amount": 10000}])
    context = database.query_answer_context("ledger", {"sheet_name": "Payments"})
    assert context.total_count == 120 and context.file_names == ["a.csv", "b.csv"]
    assert "Amount: total 180.00 across 120 row(s)" in context.numeric_summary[0]


def test_sql_numeric_summary_matches_python_for_heterogeneous_columns(database):
    rows = [
        {"Amount": "$1,000.50", "Mixed": 10, "Flag": True, "row_id": 1, "Missing": None},
        {"Amount": 2, "Mixed": "unknown", "Flag": 2, "row_id": 2, "Missing": ""},
        {"Amount": "£3.50", "row_id": 3, "Missing": float("nan")},
        {"Amount": "₹4.00", "Optional": 5},
        {"Amount": "€5.00", "Optional": "", "Nested": {"amount": 8}},
    ]
    database.upsert_ledger_records("mixed", "mixed.csv", "Sheet", rows)
    context = database.query_answer_context("ledger", {})
    assert context.numeric_summary == summarize_numeric_columns(rows)
    assert "Amount: total 1,015.00 across 5 row(s)" in context.numeric_summary[0]
    assert not any(line.startswith("- Flag:") or line.startswith("- row_id:") for line in context.numeric_summary)


def test_invoice_totals_include_all_matches_with_filters(database):
    for index in range(65):
        database.upsert_invoice(
            f"invoice-{index}", {"vendor": "BrightPack", "total_amount": 2, "workspace_id": "finance"},
            f"/invoices/{index}.pdf", f"{index}.pdf",
        )
    database.upsert_invoice("other", {"vendor": "Other", "total_amount": 900}, "/other.pdf", "other.pdf")
    context = database.query_answer_context("invoice", {"vendor": "BrightPack", "min_amount": 1}, "finance")
    assert context.total_count == 65 and len(context.rows) == 50
    assert len(context.file_names) == 65
    assert "total_amount: total 130.00 across 65 row(s)" in context.numeric_summary[0]
    assert database.query_answer_context("invoice", {"min_amount": 3}, "finance").total_count == 0


def test_direct_sql_chunk_summarizes_before_truncating_preview():
    rows = [{"row_data": {"Amount": 1}, "file_name": "all.csv"}] * 600
    chunk = sql_rows_to_chunks(rows)[0]
    assert "Amount: total 600.00 across 600 row(s)" in chunk["text"]
    assert chunk["text"].count('"Amount": 1') == 50
