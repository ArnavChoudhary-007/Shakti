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

Download [Shakti-Setup.exe](https://github.com/ArnavChoudhary-007/Shakti/releases/latest/download/Shakti-Setup.exe)
from the latest release and follow the setup wizard. It installs for the current
user without administrator access, with Start-menu and optional desktop shortcuts,
and appears in **Settings > Apps** for uninstalling. Windows 10 version 2004 or
later, or Windows 11, with x64 application support is required.

Select **Install Ollama if missing** to download its official installer, verify
its Ollama Inc. signature, and install it silently. An existing Ollama installation
and its models are kept. Setup downloads a private Python 3.12 runtime and the AI
libraries; allow several gigabytes of disk space and an internet connection.
Shakti then opens in your browser so you can choose and download your AI models.

- **Update:** run the new installer. Existing documents, indexes, pairing and
  settings stay in `%LOCALAPPDATA%\Shakti\data`. The wizard also supports updating
  a copy installed with the PowerShell script at the default location.
- **Uninstall:** use **Settings > Apps > Shakti**. Your data remains in that
  folder, and Ollama is uninstalled separately. Delete the data folder yourself
  only if you want to remove your documents and settings too.
- **Setup errors:** see `%LOCALAPPDATA%\Shakti\installer\setup.log`, then retry.
  If Ollama setup fails, deselect it and install it from the app later.

### PowerShell alternative

Press <kbd>Win</kbd>+<kbd>R</kbd>, paste this and press Enter:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/ArnavChoudhary-007/Shakti/main/install.ps1 | iex"
```

It installs for your user only (no admin rights), then opens Shakti in your
browser. The setup page helps you install Ollama, download a chat model and
the two document-search models (about 180 MB, once), then you upload
documents and start asking. After setup nothing needs the internet. [Read the script](install.ps1) first if
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

## Opening citation originals

Citation numbers still reveal the source excerpt. Each source card also has an
**Open original** action: PDFs open in a new tab at the cited page when available;
images and plain text preview in the browser, while unsupported or active formats
(including Office files, HTML and SVG) download instead.

Originals are served on demand from the companion's retained upload storage,
scoped to the document ID and active workspace. The paired website requests a
short-lived (10-minute) file-only link; the pairing token is never put in the URL.
Links expire on companion restart, and deleted or non-retained originals remain
unavailable without affecting the saved excerpt. Older citations without an ID
can match one exact filename in their workspace, with a warning that it may be a
newer version; ambiguous matches are refused. Allow pop-ups for the UI to open
originals. Viewing originals does not rerun ingestion, embeddings or inference.

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
`npm test` in `web/` (frontend regression tests with a mocked companion), and
`npm run build` in `web/` (type-checks then builds).

## Optional Supabase accounts

Accounts identify users; they do not gate local documents, replace companion
pairing, or isolate files between people sharing a Windows account. Only email,
password and authentication/session data go to Supabase. Documents, chats,
embeddings and knowledge graphs remain local. No cloud tables or storage buckets
are required for this integration. Any future cloud tables need ownership checks
and Row Level Security; protected cloud APIs must validate access tokens.

1. In Supabase, enable the Email provider, allow sign-ups, and enable **Confirm
   email**. Set a minimum password length of at least 8 characters. Configure
   custom SMTP before public launch; the built-in email service is restricted
   and is not suitable for general user registration.
2. Copy `web/.env.example` to `web/.env.local`. Set `VITE_SUPABASE_URL` and
   `VITE_SUPABASE_PUBLISHABLE_KEY` to the project's HTTPS URL and publishable key
   (a legacy `anon` key also works). Never put a `service_role`, `sb_secret_` key,
   or database password in a `VITE_*` variable. Frontend variables are public in
   the built JavaScript, even if the build platform labels them secrets.
   The build refuses incomplete configuration and privileged keys before
   generating frontend assets.
3. In **Authentication > URL Configuration**, set the Site URL to your frontend
   root. Add these exact development Redirect URLs:

   ```text
   http://127.0.0.1:5173/?auth=callback
   http://127.0.0.1:5173/?auth=recovery
   ```

   Add the corresponding pair for `http://localhost:5173/` if you use localhost,
   and for `http://127.0.0.1:8000/` (or localhost:8000) if accounts are enabled in
   the installed companion UI. Add your actual Amplify/custom-domain root with
   the same query strings for production, and make that root the Site URL.
   Do not use broad wildcard production redirect allowlists.
4. Restart Vite after configuration changes, or rebuild for production. Open
   `/#/auth/login`. Sign-up, email confirmation, login, logout, forgotten-password
   and password-update flows are available. Login also appears on the landing
   page and in the app sidebar when configured.

Shakti uses PKCE and **HashRouter**. Email callbacks return to the website root
with `?auth=callback` or `?auth=recovery`; startup exchanges the one-use code and
opens the correct hash route. Use these root/query redirect URLs, not physical
`/auth/callback` or `/auth/reset-password` paths. Leave Supabase's default email
templates using `{{ .ConfirmationURL }}` intact. Open email links in the same
browser/profile and origin that requested them; a different browser/device
does not have the PKCE verifier. Expired or invalid links show recovery guidance.

Sessions persist in this browser. Signing out ends this browser's Supabase
session only; companion pairing and local data remain. With no auth variables,
the build runs in local-only mode. An unavailable Supabase service never blocks
local document features. Authentication pages still work when the companion is
missing or unpaired. Account sessions are separate on the hosted site and local
companion origins.

### Google sign-in

The login and sign-up pages include **Continue with Google**. This uses
Supabase's Google OAuth provider and the same PKCE code exchange as email
callbacks. No Google client secret belongs in frontend configuration.

1. Create a Web application OAuth client in Google Auth Platform. Set its
   authorized redirect URI to the exact Supabase Google provider callback URL,
   `https://YOUR_PROJECT_REF.supabase.co/auth/v1/callback`.
2. Enable Google in Supabase's Authentication provider settings and save the
   Google Client ID and Client Secret there. While the Google app is in testing,
   add the intended Google accounts to its test users.
3. Allow the frontend's root/query callback in Supabase URL Configuration,
   such as `http://127.0.0.1:5173/?auth=callback`. Add localhost, companion and
   deployed origins separately if used; do not mix localhost and 127.0.0.1
   during one sign-in attempt.
4. Open the login page and choose **Continue with Google**. The browser goes
   to Google through Supabase and returns to Shakti to exchange the code.
   After successful sign-in, Shakti returns to the app page where sign-in
   started (or the home/setup screen when opened directly). The Account link
   remains available for session details and sign-out.

Google sign-in does not require a separate Shakti password or a Supabase
confirmation email. Existing email/password flows remain available and retain
their SMTP restrictions. Google authentication does not grant Shakti access to
Google Drive or move local documents to the cloud. Only the public Supabase
URL/key are needed in the app; OAuth credentials remain in Supabase.

Callback URLs are not sign-in entry points: opening one directly reports a
missing sign-in code, not an expired email. Cancellation, expired email links
and failed verification show retry guidance without erasing an existing account
session. Returning to sign-in clears the previous callback error. Pending
account initialization stops blocking the sign-in screen after 15 seconds;
local document features are never gated on it. Sign-in return destinations are
restricted to app routes and never include pairing links or external sites.

### AWS Amplify Hosting

The production website is `https://main.d3ucnkd4219yxd.amplifyapp.com/`.
New companion installations allow that exact origin, and their Settings page
generates pairing links to this address. Existing installations with an explicit
`server.web_origins` list must add the new origin to their preserved configuration.

Connect the repository/branch in Amplify Hosting and use the root `amplify.yml`
build specification. Use Node.js 22 or newer. Set `VITE_SUPABASE_URL` and
`VITE_SUPABASE_PUBLISHABLE_KEY` in Amplify's build environment and redeploy after
changes. The build publishes `rag_pipeline/webui`; hash routes do not require
SPA rewrite rules. A login screen does not require a running Python backend.

Before connecting the hosted website to the companion, configure its exact
HTTPS origin in `server.web_origins` (or `SHAKTI_WEB_ORIGINS`) and update
`PUBLIC_SITE_URL` in `web/src/lib/config.ts` to the deployed root. Rebuild the
installer with the updated defaults for distribution. Do not allow every
Amplify subdomain. Test browser local-network permissions and companion pairing
on the actual production domain; Supabase authentication does not bypass them.

For release builds, set GitHub repository **variables** `SUPABASE_URL` and
`SUPABASE_PUBLISHABLE_KEY` to include optional accounts in the installed UI.
Without them, the release workflow intentionally produces a local-only build.
The existing GitHub Pages workflow remains separate; configuring Amplify does
not automatically turn it off. No AWS or Supabase resources are provisioned by
this repository configuration.

## Trying it with the sample data

`sample_data/` holds three synthetic files about one made-up decision: a vendor
evaluation memo (PDF), an expense ledger (CSV) and a team chat (WhatsApp TXT).
Upload all three on the **Documents** page, then ask:

- *"Which vendor did the team decide to go with, and what is the contract value?"*
  → BrightPack Supplies, $842,000/year, citing the memo.
- *"What is the total amount of expenses paid to BrightPack Supplies in the ledger?"*
  → answered from the spreadsheet rows, with the total computed exactly.

Upload tracking survives navigation between pages and workspaces. Temporary
status-check failures retry automatically without uploading the file again.
Tracking is held in memory, so a full page reload clears it. If the companion
restarts and loses a job, check the document list before uploading again.

Spreadsheet and invoice totals are calculated in SQLite over **all matching
rows** in the workspace, before limiting the row preview. The model sees up to
50 individual rows plus the full count, sums, averages, minima and maxima.
When rows are omitted, the prompt and citation label identify the truncated
preview; the aggregates still cover the entire matching set.

The Knowledge graph page has its own model selector, independent of the chat
model. It defaults to `ollama.graph_model` (`llama3.2:1b` unless configured).
Download the graph model explicitly using **Download graph model**, or select
an already-installed model. Both extraction and cluster labels use that model;
changing it does not change chat. A running build keeps its original model.

Chat has priority over graph model requests, across all workspaces. Starting a
question cancels an in-flight graph request and pauses further graph requests.
After all active answers finish, fail, or are stopped, the interrupted request
is retried automatically; completed graph work is retained. The graph page
shows **Paused for chat** while waiting. Model switching can still add latency;
this does not promise instant answers or a particular speedup.

Knowledge-graph extraction uses `ollama.request_timeout` (300 seconds by
default). If Ollama is unavailable, times out, or returns invalid relationship
data, the build reports an error and keeps failed documents queued. Build the
graph again to retry without re-uploading; successful jobs are not repeated.

The graph explorer groups real extracted entities by their backend community
labels and adds source-file nodes from relationship provenance. Select a group
to animate into it, then select an entity or file to highlight its connections.
Source-file labels appear when exploring a selection. Use **Zoom out**, select
the same node again, or press **Esc** to return to the overview. Wheel zoom,
background drag-to-pan, and keyboard node selection (Enter or Space) are also
supported. Intro animations and pulsing group halos respect reduced-motion
preferences. Fonts and rendering stay local, without a font CDN or demo data.
Large overviews show up to 300 entities and 100 source files and identify when
the display is limited; this does not remove any backend graph data.

## Releasing

- Push a tag like `v0.2.1`: the **Release** workflow builds the UI and the Inno
  Setup Windows installer, tests installation, upgrade, launch and uninstall on
  a disposable Windows runner, then publishes `Shakti-Setup.exe`,
  `shakti-companion.zip` and `SHA256SUMS.txt` to the GitHub release. The tag's
  version is embedded in both packages. `install.ps1` continues to use the ZIP.
- A manual **Release** workflow run builds and tests downloadable Actions
  artifacts without publishing a release or creating a tag.
- To build locally, run `npm --prefix web ci`, `npm --prefix web run build`, then
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1`.
  Use `-Version 0.2.1` to override the package version. The script fetches pinned,
  SHA-256-verified Inno Setup and uv tools into `dist/windows-toolchain`; Inno
  runs in portable mode. The result is `dist/Shakti-Setup.exe`.
- Installer sources are in `installer/windows/`. The EXE contains the web UI,
  companion source and Python manager; Python and dependencies are downloaded
  during installation. It is not an offline installer. The generated Shakti
  installer is currently unsigned; public distribution can show a Windows
  SmartScreen warning until release signing is configured.
- For unattended setup use `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART
  /TASKS="desktopicon,ollama"`; omit `ollama` to skip its installation.
  Silent setup does not launch the app. The installation directory is fixed to
  `%LOCALAPPDATA%\Shakti`; arbitrary `/DIR` overrides are refused.
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
| `structured_db.dates_day_first` | Read `05/03/2024` as 5 March (`true`) or May 3 (`false`) | `false` |
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
