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
import time
import uuid
import hashlib
import networkx as nx
import community as community_louvain
import json
import logging
import os
import sys
import psutil
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from dotenv import load_dotenv

from rag_pipeline.core.kg_builder import build_graph_layout

load_dotenv()

import httpx
import requests
import yaml
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT.parent))

from rag_pipeline import get_config
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
from rag_pipeline.core.generator import Generator, build_prompt_preview
from rag_pipeline.core.normalizer import Normalizer
from rag_pipeline.core.chunker import Chunker
from rag_pipeline.connectors import extract_file
from rag_pipeline.structured_db.db import StructuredDB
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

_kg_cluster_cache = {}  # In-memory cache for cluster labels


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

app = FastAPI(
    title="RAG Pipeline API",
    description="Local-first, citation-grounded RAG over your documents.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(_ROOT / "uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_ROOT / "uploads")), name="uploads")


# ── Request / response models ─────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
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
    doc_count: int = 0
    chunk_count: int = 0
    source_types: List[str] = []
    # Async job fields (present when ingestion is running in background)
    job_id: Optional[str] = None
    status: Optional[str] = None   # "processing" | "done" | "failed"
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

@app.post("/query", tags=["RAG"])
async def query(req: QueryRequest):
    """
    Main RAG query endpoint.
    - Routes to SQL for structured queries, vector search for semantic queries.
    - Supports streaming (stream=true) or JSON response.
    - Every answer is grounded with numbered citations.
    """
    if req.history:
        req.query = await _get_generator().contextualize_query(req.query, req.history, model=req.model)

    route = _get_router().route(req.query)

    if route.use_sql:
        return await _handle_sql_query(req, route)

    if req.stream:
        return StreamingResponse(
            _stream_response(req, route),
            media_type="text/event-stream",
        )
    return await _handle_vector_query(req, route)


async def _handle_vector_query(req: QueryRequest, route: Any) -> QueryResponse:
    chunks = _get_retriever().retrieve(req.query, top_k=req.top_k, filters=req.filters)
    
    # 1. Vector Guard
    threshold = _CONFIG.get("vector_store", {}).get("similarity_threshold", 0.75)
    if chunks:
        best_score = chunks[0].get("combined_score", chunks[0].get("score", 0.0))
        if best_score < threshold:
            chunks = []

    if not chunks:
        return QueryResponse(
            answer="Not found in documents.",
            citations=[],
            model=req.model or _get_generator().default_text_model,
            used_sql=False,
            route_reason=route.reason,
        )

    # 2. Algorithmic Grader
    gen = _get_generator()
    is_relevant = await gen.grade_context(req.query, chunks)
    if not is_relevant:
        return QueryResponse(
            answer="Not found in documents.",
            citations=[],
            model=req.model or gen.default_text_model,
            used_sql=False,
            route_reason=route.reason,
        )

    result = await _get_generator().generate(req.query, chunks, model=req.model)
    return QueryResponse(
        answer=result.answer,
        citations=[CitationOut(**c.__dict__) for c in result.citations],
        model=result.model,
        used_sql=False,
        route_reason=route.reason,
        structure=result.structure,
    )


async def _handle_sql_query(req: QueryRequest, route: Any) -> QueryResponse:
    db = _get_struct_db()
    params = route.sql_params

    if route.query_type == "invoice":
        sql_results = db.query_invoices(
            vendor=params.get("vendor"),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
            min_amount=params.get("min_amount"),
            max_amount=params.get("max_amount"),
            invoice_number=params.get("invoice_number"),
            limit=50,
        )
    elif route.query_type == "ledger":
        sql_results = db.query_ledger(
            sheet_name=params.get("sheet_name"),
            limit=200,
        )
    else:
        sql_results = []

    if not sql_results:
        # Fall back to vector search if SQL found nothing
        return await _handle_vector_query(req, route)

    result = await _get_generator().generate_from_sql(
        req.query, sql_results, model=req.model
    )
    return QueryResponse(
        answer=result.answer,
        citations=[CitationOut(**c.__dict__) for c in result.citations],
        model=result.model,
        used_sql=True,
        route_reason=route.reason,
    )


async def _stream_response(req: QueryRequest, route: Any) -> AsyncIterator[str]:
    """SSE streaming: sends tokens as data: ... events, then a citations event."""
    chunks = _get_retriever().retrieve(req.query, top_k=req.top_k, filters=req.filters)
    
    # 1. Vector Guard
    threshold = _CONFIG.get("vector_store", {}).get("similarity_threshold", 0.75)
    if chunks:
        best_score = chunks[0].get("combined_score", chunks[0].get("score", 0.0))
        if best_score < threshold:
            chunks = []

    if not chunks:
        yield "data: Not found in documents.\n\n"
        yield "data: [DONE]\n\n"
        return

    # 2. Algorithmic Grader
    gen = _get_generator()
    is_relevant = await gen.grade_context(req.query, chunks)
    if not is_relevant:
        yield "data: Not found in documents.\n\n"
        yield "data: [DONE]\n\n"
        return
    async for token in gen.generate_stream(req.query, chunks, model=req.model):
        if token.startswith("\n__CITATIONS__:"):
            # Strip prefix and send as a special event (includes citations + structure)
            payload = token[len("\n__CITATIONS__:"):]
            yield f"event: citations\ndata: {payload}\n\n"
        else:
            # Escape newlines for SSE
            escaped = token.replace("\n", "\\n")
            yield f"data: {escaped}\n\n"
    yield "data: [DONE]\n\n"


# ── /ingest ───────────────────────────────────────────────────

@app.post("/ingest", tags=["Ingestion"])
async def ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    workspace_id: str = Form("default")
):
    """
    Async ingest: saves the file immediately, kicks off extract→chunk→embed
    in the background, and returns a job_id straight away (HTTP 202).
    Poll GET /ingest/status/{job_id} for completion.
    """
    import shutil
    from fastapi.responses import JSONResponse

    file_name = Path(file.filename).name if file.filename else "unknown.bin"
    upload_path = _ROOT / "uploads" / file_name
    upload_path.parent.mkdir(parents=True, exist_ok=True)

    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job_id = str(uuid.uuid4())
    _ingest_jobs[job_id] = {
        "job_id": job_id,
        "status": "processing",
        "file_name": file_name,
        "doc_count": 0,
        "chunk_count": 0,
        "source_types": [],
        "error": None,
        "timings": None,
    }

    background_tasks.add_task(
        _run_ingest_job, job_id, str(upload_path), file_name, workspace_id
    )

    return JSONResponse(status_code=202, content=_ingest_jobs[job_id])


@app.post("/ingest_path", response_model=IngestResponse, tags=["Ingestion"])
async def ingest_path(
    path: str = Query(..., description="Absolute path to file on disk"),
    workspace_id: str = Query("default", description="Workspace/session ID")
):
    """
    Ingest a file by path (for server-side use, e.g. CLI or sync daemon).
    Runs synchronously so callers can await the result directly.
    """
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return await _ingest_file_path(path, original_name=Path(path).name, workspace_id=workspace_id)


@app.get("/ingest/status/{job_id}", tags=["Ingestion"])
async def ingest_status(job_id: str):
    """Poll the status of an async ingestion job started by POST /ingest."""
    job = _ingest_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job


async def _run_ingest_job(
    job_id: str, file_path: str, original_name: str, workspace_id: str
) -> None:
    """Background worker: runs the full ingest pipeline and updates job state."""
    try:
        result = await _ingest_file_path(
            file_path,
            original_name=original_name,
            workspace_id=workspace_id,
        )
        _ingest_jobs[job_id].update({
            "status": "done",
            "doc_count": result.doc_count,
            "chunk_count": result.chunk_count,
            "source_types": result.source_types,
            "timings": result.timings,
        })
    except HTTPException as e:
        _ingest_jobs[job_id].update({"status": "failed", "error": e.detail})
    except Exception as e:
        logger.error(f"Ingest job {job_id} failed: {e}")
        _ingest_jobs[job_id].update({"status": "failed", "error": str(e)})


async def _ingest_file_path(file_path: str, original_name: str, workspace_id: str = "default") -> IngestResponse:
    """Core ingestion logic, shared by /ingest and /ingest_path."""
    normalizer = _get_normalizer()
    chunker = _get_chunker()
    embedder = _get_embedder()
    vs = _get_vector_store()
    db = _get_struct_db()

    t_start = time.perf_counter()
    stage_times = {"parse": 0.0, "chunk": 0.0, "embed": 0.0, "store": 0.0, "kg": 0.0}

    t0 = time.perf_counter()
    envelopes = list(extract_file(file_path))
    stage_times["parse"] = time.perf_counter() - t0
    if not envelopes:
        raise HTTPException(status_code=422, detail=f"Could not extract content from {original_name}")

    all_chunks: List[Dict[str, Any]] = []
    source_types: set = set()
    doc_count = 0

    aggregated_texts = []
    aggregated_chunk_meta = []
    docs_for_chunking: List[Any] = []

    for env in envelopes:
        doc = normalizer.normalize(env)
        doc_count += 1
        source_types.add(doc.source_type)

        # Write structured data to SQLite for invoices/tabular Excel
        if doc.structured_data:
            doc.structured_data["workspace_id"] = workspace_id

        if doc.source_type == "invoice" and doc.structured_data:
            db.upsert_invoice(
                doc_id=doc.doc_id,
                structured_data=doc.structured_data,
                file_path=env["raw_path"],
                file_name=env["raw_metadata"].get("file_name", original_name),
                raw_text=doc.text,
            )
        elif doc.source_type in ("excel", "csv") and doc.structured_data:
            records = doc.structured_data.get("records", [])
            if records:
                db.upsert_ledger_records(
                    doc_id=doc.doc_id,
                    file_name=env["raw_metadata"].get("file_name", original_name),
                    sheet_name=doc.structured_data.get("sheet_name", ""),
                    records=records,
                    file_path=env.get("raw_path", file_path),
                )

        # Defer chunking until every envelope in this file is normalized —
        # chat message logs need their sibling messages to window correctly
        # (see chunker.chunk_batch). Non-chat docs chunk the same as before,
        # just after the loop instead of inline.
        docs_for_chunking.append(doc)

        # KG: queue the raw text for extraction later, behind the "Build Graph"
        # action — no Ollama call here, so ingestion never blocks on it.
        if doc.text.strip():
            t0 = time.perf_counter()
            db.add_kg_pending_doc(
                doc_id=doc.doc_id,
                workspace_id=workspace_id,
                source_doc=original_name,
                source_type=doc.source_type,
                text=doc.text,
            )
            stage_times["kg"] += time.perf_counter() - t0

    t0 = time.perf_counter()
    all_chunked = chunker.chunk_batch(docs_for_chunking)
    stage_times["chunk"] = time.perf_counter() - t0
    for chunk in all_chunked:
        aggregated_texts.append(chunk.text)
        aggregated_chunk_meta.append((chunk, chunk.metadata.get("title", ""), workspace_id))

    # Embed and Store in batches to avoid hogging threads and memory
    if aggregated_texts:
        BATCH_SIZE = 64
        for i in range(0, len(aggregated_texts), BATCH_SIZE):
            text_batch = aggregated_texts[i:i+BATCH_SIZE]
            meta_batch = aggregated_chunk_meta[i:i+BATCH_SIZE]

            t0 = time.perf_counter()
            embeddings = await asyncio.to_thread(embedder.encode, text_batch)
            stage_times["embed"] += time.perf_counter() - t0

            batch_chunks = []
            for (chunk, title, ws_id), embedding in zip(meta_batch, embeddings):
                batch_chunks.append({
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "embedding": embedding,
                    "metadata": {
                        **chunk.citation_meta,
                        "doc_id": chunk.doc_id,
                        "source_type": chunk.source_type,
                        "title": title,
                        "chunk_index": chunk.chunk_index,
                        "total_chunks": chunk.total_chunks,
                        "workspace_id": ws_id,
                    },
                })

            if batch_chunks:
                t0 = time.perf_counter()
                vs.add(batch_chunks)
                stage_times["store"] += time.perf_counter() - t0
                all_chunks.extend(batch_chunks)

    stage_times["total"] = time.perf_counter() - t_start
    logger.info(
        "[timing] Ingest %s: parse=%.2fs chunk=%.2fs kg=%.2fs embed=%.2fs store=%.2fs total=%.2fs "
        "(envelopes=%d, chunks=%d)",
        original_name, stage_times["parse"], stage_times["chunk"], stage_times["kg"],
        stage_times["embed"], stage_times["store"], stage_times["total"],
        doc_count, len(all_chunks),
    )

    return IngestResponse(
        status="ok",
        file_name=original_name,
        doc_count=doc_count,
        chunk_count=len(all_chunks),
        source_types=list(source_types),
        timings=stage_times,
    )


# ── /knowledge_graph ──────────────────────────────────────────

@app.get("/knowledge_graph", tags=["Knowledge Graph"])
async def get_knowledge_graph(workspace_id: str = Query("default")):
    """Returns the extracted Knowledge Graph nodes, unpruned edges, and communities."""
    db = _get_struct_db()
    return db.get_knowledge_graph(workspace_id=workspace_id)

@app.post("/knowledge_graph/build", tags=["Knowledge Graph"])
async def build_knowledge_graph(
    workspace_id: str = Query("default"),
    min_edge_weight: int = Query(1, description="Minimum occurrences to keep an edge")
):
    """
    Runs community detection, centrality, LLM labeling, and edge pruning.
    Call this before rendering the graph on the frontend.
    """
    db = _get_struct_db()
    try:
        await build_graph_layout(
            workspace_id=workspace_id,
            db=db,
            host=_OLLAMA_HOST,
            model=get_config().get("generator", {}).get("model", "llama3.2:1b"),
            min_edge_weight=min_edge_weight
        )
        return {"status": "success", "message": "Graph layout built successfully"}
    except Exception as e:
        logger.error(f"Failed to build graph layout: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── /prompt_preview ───────────────────────────────────────────

@app.post("/prompt_preview", tags=["Debug"])
async def prompt_preview(req: QueryRequest):
    """
    Return the exact prompt that would be sent to the model (debug endpoint).
    """
    chunks = _get_retriever().retrieve(req.query, top_k=req.top_k)
    preview = build_prompt_preview(req.query, chunks)
    return {"prompt": preview, "chunk_count": len(chunks)}


# ── /system/clear ─────────────────────────────────────────────

@app.delete("/system/clear", tags=["System"])
async def clear_system_data(workspace_id: str = "default"):
    """Wipes all ingested chunks and structured data for the given workspace."""
    try:
        vs = _get_vector_store()
        if hasattr(vs, "clear_workspace"):
            vs.clear_workspace(workspace_id)
        
        db = _get_struct_db()
        if hasattr(db, "clear_workspace"):
            db.clear_workspace(workspace_id)
            
        return {"status": "success", "message": f"Cleared data for workspace {workspace_id}"}
    except Exception as e:
        logger.error(f"Failed to clear system data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── /ollama Endpoints ─────────────────────────────────────────
class PullRequest(BaseModel):
    model: str

@app.get("/ollama/models", tags=["Ollama"])
async def list_ollama_models():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_OLLAMA_HOST}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [{"name": m["name"], "size": m.get("size", 0)} for m in data.get("models", [])]
                return {"models": models}
    except Exception as e:
        logger.error(f"Error listing ollama models: {e}")
    return {"models": []}

@app.post("/ollama/pull", tags=["Ollama"])
async def pull_ollama_model(req: PullRequest):
    async def _stream_pull():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", f"{_OLLAMA_HOST}/api/pull", json={"name": req.model}) as resp:
                    async for chunk in resp.aiter_text():
                        yield chunk
        except Exception as e:
            yield json.dumps({"status": f"Error: {e}"}) + "\\n"
    return StreamingResponse(_stream_pull(), media_type="application/x-ndjson")

@app.delete("/ollama/models/{model_name:path}", tags=["Ollama"])
async def delete_ollama_model(model_name: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request("DELETE", f"{_OLLAMA_HOST}/api/delete", json={"name": model_name})
            if resp.status_code == 200:
                return {"status": "ok"}
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/kg/rebuild", tags=["Knowledge Graph"])
async def rebuild_kg(workspace_id: str = Query("default", description="Workspace/session ID")):
    """Manually trigger the graph layout generation using the current data in SQLite."""
    try:
        db = _get_struct_db()
        kg_model = get_config().get("generator", {}).get("model", "llama3.2:1b")
        await build_graph_layout(workspace_id=workspace_id, db=db, host=_OLLAMA_HOST, model=kg_model)
        return {"status": "success", "message": "Graph layout rebuilt successfully"}
    except Exception as e:
        logger.error(f"Failed to rebuild graph layout: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Frontend Mount ─────────────────────────────────────────────
os.makedirs(_ROOT / "uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_ROOT / "uploads")), name="uploads")
app.mount("/", StaticFiles(directory=str(_ROOT / "frontend"), html=True), name="frontend")
