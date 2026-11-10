# Architecture

This document is for whoever takes over running or extending the Shakti
companion. It covers the pipeline stages, every API endpoint, the security
model, and the single-user assumptions that remain.

## Pipeline stages

Ingestion and query are two separate paths that share the vector store
and the structured SQLite DB.

```
INGESTION  (POST /ingest; core/sync.py for watched folders)
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

At build time a file's queued pieces are packed into prompts of up to 8,000
characters (at most 12 per file, evenly sampled beyond that), so a 500-message
chat costs a handful of calls instead of 500. Answers are capped at 1,024 tokens
(small models otherwise repeat edges until the context fills); a capped answer
keeps the edges it completed, and malformed edges are dropped rather than
failing the file. A file stays queued until all its pieces succeed.

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
│   ├── search_models.py Downloads the embedder + reranker once (with
│   │                    progress), then always loads them from the local
│   │                    Hugging Face cache (no network)
│   ├── generator.py     Ollama client, citation-grounded prompting,
│   │                    streaming, and extract_kg_relationships
│   ├── kg_builder.py    Clustering/labeling/pruning for the knowledge graph
│   └── model_catalog.py Live hardware specs + Ollama model inventory for
│                        /system/recommendations (no hardcoded model list)
├── structured_db/
│   ├── db.py            SQLite: invoices, ledger rows, KG nodes/edges/
│   │                    communities, KG extraction queue, sync state
│   ├── router.py        Decides whether a query hits SQL or the vector store
│   │                    (and turns "between X and Y" / "in March 2024" into
│   │                    ISO date bounds)
│   └── dates.py         Free-text dates -> ISO; invoices store invoice_date_iso
│                        and are filtered/sorted on it, not on the text
├── api/main.py          FastAPI app — all endpoints (see below)
├── api/security.py      CompanionGuard: Host check, origin allowlist, pairing token
├── core/ollama_setup.py Detect / start / install Ollama
├── settings.py          UI-chosen settings + pairing token (data dir)
├── webui/               Built React UI from web/ (gitignored)
└── __main__.py          `python -m rag_pipeline` launcher
```

## API endpoints

Requests from the companion's own page need nothing extra. The public website
must send `X-Shakti-Token` (see "Security model" below); every other origin
gets 403 before routing. Interactive docs are at `/docs`.

### Companion & setup

| Method | Path | Purpose |
|---|---|---|
| GET | `/version` | Identifies the companion. The only route the public site may call before pairing (to detect an install). |
| GET | `/pair/token` | Pairing token for the public site. Only the companion's own page can read it. |
| GET/PUT | `/settings` | UI-chosen settings (`chat_model`), stored in `<data_dir>/settings.json`. |
| GET | `/setup/status` | Ollama installed/running/version, installed models, chosen model and whether it's installed, auto-install progress, and `search_models` (embedder/reranker downloaded + loaded, download job progress). |
| POST | `/setup/search-models` | Downloads the embedder and reranker if missing, then loads them. Runs in the background; progress via `/setup/status`. |
| POST | `/setup/ollama/start` | Launches Ollama (the tray app on Windows, else `ollama serve`). |
| POST | `/setup/ollama/install` | Windows: downloads `OllamaSetup.exe` from ollama.com, verifies its Authenticode signer is "Ollama Inc.", runs the interactive installer. Progress via `/setup/status`. |
| GET | `/health` | Ollama reachability, embedder readiness, vector store count. Never loads a model. |
| GET | `/system/recommendations` | Live RAM/CPU + installed models + curated suggestions (`data/suggested_models.json`) that fit the RAM budget. 503 if Ollama is down. |
| GET | `/models`, `/ollama/models` | Installed models (filtered / raw) |
| POST | `/ollama/pull` | Streams Ollama's pull progress (NDJSON) |
| DELETE | `/ollama/models/{name}` | Deletes a model |

### Documents & ingestion

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | Multipart upload (`file`, `workspace_id`); returns a job (202). Jobs run one at a time in a worker thread. |
| GET | `/ingest/status/{job_id}` | `queued` → `processing` (`stage`: reading / embedding / saving) → `done` (with `duplicate`, `replaced`, counts) or `failed` |
| GET | `/documents?workspace_id=` | Files in a workspace |
| DELETE | `/documents/{file_key}` | Removes one file everywhere (vectors, SQL rows, KG contributions, stored copy) |
| GET | `/workspaces` | Workspaces holding documents |
| DELETE | `/system/clear?workspace_id=` | Removes everything in a workspace |
| GET | `/docs_count` | Total chunks in the vector store |

**Identity and de-duplication.** A file's `file_key` is derived from its
workspace and content hash; every `doc_id` (`<file_key>-<n>`) and `chunk_id`
derives from it. Uploading identical bytes again is a no-op (`duplicate`);
uploading a changed file with the same name replaces the old version
(`replaced`). Files ingested before the `documents` table existed are
backfilled on startup as `legacy` rows, grouped by workspace and file name.

### Questions

| Method | Path | Purpose |
|---|---|---|
| POST | `/query` | `{query, workspace_id, model?, history?, stream}`. Routes to SQL or document search within the workspace. `stream: true` returns Server-Sent Events: `status` (`understanding` / `searching` / `generating`), many `token`, then `done` (answer, citations, `used_sql`, `route_reason`) or `error`. `stream: false` returns one JSON object. |
| POST | `/prompt_preview` | Debug: the exact prompt for a question |

SQL answers include totals/averages computed in code for numeric columns, so
the model reports numbers rather than adding them up itself.

### Knowledge graph

| Method | Path | Purpose |
|---|---|---|
| GET | `/knowledge_graph?workspace_id=` | Nodes, unpruned edges, communities, `pending_docs` (files waiting), `building`, and `build` progress (`state`, `stage`: extracting / clustering / labelling, `done`/`total`, `error`) |
| POST | `/knowledge_graph/build?workspace_id=` | Starts a background build and returns 202 at once: extracts entities from queued files, then clusters (in a worker thread), labels the larger communities and prunes. One build per workspace at a time (409 otherwise). `/kg/rebuild` is an alias. |

## Security model

The companion is single-user and local; the threat model is *other web pages
in the user's browser* (and other devices on the LAN).

- **Bind + Host check.** Listens on `127.0.0.1`. `CompanionGuard`
  (`api/security.py`) rejects any `Host` other than localhost/127.0.0.1, which
  blocks DNS-rebinding attacks.
- **Origin allowlist enforced before routing.** CORS alone only hides
  responses; browsers still *send* simple cross-site POSTs (forms, multipart).
  So the guard checks every request: no `Origin` + not cross-site (same-origin
  GETs, CLI tools) or a local origin (`http://localhost|127.0.0.1:<port>` and
  the Vite dev server on 5173) → allowed; a configured public-site origin
  (`server.web_origins`) → allowed only with a valid `X-Shakti-Token`, except
  `/version`; anything else → 403.
- **Pairing token.** Random, generated on first run in
  `<data_dir>/companion_token`. The companion's Settings page builds a
  `…/#/connect?token=…` link for the public site; the token travels in the URL
  fragment, which browsers never send to the site's server. `/pair/token` is
  readable only from local origins.
- **No file exposure.** Uploads are stored under `<data_dir>/uploads/<file_key>/`
  and never served; there is no endpoint that ingests an arbitrary path.
- **Rendering.** The UI renders answers with react-markdown, which doesn't
  render raw HTML from model output.
- **Installer launch.** The downloaded Ollama installer runs only if
  `Get-AuthenticodeSignature` reports `Valid` and the signer starts with
  `CN=Ollama Inc.`.

## Remaining single-user assumptions

- **No accounts.** Anyone using the same Windows session can use the
  companion. `workspace_id` separates collections of documents; it is not an
  access boundary.
- **In-memory job store.** `_ingest_jobs` lives in process memory: lost on
  restart (finished jobs also expire after an hour). Ingestion itself is
  durable — only the progress record is lost.
- **One process, one writer.** SQLite and Chroma's persistent client assume a
  single companion process. Don't run two companions on the same data dir.
- **KG build progress is in memory.** A restart mid-build loses the progress
  record; the queue itself is durable, so the next build resumes the files
  that weren't finished.
- **One Ollama host** (`ollama.host`) for everything.

## Model loading

The embedder, vector store and reranker are lazy singletons in `api/main.py`,
each behind its own lock, so concurrent first requests load a model once.
Anything that may load a model runs in a worker thread, never on the event
loop. On startup a background thread loads them if they're already
downloaded; it never downloads. A failed reranker load is remembered for five
minutes instead of being retried on every question (`retriever.py`), and a
successful setup download clears that.
