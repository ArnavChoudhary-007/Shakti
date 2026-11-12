import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import DocumentsPage from "./pages/Documents";
import GraphPage from "./pages/Graph";
import ChatPage from "./pages/Chat";
import { createApi, type Api, type IngestJob, type SetupStatus } from "./lib/api";
import { PullsProvider, usePulls } from "./lib/pulls";
import { UploadsProvider, useUploads } from "./lib/uploads";
import { WorkspaceProvider, useWorkspace } from "./lib/workspace";
import { sourceId } from "./components/Answer";

let api: Api;
vi.mock("./lib/connection", () => ({
  useApi: () => api,
  useConnection: () => ({ api, phase: "ready", mode: "companion", baseUrl: "", version: null }),
}));

const ready: SetupStatus = {
  platform: "windows", installed: true, running: true, version: "1", host: "localhost", download_url: "", can_auto_install: false,
  install: { state: "done", downloaded: 0, total: null, error: null }, models: ["test-model"], chat_model: "test-model", chat_model_installed: true,
};
const queued: IngestJob = {
  job_id: "job-1", status: "queued", stage: null, file_name: "notes.txt", workspace_id: "default", doc_count: 0, chunk_count: 0,
  source_types: [], duplicate: false, replaced: 0, file_key: null, error: null,
};
const done: IngestJob = { ...queued, status: "done", chunk_count: 3, file_key: "file-1" };
const emptyGraph = { nodes: [], edges: [], communities: [], pending_docs: 0, building: false };
let respond: (url: URL, init?: RequestInit) => Response | Promise<Response>;
let root: Root;
let container: HTMLDivElement;
let client: QueryClient;
let uploads: ReturnType<typeof useUploads>;
let workspace: ReturnType<typeof useWorkspace>;
let pulls: ReturnType<typeof usePulls>;

function Probe() {
  uploads = useUploads();
  workspace = useWorkspace();
  pulls = usePulls();
  return null;
}

async function advance(ms = 1) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function mount(content: ReactNode, path = "/") {
  container = document.createElement("div");
  document.body.appendChild(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  root = createRoot(container);
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <WorkspaceProvider><PullsProvider><UploadsProvider>
            <Probe />{content}
          </UploadsProvider></PullsProvider></WorkspaceProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await advance();
}

async function click(text: string) {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement | HTMLAnchorElement>("button, a")).find((element) => element.textContent === text);
  expect(button, `Missing control: ${text}`).toBeDefined();
  await act(async () => button!.click());
  await advance();
}

async function selectFile() {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["notes"], "notes.txt")] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
  await advance();
}

beforeEach(() => {
  vi.useFakeTimers();
  api = createApi({ baseUrl: "http://companion.test", token: null });
  respond = (url) => {
    switch (url.pathname) {
      case "/setup/status": return Response.json(ready);
      case "/system/recommendations": return Response.json({ specs: { total_ram_gb: 16, available_ram_gb: 8, cpu_cores: 4 }, ram_budget_gb: 8, installed_models: [], suggested_models: [] });
      case "/workspaces": return Response.json({ workspaces: [] });
      case "/documents": return Response.json({ documents: [] });
      case "/ingest": return Response.json(queued, { status: 202 });
      case "/ingest/status/job-1": return Response.json(done);
      case "/knowledge_graph": return Response.json(emptyGraph);
      default: throw new Error(`Unexpected request: ${url}`);
    }
  };
  vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => Promise.resolve(respond(new URL(input), init))));
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  client?.clear();
  container?.remove();
  vi.useRealTimers();
});

it("shows setup-status errors at home and recovers when Retry succeeds", async () => {
  const fallback = respond;
  let failing = true;
  respond = (url, init) => url.pathname === "/setup/status" && failing ? Response.json({ detail: "Setup unavailable" }, { status: 503 }) : fallback(url, init);
  await mount(<App />);
  expect(container.textContent).toContain("Couldn't check setup status");
  expect(container.textContent).toContain("Setup unavailable");
  failing = false;
  await click("Retry");
  expect(container.querySelector('textarea[aria-label="Your question"]')).not.toBeNull();
});

it("shows graph GET failures instead of an empty graph and allows retry", async () => {
  const fallback = respond;
  let failing = true;
  respond = (url, init) => url.pathname === "/knowledge_graph" && failing ? Response.json({ detail: "Graph unavailable" }, { status: 503 }) : fallback(url, init);
  await mount(<GraphPage />);
  expect(container.textContent).toContain("Couldn't load the graph");
  expect(container.textContent).toContain("Graph unavailable");
  expect(container.textContent).not.toContain("No graph yet");
  failing = false;
  await click("Retry");
  expect(container.textContent).toContain("No graph yet");
  expect(container.textContent).not.toContain("Couldn't load the graph");
});

it("routes an incomplete setup to the setup page", async () => {
  const fallback = respond;
  respond = (url, init) => url.pathname === "/setup/status" ? Response.json({ ...ready, chat_model_installed: false }) : fallback(url, init);
  await mount(<App />);
  expect(container.querySelector('textarea[aria-label="Your question"]')).toBeNull();
  expect(container.textContent).toContain("Choose an AI model");
});

it("keeps graph build errors visible", async () => {
  const fallback = respond;
  respond = (url, init) => url.pathname === "/knowledge_graph" ? Response.json({ ...emptyGraph, build: { state: "error", stage: null, done: 0, total: 1, error: "Model ran out of memory" } }) : fallback(url, init);
  await mount(<GraphPage />);
  expect(container.textContent).toContain("Model ran out of memory");
});

it("does not report incomplete downloads as a successful pull", async () => {
  const fallback = respond;
  respond = (url, init) => url.pathname === "/ollama/pull" ? new Response('{"status":"pulling manifest"}\n') : fallback(url, init);
  await mount(null);
  let result: boolean | undefined;
  await act(async () => { result = await pulls.startPull("test-model"); });
  expect(result).toBe(false);
  expect(pulls.pulls["test-model"].done).toBe(true);
  expect(pulls.pulls["test-model"].status).not.toBe("success");
  expect(pulls.pulls["test-model"].error).toContain("interrupted before completion");
});

function DocumentNavigation() {
  return <><Link to="/documents">Documents page</Link><Link to="/away">Leave documents</Link><Routes>
    <Route path="/documents" element={<DocumentsPage />} />
    <Route path="/away" element={<p>Another page</p>} />
  </Routes></>;
}

describe("upload monitoring", () => {
  it("reports rejected uploads without automatically resubmitting the file", async () => {
    const fallback = respond;
    let posts = 0;
    respond = (url, init) => {
      if (url.pathname === "/ingest") {
        posts++;
        return Response.json({ detail: "File is too large" }, { status: 413 });
      }
      return fallback(url, init);
    };
    await mount(<DocumentsPage />);
    await selectFile();
    await advance(30000);
    expect(container.textContent).toContain("File is too large");
    expect(posts).toBe(1);
    expect(uploads.uploads[0].error).toBe("File is too large");
  });

  it.each(["done", "failed"])("preserves %s results across navigation and workspace changes", async (status) => {
    const fallback = respond;
    let postedWorkspace: FormDataEntryValue | null = null;
    respond = (url, init) => {
      if (url.pathname === "/ingest") postedWorkspace = (init!.body as FormData).get("workspace_id");
      if (url.pathname === "/ingest/status/job-1") return Response.json({ ...done, status, error: status === "failed" ? "Could not read file" : null });
      return fallback(url, init);
    };
    await mount(<DocumentNavigation />, "/documents");
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await selectFile();
    await click("Leave documents");
    await act(async () => workspace.setWorkspace("another-workspace"));
    await advance(1000);
    await click("Documents page");
    expect(container.textContent).toContain("notes.txt");
    expect(container.textContent).toContain(status === "done" ? "3 passages" : "Could not read file");
    expect(uploads.uploads[0].workspace).toBe("default");
    expect(postedWorkspace).toBe("default");
    if (status === "done") {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["documents"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["workspaces"] });
    }
    await click("Clear list");
    expect(uploads.uploads).toEqual([]);
  });

  it.each([0, 503, 429, 408])("recovers from transient polling failure %s without uploading again", async (failure) => {
    const fallback = respond;
    let polls = 0;
    let posts = 0;
    respond = (url, init) => {
      if (url.pathname === "/ingest") posts++;
      if (url.pathname === "/ingest/status/job-1" && ++polls === 1) {
        if (!failure) throw new TypeError("Failed to fetch");
        return Response.json({ detail: "Try again" }, { status: failure });
      }
      return fallback(url, init);
    };
    await mount(<DocumentsPage />);
    await selectFile();
    await advance(1000);
    expect(container.textContent).toContain("Retrying automatically");
    expect(uploads.uploads[0].error).toBeNull();
    await advance(2000);
    expect(container.textContent).toContain("3 passages");
    expect(container.textContent).not.toContain("Retrying automatically");
    expect(posts).toBe(1);
    expect(polls).toBe(2);
  });

  it.each([404, 401])("stops polling on permanent status error %s with helpful guidance", async (status) => {
    const fallback = respond;
    let polls = 0;
    respond = (url, init) => {
      if (url.pathname === "/ingest/status/job-1") {
        polls++;
        return Response.json({ detail: "Unavailable" }, { status });
      }
      return fallback(url, init);
    };
    await mount(<DocumentsPage />);
    await selectFile();
    await advance(1000);
    expect(container.textContent).toContain(status === 404 ? "companion may have restarted" : "Couldn't check upload status");
    expect(container.textContent).toContain("document list before uploading again");
    await advance(30000);
    expect(polls).toBe(1);
  });

  it("stops polling and aborts requests when the provider unmounts", async () => {
    await mount(<DocumentsPage />);
    await selectFile();
    const fetchMock = vi.mocked(fetch);
    const signal = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/ingest"))![1]!.signal;
    await act(async () => root.unmount());
    expect(signal?.aborted).toBe(true);
    await advance(30000);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/ingest/status/"))).toBe(false);
  });
});

it("includes all supported upload formats but not unsupported legacy Office formats", async () => {
  await mount(<DocumentsPage />);
  const accepted = container.querySelector<HTMLInputElement>('input[type="file"]')!.accept.split(",").sort();
  expect(accepted).toEqual([
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".mbox", ".eml", ".pst", ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".mp4",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".json", ".txt", ".md", ".markdown",
  ].sort());
});

it("scrolls to the clicked answer's source when two messages both cite [1]", async () => {
  const messages = ["first", "second"].map((id) => ({
    id, role: "assistant", text: "Answer [1]", stage: null, pending: false, error: null,
    answer: {
      answer: "Answer [1]", citations: [{ index: 1, file_name: `${id}.txt`, location: "page 1", full_text: `${id} source`, source_type: "text", sender: null }],
      model: "test-model", used_sql: false, route_reason: "", structure: null, query: "Question",
    },
  }));
  localStorage.setItem("shakti.chat.default", JSON.stringify(messages));
  await mount(<ChatPage />);
  const first = document.getElementById(sourceId("first", 1))!;
  const second = document.getElementById(sourceId("second", 1))!;
  expect(first).not.toBeNull();
  expect(second).not.toBeNull();
  expect(first.id).not.toBe(second.id);
  const scroll = vi.mocked(Element.prototype.scrollIntoView);
  scroll.mockClear();
  await act(async () => container.querySelectorAll<HTMLButtonElement>('button[aria-label="Show source 1"]')[1].click());
  await advance(50);
  expect(scroll.mock.contexts.at(-1)).toBe(second);
  expect(second.textContent).toContain("second source");
  expect(first.textContent).not.toContain("first source");
});
