// Typed client for the Shakti companion API.

export type Version = { name: string; version: string; api: number };

export type InstallProgress = {
  state: "idle" | "downloading" | "verifying" | "installer_running" | "done" | "error";
  downloaded: number;
  total: number | null;
  error: string | null;
};

export type SearchModelsStatus = {
  ready: boolean;
  models: { kind: "embedding" | "reranker"; name: string; repo: string; downloaded: boolean; loaded: boolean }[];
  job: { state: "idle" | "downloading" | "loading" | "ready" | "error"; downloaded: number; total: number | null; error: string | null };
};

export type SetupStatus = {
  platform: "windows" | "macos" | "linux";
  installed: boolean;
  running: boolean;
  version: string | null;
  host: string;
  download_url: string;
  can_auto_install: boolean;
  install: InstallProgress;
  models: string[];
  chat_model: string;
  chat_model_installed: boolean;
  /** Missing on companions older than 0.2.0 — treat as ready. */
  search_models?: SearchModelsStatus;
};

/** Embedding + reranker models downloaded (older companions don't report it). */
export const searchModelsReady = (s: SetupStatus) => s.search_models?.ready ?? true;

export type InstalledModel = {
  name: string;
  size_gb: number | null;
  parameter_size: string | null;
  quantization: string | null;
  is_cloud: boolean;
  fits_comfortably: boolean;
};

export type SuggestedModel = {
  name: string;
  approx_size_gb: number | null;
  description: string;
  tags: string[];
  installed: boolean;
};

export type Recommendations = {
  specs: { total_ram_gb: number; available_ram_gb: number; cpu_cores: number };
  ram_budget_gb: number;
  installed_models: InstalledModel[];
  suggested_models: SuggestedModel[];
};

export type Settings = { chat_model: string | null; effective_chat_model: string };

export type DocumentRow = {
  file_key: string;
  workspace_id: string;
  file_name: string;
  size_bytes: number;
  source_types: string[];
  doc_count: number;
  chunk_count: number;
  legacy: boolean;
  ingested_at: string;
};

export type IngestJob = {
  job_id: string;
  status: "queued" | "processing" | "done" | "failed";
  stage: "reading" | "embedding" | "saving" | null;
  file_name: string;
  workspace_id: string;
  doc_count: number;
  chunk_count: number;
  source_types: string[];
  duplicate: boolean;
  replaced: number;
  file_key: string | null;
  error: string | null;
};

export type Citation = {
  index: number;
  file_name: string;
  location: string;
  full_text: string;
  source_type: string;
  sender: string | null;
};

export type Answer = {
  answer: string;
  citations: Citation[];
  model: string;
  used_sql: boolean;
  route_reason: string;
  structure: { has_headers?: boolean; has_bullets?: boolean } | null;
  query: string;
};

export type GraphData = {
  nodes: { id: string; label: string; type: string | null; description: string | null; community: number | null; centrality: number | null }[];
  edges: { source: string; target: string; relation: string; description: string | null; source_doc: string | null }[];
  communities: { id: number; label: string }[];
  /** Documents waiting to be added to the graph. */
  pending_docs: number;
  building: boolean;
  /** Missing on older companions. */
  build?: GraphBuild;
};

export type GraphBuild = {
  state: "idle" | "running" | "done" | "error";
  stage: "starting" | "extracting" | "clustering" | "labelling" | null;
  done: number;
  total: number;
  error: string | null;
};

export type ChatTurn = { role: "user" | "assistant"; content: string };

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export type Connection = { baseUrl: string; token: string | null };

async function errorFrom(res: Response): Promise<ApiError> {
  let message = `${res.status} ${res.statusText}`;
  let code: string | undefined;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") message = body.detail;
    code = body?.code;
  } catch {
    /* not JSON */
  }
  return new ApiError(res.status, message, code);
}

export function createApi(conn: Connection) {
  const url = (path: string) => `${conn.baseUrl}${path}`;
  const headers = (extra?: HeadersInit): HeadersInit => {
    const h: Record<string, string> = {};
    if (conn.token) h["X-Shakti-Token"] = conn.token;
    return { ...h, ...(extra as Record<string, string>) };
  };

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(url(path), { ...init, headers: headers(init.headers) });
    if (!res.ok) throw await errorFrom(res);
    return (await res.json()) as T;
  }

  const json = (method: string, body?: unknown): RequestInit => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  return {
    version: () => request<Version>("/version"),
    pairToken: () => request<{ token: string; web_origins: string[] }>("/pair/token"),
    settings: () => request<Settings>("/settings"),
    updateSettings: (s: Partial<Pick<Settings, "chat_model">>) => request<Settings>("/settings", json("PUT", s)),

    setupStatus: () => request<SetupStatus>("/setup/status"),
    startOllama: () => request<{ status: string }>("/setup/ollama/start", json("POST")),
    installOllama: () => request<InstallProgress>("/setup/ollama/install", json("POST")),
    prepareSearchModels: () => request<SearchModelsStatus["job"]>("/setup/search-models", json("POST")),

    recommendations: () => request<Recommendations>("/system/recommendations"),
    deleteModel: (name: string) => request<{ status: string }>(`/ollama/models/${encodeURIComponent(name)}`, { method: "DELETE" }),

    /** Streams Ollama pull progress (NDJSON) to onProgress. */
    async pullModel(name: string, onProgress: (p: { status: string; total?: number; completed?: number }) => void, signal?: AbortSignal) {
      const res = await fetch(url("/ollama/pull"), { ...json("POST", { model: name }), headers: headers({ "Content-Type": "application/json" }), signal });
      if (!res.ok || !res.body) throw await errorFrom(res);
      for await (const line of readLines(res.body)) {
        if (!line.trim()) continue;
        const msg = JSON.parse(line);
        if (msg.error) throw new ApiError(500, msg.error);
        onProgress(msg);
      }
    },

    workspaces: () => request<{ workspaces: { workspace_id: string; document_count: number }[] }>("/workspaces"),
    documents: (workspace: string) => request<{ documents: DocumentRow[] }>(`/documents?workspace_id=${encodeURIComponent(workspace)}`),
    deleteDocument: (fileKey: string) => request<{ status: string }>(`/documents/${encodeURIComponent(fileKey)}`, { method: "DELETE" }),
    clearWorkspace: (workspace: string) => request<{ status: string }>(`/system/clear?workspace_id=${encodeURIComponent(workspace)}`, { method: "DELETE" }),

    async upload(file: File, workspace: string): Promise<IngestJob> {
      const form = new FormData();
      form.append("file", file);
      form.append("workspace_id", workspace);
      return request<IngestJob>("/ingest", { method: "POST", body: form });
    },
    ingestStatus: (jobId: string) => request<IngestJob>(`/ingest/status/${jobId}`),

    graph: (workspace: string) => request<GraphData>(`/knowledge_graph?workspace_id=${encodeURIComponent(workspace)}`),
    buildGraph: (workspace: string) => request<GraphBuild>(`/knowledge_graph/build?workspace_id=${encodeURIComponent(workspace)}`, json("POST")),

    /** Streams an answer. Server-Sent Events: status, token, done, error. */
    async ask(
      body: { query: string; workspace_id: string; model?: string; history?: ChatTurn[] },
      handlers: {
        onStatus?: (s: { stage: string; sources?: number; used_sql?: boolean }) => void;
        onToken: (text: string) => void;
        onDone: (a: Answer) => void;
        onError: (message: string) => void;
      },
      signal?: AbortSignal,
    ) {
      const res = await fetch(url("/query"), {
        ...json("POST", { ...body, stream: true }),
        headers: headers({ "Content-Type": "application/json" }),
        signal,
      });
      if (!res.ok || !res.body) throw await errorFrom(res);
      let finished = false;
      for await (const ev of readSse(res.body)) {
        const data = JSON.parse(ev.data);
        if (ev.event === "status") handlers.onStatus?.(data);
        else if (ev.event === "token") handlers.onToken(data.text);
        else if (ev.event === "done") {
          finished = true;
          handlers.onDone(data);
        } else if (ev.event === "error") {
          finished = true;
          handlers.onError(data.message);
        }
      }
      if (!finished) handlers.onError("The connection to Shakti closed before the answer finished.");
    },
  };
}

export type Api = ReturnType<typeof createApi>;

async function* readLines(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    yield* lines;
  }
  if (buffer) yield buffer;
}

export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  let event = "message";
  let data: string[] = [];
  for await (const raw of readLines(body)) {
    const line = raw.replace(/\r$/, "");
    if (line === "") {
      if (data.length) yield { event, data: data.join("\n") };
      event = "message";
      data = [];
    } else if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      data.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (data.length) yield { event, data: data.join("\n") };
}
