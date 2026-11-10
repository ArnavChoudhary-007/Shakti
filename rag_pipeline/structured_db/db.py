"""
structured_db/db.py
SQLite database for structured/tabular data: invoices, Excel ledgers,
vendor payment records.

Tables:
  invoices         — one row per invoice document
  invoice_items    — line items FK → invoices
  ledger_records   — generic tabular rows from Excel/CSV sheets
  vendor_payments  — dedicated view / table for payment-style sheets

The Chunker/Normalizer route tabular data here (via structured_data field).
Semantic queries still hit the vector store (using NL summaries embedded
during ingestion). Precise numeric/date queries hit this DB via the router.
"""
from __future__ import annotations

import json
import logging
import pickle
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from rag_pipeline.structured_db.dates import parse_date
from rag_pipeline.structured_db.numeric import as_number, format_summary, is_identifier


@dataclass
class QueryContext:
    rows: List[Dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    file_names: List[str] = field(default_factory=list)
    numeric_summary: List[str] = field(default_factory=list)

logger = logging.getLogger(__name__)

# KG entity type enum — must match the categories in generator.py's
# extract_kg_relationships prompt. The LLM doesn't reliably stay within
# this set, so upsert_kg_data enforces it at write time rather than
# trusting the model's output verbatim.
VALID_KG_TYPES = {
    "Concept", "Material", "Process", "Organization",
    "Person", "Location", "Technology", "Other",
}


def _coerce_kg_type(value: Optional[str]) -> str:
    v = (value or "").strip()
    return v if v in VALID_KG_TYPES else "Other"


# ── Schema DDL ────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL UNIQUE,
    file_name       TEXT NOT NULL,
    file_path       TEXT,
    vendor          TEXT,
    invoice_number  TEXT,
    invoice_date    TEXT,               -- as written on the invoice
    due_date        TEXT,
    invoice_date_iso TEXT,              -- YYYY-MM-DD, used for filtering and sorting
    due_date_iso    TEXT,
    total_amount    REAL,
    currency        TEXT DEFAULT 'USD',
    bill_to         TEXT,
    raw_text        TEXT,
    workspace_id    TEXT DEFAULT 'default',
    ingested_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    description     TEXT,
    quantity        REAL,
    unit_price      REAL,
    amount          REAL
);

CREATE TABLE IF NOT EXISTS ledger_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    file_path       TEXT,
    sheet_name      TEXT,
    row_index       INTEGER,
    row_data        TEXT,          -- JSON-encoded row dict
    workspace_id    TEXT DEFAULT 'default',
    ingested_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    last_synced_at  TEXT NOT NULL,
    cursor          TEXT,          -- file mtime or message ID for incremental sync
    UNIQUE(source_type, source_path)
);

CREATE INDEX IF NOT EXISTS idx_invoices_vendor  ON invoices(vendor);
CREATE INDEX IF NOT EXISTS idx_invoices_date    ON invoices(invoice_date);
CREATE INDEX IF NOT EXISTS idx_invoices_number  ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_ledger_docid     ON ledger_records(doc_id);
CREATE INDEX IF NOT EXISTS idx_ledger_sheet     ON ledger_records(sheet_name);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id              TEXT NOT NULL,
    workspace_id    TEXT NOT NULL DEFAULT 'default',
    label           TEXT NOT NULL,
    type            TEXT,
    description     TEXT,
    community       INTEGER,
    centrality      REAL,
    ingested_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (id, workspace_id)
);

-- One row per ingested file. file_key prefixes every doc_id / chunk_id the
-- file produced, so a file can be found, de-duplicated, and deleted as a unit.
CREATE TABLE IF NOT EXISTS documents (
    file_key        TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL DEFAULT 'default',
    file_name       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    size_bytes      INTEGER,
    source_types    TEXT,
    doc_count       INTEGER DEFAULT 0,
    chunk_count     INTEGER DEFAULT 0,
    legacy          INTEGER DEFAULT 0,   -- ingested before this table existed
    ingested_at     TEXT DEFAULT (datetime('now')),
    UNIQUE (workspace_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents(workspace_id);

CREATE TABLE IF NOT EXISTS kg_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    target          TEXT NOT NULL,
    relation        TEXT,
    description     TEXT,
    source_doc      TEXT,
    workspace_id    TEXT DEFAULT 'default',
    is_pruned       BOOLEAN DEFAULT 0,
    UNIQUE(source, target, relation, workspace_id)
);

CREATE TABLE IF NOT EXISTS kg_communities (
    id              INTEGER,
    label           TEXT NOT NULL,
    workspace_id    TEXT DEFAULT 'default',
    PRIMARY KEY(id, workspace_id)
);

-- Raw document text queued for KG entity/relationship extraction.
-- Ingestion only inserts a row here (fast); extraction happens later,
-- when the "Build Graph" action runs, so it never blocks ingestion.
CREATE TABLE IF NOT EXISTS kg_pending_docs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT NOT NULL,
    workspace_id    TEXT NOT NULL DEFAULT 'default',
    source_doc      TEXT NOT NULL,
    source_type     TEXT,
    text            TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_kg_pending_workspace ON kg_pending_docs(workspace_id);

CREATE TABLE IF NOT EXISTS subjects (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    centroid_embedding BLOB,
    doc_count INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subjects(id),
    name TEXT NOT NULL,
    description TEXT,
    centroid_embedding BLOB,
    doc_count INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subject_id, name)
);
"""


# ── Database manager ──────────────────────────────────────────

class StructuredDB:
    """
    Manages the local SQLite database for structured/tabular data.
    Thread-safe via connection-per-call pattern.
    """

    def __init__(self, db_path: str, day_first: bool = False):
        self.db_path = str(Path(db_path).resolve())
        self.day_first = day_first     # how to read ambiguous dates like 05/03/2024
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        # Per-connection setting: without it ON DELETE CASCADE never fires.
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)
            # Seamless migration for existing databases
            try:
                conn.execute("ALTER TABLE invoices ADD COLUMN workspace_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError: pass
            
            try:
                conn.execute("ALTER TABLE ledger_records ADD COLUMN workspace_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError: pass
            
            try:
                conn.execute("ALTER TABLE kg_edges ADD COLUMN workspace_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError: pass
            
            try:
                conn.execute("ALTER TABLE kg_nodes ADD COLUMN description TEXT")
            except sqlite3.OperationalError: pass
            
            try:
                conn.execute("ALTER TABLE kg_edges ADD COLUMN description TEXT")
            except sqlite3.OperationalError: pass

            try:
                conn.execute("ALTER TABLE kg_nodes ADD COLUMN community INTEGER")
            except sqlite3.OperationalError: pass

            try:
                conn.execute("ALTER TABLE kg_nodes ADD COLUMN centrality REAL")
            except sqlite3.OperationalError: pass

            try:
                conn.execute("ALTER TABLE kg_edges ADD COLUMN is_pruned BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError: pass

            for column in ("invoice_date_iso", "due_date_iso"):
                try:
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {column} TEXT")
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_date_iso ON invoices(workspace_id, invoice_date_iso)")
            self._backfill_iso_dates(conn)

            self._migrate_kg_nodes_to_workspaces(conn)

        logger.info("Structured DB initialised at %s", self.db_path)

    def _iso(self, value: Any) -> Optional[str]:
        return parse_date(value, day_first=self.day_first, fuzzy=True) if value else None

    def _backfill_iso_dates(self, conn: sqlite3.Connection) -> None:
        """Invoices stored before dates were normalized: parse their text dates once."""
        rows = conn.execute(
            "SELECT id, invoice_date, due_date FROM invoices "
            "WHERE (invoice_date_iso IS NULL AND COALESCE(invoice_date, '') != '') "
            "   OR (due_date_iso IS NULL AND COALESCE(due_date, '') != '')"
        ).fetchall()
        updates = [(self._iso(r["invoice_date"]), self._iso(r["due_date"]), r["id"]) for r in rows]
        if updates:
            conn.executemany("UPDATE invoices SET invoice_date_iso = ?, due_date_iso = ? WHERE id = ?", updates)
            logger.info("Normalized dates on %d existing invoice(s).", len(updates))

    @staticmethod
    def _migrate_kg_nodes_to_workspaces(conn: sqlite3.Connection) -> None:
        """
        kg_nodes used to be global (PK id), so building one workspace's graph
        overwrote another's communities. Rebuild it keyed by (id, workspace_id),
        copying each node into every workspace whose edges reference it.
        """
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(kg_nodes)")}
        if "workspace_id" in cols:
            return
        logger.info("Migrating kg_nodes to per-workspace nodes...")
        conn.executescript("""
            ALTER TABLE kg_nodes RENAME TO kg_nodes_old;
            CREATE TABLE kg_nodes (
                id              TEXT NOT NULL,
                workspace_id    TEXT NOT NULL DEFAULT 'default',
                label           TEXT NOT NULL,
                type            TEXT,
                description     TEXT,
                community       INTEGER,
                centrality      REAL,
                ingested_at     TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (id, workspace_id)
            );
            INSERT OR IGNORE INTO kg_nodes
                (id, workspace_id, label, type, description, community, centrality, ingested_at)
            SELECT n.id, w.ws, n.label, n.type, n.description, n.community, n.centrality, n.ingested_at
            FROM kg_nodes_old n
            JOIN (SELECT source AS id, workspace_id AS ws FROM kg_edges
                  UNION SELECT target, workspace_id FROM kg_edges) w ON w.id = n.id;
            DROP TABLE kg_nodes_old;
        """)

    # ── Invoice CRUD ─────────────────────────────────────────

    def upsert_invoice(self, doc_id: str, structured_data: Dict[str, Any],
                       file_path: str, file_name: str, raw_text: str = "") -> int:
        """Insert or replace an invoice record. Returns invoice row id."""
        total_amount = _parse_amount(structured_data.get("total_amount"))
        line_items = structured_data.get("line_items") or []

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO invoices
                    (doc_id, file_name, file_path, vendor, invoice_number,
                     invoice_date, due_date, invoice_date_iso, due_date_iso,
                     total_amount, currency, bill_to, raw_text, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    vendor=excluded.vendor,
                    invoice_number=excluded.invoice_number,
                    invoice_date=excluded.invoice_date,
                    due_date=excluded.due_date,
                    invoice_date_iso=excluded.invoice_date_iso,
                    due_date_iso=excluded.due_date_iso,
                    total_amount=excluded.total_amount,
                    currency=excluded.currency,
                    bill_to=excluded.bill_to,
                    raw_text=excluded.raw_text,
                    workspace_id=excluded.workspace_id
            """, (
                doc_id, file_name, file_path,
                structured_data.get("vendor"),
                structured_data.get("invoice_number"),
                structured_data.get("invoice_date"),
                structured_data.get("due_date"),
                self._iso(structured_data.get("invoice_date")),
                self._iso(structured_data.get("due_date")),
                total_amount,
                structured_data.get("currency", "USD"),
                structured_data.get("bill_to"),
                raw_text,
                structured_data.get("workspace_id", "default")
            ))
            row = conn.execute("SELECT id FROM invoices WHERE doc_id = ?", (doc_id,)).fetchone()
            inv_id = row["id"]

            # Re-insert line items (delete first for idempotency)
            conn.execute("DELETE FROM invoice_items WHERE invoice_id = ?", (inv_id,))
            for item in line_items:
                if isinstance(item, dict):
                    conn.execute("""
                        INSERT INTO invoice_items (invoice_id, description, quantity, unit_price, amount)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        inv_id,
                        item.get("description"),
                        _parse_amount(item.get("quantity")),
                        _parse_amount(item.get("unit_price")),
                        _parse_amount(item.get("amount")),
                    ))
        return inv_id

    def query_invoices(
        self,
        vendor: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_amount: Optional[float] = None,
        max_amount: Optional[float] = None,
        invoice_number: Optional[str] = None,
        workspace_id: str = "default",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        sql, params = self._invoice_query(
            vendor, date_from, date_to, min_amount, max_amount, invoice_number, workspace_id
        )
        with self._connect() as conn:
            rows = conn.execute(sql + " LIMIT ?", [*params, limit]).fetchall()
        return [dict(row) for row in rows]

    def _invoice_query(
        self, vendor=None, date_from=None, date_to=None, min_amount=None,
        max_amount=None, invoice_number=None, workspace_id="default",
    ) -> Tuple[str, List[Any]]:
        conditions: List[str] = ["workspace_id = ?"]
        params: List[Any] = [workspace_id]

        if vendor:
            conditions.append("vendor LIKE ?")
            params.append(f"%{vendor}%")
        if invoice_number:
            conditions.append("invoice_number LIKE ?")
            params.append(f"%{invoice_number}%")
        # Compare real dates, not the text on the invoice ("April" < "March" as text).
        # Bounds may be ISO (from the router) or free text from other callers.
        for bound, op, end in ((date_from, ">=", False), (date_to, "<=", True)):
            if not bound:
                continue
            iso = parse_date(bound, end=end, day_first=self.day_first)
            if iso is None:
                logger.warning("Ignoring unparseable date filter %r", bound)
                continue
            conditions.append(f"invoice_date_iso {op} ?")
            params.append(iso)
        if min_amount is not None:
            conditions.append("total_amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            conditions.append("total_amount <= ?")
            params.append(max_amount)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return f"SELECT * FROM invoices {where} ORDER BY invoice_date_iso IS NULL, invoice_date_iso DESC, id DESC", params

    def total_invoices(self, vendor: Optional[str] = None, workspace_id: str = "default") -> float:
        """Sum of total_amount, optionally filtered by vendor."""
        if vendor:
            sql = "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE vendor LIKE ? AND workspace_id = ?"
            params = (f"%{vendor}%", workspace_id)
        else:
            sql = "SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE workspace_id = ?"
            params = (workspace_id,)
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()[0]

    # ── Ledger CRUD ──────────────────────────────────────────

    def upsert_ledger_records(
        self,
        doc_id: str,
        file_name: str,
        sheet_name: str,
        records: List[Dict[str, Any]],
        file_path: Optional[str] = None,
        workspace_id: str = "default",
    ) -> int:
        """Insert tabular rows from an Excel/CSV sheet. Returns row count inserted."""
        with self._connect() as conn:
            # Idempotent: delete existing records for this doc
            conn.execute("DELETE FROM ledger_records WHERE doc_id = ?", (doc_id,))
            conn.executemany("""
                INSERT INTO ledger_records (doc_id, file_name, file_path, sheet_name, row_index, row_data, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                (doc_id, file_name, file_path, sheet_name, i, json.dumps(row, default=str), workspace_id)
                for i, row in enumerate(records)
            ])
        return len(records)

    def query_ledger(
        self,
        doc_id: Optional[str] = None,
        sheet_name: Optional[str] = None,
        workspace_id: str = "default",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        sql, params = self._ledger_query(doc_id, sheet_name, workspace_id)
        with self._connect() as conn:
            rows = conn.execute(sql + " LIMIT ?", [*params, limit]).fetchall()
        result = []
        for row in rows:
            record = dict(row)
            record["row_data"] = json.loads(record["row_data"])
            result.append(record)
        return result

    def _ledger_query(
        self, doc_id=None, sheet_name=None, workspace_id="default", vendor=None,
    ) -> Tuple[str, List[Any]]:
        conditions: List[str] = ["workspace_id = ?"]
        params: List[Any] = [workspace_id]
        if doc_id:
            conditions.append("doc_id = ?")
            params.append(doc_id)
        if sheet_name:
            conditions.append("sheet_name LIKE ?")
            params.append(f"%{sheet_name}%")
        if vendor:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(row_data) THEN row_data "
                "ELSE rag_json(row_data) END) "
                "WHERE instr(lower(CAST(value AS TEXT)), lower(?)) > 0)"
            )
            params.append(vendor)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return f"SELECT * FROM ledger_records {where} ORDER BY row_index", params

    def query_answer_context(
        self, query_type: str, filters: Dict[str, Any], workspace_id: str = "default", limit: int = 50,
    ) -> QueryContext:
        """Aggregate every matching row in SQLite; bound only the row preview."""
        if query_type == "invoice":
            sql, params = self._invoice_query(
                vendor=filters.get("vendor"), date_from=filters.get("date_from"),
                date_to=filters.get("date_to"), min_amount=filters.get("min_amount"),
                max_amount=filters.get("max_amount"), invoice_number=filters.get("invoice_number"),
                workspace_id=workspace_id,
            )
            row_json = "json_object('total_amount', total_amount)"
        elif query_type == "ledger":
            sql, params = self._ledger_query(
                sheet_name=filters.get("sheet_name"), workspace_id=workspace_id,
                vendor=filters.get("vendor"),
            )
            row_json = "CASE WHEN json_valid(row_data) THEN row_data ELSE rag_json(row_data) END"
        else:
            return QueryContext()

        prefix = f"WITH matching AS ({sql}) "
        with self._connect() as conn:
            conn.execute("BEGIN")
            conn.create_function("rag_number", 1, as_number)
            conn.create_function("rag_json", 1, lambda text: json.dumps(
                json.loads(text, parse_constant=lambda value: None), allow_nan=False
            ))
            total_count = conn.execute(prefix + "SELECT COUNT(*) FROM matching", params).fetchone()[0]
            files = conn.execute(
                prefix + "SELECT DISTINCT file_name FROM matching ORDER BY file_name", params
            ).fetchall()
            aggregates = conn.execute(prefix + f"""
                , cells AS (
                    SELECT cell.key AS column_name, cell.type AS value_type,
                           rag_number(cell.value) AS number
                    FROM matching, json_each({row_json}) AS cell
                    WHERE cell.value IS NOT NULL AND cell.value != ''
                )
                SELECT column_name, SUM(number) AS total, COUNT(*) AS count,
                       AVG(number) AS average, MIN(number) AS minimum, MAX(number) AS maximum
                FROM cells
                GROUP BY column_name
                HAVING COUNT(number) = COUNT(*)
                   AND SUM(value_type IN ('true', 'false', 'object', 'array')) = 0
                ORDER BY column_name
            """, params).fetchall()
            rows = [dict(row) for row in conn.execute(sql + " LIMIT ?", [*params, limit]).fetchall()]
        if query_type == "ledger":
            for row in rows:
                row["row_data"] = json.loads(row["row_data"])
        summary = [format_summary(
            row["column_name"], row["total"], row["count"], row["average"], row["minimum"], row["maximum"]
        ) for row in aggregates if not is_identifier(row["column_name"])]
        return QueryContext(rows, total_count, [row["file_name"] for row in files], summary)

    def delete_by_file_path(self, file_path: str) -> None:
        """Delete all records associated with a file path."""
        with self._connect() as conn:
            conn.execute("DELETE FROM invoices WHERE file_path = ?", (file_path,))
            conn.execute("DELETE FROM ledger_records WHERE file_path = ?", (file_path,))
            logger.info("Deleted Structured DB records for file_path=%r", file_path)

    # ── Sync state ───────────────────────────────────────────

    def get_sync_cursor(self, source_type: str, source_path: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM sync_state WHERE source_type=? AND source_path=?",
                (source_type, source_path)
            ).fetchone()
        return row["cursor"] if row else None

    def update_sync_state(self, source_type: str, source_path: str, cursor: str) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO sync_state (source_type, source_path, last_synced_at, cursor)
                VALUES (?, ?, datetime('now'), ?)
                ON CONFLICT(source_type, source_path) DO UPDATE SET
                    last_synced_at=excluded.last_synced_at,
                    cursor=excluded.cursor
            """, (source_type, source_path, cursor))


# ── Knowledge Graph ───────────────────────────────────────────

    def upsert_kg_data(self, kg_data: Dict[str, list], source_doc: str, workspace_id: str = "default") -> None:
        """Insert extracted nodes and edges, updating descriptions and types."""
        nodes = kg_data.get("nodes", [])
        edges = kg_data.get("edges", [])
        
        with self._connect() as conn:
            # Upsert explicit nodes
            for node in nodes:
                node_id = str(node.get("id", "")).strip()
                if not node_id:
                    continue
                label = str(node.get("label", node_id)).strip()
                category = _coerce_kg_type(node.get("category"))
                description = str(node.get("description", "")).strip()
                
                conn.execute("""
                    INSERT INTO kg_nodes (id, workspace_id, label, type, description)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id, workspace_id) DO UPDATE SET
                        label=excluded.label,
                        type=excluded.type,
                        description=excluded.description
                """, (node_id, workspace_id, label, category, description))

            for edge in edges:
                source = str(edge.get("source", "")).strip()
                target = str(edge.get("target", "")).strip()
                relation = str(edge.get("label", edge.get("relation", ""))).strip()
                description = str(edge.get("description", "")).strip()
                
                if not source or not target:
                    continue
                    
                source_type = _coerce_kg_type(edge.get("source_type"))
                target_type = _coerce_kg_type(edge.get("target_type"))

                # Auto-create nodes if they were missing from the nodes array
                for node_id, node_type in ((source, source_type), (target, target_type)):
                    conn.execute(
                        "INSERT OR IGNORE INTO kg_nodes (id, workspace_id, label, type) VALUES (?, ?, ?, ?)",
                        (node_id, workspace_id, node_id, node_type),
                    )
                    conn.execute(
                        "UPDATE kg_nodes SET type = ? WHERE id = ? AND workspace_id = ? AND type IS NULL",
                        (node_type, node_id, workspace_id),
                    )
                
                # Upsert edge
                conn.execute("""
                    INSERT INTO kg_edges (source, target, relation, description, source_doc, workspace_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, target, relation, workspace_id) DO UPDATE SET
                        description=excluded.description,
                        source_doc=excluded.source_doc
                """, (source, target, relation, description, source_doc, workspace_id))
                
    # ── KG extraction queue (deferred to "Build Graph") ───────

    def add_kg_pending_doc(self, doc_id: str, workspace_id: str, source_doc: str,
                            source_type: str, text: str) -> None:
        """Queue a document's text for KG extraction — no LLM call here."""
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO kg_pending_docs (doc_id, workspace_id, source_doc, source_type, text)
                VALUES (?, ?, ?, ?, ?)
            """, (doc_id, workspace_id, source_doc, source_type, text))

    def get_pending_kg_docs(self, workspace_id: str = "default") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, doc_id, source_doc, source_type, text FROM kg_pending_docs "
                "WHERE workspace_id = ? ORDER BY id",
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_kg_pending_docs(self, ids: List[int]) -> None:
        if not ids:
            return
        with self._connect() as conn:
            conn.executemany("DELETE FROM kg_pending_docs WHERE id = ?", [(i,) for i in ids])

    def get_knowledge_graph(self, workspace_id: str = "default") -> Dict[str, list]:
        """Fetch all nodes, unpruned edges, and communities for visualization."""
        with self._connect() as conn:
            edges_rows = conn.execute("SELECT source, target, relation, description, source_doc FROM kg_edges WHERE workspace_id = ? AND is_pruned = 0", (workspace_id,)).fetchall()
            
            nodes_rows = conn.execute("""
                SELECT id, label, type, description, community, centrality FROM kg_nodes
                WHERE workspace_id = ? AND id IN (
                    SELECT source FROM kg_edges WHERE workspace_id = ? AND is_pruned = 0
                    UNION
                    SELECT target FROM kg_edges WHERE workspace_id = ? AND is_pruned = 0
                )
            """, (workspace_id, workspace_id, workspace_id)).fetchall()
            
            communities_rows = conn.execute("SELECT id, label FROM kg_communities WHERE workspace_id = ?", (workspace_id,)).fetchall()
            
        return {
            "nodes": [dict(r) for r in nodes_rows],
            "edges": [dict(r) for r in edges_rows],
            "communities": [dict(r) for r in communities_rows]
        }

    def save_graph_layout(self, workspace_id: str, nodes_updates: List[Dict], edges_updates: List[Dict], communities: List[Dict]) -> None:
        """Persist offline-computed communities, centralities, and edge pruning."""
        with self._connect() as conn:
            conn.executemany("UPDATE kg_nodes SET community = ?, centrality = ? WHERE id = ? AND workspace_id = ?",
                             [(n['community'], n['centrality'], n['id'], workspace_id) for n in nodes_updates])
            
            conn.executemany("UPDATE kg_edges SET is_pruned = ? WHERE source = ? AND target = ? AND relation = ? AND workspace_id = ?",
                             [(e['is_pruned'], e['source'], e['target'], e['relation'], workspace_id) for e in edges_updates])
            
            conn.execute("DELETE FROM kg_communities WHERE workspace_id = ?", (workspace_id,))
            conn.executemany("INSERT INTO kg_communities (id, label, workspace_id) VALUES (?, ?, ?)",
                             [(c['id'], c['label'], workspace_id) for c in communities])

    # ── Taxonomy ──────────────────────────────────────────────────

    def insert_subject(self, name: str, description: str, centroid_embedding: Any, created_by: str = "system") -> str:
        subject_id = str(uuid.uuid4())
        emb_blob = pickle.dumps(centroid_embedding)
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO subjects (id, name, description, centroid_embedding, created_by)
                VALUES (?, ?, ?, ?, ?)
            """, (subject_id, name, description, emb_blob, created_by))
        return subject_id

    def insert_topic(self, subject_id: str, name: str, description: str, centroid_embedding: Any, created_by: str = "system") -> str:
        topic_id = str(uuid.uuid4())
        emb_blob = pickle.dumps(centroid_embedding)
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO topics (id, subject_id, name, description, centroid_embedding, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (topic_id, subject_id, name, description, emb_blob, created_by))
        return topic_id

    def get_all_subjects(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM subjects").fetchall()
        res = []
        for r in rows:
            d = dict(r)
            if d.get("centroid_embedding"):
                d["centroid_embedding"] = pickle.loads(d["centroid_embedding"])
            res.append(d)
        return res

    def get_topics_for_subject(self, subject_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM topics WHERE subject_id = ?", (subject_id,)).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            if d.get("centroid_embedding"):
                d["centroid_embedding"] = pickle.loads(d["centroid_embedding"])
            res.append(d)
        return res

    def get_taxonomy_entity(self, entity_id: str, table: str) -> Optional[Dict[str, Any]]:
        if table not in ("subjects", "topics"):
            raise ValueError(f"Invalid table {table}")
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("centroid_embedding"):
            d["centroid_embedding"] = pickle.loads(d["centroid_embedding"])
        return d

    def get_subject_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subjects WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("centroid_embedding"):
            d["centroid_embedding"] = pickle.loads(d["centroid_embedding"])
        return d

    def get_topic_by_name(self, subject_id: str, name: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM topics WHERE subject_id = ? AND name = ?", (subject_id, name)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("centroid_embedding"):
            d["centroid_embedding"] = pickle.loads(d["centroid_embedding"])
        return d

    def update_taxonomy_centroid(self, entity_id: str, new_centroid: Any, doc_count: int, table: str):
        if table not in ("subjects", "topics"):
            raise ValueError(f"Invalid table {table}")
        emb_blob = pickle.dumps(new_centroid)
        with self._connect() as conn:
            conn.execute(f"""
                UPDATE {table} SET centroid_embedding = ?, doc_count = ? WHERE id = ?
            """, (emb_blob, doc_count, entity_id))

    def clear_workspace(self, workspace_id: str = "default") -> None:
        """Deletes all ingested records associated with the given workspace_id."""
        with self._connect() as conn:
            conn.execute("DELETE FROM invoice_items WHERE invoice_id IN (SELECT id FROM invoices WHERE workspace_id = ?)", (workspace_id,))
            conn.execute("DELETE FROM invoices WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM ledger_records WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM kg_edges WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM kg_communities WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM kg_pending_docs WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM kg_nodes WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM documents WHERE workspace_id = ?", (workspace_id,))
            logger.info(f"Cleared structured data for workspace_id={workspace_id}")

    # ── Documents (one row per ingested file) ─────────────────

    def find_document_by_hash(self, workspace_id: str, content_hash: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE workspace_id = ? AND content_hash = ?",
                (workspace_id, content_hash),
            ).fetchone()
        return dict(row) if row else None

    def find_documents_by_name(self, workspace_id: str, file_name: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE workspace_id = ? AND file_name = ?",
                (workspace_id, file_name),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, file_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE file_key = ?", (file_key,)).fetchone()
        return dict(row) if row else None

    def add_document(self, *, file_key: str, workspace_id: str, file_name: str, content_hash: str,
                     size_bytes: int, source_types: List[str], doc_count: int, chunk_count: int,
                     legacy: bool = False) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO documents
                    (file_key, workspace_id, file_name, content_hash, size_bytes, source_types,
                     doc_count, chunk_count, legacy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (file_key, workspace_id, file_name, content_hash, size_bytes,
                  ",".join(sorted(source_types)), doc_count, chunk_count, int(legacy)))

    def list_workspaces(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT workspace_id, COUNT(*) AS document_count FROM documents
                GROUP BY workspace_id ORDER BY workspace_id
            """).fetchall()
        return [dict(r) for r in rows]

    def list_documents(self, workspace_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE workspace_id = ? ORDER BY ingested_at DESC, file_name",
                (workspace_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["source_types"] = [s for s in (d.get("source_types") or "").split(",") if s]
            d["legacy"] = bool(d["legacy"])
            out.append(d)
        return out

    def delete_document(self, doc: Dict[str, Any]) -> None:
        """Remove a file's structured rows, KG contributions, and its documents row."""
        file_key, ws, name = doc["file_key"], doc["workspace_id"], doc["file_name"]
        prefix = f"{file_key}-%"
        with self._connect() as conn:
            if doc.get("legacy"):
                # Pre-documents-table rows have random doc_ids; match by file name.
                conn.execute("DELETE FROM invoices WHERE workspace_id = ? AND file_name = ?", (ws, name))
                conn.execute("DELETE FROM ledger_records WHERE workspace_id = ? AND file_name = ?", (ws, name))
                conn.execute("DELETE FROM kg_pending_docs WHERE workspace_id = ? AND source_doc = ?", (ws, name))
            else:
                conn.execute("DELETE FROM invoices WHERE doc_id LIKE ?", (prefix,))
                conn.execute("DELETE FROM ledger_records WHERE doc_id LIKE ?", (prefix,))
                conn.execute("DELETE FROM kg_pending_docs WHERE doc_id LIKE ?", (prefix,))
            conn.execute("DELETE FROM kg_edges WHERE workspace_id = ? AND source_doc = ?", (ws, name))
            conn.execute("""
                DELETE FROM kg_nodes WHERE workspace_id = ?
                  AND id NOT IN (SELECT source FROM kg_edges WHERE workspace_id = ?
                                 UNION SELECT target FROM kg_edges WHERE workspace_id = ?)
            """, (ws, ws, ws))
            conn.execute("DELETE FROM documents WHERE file_key = ?", (file_key,))

# ── Utilities ─────────────────────────────────────────────────

def _parse_amount(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    # Strip currency symbols and commas
    import re
    cleaned = re.sub(r"[^\d.]", "", str(value))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def get_db(config: Optional[Dict[str, Any]] = None) -> StructuredDB:
    """Factory: returns a StructuredDB using the path from config."""
    cfg = config or {}
    db_path = cfg.get("structured_db", {}).get("sqlite_path", "./structured_db/structured.db")
    return StructuredDB(db_path)
