import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main


@pytest.mark.parametrize("model_name", ["llama3.2:latest", "audit/model:latest", "org/team/model:q4_K_M"])
def test_delete_model_preserves_namespaced_name(monkeypatch, model_name):
    requests = []
    client_type = httpx.AsyncClient

    def capture(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "success"})

    monkeypatch.setattr(api_main.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(capture), **kwargs
    ))
    response = TestClient(api_main.app).delete(f"/ollama/models/{quote(model_name, safe='')}")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "deleted"}
    assert len(requests) == 1
    assert requests[0].method == "DELETE"
    assert requests[0].url.path == "/api/delete"
    assert json.loads(requests[0].content) == {"model": model_name}
