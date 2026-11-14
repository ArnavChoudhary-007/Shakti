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
    "wrong_shape", "invalid_edge",
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


def _wait_for_build(client, workspace, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/knowledge_graph?workspace_id={workspace}").json()
        if body["build"]["state"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("graph build did not finish")


def test_graph_build_runs_in_background_and_reports_retryable_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(api_main.user_settings, "get_graph_model", lambda config: "graph-small:1b")
    monkeypatch.setattr(api_main, "get_installed_models", lambda host: [{"name": "graph-small:1b"}])
    received = []

    async def fail_build(**kwargs):
        received.append(kwargs)
        kwargs["on_progress"]("extracting", 1, 3)
        raise GenerationError("Jobs are still queued. Build again to retry.")

    db = StructuredDB(str(tmp_path / "g.db"))
    monkeypatch.setattr(api_main, "build_graph_layout", fail_build)
    monkeypatch.setattr(api_main, "_get_struct_db", lambda: db)
    monkeypatch.setattr(api_main, "_kg_locks", {})
    monkeypatch.setattr(api_main, "_kg_jobs", {})
    monkeypatch.setitem(api_main._CONFIG, "ollama", {"request_timeout": 123, "keep_alive": "5m", "num_ctx": 4096})
    with TestClient(api_main.app) as client:
        response = client.post("/knowledge_graph/build?workspace_id=work")
        assert response.status_code == 202          # returns at once; the build runs in the background
        body = _wait_for_build(client, "work")
    assert body["build"]["state"] == "error"
    assert "still queued" in body["build"]["error"]
    assert (body["build"]["done"], body["build"]["total"]) == (1, 3)
    assert body["building"] is False
    assert received[0]["timeout"] == 123.0
    assert received[0]["model"] == "graph-small:1b"
    assert received[0]["run_inference"] == api_main._inference_scheduler.background
    assert (received[0]["keep_alive"], received[0]["num_ctx"]) == ("5m", 4096)
    assert not api_main._kg_locks["work"].locked()


def test_second_build_while_running_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(api_main.user_settings, "get_graph_model", lambda config: "graph-small:1b")
    monkeypatch.setattr(api_main, "get_installed_models", lambda host: [{"name": "graph-small:1b"}])
    import threading
    release = threading.Event()

    async def slow_build(**kwargs):
        while not release.is_set():
            await asyncio.sleep(0.01)

    db = StructuredDB(str(tmp_path / "g.db"))
    monkeypatch.setattr(api_main, "build_graph_layout", slow_build)
    monkeypatch.setattr(api_main, "_get_struct_db", lambda: db)
    monkeypatch.setattr(api_main, "_kg_locks", {})
    monkeypatch.setattr(api_main, "_kg_jobs", {})
    with TestClient(api_main.app) as client:
        assert client.post("/knowledge_graph/build?workspace_id=w").status_code == 202
        assert client.post("/knowledge_graph/build?workspace_id=w").status_code == 409
        assert client.get("/knowledge_graph?workspace_id=w").json()["building"] is True
        release.set()
        assert _wait_for_build(client, "w")["build"]["state"] == "done"


def test_a_files_queued_parts_are_packed_into_few_calls(database, mock_ollama):
    # A chat export queues one row per message window; 60 rows of ~600 chars
    # used to mean 60 model calls.
    for i in range(60):
        database.add_kg_pending_doc(f"chat-{i}", "work", "chat.txt", "whatsapp", f"[{i}] " + "x" * 600)
    database.add_kg_pending_doc("memo", "work", "memo.pdf", "pdf", "Alice builds Shakti.")
    requests = mock_ollama(lambda request: httpx.Response(200, json={"response": json.dumps([EDGE])}))
    progress = []
    asyncio.run(_extract_pending_kg("work", database, "http://ollama.test", "model",
                                    keep_alive="30m", num_ctx=8192,
                                    on_progress=lambda *a: progress.append(a)))
    assert len(requests) == 6          # 5 for the chat (36k chars / 8k) + 1 for the memo
    body = json.loads(requests[0].content)
    assert body["keep_alive"] == "30m" and body["options"]["num_ctx"] == 8192
    assert progress[-1] == ("extracting", 6, 6)
    assert database.get_pending_kg_docs("work") == []


def test_pack_pieces_bounds_calls_but_keeps_start_and_end():
    from rag_pipeline.core.kg_builder import pack_pieces
    texts = [f"part{i:03d} " + "y" * 990 for i in range(200)]   # ~200k chars
    pieces = pack_pieces(texts, piece_chars=8000, max_pieces=12)
    assert len(pieces) == 12
    assert all(len(p) <= 8000 for p in pieces)
    assert pieces[0].startswith("part000") and "part199" in pieces[-1]
    assert pack_pieces(["short"]) == ["short"]
    assert pack_pieces(["", "  "]) == []


def test_output_is_capped_and_a_truncated_answer_keeps_complete_edges(mock_ollama):
    second = {"source": "Bob", "target": "Shakti", "relation": "tests"}
    cut = json.dumps({"edges": [EDGE, second]})[:-25]          # cut inside the second edge
    requests = mock_ollama(lambda request: httpx.Response(200, json={"response": cut, "done_reason": "length"}))
    assert asyncio.run(extract_kg_relationships("notes", host="http://ollama.test")) == [EDGE]
    assert json.loads(requests[0].content)["options"]["num_predict"] == generator_module.KG_MAX_OUTPUT_TOKENS


def test_invalid_json_that_was_not_truncated_still_fails(mock_ollama):
    cut = json.dumps([EDGE, EDGE])[:-5]
    mock_ollama(lambda request: httpx.Response(200, json={"response": cut, "done_reason": "stop"}))
    with pytest.raises(GenerationError):
        asyncio.run(extract_kg_relationships("notes", host="http://ollama.test"))


def test_malformed_edges_are_dropped_and_the_good_ones_kept(database, mock_ollama):
    database.add_kg_pending_doc("doc", "work", "notes.txt", "text", "Alice builds Shakti.")
    answer = json.dumps([EDGE, {"source": "Alice"}, {"source": "A", "target": "B", "relation": 7}])
    mock_ollama(lambda request: httpx.Response(200, json={"response": answer}))
    asyncio.run(_extract_pending_kg("work", database, "http://ollama.test", "model"))
    assert database.get_pending_kg_docs("work") == []
    assert len(database.get_knowledge_graph("work")["edges"]) == 1
