import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main
import rag_pipeline.core.retriever as retriever_module
from rag_pipeline.core.generator import Generator, NOT_FOUND_ANSWER


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("scores,expected", [
    ([-11.1, -11.2], NOT_FOUND_ANSWER),
    ([-11.1], NOT_FOUND_ANSWER),
    ([-0.7, -11.2], "BrightPack [1]."),
    ([-8.0], "BrightPack [1]."),
])
def test_relevance_gate_skips_generation_for_off_topic_questions(monkeypatch, stream, scores, expected):
    chunks = [{
        "text": "The team chose BrightPack.",
        "metadata": {"file_name": "team.txt"},
        "score": 0.95,
        "combined_score": 0.95,
        "rerank_score": score,
    } for score in scores]
    retrieval = Mock(return_value=chunks)
    monkeypatch.setattr(api_main, "_get_retriever", lambda: SimpleNamespace(retrieve=retrieval))
    route = SimpleNamespace(use_sql=False, reason="document search")
    monkeypatch.setattr(api_main, "_get_router", lambda: SimpleNamespace(route=lambda query: route))
    monkeypatch.setitem(api_main._CONFIG, "retrieval", {})
    generator = Generator({})
    generated = []

    async def complete(model, prompt, **kwargs):
        generated.append(prompt)
        return "BrightPack [1]."

    async def generate_stream(query, sources, model=None):
        generated.append(sources)
        yield "BrightPack [1]."

    monkeypatch.setattr(generator, "_complete", complete)
    monkeypatch.setattr(generator, "generate_stream", generate_stream)
    monkeypatch.setattr(api_main, "_get_generator", lambda: generator)
    client = TestClient(api_main.app)
    response = client.post("/query", json={
        "query": "Which vendor did the team choose?", "stream": stream,
        "model": "test-model", "workspace_id": "test-workspace",
    })
    assert response.status_code == 200
    if stream:
        events = [dict(line.split(": ", 1) for line in block.splitlines())
                  for block in response.text.strip().split("\n\n")]
        assert events[-1]["event"] == "done"
        result = json.loads(events[-1]["data"])
        if expected == NOT_FOUND_ANSWER:
            assert not any(event["event"] == "token" for event in events)
    else:
        result = response.json()
    assert result["answer"] == expected
    assert bool(generated) == (expected != NOT_FOUND_ANSWER)
    assert len(result["citations"]) == (0 if expected == NOT_FOUND_ANSWER else 1)
    assert retrieval.call_args.kwargs["filters"] == {"workspace_id": "test-workspace"}


def test_single_candidate_is_reranked(monkeypatch):
    reranker = SimpleNamespace(predict=Mock(return_value=[-11.1]))
    monkeypatch.setattr(retriever_module, "get_reranker", lambda model: reranker)
    store = SimpleNamespace(query=lambda **kwargs: [{"text": "unrelated", "score": 0.8}])
    embedder = SimpleNamespace(encode_single=lambda query: [0.1])
    retriever = retriever_module.Retriever(store, embedder, use_bm25=False)
    chunks = retriever.retrieve("What is the capital of France?")
    assert chunks[0]["rerank_score"] == -11.1
    reranker.predict.assert_called_once_with([("What is the capital of France?", "unrelated")])
