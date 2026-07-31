# Local-First Citation-Grounded RAG Pipeline

A fully offline, local-first RAG (Retrieval-Augmented Generation) system with:
- **Perplexity-style inline citations** — every answer cites exact source chunks
- **Streaming LLM responses** via Ollama (swappable model per request)
- **Multi-source ingestion** — PDFs, Excel, emails, chats, audio, invoices
- **Multimodal** — text + vision models both via Ollama
- **Dynamic Hardware Recommendations** — Automatically suggests models tailored to your system's available RAM and CPU
- **Asynchronous Knowledge Graph Extraction** — Non-blocking, decoupled entity extraction for lightning-fast ingestion
- **Zero cloud dependencies** — runs fully offline after initial setup

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai) running locally (`ollama serve`)
- Tesseract OCR (for scanned PDFs): install via system package manager
- At least one Ollama text model pulled: `ollama pull llama3.2`
- At least one Ollama vision model pulled: `ollama pull llava`

## Quick Start (For Non-Technical Users on Windows)

We have included a 1-click launcher to handle all the complex technical setup for you.

1. Ensure you have installed **Python** (from python.org) and **Ollama** (from ollama.ai).
2. Simply double-click the **`start_windows.bat`** file in the project folder.
3. The script will automatically install dependencies, download the necessary AI models, and open the web app in your browser!

---

## Developer / Manual Setup (For Advanced Users)

If you are on macOS/Linux, or prefer to set up the environment manually:

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # macOS/Linux

# 2. Install dependencies (pinned to exact versions — see rag_pipeline/requirements.txt)
pip install -r rag_pipeline/requirements.txt

# 3. (Optional) Install PyTorch CPU-only for a smaller download
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 4. Pull required Ollama models
ollama pull llama3.2
ollama pull llava

# 5. Configure
cp rag_pipeline/config.example.yaml rag_pipeline/config.yaml
# Edit rag_pipeline/config.yaml if you need to change the Ollama host,
# model names, chunk sizes, etc. — config.yaml is gitignored, so your
# local edits never get committed.

# 6. Run the API server — always from the repo root (see note below)
uvicorn rag_pipeline.api.main:app --reload --port 8000
```

> **Always launch from the repo root**, not from inside `rag_pipeline/`.
> `chroma_persist_dir` and `sqlite_path` in `config.yaml` are relative
> paths, resolved against whatever directory the process starts in — launching
> from the wrong directory silently creates a second, empty database instead
> of using the real one. `start_windows.bat` already does this correctly.

## Running the Demo

Once the server is running (`http://localhost:8000` in your browser):

1. Go to the **Ingest** tab and upload the files in `sample_data/` — a
   short PDF, a CSV, and a synthetic WhatsApp-style chat export. None of it
   is real; it exists so you can see the whole pipeline work without
   needing your own documents.
2. Once ingestion finishes (watch the per-file progress in the UI), switch
   to the **Chat** tab and ask a question the sample data can actually
   answer — e.g. *"What did the team decide about the vendor contract?"*
   or *"What's in the invoice?"* (adjust to whatever ends up in
   `sample_data/` — see the folder's own contents for what to ask).
3. The answer should stream in with numbered citations. Click one — it
   should open the original file at the right page/location, not just
   show a text snippet.
4. Optional: go to the **Knowledge Graph** tab and click **Rebuild
   Graph** to see entity extraction run (this makes real Ollama calls
   and can take a minute or two even on this small sample set).

## Out of Scope

This is a local, single-user tool. Explicitly **not** handled:

- **Multiple concurrent users / multi-tenancy.** There's no
  authentication, no per-user data isolation, and `workspace_id` is a
  logical label, not a security boundary. See `ARCHITECTURE.md` for the
  full list of single-user assumptions baked into the code.
- **Any LLM provider other than Ollama.** No OpenAI/Anthropic/Gemini/etc.
  integration exists or is planned; the only non-local option is an
  Ollama `:cloud` model.
- **A distributed task queue.** Background ingestion uses FastAPI's
  built-in `BackgroundTasks`, not Celery/RQ/etc. — by design, not as a
  stopgap.
- **GPU as a requirement.** The tested default path is CPU-only
  (targeting modest hardware, e.g. 16GB RAM, no dedicated GPU); a GPU
  will be used opportunistically if `config.yaml`'s `device` is set to
  `cuda`, but nothing is validated against one.
- **Production-grade auth, rate limiting, or hardening.** CORS is wide
  open and every endpoint is unauthenticated — appropriate for a tool
  that only ever talks to itself on `localhost`, not for anything
  exposed to a network.
- **PST email support out of the box.** The mbox/eml paths work with the
  pinned dependencies; PST requires `libpff-python`, which isn't
  installed by default (see the comment in `requirements.txt`) since it
  can require build tools on Windows.
- **Non-English content quality.** Nothing in the pipeline assumes a
  language, but chunk sizes, the entity-type prompt, and citation
  formatting were only tuned and tested against English documents.

## Project Structure

```
p1/
├── sample_data/                    # Synthetic files for the demo — see "Running the Demo"
├── ARCHITECTURE.md                 # Pipeline internals, API reference, single-user assumptions
├── start_windows.bat               # 1-click launcher (Windows)
├── chroma_db/                      # Chroma persistent storage (auto-created, gitignored)
├── structured_db/                  # SQLite data files (auto-created, gitignored)
└── rag_pipeline/
    ├── connectors/                 # One module per source type
    ├── core/
    │   ├── normalizer.py           # Canonical document schema
    │   ├── chunker.py              # Source-aware chunking
    │   ├── embedder.py             # sentence-transformers encoding
    │   ├── vectorstore.py          # Chroma/FAISS interface
    │   ├── retriever.py            # Hybrid retrieval
    │   ├── generator.py            # Citation-grounded streaming generator
    │   ├── kg_builder.py           # Knowledge graph clustering/labeling
    │   └── model_catalog.py        # Live hardware + Ollama model detection
    ├── structured_db/              # SQLite access layer (db.py, router.py)
    ├── api/                        # FastAPI app (main.py)
    ├── frontend/                   # Plain HTML/CSS/JS UI, no build step
    ├── tests/                      # pytest suite + synthetic fixtures
    ├── config.example.yaml         # Template — copy to config.yaml
    ├── config.yaml                 # Your local config (gitignored, not committed)
    └── requirements.txt            # Pinned to exact versions
```

## Configuration

All tunable parameters live in `config.yaml`:

| Key | Description | Default |
|-----|-------------|---------|
| `embedding.model_name` | sentence-transformers model | `all-MiniLM-L6-v2` |
| `vector_store.backend` | `chroma` or `faiss` | `chroma` |
| `ollama.host` | Ollama API URL | `http://localhost:11434` |
| `ollama.default_text_model` | Default LLM | `llama3.2` |
| `ollama.default_vision_model` | Default vision model | `llava` |
| `chunking.default_chunk_size` | Chars per chunk | `800` |
| `chunking.default_chunk_overlap` | Overlap between chunks | `150` |
| `audio.whisper_model_size` | Whisper model size | `base` |
| `audio.diarization_enabled` | Speaker diarization (needs HF token) | `false` |

## Citations

Every generated answer contains inline `[N]` markers that map to:
- **File name** — original source file
- **Location** — page number, timestamp, or message range
- **Snippet** — the exact text the model cited

The frontend renders these as hoverable pills showing full source context.

## Supported Source Types

| Source | Format | Notes |
|--------|--------|-------|
| PDFs | `.pdf` | Digital text + OCR fallback for scanned |
| Excel / CSV | `.xlsx`, `.xls`, `.csv` | Tabular sheets → SQLite; narrative → vector |
| Emails | `.mbox`, `.pst` | PST requires `libpff-python` |
| WhatsApp | `.txt` | Standard export format |
| Telegram | `.json` | Standard export format |
| Slack | `.json` | Channel-based export |
| Teams | `.json` | Standard export format |
| Invoices | `.pdf`, images | Structured field extraction |
| Audio calls | `.mp3`, `.wav`, `.m4a` | Transcription + optional diarization |

## Architecture

See `ARCHITECTURE.md` for pipeline internals, the full API endpoint
reference, and — importantly — exactly where this codebase assumes a
single user on a single machine.
