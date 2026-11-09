"""
tests/test_security.py
The companion API must only obey its own page and the paired public website.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag_pipeline.api.main import app, _WEB_ORIGINS

client = TestClient(app)
EVIL = {"Origin": "https://evil.example"}
WEB_ORIGIN = sorted(_WEB_ORIGINS)[0]
WEB = {"Origin": WEB_ORIGIN}


def _token() -> str:
    return client.get("/pair/token").json()["token"]


def test_local_page_needs_no_token():
    assert client.get("/settings").status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/version"),
    ("GET", "/settings"),
    ("POST", "/ingest"),
    ("DELETE", "/system/clear"),
])
def test_unknown_origin_is_rejected_before_routing(method, path):
    assert client.request(method, path, headers=EVIL).status_code == 403


def test_cross_site_request_without_origin_is_rejected():
    headers = {"Sec-Fetch-Site": "cross-site"}
    assert client.delete("/system/clear", headers=headers).status_code == 403


def test_public_site_can_detect_companion_before_pairing():
    assert client.get("/version", headers=WEB).status_code == 200


def test_public_site_needs_token():
    assert client.get("/settings", headers=WEB).status_code == 401
    bad = {**WEB, "X-Shakti-Token": "wrong"}
    assert client.get("/settings", headers=bad).status_code == 401
    good = {**WEB, "X-Shakti-Token": _token()}
    assert client.get("/settings", headers=good).status_code == 200


def test_public_site_cannot_read_token_even_when_paired():
    headers = {**WEB, "X-Shakti-Token": _token()}
    assert client.get("/pair/token", headers=headers).status_code == 403


def test_preflight_allowed_for_public_site():
    r = client.options("/settings", headers={
        **WEB,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-shakti-token",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == WEB_ORIGIN


def test_dns_rebinding_host_rejected():
    assert client.get("/version", headers={"Host": "evil.example"}).status_code == 400


def test_removed_routes_are_gone():
    assert client.post("/ingest_path", params={"path": "C:/Windows/win.ini"}).status_code in (404, 405)
    assert client.get("/uploads/anything").status_code == 404
