"""
tests/test_upload_errors.py
Failed uploads must explain the problem in plain words and never show local
paths or library internals (the page may be the public website).
"""
from __future__ import annotations

import zipfile

import pytest
from fastapi.testclient import TestClient

import rag_pipeline.api.main as api_main
from rag_pipeline.core.embedder import Embedder
from rag_pipeline.core.user_errors import describe_ingest_error


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main._CONFIG, "structured_db", {"sqlite_path": str(tmp_path / "structured.db")})
    monkeypatch.setitem(api_main._CONFIG, "vector_store", {
        "backend": "faiss", "faiss_index_path": str(tmp_path / "index.faiss"),
    })
    monkeypatch.setattr(api_main, "_UPLOAD_DIR", tmp_path / "uploads")
    for name in ("_vector_store", "_struct_db", "_retriever"):
        monkeypatch.setattr(api_main, name, None)
    return TestClient(api_main.app)


def _upload_error(client, tmp_path, name: str, content: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(content)
    with open(path, "rb") as f:
        r = client.post("/ingest", files={"file": (name, f)}, data={"workspace_id": "w"})
    job = client.get(f"/ingest/status/{r.json()['job_id']}").json()
    assert job["status"] == "failed", job
    return job["error"]


def _assert_clean(message: str, tmp_path) -> None:
    for leak in (str(tmp_path), "C:\\", "/tmp", "uploads", "_incoming", "Traceback", "EXTENSION_MAP", "source_type"):
        assert leak not in message, f"{leak!r} leaked into: {message}"


@pytest.mark.parametrize("name,content,expected", [
    ("setup.exe", b"MZ\x90\x00", "can't read .exe files"),
    ("noext", b"hello", "files without an extension"),
    ("corrupt.pdf", b"%PDF-1.4 this is not really a pdf", "looks damaged"),
    ("fake.docx", b"plain text, not a zip", "isn't a valid Word file"),
    ("fake.pptx", b"plain text, not a zip", "isn't a valid PowerPoint file"),
    ("broken.json", b"{not json", "isn't valid JSON"),
    ("old.doc", b"\xd0\xcf\x11\xe0 legacy", "save it as .docx"),
])
def test_upload_errors_are_plain_and_leak_no_paths(client, tmp_path, name, content, expected):
    message = _upload_error(client, tmp_path, name, content)
    assert expected in message
    _assert_clean(message, tmp_path)


def test_zip_that_isnt_a_docx(client, tmp_path):
    path = tmp_path / "payload.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("readme.txt", "hi")
    message = _upload_error(client, tmp_path, "report.docx", path.read_bytes())
    assert "isn't a valid Word file" in message
    _assert_clean(message, tmp_path)


def test_model_failure_points_to_setup_not_the_file(client, tmp_path, monkeypatch):
    def offline(self):
        raise OSError(f"We couldn't connect to 'https://huggingface.co' to load {tmp_path}\\model")

    monkeypatch.setattr(Embedder, "_load_model", offline)
    monkeypatch.setattr(api_main, "_embedder", None)
    message = _upload_error(client, tmp_path, "notes.txt", b"The codename is Falcon.")
    assert "search models" in message and "Setup" in message
    _assert_clean(message, tmp_path)


def test_empty_file_keeps_its_specific_message(client, tmp_path):
    message = _upload_error(client, tmp_path, "empty.txt", b"   ")
    assert "Couldn't find any text in empty.txt" in message


@pytest.mark.parametrize("exc,name,stage,expected", [
    (MemoryError(), "big.pdf", "reading", "enough memory"),
    (RuntimeError("PST support requires libpff-python."), "mail.pst", "reading", ".mbox"),
    (RuntimeError("faster-whisper not installed."), "talk.mp3", "reading", "Audio transcription isn't installed"),
    (RuntimeError("Failed to open file 'C:\\x\\a.pdf': password required"), "a.pdf", "reading", "password-protected"),
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "notes.txt", "reading", "UTF-8"),
    (OSError("disk I/O error at C:\\data"), "notes.txt", "saving", "free space"),
    (ValueError("weird"), "x.txt", "reading", "couldn't read x.txt"),
])
def test_describe_ingest_error(exc, name, stage, expected):
    message = describe_ingest_error(exc, name, stage)
    assert expected in message
    assert "C:\\" not in message
