"""
tests/test_embedder_vectorstore.py
Phase 5 + 6 verification:
  - Embedder: model loads, vectors correct shape, batching works
  - VectorStore (Chroma): add/query/count/delete round-trip
  - VectorStore (FAISS): add/query/count/delete round-trip
  - Metadata preserved on every retrieved chunk

Run: python rag_pipeline/tests/test_embedder_vectorstore.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rag_pipeline.core.embedder import Embedder
from rag_pipeline.core.vectorstore import ChromaVectorStore, FaissVectorStore, get_vector_store

# Use a tiny model so tests run fast without GPU
TEST_MODEL = "all-MiniLM-L6-v2"


# ── Sample chunks ─────────────────────────────────────────────

def _make_chunks(embedder: Embedder, texts_meta: list) -> list:
    import uuid
    chunks = []
    texts = [tm[0] for tm in texts_meta]
    embeddings = embedder.encode(texts)
    for i, (text, meta) in enumerate(texts_meta):
        chunks.append({
            "chunk_id": str(uuid.uuid4()),
            "text": text,
            "embedding": embeddings[i],
            "metadata": meta,
        })
    return chunks


SAMPLE_TEXTS = [
    ("Acme Corp invoiced $5,000 for software licenses in January 2024.",
     {"doc_id": "doc_001", "source_type": "invoice", "file_name": "inv001.pdf",
      "file_path": "/invoices/inv001.pdf", "location_label": "invoice INV-001",
      "sender": "Acme Corp", "title": "Invoice from Acme Corp #INV-001"}),
    ("The Q4 earnings report shows revenue grew 15% year-over-year.",
     {"doc_id": "doc_002", "source_type": "pdf", "file_name": "q4_report.pdf",
      "file_path": "/docs/q4_report.pdf", "location_label": "page 3 of 20",
      "sender": "Finance Dept", "title": "Q4 Earnings Report — page 3"}),
    ("Alice: Did you approve the payment to Beta Ltd? Bob: Yes, processed yesterday.",
     {"doc_id": "doc_003", "source_type": "whatsapp", "file_name": "chat.txt",
      "file_path": "/exports/chat.txt", "location_label": "msg 5 at 1/15/2024 10:05 AM",
      "sender": "Alice", "title": "WhatsApp: chat.txt"}),
    ("Vendor Beta Ltd has an outstanding balance of $12,500 due February 2024.",
     {"doc_id": "doc_004", "source_type": "excel", "file_name": "ledger.xlsx",
      "file_path": "/data/ledger.xlsx", "location_label": "sheet: Payments",
      "sender": None, "title": "ledger.xlsx — Payments"}),
    ("The CEO discussed international expansion plans in the Q1 strategy meeting.",
     {"doc_id": "doc_005", "source_type": "audio", "file_name": "meeting.mp3",
      "file_path": "/calls/meeting.mp3", "location_label": "00:05:12-00:07:45",
      "sender": "SPEAKER_00", "title": "meeting.mp3 — SPEAKER_00 at 00:05:12"}),
]


# ── Phase 5: Embedder tests ───────────────────────────────────

import pytest

@pytest.fixture(scope="module")
def embedder_and_dim():
    emb = Embedder(model_name=TEST_MODEL)
    dim = emb.dimension
    assert dim > 0, "Embedding dimension must be > 0"
    return emb, dim

@pytest.fixture(scope="module")
def embedder(embedder_and_dim):
    return embedder_and_dim[0]

@pytest.fixture(scope="module")
def dim(embedder_and_dim):
    return embedder_and_dim[1]


def test_embedder_encode(embedder: Embedder, dim: int):
    texts = ["Hello world", "Invoice for $5,000", ""]
    vecs = embedder.encode(texts)
    assert len(vecs) == 3
    assert len(vecs[0]) == dim
    assert len(vecs[1]) == dim
    assert all(v == 0.0 for v in vecs[2]), "Empty string should produce zero vector"
    # Verify vectors are different for different texts
    assert vecs[0] != vecs[1], "Different texts should have different embeddings"
    print("  [PASS] Embedder.encode: shapes correct, empty=zeros, distinct texts differ")


def test_embedder_batch(embedder: Embedder, dim: int):
    texts = ["text %d" % i for i in range(150)]
    vecs = embedder.encode(texts)
    assert len(vecs) == 150
    assert all(len(v) == dim for v in vecs)
    print("  [PASS] Embedder batch (150 texts): all vectors correct shape")


# ── Phase 6: VectorStore tests ────────────────────────────────

def _run_store_tests(store, chunks: list, label: str) -> None:
    # Add
    store.add(chunks)
    count = store.count()
    assert count == len(chunks), "%s: expected %d chunks, got %d" % (label, len(chunks), count)
    print("  [PASS] %s add(): %d chunks stored" % (label, count))

    # Query — semantic search for "invoice payment vendor"
    query_vec = chunks[0]["embedding"]  # use first chunk's embedding
    results = store.query(query_vec, top_k=3)
    assert len(results) >= 1, "%s: query returned no results" % label
    assert "text" in results[0]
    assert "metadata" in results[0]
    assert "score" in results[0]
    assert results[0]["metadata"]["file_name"], "%s: file_name missing from metadata" % label
    assert results[0]["metadata"]["location_label"], "%s: location_label missing" % label
    print("  [PASS] %s query(): top result = %r (score=%.3f)" % (
        label, results[0]["metadata"]["file_name"], results[0]["score"]
    ))

    # Filter query
    results_filtered = store.query(query_vec, top_k=5, filters={"source_type": "invoice"})
    for r in results_filtered:
        assert r["metadata"]["source_type"] == "invoice", (
            "%s: filter not applied, got source_type=%r" % (label, r["metadata"]["source_type"])
        )
    print("  [PASS] %s filtered query (source_type=invoice): %d results" % (label, len(results_filtered)))

    # Upsert idempotency — add same chunks again
    store.add(chunks)
    count_after_upsert = store.count()
    assert count_after_upsert == len(chunks), (
        "%s: upsert should not duplicate, got %d" % (label, count_after_upsert)
    )
    print("  [PASS] %s upsert idempotency: count stable at %d" % (label, count_after_upsert))

    # Delete
    doc_id_to_delete = chunks[0]["metadata"]["doc_id"]
    store.delete_by_doc_id(doc_id_to_delete)
    count_after_delete = store.count()
    assert count_after_delete == len(chunks) - 1, (
        "%s: after delete expected %d, got %d" % (label, len(chunks) - 1, count_after_delete)
    )
    print("  [PASS] %s delete_by_doc_id: count now %d" % (label, count_after_delete))


def test_chroma_store(embedder: Embedder, dim: int):
    chunks = _make_chunks(embedder, SAMPLE_TEXTS)
    tmp_obj = tempfile.TemporaryDirectory()
    try:
        chroma_dir = str(Path(tmp_obj.name) / "chroma")
        store = ChromaVectorStore(persist_dir=chroma_dir, collection_name="test_rag")
        _run_store_tests(store, chunks, "Chroma")
        # Explicitly close Chroma client before tempdir cleanup (Windows file locks)
        if store._client is not None:
            try:
                store._client.reset()  # flush and release file handles
            except Exception:
                pass
    finally:
        try:
            tmp_obj.cleanup()
        except Exception:
            pass   # Windows may still hold handles; ignore cleanup errors in tests


def test_faiss_store(embedder: Embedder, dim: int):
    chunks = _make_chunks(embedder, SAMPLE_TEXTS)
    with tempfile.TemporaryDirectory() as tmp:
        index_path = str(Path(tmp) / "test.faiss")
        store = FaissVectorStore(index_path=index_path, dim=dim)
        _run_store_tests(store, chunks, "FAISS")


def test_factory_chroma(dim: int):
    config = {"vector_store": {"backend": "chroma", "chroma_persist_dir": "./chroma_db", "collection_name": "rag_chunks"}}
    with tempfile.TemporaryDirectory() as tmp:
        config["vector_store"]["chroma_persist_dir"] = tmp
        store = get_vector_store(config, embedder_dim=dim)
        assert isinstance(store, ChromaVectorStore)
    print("  [PASS] get_vector_store factory returns ChromaVectorStore for backend=chroma")


def test_factory_faiss(dim: int):
    with tempfile.TemporaryDirectory() as tmp:
        config = {"vector_store": {"backend": "faiss", "faiss_index_path": str(Path(tmp) / "idx.faiss")}}
        store = get_vector_store(config, embedder_dim=dim)
        assert isinstance(store, FaissVectorStore)
    print("  [PASS] get_vector_store factory returns FaissVectorStore for backend=faiss")


def run_all():
    print("\n=== Phase 5: Embedder Verification ===\n")
    p5_passed = p5_failed = 0
    try:
        # Cannot easily run test_embedder_loads here anymore since it's a fixture,
        # but for script mode, we can just instantiate Embedder directly
        embedder = Embedder(model_name=TEST_MODEL)
        dim = embedder.dimension
        print("  [PASS] Embedder loaded. Dimension: %d" % dim)
        p5_passed += 1
    except Exception as e:
        print("  [FAIL] test_embedder_loads: %s" % e)
        p5_failed += 1
        print("\nCannot continue without embedder. Aborting.")
        return False

    for fn in (test_embedder_encode, test_embedder_batch):
        try:
            fn(embedder, dim)
            p5_passed += 1
        except Exception as e:
            print("  [FAIL] %s: %s" % (fn.__name__, e))
            p5_failed += 1

    print("\n=== Phase 6: Vector Store Verification ===\n")
    p6_passed = p6_failed = 0
    vs_tests = [
        (test_chroma_store, (embedder, dim)),
        (test_faiss_store, (embedder, dim)),
        (test_factory_chroma, (dim,)),
        (test_factory_faiss, (dim,)),
    ]
    for fn, args in vs_tests:
        try:
            fn(*args)
            p6_passed += 1
        except Exception as e:
            print("  [FAIL] %s: %s" % (fn.__name__, e))
            p6_failed += 1

    total_passed = p5_passed + p6_passed
    total_failed = p5_failed + p6_failed
    print("\nResults: %d passed, %d failed" % (total_passed, total_failed))
    return total_failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
