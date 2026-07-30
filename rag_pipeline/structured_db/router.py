"""
structured_db/router.py
Query router: decides whether a natural-language question should be
answered via SQL (structured DB) or vector search (semantic retrieval).

Decision logic:
  1. Keyword matching against SQL signal words from config.yaml
  2. Named entity detection for vendor names / invoice numbers
  3. Returns a QueryRoute with the decision and extracted parameters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Default SQL keywords (overridden by config.yaml structured_db.sql_router_keywords)
_DEFAULT_SQL_KEYWORDS = {
    "total", "sum", "how much", "amount", "balance",
    "invoice number", "vendor", "payment", "date range",
    "between", "paid", "outstanding", "overdue",
    "how many invoices", "list all", "find all invoices",
    "average", "minimum", "maximum", "count",
}

# Patterns for structured extraction from query text
_VENDOR_PATTERN = re.compile(
    r"(?:from|vendor|supplier|by|paid to|payment to)\s+([A-Z][A-Za-z0-9\s&\.]{2,40}?)(?:\s+(?:in|for|on|between|during|$)|\?|$)",
    re.IGNORECASE,
)
_INVOICE_NO_PATTERN = re.compile(
    r"\b(INV[-\s]?[\d]{3,}[-\d]*|[A-Z]{2,}-\d{4}-[\d-]+)\b",
    re.IGNORECASE,
)
_DATE_RANGE_PATTERN = re.compile(
    r"between\s+([\w\s,]+?)\s+and\s+([\w\s,]+?)(?:\s|$|\?)",
    re.IGNORECASE,
)
_AMOUNT_PATTERN = re.compile(
    r"(?:more than|greater than|less than|over|under|above|below)\s*\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)

# SQL keywords with word-boundary matching (avoids 'sum' matching 'summarise')
_SQL_KEYWORD_PATTERNS = {
    kw: re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    for kw in _DEFAULT_SQL_KEYWORDS
}


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
        kw_list = cfg.get("structured_db", {}).get("sql_router_keywords", [])
        self.sql_keywords = set(kw_list) | _DEFAULT_SQL_KEYWORDS

    def route(self, query: str) -> QueryRoute:
        q_lower = query.lower().strip()

        # ── Step 0: semantic bypass signals ─────────────────
        # These words strongly indicate a narrative/semantic question
        _SEMANTIC_SIGNALS = {
            "summarise", "summarize", "summary", "explain", "describe",
            "what did", "who said", "why did", "how does", "what is the purpose",
            "strategy", "overview", "background", "context",
        }
        has_semantic_signal = any(sig in q_lower for sig in _SEMANTIC_SIGNALS)

        # ── Step 1: keyword signal (word-boundary matching) ──
        matched_keywords = [
            kw for kw, pat in _SQL_KEYWORD_PATTERNS.items()
            if pat.search(q_lower)
        ]
        keyword_score = min(len(matched_keywords) / 2.0, 1.0)

        # If only weak SQL signals + strong semantic signal -> route to vector
        if has_semantic_signal and keyword_score < 0.5:
            return QueryRoute(
                use_sql=False,
                confidence=0.85,
                reason="Semantic signal overrides weak SQL keyword match",
                query_type="semantic",
            )

        if not matched_keywords:
            # Still check for an explicit invoice number before giving up
            inv_m = _INVOICE_NO_PATTERN.search(query)
            if inv_m:
                return QueryRoute(
                    use_sql=True,
                    confidence=0.95,
                    reason="Invoice number detected: %s" % inv_m.group(1),
                    sql_params={"invoice_number": inv_m.group(1)},
                    query_type="invoice",
                )
            return QueryRoute(
                use_sql=False,
                confidence=0.9,
                reason="No SQL-signal keywords found -> semantic search",
                query_type="semantic",
            )

        # ── Step 2: determine query type ─────────────────────
        query_type = "semantic"
        if any(kw in q_lower for kw in ("invoice", "invoice number", "vendor", "bill", "paid")):
            query_type = "invoice"
        elif any(kw in q_lower for kw in ("ledger", "payment record", "sheet", "row")):
            query_type = "ledger"
        else:
            query_type = "invoice"   # default SQL type

        # ── Step 3: extract structured parameters ────────────
        sql_params: Dict[str, Any] = {}

        # Vendor
        m = _VENDOR_PATTERN.search(query)
        if m:
            sql_params["vendor"] = m.group(1).strip().rstrip(".,?")

        # Invoice number
        m = _INVOICE_NO_PATTERN.search(query)
        if m:
            sql_params["invoice_number"] = m.group(1)

        # Date range
        m = _DATE_RANGE_PATTERN.search(query)
        if m:
            sql_params["date_from"] = m.group(1).strip()
            sql_params["date_to"] = m.group(2).strip()

        # Amount threshold
        amount_matches = _AMOUNT_PATTERN.findall(query)
        if amount_matches:
            for i, amt_str in enumerate(amount_matches):
                amt = float(re.sub(r"[,\s]", "", amt_str))
                # Determine if min or max
                ctx = query[max(0, query.lower().find(amt_str) - 20):query.lower().find(amt_str) + len(amt_str)]
                if any(w in ctx.lower() for w in ("more than", "greater", "over", "above")):
                    sql_params["min_amount"] = amt
                else:
                    sql_params["max_amount"] = amt

        # ── Step 4: decide ────────────────────────────────────
        # Invoice number match always forces SQL path
        if sql_params.get("invoice_number"):
            use_sql = True
            keyword_score = max(keyword_score, 0.9)
        else:
            use_sql = keyword_score >= 0.5 or bool(sql_params)
        reason_parts = ["keywords=%s" % matched_keywords]
        if sql_params:
            reason_parts.append("params=%s" % sql_params)

        return QueryRoute(
            use_sql=use_sql,
            confidence=keyword_score if use_sql else 1.0 - keyword_score,
            reason="; ".join(reason_parts),
            sql_params=sql_params,
            query_type=query_type,
        )

    def describe(self, route: QueryRoute) -> str:
        """Human-readable description of the routing decision."""
        target = "SQL database" if route.use_sql else "vector search"
        return (
            f"Route -> {target} (confidence={route.confidence:.0%}, "
            f"type={route.query_type}, reason: {route.reason})"
        )
