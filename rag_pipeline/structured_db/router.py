"""
structured_db/router.py
Query router: decides whether a natural-language question should be
answered via SQL (structured DB) or vector search (semantic retrieval).

Decision logic (SQL only when the question is clearly about the tables):
  1. An explicit invoice number always goes to SQL.
  2. Narrative signals ("summarize", "explain", "who said", ...) go to
     vector search.
  3. Otherwise SQL needs BOTH a table noun (invoice, ledger, expense, ...)
     AND an aggregate/amount signal (total, how much, more than $X, ...).
     A single loose word like "vendor" or "payment" is not enough —
     "Which vendor did the team pick?" is a question about documents.
  4. If SQL returns no rows, the API falls back to vector search.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Pattern

# Aggregate / amount signals. Extended by config structured_db.sql_router_keywords.
_DEFAULT_AGGREGATE_KEYWORDS = {
    "total", "sum", "how much", "how many", "count", "average", "minimum",
    "maximum", "amount", "balance", "outstanding", "overdue", "paid",
    "list all", "find all", "spent", "cost",
}

# Nouns naming the structured tables (invoices, ledger rows).
_INVOICE_NOUNS = {"invoice", "invoices", "bill", "bills", "billed"}
_LEDGER_NOUNS = {
    "ledger", "expense", "expenses", "spend", "spending", "transaction",
    "transactions", "payment", "payments", "sheet", "spreadsheet", "row", "rows",
}

_SEMANTIC_SIGNALS = (
    "summarise", "summarize", "summary", "explain", "describe", "what did",
    "who said", "why did", "why was", "how does", "what is the purpose",
    "strategy", "overview", "background", "context", "opinion", "decide", "decision",
)

# Capture group is case-sensitive: vendor names start with a capital letter,
# so "vendor did the team pick" doesn't become vendor="did the team pick".
_VENDOR_PATTERN = re.compile(
    r"(?i:\b(?:from|vendor|supplier|paid to|payment to|billed by))\s+"
    r"([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,4})"
)
_INVOICE_NO_PATTERN = re.compile(
    r"\b(INV[-\s]?\d{3,}[-\d]*|[A-Z]{2,}-\d{4}-[\d-]+)\b",
    re.IGNORECASE,
)
_DATE_RANGE_PATTERN = re.compile(
    r"between\s+([\w\s,]+?)\s+and\s+([\w\s,]+?)(?:\s|$|\?)",
    re.IGNORECASE,
)
_AMOUNT_PATTERN = re.compile(
    r"(more than|greater than|over|above|less than|under|below)\s*[$₹€£]?\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _word_patterns(words: Iterable[str]) -> Dict[str, Pattern[str]]:
    return {w: re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words}


@dataclass
class QueryRoute:
    use_sql: bool                               # True → hit SQLite; False → vector search
    confidence: float                           # 0.0–1.0
    reason: str                                 # human-readable explanation
    sql_params: Dict[str, Any] = field(default_factory=dict)  # extracted filter params
    query_type: str = "semantic"                # "invoice" | "ledger" | "semantic"


class QueryRouter:
    """
    Determines whether a query should go to the SQL structured DB
    or the semantic vector store.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        extra = cfg.get("structured_db", {}).get("sql_router_keywords", []) or []
        self.aggregate_patterns = _word_patterns(_DEFAULT_AGGREGATE_KEYWORDS | {k.lower() for k in extra})
        self.invoice_patterns = _word_patterns(_INVOICE_NOUNS)
        self.ledger_patterns = _word_patterns(_LEDGER_NOUNS)

    def route(self, query: str) -> QueryRoute:
        q_lower = query.lower().strip()

        inv_m = _INVOICE_NO_PATTERN.search(query)
        if inv_m:
            return QueryRoute(
                use_sql=True,
                confidence=0.95,
                reason=f"Invoice number detected: {inv_m.group(1)}",
                sql_params={"invoice_number": inv_m.group(1)},
                query_type="invoice",
            )

        if any(sig in q_lower for sig in _SEMANTIC_SIGNALS):
            return QueryRoute(use_sql=False, confidence=0.85,
                              reason="Narrative question -> semantic search", query_type="semantic")

        invoice_nouns = [w for w, p in self.invoice_patterns.items() if p.search(query)]
        ledger_nouns = [w for w, p in self.ledger_patterns.items() if p.search(query)]
        aggregates = [w for w, p in self.aggregate_patterns.items() if p.search(query)]
        amount_filters = _AMOUNT_PATTERN.findall(query)

        if not (invoice_nouns or ledger_nouns) or not (aggregates or amount_filters):
            return QueryRoute(use_sql=False, confidence=0.8,
                              reason="Not a question about invoice/ledger figures -> semantic search",
                              query_type="semantic")

        query_type = "invoice" if invoice_nouns else "ledger"
        sql_params: Dict[str, Any] = {}

        m = _VENDOR_PATTERN.search(query)
        if m:
            sql_params["vendor"] = m.group(1).strip().rstrip(".,?")

        m = _DATE_RANGE_PATTERN.search(query)
        if m:
            sql_params["date_from"] = m.group(1).strip()
            sql_params["date_to"] = m.group(2).strip()

        for direction, amount in amount_filters:
            value = float(amount.replace(",", ""))
            if direction.lower() in ("more than", "greater than", "over", "above"):
                sql_params["min_amount"] = value
            else:
                sql_params["max_amount"] = value

        signals = sorted(set(invoice_nouns + ledger_nouns + aggregates))
        reason = f"structured question; signals={signals}"
        if sql_params:
            reason += f"; params={sql_params}"
        return QueryRoute(use_sql=True, confidence=0.75, reason=reason,
                          sql_params=sql_params, query_type=query_type)

    def describe(self, route: QueryRoute) -> str:
        """Human-readable description of the routing decision."""
        target = "SQL database" if route.use_sql else "vector search"
        return (
            f"Route -> {target} (confidence={route.confidence:.0%}, "
            f"type={route.query_type}, reason: {route.reason})"
        )
