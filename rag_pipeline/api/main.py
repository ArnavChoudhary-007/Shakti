"""
api/main.py
FastAPI application — citation-grounded RAG API.

Endpoints:
  POST /query         — hybrid retrieval + generation (streaming or JSON)
  POST /ingest        — ingest a file on disk into the pipeline
  GET  /health        — Ollama + embedder health check
  GET  /docs_count    — how many chunks are in the vector store
  GET  /ollama/models — list available Ollama models

All structured config loaded from config.yaml at startup.
Ollama model is swappable per-request via ?model=llama3.2.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

_ROOT = Path(__file__).parent.parent

from rag_pipeline import __version__, get_config, get_data_dir
from rag_pipeline import settings as user_settings
from rag_pipeline.api.security import (
    TOKEN_HEADER,
    CompanionGuard,
    cors_origins,
    is_local_request,
    local_origins,
    server_port,
    web_origins,
)
from rag_pipeline.core import ollama_setup
from rag_pipeline.core.kg_builder import build_graph_layout
from rag_pipeline.core.embedder import Embedder
from rag_pipeline.core.model_catalog import (
    get_system_specs,
    get_installed_models,
    build_installed_view,
    load_suggested_models,
    build_suggestions_view,
)
from rag_pipeline.core.vectorstore import get_vector_store
from rag_pipeline.core.retriever import Retriever
from rag_pipeline.core.generator import (
    NOT_FOUND_ANSWER,
    GenerationError,
    Generator,
    build_prompt_preview,
    sql_rows_to_chunks,
)
from rag_pipeline.core.normalizer import Normalizer
from rag_pipeline.core.chunker import Chunker
from rag_pipeline.connectors import extract_file
from rag_pipeline.structured_db.db import QueryContext, StructuredDB
from rag_pipeline.structured_db.router import QueryRouter

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Async ingestion job store ─────────────────────────────────
# Maps job_id -> job state dict. In-memory only; cleared on restart.
_ingest_jobs: Dict[str, Dict[str, Any]] = {}

# ── Load config at startup ────────────────────────────────────
_CONFIG = get_config()
_OLLAMA_HOST = _CONFIG.get("ollama", {}).get("host", "http://localhost:11434")

# ── Lazy singletons ───────────────────────────────────────────
_embedder: Optional[Embedder] = None
_vector_store = None
_retriever: Optional[Retriever] = None
_generator: Optional[Generator] = None
_normalizer: Optional[Normalizer] = None
_chunker: Optional[Chunker] = None
_struct_db: Optional[StructuredDB] = None
_router: Optional[QueryRouter] = None


def _get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder.from_config(_CONFIG)
    return _embedder


def _get_vector_store():
    global _vector_store
    if _vector_store is None:
        _vector_store = get_vector_store(_CONFIG, embedder_dim=_get_embedder().dimension)
    return _vector_store


def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever(
            vector_store=_get_vector_store(),
            embedder=_get_embedder(),
            config=_CONFIG,
        )
    return _retriever


def _get_generator() -> Generator:
    global _generator
    if _generator is None:
        _generator = Generator(_CONFIG)
    return _generator


def _get_normalizer() -> Normalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = Normalizer()
    return _normalizer


def _get_chunker() -> Chunker:
    global _chunker
    if _chunker is None:
        _chunker = Chunker(_CONFIG)
    return _chunker


def _get_struct_db() -> StructuredDB:
    global _struct_db
    if _struct_db is None:
        db_path = _CONFIG.get("structured_db", {}).get("sqlite_path", "./structured_db/structured.db")
        _struct_db = StructuredDB(db_path)
    return _struct_db


def _get_router() -> QueryRouter:
    global _router
    if _router is None:
        _router = QueryRouter(_CONFIG)
    return _router


# ── FastAPI app ───────────────────────────────────────────────

_UPLOAD_DIR = get_data_dir() / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_LOCAL_ORIGINS = local_origins(server_port(_CONFIG))
_WEB_ORIGINS = web_origins(_CONFIG)

# Shared HTTP client for Ollama calls (connection pooling).
_http: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=10.0)
    return _http


@asynccontextmanager
async def _lifespan(app: FastAPI):
    user_settings.get_pairing_token()   # create on first run
    try:
        await asyncio.to_thread(_backfill_legacy_documents)
    except Exception as e:
        logger.warning("Legacy document backfill skipped: %s", e)
    yield
    if _http is not None:
        await _http.aclose()
    if _generator is not None:
        await _generator.aclose()


app = FastAPI(
    title="Shakti Companion API",
    description="Local-first, citation-grounded RAG over your documents.",
    version=__version__,
    lifespan=_lifespan,
)

# Order matters: the last middleware added is the outermost, so CORS answers
# preflight requests before the guard (which would reject them for lacking a
# token) ever sees them.
app.add_middleware(
    CompanionGuard,
    local=_LOCAL_ORIGINS,
    web=_WEB_ORIGINS,
    token_provider=user_settings.get_pairing_token,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(_LOCAL_ORIGINS, _WEB_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", TOKEN_HEADER],
)


# ── Request / response models ─────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    workspace_id: str = "default"
    top_k: Optional[int] = None          # None → config retrieval.final_k
    model: Optional[str] = None
    stream: bool = False
    filters: Optional[Dict[str, Any]] = None
    history: Optional[List[Dict[str, str]]] = None


class CitationOut(BaseModel):
    index: int
    file_name: str
    location: str
    full_text: str
    source_type: str
    sender: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
    citations: List[CitationOut]
    model: str
    used_sql: bool
    route_reason: str
    structure: Optional[Dict[str, Any]] = None


class IngestResponse(BaseModel):
    file_name: str = ""
    file_key: Optional[str] = None
    doc_count: int = 0
    chunk_count: int = 0
    source_types: List[str] = []
    duplicate: bool = False        # identical file already in this workspace
    replaced: int = 0              # older versions (same name) replaced
    status: Optional[str] = None   # "done"
    error: Optional[str] = None
    # Per-stage timing breakdown (seconds), populated once ingestion finishes.
    timings: Optional[Dict[str, float]] = None


class HealthResponse(BaseModel):
    status: str
    ollama: str
    embedder: str
    vector_store_count: int
    ollama_models: List[str]


# ── /health ───────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    """Check Ollama connectivity, embedder readiness, and vector store count."""
    ollama_status = "unreachable"
    available_models: List[str] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_OLLAMA_HOST}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                available_models = [m["name"] for m in data.get("models", [])]
                ollama_status = "ok"
    except Exception as e:
        ollama_status = f"error: {e}"

    # Cloud models will be returned natively by /api/tags if they are pulled

    embedder_status = "ok"
    try:
        emb = _get_embedder()
        _ = emb.dimension
    except Exception as e:
        embedder_status = f"error: {e}"

    vs_count = 0
    try:
        vs_count = _get_vector_store().count()
    except Exception:
        pass

    return HealthResponse(
        status="ok" if ollama_status == "ok" and embedder_status == "ok" else "degraded",
        ollama=ollama_status,
        embedder=embedder_status,
        vector_store_count=vs_count,
        ollama_models=available_models,
    )


# ── Companion identity, pairing, settings ─────────────────────

@app.get("/version", tags=["System"])
async def version():
    """Lets the website detect an installed companion. Callable before pairing."""
    return {"name": "shakti-companion", "version": __version__, "api": 1}


@app.get("/pair/token", tags=["System"])
async def pair_token(request: Request):
    """
    The pairing token for the public website. Only the companion's own UI
    (or a local non-browser client) may read it.
    """
    if not is_local_request(request, _LOCAL_ORIGINS):
        raise HTTPException(status_code=403, detail="Only available from the companion's own page.")
    return {"token": user_settings.get_pairing_token(), "web_origins": sorted(_WEB_ORIGINS)}


class SettingsUpdate(BaseModel):
    chat_model: Optional[str] = None


@app.get("/settings", tags=["System"])
async def get_settings():
    return {**user_settings.load_settings(), "effective_chat_model": user_settings.get_chat_model(_CONFIG)}


@app.put("/settings", tags=["System"])
async def put_settings(update: SettingsUpdate):
    saved = user_settings.update_settings(update.model_dump(exclude_unset=True))
    return {**saved, "effective_chat_model": user_settings.get_chat_model(_CONFIG)}


# ── /setup — help the user install and start Ollama ───────────

@app.get("/setup/status", tags=["Setup"])
async def setup_status():
    """Ollama installed/running state plus the chosen chat model."""
    status = await ollama_setup.get_status(_OLLAMA_HOST, _client())
    models: List[str] = []
    if status["running"]:
        try:
            resp = await _client().get(f"{_OLLAMA_HOST}/api/tags", timeout=5.0)
            models = [m["name"] for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            pass
    chat_model = user_settings.get_chat_model(_CONFIG)
    status.update({
        "models": models,
        "chat_model": chat_model,
        "chat_model_installed": chat_model in models,
    })
    return status


@app.post("/setup/ollama/start", tags=["Setup"])
async def setup_start_ollama():
    try:
        await asyncio.to_thread(ollama_setup.start_ollama)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"status": "starting"}


@app.post("/setup/ollama/install", tags=["Setup"])
async def setup_install_ollama():
    """Download the signed official installer and launch its setup wizard."""
    try:
        return ollama_setup.begin_install()
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))


# ── /models ───────────────────────────────────────────────────

@app.get("/models", tags=["System"])
async def models():
    """List all locally available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_OLLAMA_HOST}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            valid_models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                if not name or "/" in name:
                    logger.warning(f"Filtering out suspicious non-Ollama model name: {name}")
                    continue
                valid_models.append({
                    "name": name,
                    "size": m.get("size"),
                    "modified_at": m.get("modified_at")
                })
            
            return {"models": valid_models}
    except Exception as e:
        logger.error(f"Cannot reach Ollama: {e}")
        return {"models": []}


# ── /ollama/models ────────────────────────────────────────────

@app.get("/ollama/models", tags=["System"])
async def list_models():
    """List all locally available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_OLLAMA_HOST}/api/tags")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach Ollama: {e}")

@app.delete("/ollama/models/{model_name}", tags=["System"])
async def delete_model(model_name: str):
    """Delete a downloaded model from Ollama."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request("DELETE", f"{_OLLAMA_HOST}/api/delete", json={"model": model_name})
            resp.raise_for_status()
            return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete model: {e}")

class PullRequest(BaseModel):
    model: str

@app.get("/system/recommendations", tags=["System"])
async def system_recommendations():
    """
    Live hardware specs + live Ollama model inventory. Never returns
    hardcoded hardware or hardcoded model data — if Ollama isn't reachable,
    this fails loudly (503) rather than falling back to stale/fake data.
    """
    specs = get_system_specs()
    headroom = _CONFIG.get("system", {}).get("ram_headroom_factor", 0.6)
    budget_gb = round(specs["total_ram_gb"] * headroom, 1)

    try:
        installed_raw = await asyncio.to_thread(get_installed_models, _OLLAMA_HOST)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama is not running or unreachable at {_OLLAMA_HOST} — start it to see available models. ({e})",
        )

    installed = build_installed_view(installed_raw, budget_gb)
    installed_names = {m["name"] for m in installed}
    suggested = build_suggestions_view(load_suggested_models(), installed_names, budget_gb)

    return {
        "specs": specs,
        "ram_budget_gb": budget_gb,
        "installed_models": installed,
        "suggested_models": suggested,
    }

@app.post("/ollama/pull", tags=["System"])
async def pull_model(req: PullRequest):
    """Proxy the model pull request to Ollama and stream the JSON progress back."""
    async def stream_pull():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{_OLLAMA_HOST}/api/pull", json={"model": req.model}) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(stream_pull(), media_type="application/x-ndjson")


# ── /docs_count ───────────────────────────────────────────────

@app.get("/docs_count", tags=["System"])
async def docs_count():
    """Return the number of chunks currently indexed."""
    return {"chunk_count": _get_vector_store().count()}


# ── /query ────────────────────────────────────────────────────

def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _run_sql(route: Any, workspace_id: str) -> QueryContext:
    return _get_struct_db().query_answer_context(route.query_type, route.sql_params, workspace_id)


async def _gather_context(req: QueryRequest, query_text: str):
    """
    Route the question and collect the sources to answer from.
    Returns (chunks, used_sql, route). Blocking work runs in threads so the
    server stays responsive while embeddings / reranking / SQL run.
    """
    route = _get_router().route(query_text)
    if route.use_sql:
        context = await asyncio.to_thread(_run_sql, route, req.workspace_id)
        if context.total_count:
            return sql_rows_to_chunks(
                context.rows, total_count=context.total_count, numeric_summary=context.numeric_summary,
                file_names=context.file_names,
            ), True, route
        # SQL found nothing → fall back to document search below.

    filters = {**(req.filters or {}), "workspace_id": req.workspace_id}
    chunks = await asyncio.to_thread(
        _get_retriever().retrieve, query_text, top_k=req.top_k, filters=filters
    )

    retrieval_cfg = _CONFIG.get("retrieval", {})
    threshold = float(_CONFIG.get("vector_store", {}).get("similarity_threshold", 0.0))
    if chunks:
        if all("rerank_score" in chunk for chunk in chunks):
            min_score = float(retrieval_cfg.get("min_rerank_score", -8.0))
            chunks = [chunk for chunk in chunks if chunk["rerank_score"] >= min_score]
        else:
            best = max(chunk.get("combined_score", chunk.get("score", 0.0)) for chunk in chunks)
            if best < threshold:
                chunks = []

    if chunks and retrieval_cfg.get("use_llm_grader", False):
        if not await _get_generator().grade_context(query_text, chunks):
            chunks = []
    return chunks, False, route


def _result_payload(result: Any, route: Any, query_text: str) -> Dict[str, Any]:
    return {
        "answer": result.answer,
        "citations": [c.__dict__ for c in result.citations],
        "model": result.model,
        "used_sql": result.used_sql,
        "route_reason": route.reason,
        "structure": result.structure,
        "query": query_text,
    }


@app.post("/query", tags=["RAG"])
async def query(req: QueryRequest):
    """
    Main RAG query endpoint.
    - Routes to SQL for tabular questions, document search otherwise.
    - stream=true → Server-Sent Events: `status`, `token` (many), then
      `done` (answer + citations) or `error`. Every route streams.
    - stream=false → a single JSON QueryResponse.
    """
    gen = _get_generator()
    model = gen._resolve_model(req.model)

    if req.stream:
        return StreamingResponse(
            _stream_answer(req, model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        query_text = await gen.contextualize_query(req.query, req.history or [], model=model)
        chunks, used_sql, route = await _gather_context(req, query_text)
        if not chunks:
            result = gen.finalize(NOT_FOUND_ANSWER, [], model)
        else:
            result = await gen.generate(query_text, chunks, model=model, used_sql=used_sql)
    except GenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return QueryResponse(
        answer=result.answer,
        citations=[CitationOut(**c.__dict__) for c in result.citations],
        model=result.model,
        used_sql=result.used_sql,
        route_reason=route.reason,
        structure=result.structure,
    )


async def _stream_answer(req: QueryRequest, model: str) -> AsyncIterator[str]:
    gen = _get_generator()
    try:
        query_text = req.query
        if req.history:
            yield _sse("status", {"stage": "understanding"})
            query_text = await gen.contextualize_query(req.query, req.history, model=model)

        yield _sse("status", {"stage": "searching"})
        chunks, used_sql, route = await _gather_context(req, query_text)

        if not chunks:
            yield _sse("done", _result_payload(gen.finalize(NOT_FOUND_ANSWER, [], model), route, query_text))
            return

        yield _sse("status", {"stage": "generating", "sources": len(chunks), "used_sql": used_sql})
        parts: List[str] = []
        async for token in gen.generate_stream(query_text, chunks, model=model):
            parts.append(token)
            yield _sse("token", {"text": token})

        result = gen.finalize("".join(parts), chunks, model, used_sql=used_sql)
        yield _sse("done", _result_payload(result, route, query_text))
    except GenerationError as e:
        yield _sse("error", {"message": str(e)})
    except Exception as e:  # never leave the client hanging mid-stream
        logger.exception("Query failed")
        yield _sse("error", {"message": f"Something went wrong while answering: {e}"})


# ── /ingest ───────────────────────────────────────────────────

# One ingestion at a time: parsing/OCR/embedding are CPU-heavy, and running
# several at once just makes every one of them slower.
_INGEST_SEMAPHORE = asyncio.Semaphore(1)
_JOB_TTL_SECONDS = 3600
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def _prune_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    for job_id in [j for j, v in _ingest_jobs.items()
                   if v["status"] in ("done", "failed") and v.get("finished_at", 0) < cutoff]:
        _ingest_jobs.pop(job_id, None)


@app.post("/ingest", tags=["Ingestion"])
async def ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    workspace_id: str = Form("default"),
):
    """
    Saves the upload, queues extract→chunk→embed in the background, and
    returns a job_id straight away (HTTP 202). Poll GET /ingest/status/{job_id}.
    """
    from fastapi.responses import JSONResponse

    file_name = Path(file.filename).name if file.filename else "upload.bin"
    job_id = str(uuid.uuid4())
    incoming = _UPLOAD_DIR / "_incoming" / job_id
    incoming.mkdir(parents=True, exist_ok=True)
    target = incoming / file_name

    size = 0
    with open(target, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                f.close()
                shutil.rmtree(incoming, ignore_errors=True)
                raise HTTPException(status_code=413, detail="File is larger than 512 MB.")
            f.write(chunk)

    _prune_jobs()
    _ingest_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",            # queued → processing → done | failed
        "stage": None,
        "file_name": file_name,
        "workspace_id": workspace_id,
        "doc_count": 0,
        "chunk_count": 0,
        "source_types": [],
        "duplicate": False,
        "replaced": 0,
        "file_key": None,
        "error": None,
        "timings": None,
        "created_at": time.time(),
    }
    background_tasks.add_task(_run_ingest_job, job_id, target, file_name, workspace_id)
    return JSONResponse(status_code=202, content=_ingest_jobs[job_id])


@app.get("/ingest/status/{job_id}", tags=["Ingestion"])
async def ingest_status(job_id: str):
    """Poll the status of an async ingestion job started by POST /ingest."""
    job = _ingest_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


async def _run_ingest_job(job_id: str, incoming_path: Path, original_name: str, workspace_id: str) -> None:
    job = _ingest_jobs[job_id]

    def on_stage(stage: str) -> None:
        job["stage"] = stage

    try:
        async with _INGEST_SEMAPHORE:
            job["status"] = "processing"
            result = await asyncio.to_thread(
                _ingest_file_sync, incoming_path, original_name, workspace_id,
                store_upload=True, on_stage=on_stage,
            )
        job.update({"status": "done", **result.model_dump(exclude={"status", "error"})})
    except Exception as e:
        logger.error("Ingest job %s failed: %s", job_id, e)
        detail = e.detail if isinstance(e, HTTPException) else str(e)
        job.update({"status": "failed", "error": detail})
    finally:
        job["finished_at"] = time.time()
        shutil.rmtree(incoming_path.parent, ignore_errors=True)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _delete_document(doc: Dict[str, Any]) -> None:
    """Remove one ingested file everywhere: vectors, SQL rows, KG, upload copy."""
    if doc.get("legacy"):
        _get_vector_store().delete_where({"workspace_id": doc["workspace_id"], "file_name": doc["file_name"]})
    else:
        _get_vector_store().delete_where({"file_key": doc["file_key"]})
    _get_struct_db().delete_document(doc)
    shutil.rmtree(_UPLOAD_DIR / doc["file_key"], ignore_errors=True)


def _ingest_file_sync(
    file_path: Path,
    original_name: str,
    workspace_id: str = "default",
    *,
    store_upload: bool = False,
    on_stage=None,
) -> IngestResponse:
    """
    Core ingestion (blocking — call via asyncio.to_thread).

    IDs are derived from the file's content, so re-uploading an identical
    file is a no-op, and uploading a changed file with the same name
    replaces the old version instead of duplicating it.
    """
    stage = on_stage or (lambda s: None)
    db = _get_struct_db()

    file_path = Path(file_path)
    content_hash = _hash_file(file_path)
    existing = db.find_document_by_hash(workspace_id, content_hash)
    if existing:
        return IngestResponse(
            status="done", file_name=original_name, file_key=existing["file_key"],
            doc_count=existing["doc_count"], chunk_count=existing["chunk_count"],
            source_types=[s for s in (existing.get("source_types") or "").split(",") if s],
            duplicate=True,
        )

    file_key = hashlib.sha256(f"{workspace_id}\0{content_hash}".encode()).hexdigest()[:20]
    stored_dir: Optional[Path] = None
    if store_upload:
        stored_dir = _UPLOAD_DIR / file_key
        stored_dir.mkdir(parents=True, exist_ok=True)
        stored = stored_dir / original_name
        shutil.move(str(file_path), stored)
        file_path = stored

    try:
        return _index_file(file_path, original_name, workspace_id, file_key, content_hash, stage)
    except Exception:
        if stored_dir is not None:
            shutil.rmtree(stored_dir, ignore_errors=True)
        raise


def _index_file(file_path: Path, original_name: str, workspace_id: str,
                file_key: str, content_hash: str, stage) -> IngestResponse:
    db = _get_struct_db()
    vs = _get_vector_store()
    t_start = time.perf_counter()
    stage_times = {"parse": 0.0, "chunk": 0.0, "embed": 0.0, "store": 0.0, "kg": 0.0}

    # ── Parse + normalize ──
    stage("reading")
    t0 = time.perf_counter()
    envelopes = list(extract_file(str(file_path)))
    if not envelopes:
        raise HTTPException(status_code=422, detail=f"Couldn't find any text in {original_name}.")
    normalizer = _get_normalizer()
    docs = []
    for i, env in enumerate(envelopes):
        doc = normalizer.normalize(env)
        doc.doc_id = f"{file_key}-{i}"
        docs.append((env, doc))
    stage_times["parse"] = time.perf_counter() - t0

    # ── Chunk ──
    t0 = time.perf_counter()
    chunks = _get_chunker().chunk_batch([d for _, d in docs])
    stage_times["chunk"] = time.perf_counter() - t0

    # ── Embed (the slow part) before touching any stored data ──
    stage("embedding")
    t0 = time.perf_counter()
    embedder = _get_embedder()
    embeddings: List[List[float]] = []
    batch_size = 64
    for i in range(0, len(chunks), batch_size):
        embeddings.extend(embedder.encode([c.text for c in chunks[i:i + batch_size]]))
    stage_times["embed"] = time.perf_counter() - t0

    # ── Replace older versions of this file (same name, same workspace) ──
    stage("saving")
    previous = db.find_documents_by_name(workspace_id, original_name)
    for old in previous:
        _delete_document(old)

    # ── Store vectors ──
    t0 = time.perf_counter()
    records = []
    for chunk, embedding in zip(chunks, embeddings):
        records.append({
            "chunk_id": chunk.chunk_id,
            "text": chunk.text,
            "embedding": embedding,
            "metadata": {
                **chunk.citation_meta,
                "doc_id": chunk.doc_id,
                "file_key": file_key,
                "source_type": chunk.source_type,
                "title": chunk.metadata.get("title", ""),
                "chunk_index": chunk.chunk_index,
                "total_chunks": chunk.total_chunks,
                "workspace_id": workspace_id,
            },
        })
    for i in range(0, len(records), 256):
        vs.add(records[i:i + 256])
    stage_times["store"] = time.perf_counter() - t0

    # ── Structured rows + KG queue ──
    t0 = time.perf_counter()
    source_types = set()
    for env, doc in docs:
        source_types.add(doc.source_type)
        file_name = env["raw_metadata"].get("file_name", original_name)
        if doc.source_type == "invoice" and doc.structured_data:
            doc.structured_data["workspace_id"] = workspace_id
            db.upsert_invoice(doc_id=doc.doc_id, structured_data=doc.structured_data,
                              file_path=env["raw_path"], file_name=file_name, raw_text=doc.text)
        elif doc.source_type in ("excel", "csv") and doc.structured_data:
            rows = doc.structured_data.get("records", [])
            if rows:
                db.upsert_ledger_records(doc_id=doc.doc_id, file_name=file_name,
                                         sheet_name=doc.structured_data.get("sheet_name", ""),
                                         records=rows, file_path=env.get("raw_path"),
                                         workspace_id=workspace_id)
        if doc.text.strip():
            # KG extraction is deferred to "Build graph" — no LLM call here.
            db.add_kg_pending_doc(doc_id=doc.doc_id, workspace_id=workspace_id,
                                  source_doc=original_name, source_type=doc.source_type, text=doc.text)
    stage_times["kg"] = time.perf_counter() - t0

    db.add_document(
        file_key=file_key, workspace_id=workspace_id, file_name=original_name,
        content_hash=content_hash, size_bytes=file_path.stat().st_size,
        source_types=sorted(source_types), doc_count=len(docs), chunk_count=len(records),
    )

    stage_times["total"] = time.perf_counter() - t_start
    logger.info(
        "[timing] Ingest %s: parse=%.2fs chunk=%.2fs embed=%.2fs store=%.2fs kg=%.2fs total=%.2fs "
        "(envelopes=%d, chunks=%d, replaced=%d)",
        original_name, stage_times["parse"], stage_times["chunk"], stage_times["embed"],
        stage_times["store"], stage_times["kg"], stage_times["total"], len(docs), len(records), len(previous),
    )
    return IngestResponse(
        status="done", file_name=original_name, file_key=file_key,
        doc_count=len(docs), chunk_count=len(records), source_types=sorted(source_types),
        replaced=len(previous), timings=stage_times,
    )


async def _ingest_file_path(file_path: str, original_name: str, workspace_id: str = "default") -> IngestResponse:
    """Async wrapper used by the sync daemon (ingests the file in place)."""
    async with _INGEST_SEMAPHORE:
        return await asyncio.to_thread(_ingest_file_sync, Path(file_path), original_name, workspace_id)


# ── /documents ────────────────────────────────────────────────

def _backfill_legacy_documents() -> None:
    """
    Files ingested before the documents table existed have no row there, so
    they'd be invisible in the Documents list. Rebuild rows from vector-store
    metadata once, grouped by (workspace, file name).
    """
    db = _get_struct_db()
    with db._connect() as conn:
        if conn.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
            return
    vs = _get_vector_store()
    if vs.count() == 0:
        return
    groups: Dict[tuple, Dict[str, Any]] = {}
    for meta in vs.iter_metadata():
        key = (meta.get("workspace_id") or "default", meta.get("file_name") or "unknown")
        g = groups.setdefault(key, {"chunks": 0, "types": set()})
        g["chunks"] += 1
        if meta.get("source_type"):
            g["types"].add(meta["source_type"])
    for (ws, name), g in groups.items():
        legacy_key = "legacy-" + hashlib.sha256(f"{ws}\0{name}".encode()).hexdigest()[:16]
        db.add_document(file_key=legacy_key, workspace_id=ws, file_name=name, content_hash=legacy_key,
                        size_bytes=0, source_types=sorted(g["types"]), doc_count=0,
                        chunk_count=g["chunks"], legacy=True)
    logger.info("Backfilled %d legacy document row(s).", len(groups))


@app.get("/workspaces", tags=["Ingestion"])
async def list_workspaces():
    """Workspaces that hold at least one document (plus 'default')."""
    rows = await asyncio.to_thread(_get_struct_db().list_workspaces)
    if not any(r["workspace_id"] == "default" for r in rows):
        rows.insert(0, {"workspace_id": "default", "document_count": 0})
    return {"workspaces": rows}


@app.get("/documents", tags=["Ingestion"])
async def list_documents(workspace_id: str = Query("default")):
    return {"documents": await asyncio.to_thread(_get_struct_db().list_documents, workspace_id)}


@app.delete("/documents/{file_key}", tags=["Ingestion"])
async def delete_document(file_key: str):
    doc = await asyncio.to_thread(_get_struct_db().get_document, file_key)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    async with _INGEST_SEMAPHORE:
        await asyncio.to_thread(_delete_document, doc)
    return {"status": "deleted", "file_key": file_key}


# ── /knowledge_graph ──────────────────────────────────────────

_kg_locks: Dict[str, asyncio.Lock] = {}


@app.get("/knowledge_graph", tags=["Knowledge Graph"])
async def get_knowledge_graph(workspace_id: str = Query("default")):
    """Returns the extracted Knowledge Graph nodes, unpruned edges, and communities."""
    graph = await asyncio.to_thread(_get_struct_db().get_knowledge_graph, workspace_id=workspace_id)
    with _get_struct_db()._connect() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM kg_pending_docs WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()[0]
    return {**graph, "pending_docs": pending, "building": _kg_locks.get(workspace_id, asyncio.Lock()).locked()}


@app.post("/knowledge_graph/build", tags=["Knowledge Graph"])
@app.post("/kg/rebuild", tags=["Knowledge Graph"], include_in_schema=False)
async def build_knowledge_graph(
    workspace_id: str = Query("default"),
    min_edge_weight: int = Query(1, description="Minimum occurrences to keep an edge"),
):
    """
    Extracts entities from newly ingested documents, then runs community
    detection, centrality, LLM labeling, and edge pruning.
    """
    lock = _kg_locks.setdefault(workspace_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="The graph for this workspace is already being built.")
    async with lock:
        try:
            await build_graph_layout(
                workspace_id=workspace_id,
                db=_get_struct_db(),
                host=_OLLAMA_HOST,
                model=user_settings.get_chat_model(_CONFIG),
                min_edge_weight=min_edge_weight,
            )
        except Exception as e:
            logger.error(f"Failed to build graph layout: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "success", "message": "Graph built."}


# ── /prompt_preview ───────────────────────────────────────────

@app.post("/prompt_preview", tags=["Debug"])
async def prompt_preview(req: QueryRequest):
    """Return the exact prompt that would be sent to the model (debug endpoint)."""
    chunks, _, _ = await _gather_context(req, req.query)
    return {"prompt": build_prompt_preview(req.query, chunks), "chunk_count": len(chunks)}


# ── /system/clear ─────────────────────────────────────────────

@app.delete("/system/clear", tags=["System"])
async def clear_system_data(workspace_id: str = "default"):
    """Wipes all ingested chunks, structured data and stored uploads for a workspace."""
    def _clear() -> None:
        db = _get_struct_db()
        for doc in db.list_documents(workspace_id):
            shutil.rmtree(_UPLOAD_DIR / doc["file_key"], ignore_errors=True)
        _get_vector_store().clear_workspace(workspace_id)
        db.clear_workspace(workspace_id)

    try:
        async with _INGEST_SEMAPHORE:
            await asyncio.to_thread(_clear)
        return {"status": "success", "message": f"Cleared data for workspace {workspace_id}"}
    except Exception as e:
        logger.error(f"Failed to clear system data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Frontend Mount ─────────────────────────────────────────────
# The built React app (web/ → rag_pipeline/webui) if present, else the legacy
# single-file UI.
_WEBUI_DIR = _ROOT / "webui"
_UI_DIR = _WEBUI_DIR if (_WEBUI_DIR / "index.html").is_file() else _ROOT / "frontend"
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="frontend")
