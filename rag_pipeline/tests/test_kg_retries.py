import asyncio
import json
import sqlite3
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main
import rag_pipeline.core.generator as generator_module
from rag_pipeline.core.generator import GenerationError, extract_kg_relationships
from rag_pipeline.core.kg_builder import _extract_pending_kg
from rag_pipeline.structured_db.db import StructuredDB


EDGE = {"source": "Alice", "target": "Shakti", "relation": "builds"}


@pytest.fixture()
def database(tmp_path):
    return StructuredDB(str(tmp_path / "graph.db"))


@pytest.fixture()
def mock_ollama(monkeypatch):
    client_type = httpx.AsyncClient

    def install(handler):
        requests = []

        def capture(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(generator_module.httpx, "AsyncClient", lambda **kwargs: client_type(
            transport=httpx.MockTransport(capture), **kwargs
        ))
        return requests

    return install


@pytest.mark.parametrize("failure", [
    "offline", "timeout", "missing_model", "http_error", "bad_json", "bad_envelope",
    "wrong_shape", "invalid_edge", "partial_edges",
])
def test_failed_extraction_keeps_job_and_can_retry_without_upload(database, mock_ollama, failure):
    database.add_kg_pending_doc("doc", "work", "notes.txt", "text", "Alice builds Shakti.")
    pending = database.get_pending_kg_docs("work")

    def fail(request):
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow model", request=request)
        if failure == "missing_model":
            return httpx.Response(404, json={"error": "model not found"})
        if failure == "http_error":
            return httpx.Response(500, json={"error": "model failed"})
        responses = {
            "bad_json": {"response": "not JSON"},
            "bad_envelope": {},
            "wrong_shape": {"response": '{}'},
            "invalid_edge": {"response": json.dumps([{"source": "Alice"}])},
            "partial_edges": {"response": json.dumps([EDGE, {"source": "Alice"}])},
        }
        return httpx.Response(200, json=responses[failure])

    mock_ollama(fail)
    with pytest.raises(GenerationError, match="still queued"):
        asyncio.run(_extract_pending_kg("work", database, "http://ollama.test", "model"))
    assert database.get_pending_kg_docs("work") == pending
    assert database.get_knowledge_graph("work")["edges"] == []

    requests = mock_ollama(lambda request: httpx.Response(200, json={"response": json.dumps([EDGE])}))
    asyncio.run(_extract_pending_kg("work", database, "http://ollama.test", "model"))
    assert database.get_pending_kg_docs("work") == []
    assert len(database.get_knowledge_graph("work")["edges"]) == 1
    assert len(requests) == 1
    assert requests[0].extensions["timeout"]["read"] == 300.0


@pytest.mark.parametrize("data,expected", [
    ([], []), ({"edges": []}, []), ([EDGE], [EDGE]), ({"edges": [EDGE]}, [EDGE]), (EDGE, [EDGE]),
])
def test_valid_empty_and_nonempty_extractions_succeed(mock_ollama, data, expected):
    requests = mock_ollama(lambda request: httpx.Response(200, json={"response": json.dumps(data)}))
    result = asyncio.run(extract_kg_relationships("notes", host="http://ollama.test", timeout=123.0))
    assert result == expected
    assert requests[0].extensions["timeout"]["read"] == 123.0


def test_mixed_jobs_only_remove_successes_and_keep_other_workspaces(database, mock_ollama):
    for name in ("good", "bad", "empty"):
        database.add_kg_pending_doc(name, "work", f"{name}.txt", "text", name)
    database.add_kg_pending_doc("private", "other", "private.txt", "text", "private")

    def respond(request):
        prompt = json.loads(request.content)["prompt"]
        response = "not JSON" if prompt.endswith("bad") else json.dumps([] if prompt.endswith("empty") else [EDGE])
        return httpx.Response(200, json={"response": response})

    mock_ollama(respond)
    with pytest.raises(GenerationError, match="1 document"):
        asyncio.run(_extract_pending_kg("work", database, "http://ollama.test", "model"))
    assert [row["source_doc"] for row in database.get_pending_kg_docs("work")] == ["bad.txt"]
    assert len(database.get_pending_kg_docs("other")) == 1
    assert len(database.get_knowledge_graph("work")["edges"]) == 1


def test_failed_graph_save_keeps_job(database, mock_ollama, monkeypatch):
    database.add_kg_pending_doc("doc", "work", "notes.txt", "text", "notes")
    mock_ollama(lambda request: httpx.Response(200, json={"response": json.dumps([EDGE])}))
    monkeypatch.setattr(database, "upsert_kg_data", Mock(side_effect=sqlite3.OperationalError("busy")))
    with pytest.raises(GenerationError, match="still queued"):
        asyncio.run(_extract_pending_kg("work", database, "http://ollama.test", "model"))
    assert len(database.get_pending_kg_docs("work")) == 1


def test_graph_build_endpoint_reports_retryable_failure(monkeypatch):
    received = []

    async def fail_build(**kwargs):
        received.append(kwargs)
        raise GenerationError("Jobs are still queued. Build again to retry.")

    monkeypatch.setattr(api_main, "build_graph_layout", fail_build)
    monkeypatch.setattr(api_main, "_get_struct_db", lambda: None)
    monkeypatch.setattr(api_main, "_kg_locks", {})
    monkeypatch.setitem(api_main._CONFIG, "ollama", {"request_timeout": 123})
    response = TestClient(api_main.app).post("/knowledge_graph/build?workspace_id=work")
    assert response.status_code == 502
    assert "still queued" in response.json()["detail"]
    assert received[0]["timeout"] == 123.0
    assert not api_main._kg_locks["work"].locked()
