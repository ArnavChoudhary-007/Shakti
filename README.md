# Shakti — private document AI

Ask questions about your own documents and get answers with citations, using
AI that runs **entirely on your PC**. No account, no cloud, no subscription.

- **Answers with sources.** Every claim cites the exact page, message or row it
  came from.
- **All your formats.** PDF, Word, PowerPoint, Excel/CSV, text & Markdown,
  emails, WhatsApp/Telegram/Slack/Teams exports, audio, scanned invoices.
- **Runs on your hardware.** Uses [Ollama](https://ollama.com) for the AI
  model; Shakti suggests models that fit your PC's memory.
- **Workspaces** keep separate collections of documents apart.
- **Knowledge graph** of the people, organizations and ideas in your files.

## Install (Windows)

Press <kbd>Win</kbd>+<kbd>R</kbd>, paste this and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/ArnavChoudhary-007/Shakti/main/install.ps1 | iex"
```

It installs for your user only (no admin rights), then opens Shakti in your
browser. The setup page helps you install Ollama and download a model, then
you upload documents and start asking. [Read the script](install.ps1) first if
you like — it installs [uv](https://docs.astral.sh/uv/) (which brings its own
Python), downloads the latest release, and adds a Start-menu shortcut.

- **Update:** run the same command again. Your documents and settings are kept.
- **Uninstall:** `& ([scriptblock]::Create((irm https://raw.githubusercontent.com/ArnavChoudhary-007/Shakti/main/install.ps1))) -Uninstall`
  (removes the app; your data stays in `%LOCALAPPDATA%\Shakti\data` until you delete it).

macOS and Linux installers aren't ready yet — use the developer setup below.

## How it works

```
 Your browser                              Your PC
┌──────────────────────────┐   127.0.0.1  ┌──────────────────────────────┐
│ Shakti website           │ ───────────▶ │ Shakti companion (FastAPI)   │
│  • served by the         │              │  parse → chunk → embed →     │
│    companion, or         │              │  search → cite               │
│  • the public site       │              │  SQLite + Chroma, data dir   │
│    (GitHub Pages)        │              └──────────────┬───────────────┘
└──────────────────────────┘                             │ 127.0.0.1:11434
                                                         ▼
                                                   Ollama (the LLM)
```

The **companion** is the Python pipeline in `rag_pipeline/`. It listens only on
`127.0.0.1`, stores everything under one data folder
(`%LOCALAPPDATA%\Shakti\data` when installed), and serves the web UI at
<http://127.0.0.1:8000>.

The same web UI is also published as a static site. Opened there, it finds the
companion on your PC. Before it can do anything it must be **paired**: open
Shakti → Settings → *Connect the website*. Every other website is refused —
see [Security](#security).

## Developer setup

Requirements: Python 3.10–3.12 (the installer uses 3.12; 3.9 still runs the
tests), Node.js 20+, and [Ollama](https://ollama.com/download).

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r rag_pipeline/requirements.txt

cd web && npm ci && npm run build && cd ..   # builds the UI into rag_pipeline/webui

python -m rag_pipeline            # starts on http://127.0.0.1:8000 and opens the browser
```

On Windows, `start_windows.bat` does all of the above.

Useful options: `python -m rag_pipeline --data-dir D:\shakti-data --port 8001 --no-browser`.
Without `--data-dir` (or `SHAKTI_DATA_DIR`) a dev checkout keeps its data in the
repo root (`chroma_db/`, `structured_db/`, `uploads/`), all gitignored.

**Working on the UI:** run the companion, then `npm run dev` in `web/` and open
<http://localhost:5173> — Vite hot-reloads, and the dev origin is trusted by the
companion so no pairing is needed.

**Tests:** `python -m pytest rag_pipeline/tests` (no Ollama needed — the API
end-to-end test stubs the model and uses a temporary data folder), and
`npm run build` in `web/` (type-checks then builds).

## Trying it with the sample data

`sample_data/` holds three synthetic files about one made-up decision: a vendor
evaluation memo (PDF), an expense ledger (CSV) and a team chat (WhatsApp TXT).
Upload all three on the **Documents** page, then ask:

- *"Which vendor did the team decide to go with, and what is the contract value?"*
  → BrightPack Supplies, $842,000/year, citing the memo.
- *"What is the total amount of expenses paid to BrightPack Supplies in the ledger?"*
  → answered from the spreadsheet rows, with the total computed exactly.

## Releasing

- Push a tag like `v0.2.1`: the **Release** workflow builds the UI, packages
  `dist/shakti-companion.zip` (`scripts/package_release.py`) and attaches it to a
  GitHub release — that's what `install.ps1` downloads.
- Pushes to `main` that touch `web/` redeploy the public site (**Website**
  workflow). Enable it once under *Settings → Pages → Source: GitHub Actions*.
- If you fork, update `web/src/lib/config.ts` (repo, site URL) and
  `server.web_origins` in `config.example.yaml`.

## Configuration

Installed copies read `%LOCALAPPDATA%\Shakti\data\config.yaml`; dev checkouts
read `rag_pipeline/config.yaml`, falling back to `config.example.yaml`. The
chat model chosen in the UI is saved in `settings.json` and overrides
`ollama.default_text_model`.

| Key | Description | Default |
|-----|-------------|---------|
| `server.port` | Companion port (`SHAKTI_PORT`) | `8000` |
| `server.web_origins` | Public sites allowed to pair (`SHAKTI_WEB_ORIGINS`) | the GitHub Pages site |
| `ollama.host` | Ollama API URL | `http://localhost:11434` |
| `ollama.keep_alive` / `num_ctx` | Keep the model loaded / context window | `30m` / `8192` |
| `embedding.model_name` | sentence-transformers model | `all-MiniLM-L6-v2` |
| `vector_store.backend` | `chroma` or `faiss` | `chroma` |
| `retrieval.final_k` | Passages given to the model per question | `6` |
| `retrieval.use_llm_grader` | Extra relevance check (slower) | `false` |
| `chunking.default_chunk_size` | Characters per chunk | `800` |
| `audio.whisper_model_size` | Whisper model size | `base` |

## Security

The companion is a local service, so the threat is *other websites in your
browser* calling it. It defends against that:

- It listens on `127.0.0.1` only and rejects requests whose `Host` isn't
  localhost (blocks DNS rebinding).
- Requests from any origin other than its own page are refused **before
  routing** — including form posts that CORS alone would let through. The
  public site is allowed only with the pairing token, and can never read it.
- Uploaded files aren't served back over HTTP, and there's no endpoint that
  reads arbitrary paths.
- Model output is rendered as Markdown without raw HTML.
- The Ollama installer is only launched if it carries a valid "Ollama Inc."
  code signature.

It is still a single-user tool: there are no accounts, and anyone with access
to your Windows session can use it. See `ARCHITECTURE.md` for internals.

## Limits

- Windows is the only tested platform for now.
- Answers are only as good as the local model; small models (1–3B) can
  misread tables or mix up details. Check the cited sources for anything
  important.
- `.doc`/`.ppt` (pre-2007 Office) files must be saved as `.docx`/`.pptx`.
  PST email needs `libpff-python`, which isn't installed by default.
- Tuned and tested with English documents.

## Project structure

```
├── install.ps1              # one-line Windows installer
├── start_windows.bat        # dev launcher
├── scripts/package_release.py
├── web/                     # React + Vite + TypeScript UI → builds into rag_pipeline/webui
├── sample_data/             # synthetic demo files
└── rag_pipeline/
    ├── __main__.py          # `python -m rag_pipeline` launcher
    ├── api/                 # FastAPI app (main.py) + request guard (security.py)
    ├── connectors/          # one module per source type
    ├── core/                # normalize, chunk, embed, store, retrieve, generate, KG, Ollama setup
    ├── structured_db/       # SQLite (invoices, ledgers, KG, documents) + query router
    ├── settings.py          # UI-chosen settings + pairing token
    ├── tests/
    └── config.example.yaml
```
