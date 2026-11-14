from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.file_access as file_access
import rag_pipeline.api.main as api_main
from rag_pipeline.api.file_access import FileViewTokens
from rag_pipeline.core.generator import _citation_for


@pytest.fixture()
def originals(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main._CONFIG, "structured_db", {"sqlite_path": str(tmp_path / "structured.db")})
    monkeypatch.setattr(api_main, "_UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "_struct_db", None)
    client = TestClient(api_main.app)

    def add(file_key="file-1", name="report.pdf", workspace="default", content=b"%PDF-1.4 original"):
        api_main._get_struct_db().add_document(
            file_key=file_key, workspace_id=workspace, file_name=name, content_hash=file_key,
            size_bytes=len(content), source_types=["pdf"], doc_count=1, chunk_count=1,
        )
        directory = api_main._UPLOAD_DIR / file_key
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(content)
        return path

    return client, add


def test_original_pdf_supports_scoped_navigation_ranges_and_head(originals):
    client, add = originals
    path = add(workspace="project a")
    web_origin = sorted(api_main._WEB_ORIGINS)[0]
    token = client.get("/pair/token").json()["token"]
    response = client.post("/documents/original", json={"file_key": "file-1", "workspace_id": "project a"}, headers={"Origin": web_origin, "X-Shakti-Token": token})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    grant = response.json()
    assert grant["previewable"] and not grant["matched_by_name"]
    assert token not in grant["path"]
    navigation = {"Sec-Fetch-Site": "cross-site"}
    opened = client.get(grant["path"], headers=navigation)
    assert opened.status_code == 200
    assert opened.content == path.read_bytes()
    assert opened.headers["content-type"] == "application/pdf"
    assert opened.headers["content-disposition"].startswith("inline;")
    assert opened.headers["x-content-type-options"] == "nosniff"
    assert opened.headers["referrer-policy"] == "no-referrer"
    assert opened.headers["cache-control"] == "private, no-store"
    ranged = client.get(grant["path"], headers={**navigation, "Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.content == b"%PDF"
    head = client.head(grant["path"], headers=navigation)
    assert head.status_code == 200 and not head.content
    assert head.headers["content-length"] == str(path.stat().st_size)


@pytest.mark.parametrize("name,inline,mime", [
    ("notes.txt", True, "text/plain"), ("notes.md", True, "text/plain"),
    ("data.json", True, "text/plain"), ("photo.png", True, "image/png"),
    ("report.docx", False, "application/octet-stream"),
    ("active.html", False, "application/octet-stream"),
    ("active.svg", False, "application/octet-stream"),
])
def test_safe_preview_types_and_attachment_fallback(originals, name, inline, mime):
    client, add = originals
    add(name=name)
    grant = client.post("/documents/original", json={"file_key": "file-1"}).json()
    assert grant["previewable"] is inline
    response = client.get(grant["path"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(mime)
    assert response.headers["content-disposition"].startswith("inline;" if inline else "attachment;")


def test_older_citations_only_resolve_unique_names_in_their_workspace(originals):
    client, add = originals
    add(workspace="first")
    add(file_key="file-2", workspace="second")
    grant = client.post("/documents/original", json={"file_name": "report.pdf", "workspace_id": "first"}).json()
    assert grant["matched_by_name"]
    assert "/file-1/content?" in grant["path"]
    add(file_key="file-3", workspace="first")
    assert client.post("/documents/original", json={"file_name": "report.pdf", "workspace_id": "first"}).status_code == 409
    assert client.post("/documents/original", json={"file_name": "report.pdf", "workspace_id": "missing"}).status_code == 404


def test_stale_ids_never_fall_back_to_replacement_files(originals):
    client, add = originals
    add()
    assert client.post("/documents/original", json={"file_key": "deleted", "file_name": "report.pdf"}).status_code == 404
    assert client.post("/documents/original", json={"file_key": "file-1", "workspace_id": "other"}).status_code == 404
    assert client.get("/documents/file-1/content?workspace_id=other").status_code == 404
    assert client.post("/documents/original", json={}).status_code == 422


def test_missing_originals_and_deleted_documents_fail_cleanly(originals, tmp_path):
    client, add = originals
    path = add()
    grant = client.post("/documents/original", json={"file_key": "file-1"}).json()
    path.unlink()
    missing = client.get(grant["path"])
    assert missing.status_code == 404
    assert str(tmp_path) not in missing.text
    assert client.post("/documents/original", json={"file_key": "file-1"}).status_code == 404
    path.write_bytes(b"restored")
    with api_main._get_struct_db()._connect() as connection:
        connection.execute("DELETE FROM documents WHERE file_key = ?", ("file-1",))
    assert client.get(grant["path"]).status_code == 404


@pytest.mark.parametrize("name", ["../private.pdf", "..\\private.pdf", "C:private.pdf", "/private.pdf"])
def test_database_filenames_cannot_escape_upload_storage(originals, name):
    client, add = originals
    add()
    with api_main._get_struct_db()._connect() as connection:
        connection.execute("UPDATE documents SET file_name = ? WHERE file_key = ?", (name, "file-1"))
    assert client.post("/documents/original", json={"file_key": "file-1"}).status_code == 404
    assert client.get("/documents/file-1/content").status_code == 404


def test_symlink_escape_is_not_served(originals, tmp_path):
    client, add = originals
    path = add()
    outside = tmp_path / "private.pdf"
    outside.write_bytes(b"private")
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires privileges on this system")
    assert client.get("/documents/file-1/content").status_code == 404


def test_directory_alias_cannot_expose_another_documents_original(originals, monkeypatch):
    client, add = originals
    add()
    other_path = add(file_key="file-2", workspace="private")
    directory = api_main._UPLOAD_DIR / "file-1"
    original_resolve = type(directory).resolve

    def resolve(path, *args, **kwargs):
        if path == directory:
            return original_resolve(other_path.parent)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(type(directory), "resolve", resolve)
    assert client.get("/documents/file-1/content").status_code == 404
    assert client.post("/documents/original", json={"file_key": "file-1"}).status_code == 404


def test_file_grants_do_not_weaken_pairing_or_scope(originals):
    client, add = originals
    add()
    add(file_key="file-2")
    origin = sorted(api_main._WEB_ORIGINS)[0]
    assert client.post("/documents/original", json={"file_key": "file-1"}, headers={"Origin": origin}).status_code == 401
    grant = client.post("/documents/original", json={"file_key": "file-1"}).json()["path"]
    navigation = {"Sec-Fetch-Site": "cross-site"}
    assert client.get("/documents/file-1/content", headers=navigation).status_code == 403
    assert client.get(grant.replace("file-1", "file-2"), headers=navigation).status_code == 403
    assert client.get(grant.replace("workspace_id=default", "workspace_id=other"), headers=navigation).status_code == 403
    assert client.get(grant + "x", headers=navigation).status_code == 403
    assert client.delete(grant, headers=navigation).status_code == 403
    assert client.get("/settings?" + grant.split("?", 1)[1], headers=navigation).status_code == 403
    assert client.get(grant, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get(grant, headers={"Host": "evil.example"}).status_code == 400


def test_tokens_expire_and_are_not_valid_after_process_restart(monkeypatch):
    clock = SimpleNamespace(time=lambda: 1000)
    monkeypatch.setattr(file_access, "time", clock)
    tokens = FileViewTokens(ttl=60)
    token = tokens.issue("file-1", "default")
    assert tokens.valid(token, "file-1", "default")
    assert not FileViewTokens(ttl=60).valid(token, "file-1", "default")
    assert not tokens.valid("bad", "file-1", "default")
    clock.time = lambda: 1060
    assert not tokens.valid(token, "file-1", "default")


def test_citations_retain_file_identity_without_breaking_old_chunks():
    citation = _citation_for(1, {"text": "source", "metadata": {"file_name": "report.pdf", "file_key": "file-1"}})
    assert citation.file_key == "file-1"
    assert api_main.CitationOut(**citation.__dict__).file_key == "file-1"
    assert _citation_for(1, {"text": "older", "metadata": {}}).file_key is None
