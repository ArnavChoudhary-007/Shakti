# Local-First Citation-Grounded RAG Pipeline

A fully offline, local-first RAG (Retrieval-Augmented Generation) system with:
- **Perplexity-style inline citations** — every answer cites exact source chunks
- **Streaming LLM responses** via Ollama (swappable model per request)
- **Multi-source ingestion** — PDFs, Excel, emails, chats, audio, invoices
- **Multimodal** — text + vision models both via Ollama
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

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Install PyTorch CPU-only for a smaller download
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 4. Pull required Ollama models
ollama pull llama3.2
ollama pull llava

# 5. Configure
# Edit config.yaml — set Ollama host, model names, chunk sizes, etc.

# 6. Run the API server
uvicorn rag_pipeline.api.main:app --reload --port 8000
```

## Project Structure

```
rag_pipeline/
├── connectors/         # One module per source type (Phase 1)
├── core/
│   ├── normalizer.py   # Canonical document schema (Phase 2)
│   ├── chunker.py      # Source-aware chunking (Phase 3)
│   ├── embedder.py     # sentence-transformers encoding (Phase 5)
│   ├── vectorstore.py  # Chroma/FAISS interface (Phase 6)
│   ├── retriever.py    # Hybrid retrieval (Phase 7)
│   └── generator.py    # Citation-grounded streaming generator (Phase 7)
├── structured_db/      # SQLite for tabular/invoice data (Phase 4)
├── api/                # FastAPI endpoints (Phase 8)
├── frontend/           # Plain HTML/CSS/JS UI (Phase 9)
├── chroma_db/          # Chroma persistent storage (auto-created)
├── config.yaml         # All runtime configuration
└── requirements.txt
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

## Build Status

| Phase | Status | Description |
|-------|--------|-------------|
| 0 | ✅ Done | Project scaffold |
| 1 | ⏳ Next | Ingestion connectors |
| 2–11 | 🔲 Pending | See plan |
