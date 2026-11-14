"""
tests/test_model_loading.py
Model loading must (a) happen once even when requests race, (b) never run on
the event loop, (c) not retry a broken reranker on every question, and (d) be
downloadable up front from the setup page.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main
from rag_pipeline.core import retriever as retriever_module
from rag_pipeline.core import search_models
from rag_pipeline.core.embedder import Embedder


def _race(fn, n=8):
    barrier = threading.Barrier(n)
    results, errors = [], []

    def run():
        barrier.wait()
        try:
            results.append(fn())
        except Exception as e:  # pragma: no cover - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, errors
    return results


# ── Loaded once under concurrency ─────────────────────────────

def test_singleton_factory_runs_once_when_requests_race(monkeypatch):
    monkeypatch.setattr(api_main, "_generator", None)
    calls = []

    def slow_factory():
        calls.append(1)
        time.sleep(0.2)
        return object()

    results = _race(lambda: api_main._singleton("_generator", slow_factory))
    assert len(calls) == 1
    assert len({id(r) for r in results}) == 1


def test_embedder_model_loads_once_when_requests_race(monkeypatch):
    import sentence_transformers

    created = []

    class SlowModel:
        def __init__(self, *args, **kwargs):
            created.append(kwargs)
            time.sleep(0.2)

        def get_sentence_embedding_dimension(self):
            return 7

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", SlowModel)
    emb = Embedder(model_name="not-a-real-model")
    assert _race(lambda: emb.dimension) == [7] * 8
    assert len(created) == 1
    assert emb.is_loaded


# ── Reranker failure is remembered ────────────────────────────

@pytest.fixture()
def fresh_reranker(monkeypatch):
    monkeypatch.setattr(retriever_module, "_reranker", None)
    monkeypatch.setattr(retriever_module, "_reranker_error", None)
    monkeypatch.setattr(retriever_module, "_reranker_failed_at", 0.0)
    attempts = []

    class BrokenCrossEncoder:
        def __init__(self, *args, **kwargs):
            attempts.append(1)
            raise OSError("We couldn't connect to huggingface.co")

    monkeypatch.setattr(retriever_module, "CrossEncoder", BrokenCrossEncoder)
    return attempts


def test_failed_reranker_load_is_not_retried_on_every_question(fresh_reranker):
    for _ in range(3):
        assert retriever_module.get_reranker("cross-encoder/x") is None
    assert len(fresh_reranker) == 1
    assert "huggingface" in retriever_module.reranker_state()["error"]


def test_reranker_retries_after_reset_or_cooldown(fresh_reranker, monkeypatch):
    retriever_module.get_reranker("cross-encoder/x")
    retriever_module.reset_reranker_failure()
    retriever_module.get_reranker("cross-encoder/x")
    assert len(fresh_reranker) == 2

    monkeypatch.setattr(retriever_module, "RERANKER_RETRY_SECONDS", 0.0)
    retriever_module.get_reranker("cross-encoder/x")
    assert len(fresh_reranker) == 3


# ── The event loop stays free ─────────────────────────────────

def test_first_question_loads_models_off_the_event_loop(monkeypatch):
    class FakeRetriever:
        def retrieve(self, query, top_k=None, filters=None):
            return [{"text": "Pricing was $842,000.", "metadata": {}, "rerank_score": 3.0}]

    def slow_get_retriever():
        time.sleep(0.8)          # stands in for loading the embedding model
        return FakeRetriever()

    monkeypatch.setattr(api_main, "_get_retriever", slow_get_retriever)
    req = api_main.QueryRequest(query="What did the memo say about pricing?", workspace_id="w")

    async def scenario():
        task = asyncio.create_task(api_main._gather_context(req, req.query))
        started = time.perf_counter()
        await asyncio.sleep(0.05)              # the task starts loading meanwhile;
        loop_delay = time.perf_counter() - started   # other work must still get the loop
        chunks, used_sql, _ = await task
        return loop_delay, chunks, used_sql

    loop_delay, chunks, used_sql = asyncio.run(scenario())
    assert loop_delay < 0.3, f"event loop was blocked for {loop_delay:.2f}s"
    assert used_sql is False and len(chunks) == 1


def test_health_does_not_load_models(monkeypatch):
    monkeypatch.setattr(api_main, "_embedder", None)
    monkeypatch.setattr(api_main, "_vector_store", None)
    monkeypatch.setattr(api_main, "_OLLAMA_HOST", "http://127.0.0.1:9")

    def boom(self):
        raise AssertionError("/health must not load the embedding model")

    monkeypatch.setattr(Embedder, "_load_model", boom)
    r = TestClient(api_main.app).get("/health")
    assert r.status_code == 200
    assert api_main._embedder is None


# ── Download from the setup page ──────────────────────────────

@pytest.fixture()
def idle_job(monkeypatch):
    monkeypatch.setattr(search_models, "_job", {"state": "idle", "downloaded": 0, "total": None, "error": None})


def _wait_for_job(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = search_models.job_state()
        if state["state"] not in ("downloading", "loading"):
            return state
        time.sleep(0.02)
    raise AssertionError("search model job did not finish")


def test_prepare_downloads_missing_models_then_loads(idle_job, monkeypatch):
    downloaded, loaded = [], []
    monkeypatch.setattr(search_models, "cached_path",
                        lambda repo: "/cache" if repo.startswith("cross-encoder/") else None)
    monkeypatch.setattr(search_models, "_download", lambda repos: downloaded.extend(repos))
    config = {"embedding": {"model_name": "all-MiniLM-L6-v2"},
              "retrieval": {"reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2"}}

    search_models.start_prepare(config, lambda: loaded.append(1))
    assert _wait_for_job()["state"] == "ready"
    assert downloaded == ["sentence-transformers/all-MiniLM-L6-v2"]   # reranker was cached
    assert loaded == [1]


def test_prepare_reports_a_friendly_error_when_offline(idle_job, monkeypatch):
    monkeypatch.setattr(search_models, "cached_path", lambda repo: None)

    def offline(repos):
        raise OSError("Max retries exceeded with url: /api/models/...")

    monkeypatch.setattr(search_models, "_download", offline)
    search_models.start_prepare({}, lambda: None)
    state = _wait_for_job()
    assert state["state"] == "error"
    assert state["error"] == search_models.DOWNLOAD_ERROR


def test_setup_status_reports_search_models(idle_job, monkeypatch):
    monkeypatch.setattr(search_models, "cached_path", lambda repo: None)
    monkeypatch.setattr(api_main, "_OLLAMA_HOST", "http://127.0.0.1:9")
    body = TestClient(api_main.app).get("/setup/status").json()
    sm = body["search_models"]
    assert sm["ready"] is False
    assert {m["kind"] for m in sm["models"]} == {"embedding", "reranker"}
    assert sm["job"]["state"] == "idle"


def test_repo_ids_match_sentence_transformers_naming():
    assert search_models.repo_id("all-MiniLM-L6-v2", "embedding") == "sentence-transformers/all-MiniLM-L6-v2"
    assert search_models.repo_id("cross-encoder/ms-marco-MiniLM-L-6-v2", "reranker") == "cross-encoder/ms-marco-MiniLM-L-6-v2"
