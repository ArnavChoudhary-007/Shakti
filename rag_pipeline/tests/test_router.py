"""
tests/test_router.py
Routing must only send clearly tabular questions to SQL.
"""
from __future__ import annotations

import pytest

from rag_pipeline.structured_db.router import QueryRouter

router = QueryRouter({"structured_db": {"sql_router_keywords": ["reimbursed"]}})


@pytest.mark.parametrize("query", [
    "Which vendor did the team decide to go with, and what's the contract value?",
    "Summarize the vendor evaluation memo",
    "Who said the payment terms were a problem?",
    "What was the total impact of the vendor decision on the team?",
    "How many people attended the review between sessions?",
    "Explain the invoice approval process",
])
def test_narrative_questions_use_vector_search(query):
    assert router.route(query).use_sql is False


@pytest.mark.parametrize("query,query_type", [
    ("What is the total amount of invoices from Acme Corp?", "invoice"),
    ("List all invoices over $5,000", "invoice"),
    ("How much did we spend on expenses in the ledger?", "ledger"),
    ("Show INV-2024-0042", "invoice"),
])
def test_tabular_questions_use_sql(query, query_type):
    route = router.route(query)
    assert route.use_sql is True
    assert route.query_type == query_type


def test_vendor_param_only_captures_capitalized_names():
    assert router.route("What is the total of invoices from Acme Corp?").sql_params["vendor"] == "Acme Corp"
    assert "vendor" not in router.route("total invoice amount for the vendor we chose").sql_params


def test_amount_filters():
    params = router.route("List invoices more than $1,000 and less than 5000").sql_params
    assert params["min_amount"] == 1000.0
    assert params["max_amount"] == 5000.0


def test_config_keywords_are_used():
    assert router.route("Which expenses were reimbursed?").use_sql is True
