"""
tests/test_dates.py
Invoice dates are compared as real dates, whatever format they were written in.
"""
from __future__ import annotations

import sqlite3

import pytest

from rag_pipeline.structured_db.dates import extract_date_range, parse_date
from rag_pipeline.structured_db.db import StructuredDB
from rag_pipeline.structured_db.router import QueryRouter


@pytest.mark.parametrize("text,expected", [
    ("January 15, 2024", "2024-01-15"),
    ("15 Jan 2024", "2024-01-15"),
    ("2024-01-15", "2024-01-15"),
    ("01/15/2024", "2024-01-15"),
    ("Jan 15th, 2024", "2024-01-15"),
    ("March 2024", "2024-03-01"),
    ("2024", "2024-01-01"),
    ("", None),
    ("soon", None),
    ("15 January", None),            # no year → not usable for filtering
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_end_of_period_and_day_first():
    assert parse_date("February 2024", end=True) == "2024-02-29"
    assert parse_date("2023", end=True) == "2023-12-31"
    assert parse_date("05/03/2024") == "2024-05-03"
    assert parse_date("05/03/2024", day_first=True) == "2024-03-05"


@pytest.mark.parametrize("query,expected", [
    ("Total of invoices between January 1, 2024 and March 31, 2024?", ("2024-01-01", "2024-03-31")),
    ("invoice total between 2024-01-01 and 2024-03-31", ("2024-01-01", "2024-03-31")),
    ("How much did we invoice from Jan 2024 to Mar 2024", ("2024-01-01", "2024-03-31")),
    ("total invoices in March 2024", ("2024-03-01", "2024-03-31")),
    ("total invoiced during 2023?", ("2023-01-01", "2023-12-31")),
    ("invoices after February 2024 total", ("2024-03-01", None)),
    ("invoices before March 2024 total", (None, "2024-02-29")),
    ("total invoices since 1 April 2024", ("2024-04-01", None)),
    ("total invoices between 1000 and 5000 dollars", (None, None)),   # amounts, not years
    ("What is the total of all invoices?", (None, None)),
])
def test_extract_date_range(query, expected):
    assert extract_date_range(query) == expected


def test_router_sends_iso_dates_and_ignores_month_as_vendor():
    route = QueryRouter().route("What is the total of invoices from January 2024 to March 2024?")
    assert route.use_sql
    assert route.sql_params.get("date_from") == "2024-01-01"
    assert route.sql_params.get("date_to") == "2024-03-31"
    assert "vendor" not in route.sql_params

    route = QueryRouter().route("Total invoices from BrightPack in March 2024")
    assert route.sql_params["vendor"] == "BrightPack"
    assert (route.sql_params["date_from"], route.sql_params["date_to"]) == ("2024-03-01", "2024-03-31")


def _invoice(db, n, date_text, amount):
    db.upsert_invoice(f"d{n}", {"vendor": "Acme", "invoice_number": f"INV-{n:03d}", "invoice_date": date_text,
                                "total_amount": amount, "workspace_id": "w"}, file_path="", file_name=f"inv{n}.pdf")


def test_date_filter_compares_dates_not_text(tmp_path):
    db = StructuredDB(str(tmp_path / "s.db"))
    _invoice(db, 1, "January 15, 2024", 100)
    _invoice(db, 2, "April 2, 2024", 200)       # "April" < "January" as text
    _invoice(db, 3, "2024-03-10", 300)
    _invoice(db, 4, "15/02/2024", 400)          # unambiguous day-first
    _invoice(db, 5, "no date", 999)

    q1 = db.query_answer_context("invoice", {"date_from": "2024-01-01", "date_to": "2024-03-31"}, "w")
    assert q1.total_count == 3
    rows = db.query_invoices(date_from="2024-01-01", date_to="2024-03-31", workspace_id="w")
    assert sorted(r["invoice_number"] for r in rows) == ["INV-001", "INV-003", "INV-004"]
    # Newest first, by real date.
    assert [r["invoice_number"] for r in db.query_invoices(workspace_id="w")][:4] == ["INV-002", "INV-003", "INV-004", "INV-001"]
    # Free-text bounds from other callers work too.
    assert len(db.query_invoices(date_from="March 2024", date_to="March 2024", workspace_id="w")) == 1


def test_existing_invoices_are_backfilled(tmp_path):
    path = tmp_path / "old.db"
    db = StructuredDB(str(path))
    _invoice(db, 1, "January 15, 2024", 100)
    with sqlite3.connect(path) as conn:          # simulate a row stored before the fix
        conn.execute("UPDATE invoices SET invoice_date_iso = NULL")
    db = StructuredDB(str(path))
    assert db.query_invoices(workspace_id="w")[0]["invoice_date_iso"] == "2024-01-15"
