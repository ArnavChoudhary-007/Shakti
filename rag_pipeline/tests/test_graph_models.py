import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main
from rag_pipeline import settings
from rag_pipeline.core.generator import GenerationError, Generator
from rag_pipeline.core.inference_scheduler import InferenceScheduler
from rag_pipeline.core.kg_builder import build_graph_layout
from rag_pipeline.structured_db.db import StructuredDB


@pytest.fixture()
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "get_data_dir", lambda: tmp_path)
    monkeypatch.setitem(api_main._CONFIG, "ollama", {"default_text_model": "chat-large:8b", "graph_model": "graph-small:1b"})
    return tmp_path


def test_graph_model_is_persisted_independently_of_chat(isolated_settings):
    client = TestClient(api_main.app)
    original = client.get("/settings").json()
    assert original["effective_graph_model"] == "graph-small:1b"
    assert original["effective_chat_model"] == "chat-large:8b"
    assert client.put("/settings", json={"chat_model": "chat-other:4b"}).status_code == 200
    changed = client.put("/settings", json={"graph_model": "graph-other:1b"}).json()
    assert changed["effective_chat_model"] == "chat-other:4b"
    assert changed["effective_graph_model"] == "graph-other:1b"
    assert json.loads((isolated_settings / "settings.json").read_text())["graph_model"] == "graph-other:1b"
    reset = client.put("/settings", json={"graph_model": None}).json()
    assert reset["effective_graph_model"] == "graph-small:1b"
    assert reset["effective_chat_model"] == "chat-other:4b"
    assert client.put("/settings", json={"graph_model": ""}).status_code == 422


def test_old_settings_default_to_a_separate_small_graph_model(isolated_settings):
    (isolated_settings / "settings.json").write_text(json.dumps({"chat_model": "phi3:mini"}))
    assert settings.get_graph_model({"ollama": {"default_text_model": "chat:8b"}}) == "llama3.2:1b"
    assert settings.get_chat_model({}) == "phi3:mini"


@pytest.mark.parametrize("offline", [False, True])
def test_missing_graph_model_fails_before_starting_and_releases_lock(monkeypatch, isolated_settings, offline):
    def inventory(host):
        if offline:
            raise requests.ConnectionError("offline")
        return [{"name": "chat-large:8b"}]

    monkeypatch.setattr(api_main, "get_installed_models", inventory)
    monkeypatch.setattr(api_main, "_kg_locks", {})
    monkeypatch.setattr(api_main, "_kg_jobs", {})

    async def scenario():
        with pytest.raises(HTTPException) as failure:
            await api_main.build_knowledge_graph(workspace_id="work", min_edge_weight=1)
        assert failure.value.status_code == (503 if offline else 409)
        assert not api_main._kg_locks["work"].locked()
        assert "work" not in api_main._kg_jobs

    asyncio.run(scenario())


def test_build_pins_graph_model_and_reports_chat_pause(monkeypatch, isolated_settings, tmp_path):
    received = []
    scheduler = InferenceScheduler()
    monkeypatch.setattr(api_main, "_inference_scheduler", scheduler)
    monkeypatch.setattr(api_main, "_kg_locks", {})
    monkeypatch.setattr(api_main, "_kg_jobs", {})
    monkeypatch.setattr(api_main, "get_installed_models", lambda host: [{"name": "graph-small:1b"}])
    monkeypatch.setattr(api_main, "_get_struct_db", lambda: StructuredDB(str(tmp_path / "graph.db")))

    async def build(**kwargs):
        received.append(kwargs)
        kwargs["on_progress"]("extracting", 2, 5)

    monkeypatch.setattr(api_main, "build_graph_layout", build)

    async def scenario():
        async with scheduler.chat():
            result = await api_main.build_knowledge_graph(workspace_id="work", min_edge_weight=1)
            assert result["model"] == "graph-small:1b"
            assert result["paused_for_chat"]
            settings.update_settings({"graph_model": "different:1b", "chat_model": "chat-new:8b"})
            await asyncio.gather(*api_main._kg_tasks)
        assert received[0]["model"] == "graph-small:1b"
        assert received[0]["run_inference"] == scheduler.background
        assert api_main._kg_job("work")["state"] == "done"
        assert not api_main._kg_job("work")["paused_for_chat"]

    asyncio.run(scenario())


@pytest.mark.parametrize("streaming,error", [(False, False), (False, True), (True, False), (True, True)])
def test_query_holds_chat_priority_through_retrieval_and_releases_on_error(monkeypatch, isolated_settings, streaming, error):
    scheduler = InferenceScheduler()
    generator = Generator(api_main._CONFIG)
    monkeypatch.setattr(api_main, "_inference_scheduler", scheduler)
    monkeypatch.setattr(api_main, "_get_generator", lambda: generator)

    async def gather(*args):
        assert scheduler.chatting
        if error:
            raise GenerationError("model unavailable")
        return [], False, SimpleNamespace(reason="document search")

    monkeypatch.setattr(api_main, "_gather_context", gather)

    async def scenario():
        request = api_main.QueryRequest(query="Question", stream=streaming)
        if streaming:
            events = [event async for event in api_main._stream_answer(request, "chat-large:8b")]
            assert any(("event: error" if error else "event: done") in event for event in events)
        elif error:
            with pytest.raises(HTTPException):
                await api_main.query(request)
        else:
            await api_main.query(request)
        assert not scheduler.chatting

    asyncio.run(scenario())


def test_closing_stream_releases_graph_pause(monkeypatch, isolated_settings):
    scheduler = InferenceScheduler()
    monkeypatch.setattr(api_main, "_inference_scheduler", scheduler)
    monkeypatch.setattr(api_main, "_get_generator", lambda: Generator(api_main._CONFIG))

    async def scenario():
        stream = api_main._stream_answer(api_main.QueryRequest(query="Question"), "chat-large:8b")
        await stream.__anext__()
        assert scheduler.chatting
        await stream.aclose()
        assert not scheduler.chatting

    asyncio.run(scenario())


def test_cancelling_query_releases_graph_pause(monkeypatch, isolated_settings):
    scheduler = InferenceScheduler()
    monkeypatch.setattr(api_main, "_inference_scheduler", scheduler)
    monkeypatch.setattr(api_main, "_get_generator", lambda: Generator(api_main._CONFIG))

    async def scenario():
        entered = asyncio.Event()

        async def gather(*args):
            assert scheduler.chatting
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(api_main, "_gather_context", gather)
        question = asyncio.create_task(api_main.query(api_main.QueryRequest(query="Question")))
        await entered.wait()
        question.cancel()
        with pytest.raises(asyncio.CancelledError):
            await question
        assert not scheduler.chatting

    asyncio.run(scenario())


@pytest.mark.parametrize("pause_stage", ["extracting", "labelling"])
def test_real_extraction_retries_interrupted_piece_and_schedules_labels(monkeypatch, tmp_path, pause_stage):
    scheduler = InferenceScheduler()
    database = StructuredDB(str(tmp_path / "graph.db"))
    database.add_kg_pending_doc("piece", "work", "notes.txt", "text", "Alice and Bob build Shakti.")
    started = None
    calls = []
    client_type = httpx.AsyncClient
    edges = [{"source": person, "target": "Shakti", "relation": "builds"} for person in ("Alice", "Bob")]

    async def capture(request):
        body = json.loads(request.content)
        calls.append(body)
        extracting = body["prompt"].startswith("Extract key entities")
        if not started.is_set() and extracting == (pause_stage == "extracting"):
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"response": json.dumps(edges) if extracting else "Software team"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(capture), **kwargs))

    async def scenario():
        nonlocal started
        started = asyncio.Event()
        progress = []
        build = asyncio.create_task(build_graph_layout("work", database, host="http://ollama.test", model="graph-small:1b",
            run_inference=scheduler.background, on_progress=lambda *args: progress.append(args)))
        await started.wait()
        async with scheduler.chat():
            assert len(database.get_pending_kg_docs("work")) == (1 if pause_stage == "extracting" else 0)
            assert (("extracting", 1, 1) in progress) == (pause_stage == "labelling")
        await build
        assert not database.get_pending_kg_docs("work")
        assert len(calls) == 3
        assert all(call["model"] == "graph-small:1b" for call in calls)
        graph = database.get_knowledge_graph("work")
        assert len(graph["edges"]) == 2
        assert graph["communities"][0]["label"] == "Software team"

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
