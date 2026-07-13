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
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import yaml
from fastapi import FastAPI, File, HTTPException, Query, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Path setup ────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT.parent))

from rag_pipeline import get_config
from rag_pipeline.core.embedder import Embedder
from rag_pipeline.core.vectorstore import get_vector_store
from rag_pipeline.core.retriever import Retriever
from rag_pipeline.core.generator import Generator, build_prompt_preview, extract_kg_relationships
from rag_pipeline.core.normalizer import Normalizer
from rag_pipeline.core.chunker import Chunker
from rag_pipeline.connectors import extract_file
from rag_pipeline.structured_db.db import StructuredDB
from rag_pipeline.structured_db.router import QueryRouter

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

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
    snippet: str
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
    doc_count: int
    chunk_count: int
    source_types: List[str]


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

@app.post("/ingest", response_model=IngestResponse, tags=["Ingestion"])
async def ingest(
    file: UploadFile = File(...),
    workspace_id: str = Form("default")
):
    """
    Ingest a single file: extract → normalise → chunk → embed → store.
    Also writes structured data (invoices, ledger rows) to SQLite.
    """
    # Save upload to a temp path
    import tempfile, shutil
    suffix = Path(file.filename).suffix if file.filename else ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        return await _ingest_file_path(tmp_path, original_name=file.filename or "unknown", workspace_id=workspace_id)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@app.post("/ingest_path", response_model=IngestResponse, tags=["Ingestion"])
async def ingest_path(
    path: str = Query(..., description="Absolute path to file on disk"),
    workspace_id: str = Query("default", description="Workspace/session ID")
):
    """
    Ingest a file by path (for server-side use, e.g. CLI or sync daemon).
    """
    if not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return await _ingest_file_path(path, original_name=Path(path).name, workspace_id=workspace_id)


async def _ingest_file_path(file_path: str, original_name: str, workspace_id: str = "default") -> IngestResponse:
    """Core ingestion logic, shared by /ingest and /ingest_path."""
    normalizer = _get_normalizer()
    chunker = _get_chunker()
    embedder = _get_embedder()
    vs = _get_vector_store()
    db = _get_struct_db()

    envelopes = list(extract_file(file_path))
    if not envelopes:
        raise HTTPException(status_code=422, detail=f"Could not extract content from {original_name}")

    all_chunks: List[Dict[str, Any]] = []
    source_types: set = set()
    doc_count = 0

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

        # Chunk and embed
        chunks = chunker.chunk(doc)
        if not chunks:
            continue

        texts = [c.text for c in chunks]
        embeddings = embedder.encode(texts)

        for chunk, embedding in zip(chunks, embeddings):
            chunk_dict = {
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "embedding": embedding,
                "metadata": {
                    **chunk.citation_meta,
                    "doc_id": chunk.doc_id,
                    "source_type": chunk.source_type,
                    "title": doc.title,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "workspace_id": workspace_id,
                },
            }
            all_chunks.append(chunk_dict)

        if all_chunks:
            vs.add(all_chunks)

        # KG Extraction
        if doc.text.strip():
            logger.info(f"Extracting KG for {original_name}")
            kg_model = get_config().get("generator", {}).get("model", "llama3.2:1b")
            edges = await extract_kg_relationships(doc.text, model=kg_model, host=_OLLAMA_HOST)
            if edges:
                db.upsert_kg_edges(edges, source_doc=original_name, workspace_id=workspace_id)

    return IngestResponse(
        status="ok",
        file_name=original_name,
        doc_count=doc_count,
        chunk_count=len(all_chunks),
        source_types=list(source_types),
    )


# ── /knowledge_graph ──────────────────────────────────────────

@app.get("/knowledge_graph", tags=["Knowledge Graph"])
async def get_knowledge_graph(workspace_id: str = Query("default")):
    """Returns the extracted Knowledge Graph nodes and edges."""
    db = _get_struct_db()
    return db.get_knowledge_graph(workspace_id=workspace_id)


# ── /prompt_preview ───────────────────────────────────────────

@app.post("/prompt_preview", tags=["Debug"])
async def prompt_preview(req: QueryRequest):
    """
    Return the exact prompt that would be sent to the model (debug endpoint).
    """
    chunks = _get_retriever().retrieve(req.query, top_k=req.top_k)
    preview = build_prompt_preview(req.query, chunks)
    return {"prompt": preview, "chunk_count": len(chunks)}
