# Architecture

This document is for whoever takes over running or extending this
service. It covers the pipeline stages, every API endpoint, and — since
this was built as a single-user local tool — exactly where that
assumption is baked in, so you know what has to change before this
could ever serve more than one person at a time.

## Pipeline stages

Ingestion and query are two separate paths that share the vector store
and the structured SQLite DB.

```
INGESTION  (POST /ingest, /ingest_path)
  file on disk
    -> connectors/*  (extract_file)         one envelope per logical unit
                                             (PDF page, chat message, email, ...)
    -> core/normalizer.py                   envelope -> NormalizedDocument
                                             (uniform schema + citation_meta)
    -> core/chunker.py                      NormalizedDocument(s) -> Chunk(s)
                                             (source-aware: text-split / single-
                                             chunk / tumbling window for chat)
    -> core/embedder.py                     Chunk.text -> embedding vector
                                             (batched, sentence-transformers)
    -> core/vectorstore.py                  Chunk + embedding + metadata -> Chroma
    -> structured_db/db.py                  invoice/ledger rows -> SQLite
                                             KG text queued -> kg_pending_docs
                                             (NOT extracted yet — see below)

KNOWLEDGE GRAPH  (POST /knowledge_graph/build, /kg/rebuild — separate action)
    -> core/generator.py:extract_kg_relationships   one Ollama call per queued doc
    -> structured_db/db.py:upsert_kg_data           entities/edges -> kg_nodes, kg_edges
                                                     (type coerced to one of 8 categories)
    -> core/kg_builder.py                           NetworkX + Louvain clustering,
                                                     LLM community labeling,
                                                     edge weight pruning
    -> structured_db/db.py:save_graph_layout        community/centrality -> kg_nodes,
                                                     labels -> kg_communities

QUERY  (POST /query)
    -> structured_db/router.py              routes to SQL (structured_db/db.py) or
                                             vector search (core/retriever.py)
    -> core/retriever.py                    embed query -> vector_store.query()
                                             (+ BM25 hybrid, + reranker)
    -> core/generator.py                    retrieved chunks -> Ollama prompt ->
                                             streamed, citation-grounded answer
```

**Why KG extraction is a separate action, not part of ingestion:** it used to run
synchronously per envelope during `/ingest` — one Ollama call per PDF page or chat
message, which dominated ingestion time (measured: 97.8% of wall-clock on a
representative benchmark). Ingestion now only writes rows to `kg_pending_docs`;
the actual LLM extraction happens when `/knowledge_graph/build` is called, batched
with the clustering step that already ran there.

## Component map

```
rag_pipeline/
├── connectors/        One module per source type: pdf, whatsapp, telegram,
│                       slack, teams, email, excel, invoice, audio, json.
│                       connectors/__init__.py routes a file to the right one
│                       by extension, with content-sniffing for .json (which
│                       telegram/slack/teams/generic all share).
├── core/
│   ├── normalizer.py   Envelope -> NormalizedDocument (uniform schema)
│   ├── chunker.py       Source-aware chunking strategies
│   ├── embedder.py      sentence-transformers wrapper, batched
│   ├── vectorstore.py   Chroma/FAISS interface (config-selected backend)
│   ├── retriever.py     Hybrid retrieval (vector + BM25 + reranker)
│   ├── generator.py     Ollama client, citation-grounded prompting,
│   │                    streaming, and extract_kg_relationships
│   ├── kg_builder.py    Clustering/labeling/pruning for the knowledge graph
│   └── model_catalog.py Live hardware specs + Ollama model inventory for
│                        /system/recommendations (no hardcoded model list)
├── structured_db/
│   ├── db.py            SQLite: invoices, ledger rows, KG nodes/edges/
│   │                    communities, KG extraction queue, sync state
│   └── router.py        Decides whether a query hits SQL or the vector store
├── api/main.py          FastAPI app — all endpoints (see below)
└── frontend/index.html  Single-file HTML/CSS/JS UI, no build step
```

## API endpoints

All routes are unauthenticated and unversioned. Grouped as tagged in the app.

### System

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Ollama reachability, embedder readiness, vector store count |
| GET | `/models` | Locally available Ollama models, filtered to plausible names |
| GET | `/ollama/models` | Raw passthrough of Ollama's `/api/tags` |
| DELETE | `/ollama/models/{model_name}` | Delete a pulled model via Ollama |
| GET | `/system/recommendations` | Live hardware specs (`psutil`) + installed models (from Ollama, not hardcoded) + curated not-yet-installed suggestions (`data/suggested_models.json`), filtered by detected RAM budget. 503 if Ollama is unreachable — never falls back to stale data. |
| POST | `/ollama/pull` | Streams Ollama's model-pull progress back to the client (NDJSON) |
| GET | `/docs_count` | Total chunk count in the vector store |
| DELETE | `/system/clear` | Wipes all chunks + structured data (invoices, ledger, KG, KG queue) for a `workspace_id` |

### RAG

| Method | Path | Purpose |
|---|---|---|
| POST | `/query` | Main entry point. Routes to SQL or vector search, supports `stream: true` (SSE) or JSON. Every answer carries numbered citations. |
| POST | `/prompt_preview` | Debug: returns the exact prompt that would be sent to the model, without generating |

### Ingestion

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | Upload a file (multipart), returns a `job_id` immediately (202), processes in a FastAPI `BackgroundTask` |
| GET | `/ingest/status/{job_id}` | Poll job status: `processing` / `done` / `failed`, includes a per-stage timing breakdown once done |
| POST | `/ingest_path` | Ingest a file already on disk by absolute path, runs synchronously (server-side/CLI use, not the browser UI) |

### Knowledge Graph

| Method | Path | Purpose |
|---|---|---|
| GET | `/knowledge_graph` | Nodes, unpruned edges, and communities for a `workspace_id` |
| POST | `/knowledge_graph/build` | Drains the KG extraction queue, then runs clustering/labeling/pruning. This is where the Ollama calls for entity extraction actually happen. |
| POST | `/kg/rebuild` | Same as above — kept as a separate route for the frontend's "Rebuild Graph" button; there is no functional difference between the two today. |

## Where the single-user assumptions live

This was built to run as one process on one machine for one person. None
of the following would work correctly if exposed to multiple concurrent
users without changes:

- **In-memory job store.** `_ingest_jobs` (`api/main.py`) is a plain Python
  dict living in process memory. It's lost on restart, never expires (grows
  unbounded for the life of the process), and isn't shared across worker
  processes — running `uvicorn` with `--workers > 1` would mean a client
  polling `/ingest/status/{job_id}` might hit a worker that never saw the
  job. There's no request-scoped or per-user isolation here at all.
- **No authentication anywhere.** Every endpoint is open — anything that can
  reach the port can ingest, query, delete, or clear any workspace's data.
  `workspace_id` is a logical partition for organizing data, not an access
  boundary; nothing ties a workspace to an identity or checks that the
  caller is allowed to touch it.
- **CORS is wide open** (`allow_origins=["*"]` in `api/main.py`) — fine for
  a local tool talking to its own bundled frontend, not fine if this ever
  faces the network.
- **Global singleton services.** `_embedder`, `_vector_store`, `_generator`,
  `_struct_db`, etc. are lazily-created module-level singletons, one per
  process, shared by every request. There's no per-request or per-user
  instantiation — this is fine for one user's traffic, but means one slow
  request (e.g. a big batch embed) blocks the shared resources for
  everyone else hitting the same process.
- **Shared upload directory, filename-keyed.** `/ingest` writes to
  `uploads/{original_filename}` with no per-user namespacing. Two
  different "users" (or the same user re-uploading) uploading a
  same-named file overwrite each other on disk.
- **File-based, single-writer stores.** SQLite (`structured_db/structured.db`)
  and Chroma's persistent client are both local-file-based and assume one
  writer. They'll work under moderate concurrent reads but weren't chosen
  or configured for concurrent multi-process writes.
- **Relative paths resolved against process CWD.** `chroma_persist_dir` and
  `sqlite_path` in `config.yaml` are relative (`./chroma_db`,
  `./structured_db/structured.db`). They resolve against whatever directory
  the server happens to be started from — start it from two different
  directories and you get two different, silently-diverging databases. (This
  bit a debugging session during development; always launch from the repo
  root, as `start_windows.bat` and this README do.)
- **One Ollama host for everyone.** `config.yaml`'s `ollama.host` is a single
  shared endpoint — there's no per-user routing to different model servers
  or API keys.
- **KG extraction runs in-request, not in a job queue.** `/knowledge_graph/build`
  makes all its Ollama calls synchronously within that one HTTP request —
  by design, per this project's constraints (no Celery/Redis, single-user
  local tool) — but it means the request blocks for as long as extraction +
  clustering takes (can be minutes on a large workspace), and two
  simultaneous callers building the same workspace's graph would race each
  other with no locking.

None of this is a defect for the tool's actual purpose (one person, one
machine, local documents). It's the list of what would need to change —
auth, per-user resource scoping, a real job queue, a database that
tolerates concurrent writers — before this could be deployed for more
than one person at a time.
