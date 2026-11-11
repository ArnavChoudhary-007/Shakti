"""
tests/test_api_e2e.py
End-to-end API test: upload → documents list → dedupe → workspace isolation
→ streaming answers (document and SQL routes) → delete → clear.

Runs against a temporary data directory with a FAISS index, and replaces the
Ollama calls with a stub, so it needs neither Ollama nor the real database.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main
from rag_pipeline.core.generator import Generator

SAMPLES = Path(__file__).resolve().parents[2] / "sample_data"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main._CONFIG, "structured_db", {"sqlite_path": str(tmp_path / "structured.db")})
    monkeypatch.setitem(api_main._CONFIG, "vector_store", {
        "backend": "faiss", "faiss_index_path": str(tmp_path / "index.faiss"),
    })
    monkeypatch.setattr(api_main, "_UPLOAD_DIR", tmp_path / "uploads")
    for name in ("_vector_store", "_struct_db", "_retriever"):
        monkeypatch.setattr(api_main, name, None)

    async def fake_stream(self, query, chunks, model=None):
        for token in ["BrightPack ", "Supplies ", "[1]."]:
            yield token

    async def fake_complete(self, model, prompt, **kwargs):
        return "BrightPack Supplies [1]."

    monkeypatch.setattr(Generator, "generate_stream", fake_stream)
    monkeypatch.setattr(Generator, "_complete", fake_complete)
    yield TestClient(api_main.app)


def _upload(client: TestClient, name: str, workspace: str) -> Dict[str, Any]:
    with open(SAMPLES / name, "rb") as f:
        r = client.post("/ingest", files={"file": (name, f)}, data={"workspace_id": workspace})
    assert r.status_code == 202, r.text
    # TestClient runs background tasks before returning, so the job is finished.
    job = client.get(f"/ingest/status/{r.json()['job_id']}").json()
    assert job["status"] == "done", job
    return job


def _sse_events(text: str) -> List[Dict[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append({"event": lines["event"], "data": json.loads(lines["data"])})
    return events


def _docs(client: TestClient, workspace: str) -> List[Dict[str, Any]]:
    return client.get("/documents", params={"workspace_id": workspace}).json()["documents"]


def test_full_flow(client):
    chat = _upload(client, "team_chat.txt", "w1")
    assert chat["source_types"] == ["whatsapp"] and chat["chunk_count"] > 0
    _upload(client, "expense_ledger.csv", "w1")
    _upload(client, "vendor_evaluation.pdf", "w1")
    assert {d["file_name"] for d in _docs(client, "w1")} == {
        "team_chat.txt", "expense_ledger.csv", "vendor_evaluation.pdf",
    }
    total_chunks = client.get("/docs_count").json()["chunk_count"]

    # Same bytes again → skipped, nothing duplicated.
    again = _upload(client, "team_chat.txt", "w1")
    assert again["duplicate"] is True
    assert client.get("/docs_count").json()["chunk_count"] == total_chunks
    assert len(_docs(client, "w1")) == 3

    # Workspace isolation: w2 only sees its own file.
    _upload(client, "team_chat.txt", "w2")
    preview = client.post("/prompt_preview", json={"query": "packaging vendor", "workspace_id": "w2"}).json()
    assert preview["chunk_count"] > 0
    assert "expense_ledger.csv" not in preview["prompt"]
    assert "vendor_evaluation.pdf" not in preview["prompt"]

    # Streaming answer over documents: status → tokens → done with citations.
    r = client.post("/query", json={
        "query": "Which vendor did the team decide to go with?", "workspace_id": "w1", "stream": True,
    })
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(r.text)
    assert [e["event"] for e in events][0] == "status"
    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == "BrightPack Supplies [1]."
    done = events[-1]
    assert done["event"] == "done"
    assert done["data"]["used_sql"] is False
    assert done["data"]["citations"][0]["index"] == 1

    # A ledger question routes to SQL and still streams.
    r = client.post("/query", json={
        "query": "What is the total amount of expenses in the ledger?", "workspace_id": "w1", "stream": True,
    })
    done = _sse_events(r.text)[-1]
    assert done["event"] == "done" and done["data"]["used_sql"] is True

    # Non-streaming JSON still works.
    r = client.post("/query", json={"query": "Which vendor was chosen?", "workspace_id": "w1"})
    assert r.status_code == 200 and r.json()["citations"]

    # Delete one document: gone from the list and from the vector store.
    chat_doc = next(d for d in _docs(client, "w1") if d["file_name"] == "team_chat.txt")
    assert client.delete(f"/documents/{chat_doc['file_key']}").status_code == 200
    assert "team_chat.txt" not in {d["file_name"] for d in _docs(client, "w1")}
    # (w2's copy of the chat was added, w1's copy removed.)
    assert client.get("/docs_count").json()["chunk_count"] == total_chunks

    # Clearing w1 leaves w2 intact.
    assert client.delete("/system/clear", params={"workspace_id": "w1"}).status_code == 200
    assert _docs(client, "w1") == []
    assert len(_docs(client, "w2")) == 1


def test_changed_file_replaces_old_version(client, tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("The project codename is Falcon.", encoding="utf-8")
    with open(src, "rb") as f:
        client.post("/ingest", files={"file": ("notes.txt", f)}, data={"workspace_id": "w"})
    src.write_text("The project codename is Osprey.", encoding="utf-8")
    with open(src, "rb") as f:
        r = client.post("/ingest", files={"file": ("notes.txt", f)}, data={"workspace_id": "w"})
    job = client.get(f"/ingest/status/{r.json()['job_id']}").json()
    assert job["replaced"] == 1
    docs = _docs(client, "w")
    assert len(docs) == 1
    preview = client.post("/prompt_preview", json={"query": "project codename", "workspace_id": "w"}).json()
    assert "Osprey" in preview["prompt"] and "Falcon" not in preview["prompt"]


def test_unsupported_file_fails_cleanly(client, tmp_path):
    bad = tmp_path / "old.doc"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 legacy")
    with open(bad, "rb") as f:
        r = client.post("/ingest", files={"file": ("old.doc", f)}, data={"workspace_id": "w"})
    job = client.get(f"/ingest/status/{r.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert ".docx" in job["error"]
    assert _docs(client, "w") == []
    leftovers = [p for p in (tmp_path / "uploads").rglob("*") if p.is_file()]
    assert leftovers == []


@pytest.mark.parametrize("failure_point", ["vectors_before", "vectors_after", "ledger", "queue", "document"])
@pytest.mark.parametrize("replace_existing", [False, True])
def test_failed_upload_preserves_old_data_and_removes_partial_writes(client, monkeypatch, failure_point, replace_existing):
    name = "payments.csv"
    original = b"Vendor,Amount\nFalcon,100\nFalcon,200\n"
    replacement = b"Vendor,Amount\nOsprey,300\nOsprey,400\n"

    def upload(data, workspace="w"):
        response = client.post("/ingest", files={"file": (name, data)}, data={"workspace_id": workspace})
        assert response.status_code == 202
        return client.get(f"/ingest/status/{response.json()['job_id']}").json()

    assert upload(original, "other")["status"] == "done"
    if replace_existing:
        assert upload(original)["status"] == "done"
    database = api_main._get_struct_db()
    store = api_main._get_vector_store()
    if replace_existing:
        database.upsert_kg_data({"edges": [{"source": "Falcon", "target": "Payments", "relation": "received"}]},
                                source_doc=name, workspace_id="w")
    old_documents = _docs(client, "w")
    old_metadata = list(store.iter_metadata())
    old_rows = database.query_ledger(workspace_id="w")
    old_pending = database.get_pending_kg_docs("w")
    old_graph = database.get_knowledge_graph("w")
    old_files = {str(path): path.read_bytes() for path in api_main._UPLOAD_DIR.rglob("*") if path.is_file()}
    target, method = {
        "vectors_before": (store, "add"), "vectors_after": (store, "add"),
        "ledger": (database, "upsert_ledger_records"),
        "queue": (database, "add_kg_pending_doc"), "document": (database, "add_document"),
    }[failure_point]
    original_method = getattr(target, method)

    def fail(*args, **kwargs):
        if failure_point not in ("vectors_before", "document"):
            original_method(*args, **kwargs)
        raise OSError("simulated storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(target, method, fail)
        job = upload(replacement)
    assert job["status"] == "failed"
    assert _docs(client, "w") == old_documents
    assert list(store.iter_metadata()) == old_metadata
    assert database.query_ledger(workspace_id="w") == old_rows
    assert database.get_pending_kg_docs("w") == old_pending
    assert database.get_knowledge_graph("w") == old_graph
    assert {str(path): path.read_bytes() for path in api_main._UPLOAD_DIR.rglob("*") if path.is_file()} == old_files
    assert len(_docs(client, "other")) == 1
    retry = upload(replacement)
    assert retry["status"] == "done" and retry["replaced"] == int(replace_existing)
    assert len(_docs(client, "w")) == 1
    assert {row["row_data"]["Vendor"] for row in database.query_ledger(workspace_id="w")} == {"Osprey"}
    assert all(row["doc_id"].startswith(retry["file_key"]) for row in database.get_pending_kg_docs("w"))


def test_replacing_legacy_document_keeps_new_vectors_and_queue(client):
    response = client.post("/ingest", files={"file": ("notes.txt", b"The codename is Falcon.")}, data={"workspace_id": "w"})
    old = client.get(f"/ingest/status/{response.json()['job_id']}").json()
    database = api_main._get_struct_db()
    with database._connect() as connection:
        connection.execute("UPDATE documents SET legacy = 1 WHERE file_key = ?", (old["file_key"],))
    response = client.post("/ingest", files={"file": ("notes.txt", b"The codename is Osprey.")}, data={"workspace_id": "w"})
    new = client.get(f"/ingest/status/{response.json()['job_id']}").json()
    assert new["status"] == "done" and new["replaced"] == 1
    assert {meta["file_key"] for meta in api_main._get_vector_store().iter_metadata()} == {new["file_key"]}
    assert [doc["file_key"] for doc in _docs(client, "w")] == [new["file_key"]]
    assert all(row["doc_id"].startswith(new["file_key"]) for row in database.get_pending_kg_docs("w"))
    assert database.get_pending_kg_docs("w")


def test_old_version_cleanup_failure_keeps_committed_replacement(client, monkeypatch):
    response = client.post("/ingest", files={"file": ("notes.txt", b"The codename is Falcon.")}, data={"workspace_id": "w"})
    assert client.get(f"/ingest/status/{response.json()['job_id']}").json()["status"] == "done"

    def fail_cleanup(*args, **kwargs):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(api_main, "_delete_document", fail_cleanup)
    response = client.post("/ingest", files={"file": ("notes.txt", b"The codename is Osprey.")}, data={"workspace_id": "w"})
    assert client.get(f"/ingest/status/{response.json()['job_id']}").json()["status"] == "failed"
    documents = _docs(client, "w")
    assert len(documents) == 2
    for document in documents:
        assert (api_main._UPLOAD_DIR / document["file_key"] / "notes.txt").is_file()
