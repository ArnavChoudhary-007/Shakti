"""
backfill_kg_type_coercion.py
One-off backfill: apply the same enum coercion upsert_kg_data now enforces
at write time (structured_db/db.py:VALID_KG_TYPES) to existing kg_nodes
rows, so historical data matches what new writes will produce.

Deliberately NOT an LLM re-classification (unlike backfill_kg_types.py,
which fills in genuinely missing types by asking a model) — this just
collapses any type outside the 8-category enum straight to "Other",
deterministically, with no Ollama calls.
"""
import sqlite3

from rag_pipeline.structured_db.db import VALID_KG_TYPES

DB_PATH = "structured_db/structured.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    before = cur.execute("SELECT COUNT(DISTINCT COALESCE(type, '')) FROM kg_nodes").fetchone()[0]

    placeholders = ",".join("?" for _ in VALID_KG_TYPES)
    cur.execute(
        f"UPDATE kg_nodes SET type = 'Other' "
        f"WHERE type IS NULL OR type NOT IN ({placeholders})",
        tuple(VALID_KG_TYPES),
    )
    updated = cur.rowcount
    conn.commit()

    after = cur.execute("SELECT COUNT(DISTINCT type) FROM kg_nodes").fetchone()[0]
    print(f"Rows updated: {updated}")
    print(f"Distinct type count: {before} -> {after}")
    conn.close()


if __name__ == "__main__":
    main()
