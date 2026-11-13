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
import { SourceList, sourceId } from "./components/Answer";
import { graphFixture } from "./test/graph-fixture";

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
      case "/settings": return Response.json({ chat_model: null, effective_chat_model: "test-model", graph_model: null, effective_graph_model: "graph-small:1b" });
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

describe("graph panning", () => {
  async function mountGraph() {
    const fallback = respond;
    respond = (url, init) => url.pathname === "/knowledge_graph" ? Response.json({
      ...emptyGraph,
      nodes: [
        { id: "alice", label: "Alice", type: "person", community: null, centrality: 1 },
        { id: "bob", label: "Bob", type: "person", community: null, centrality: 1 },
      ],
      edges: [{ source: "alice", target: "bob", relation: "knows" }],
    }) : fallback(url, init);
    await mount(<GraphPage />);
    const graph = container.querySelector<SVGSVGElement>('svg[aria-label="Knowledge graph"]')!;
    const captured = new Set<number>();
    Object.assign(graph, {
      setPointerCapture: vi.fn((pointerId: number) => captured.add(pointerId)),
      hasPointerCapture: (pointerId: number) => captured.has(pointerId),
      releasePointerCapture: vi.fn((pointerId: number) => captured.delete(pointerId)),
      getScreenCTM: () => {
        const [, , width, height] = graph.getAttribute("viewBox")!.split(" ").map(Number);
        const scale = Math.min(800 / width, 480 / height);
        return { inverse: () => ({ a: 1 / scale, b: 0, c: 0, d: 1 / scale }) };
      },
    });
    return graph;
  }

  function pointer(graph: SVGSVGElement, type: string, clientX: number, clientY: number, pointerId = 1, button = 0) {
    const event = new MouseEvent(type, { bubbles: true, clientX, clientY, button });
    Object.defineProperty(event, "pointerId", { value: pointerId });
    graph.dispatchEvent(event);
  }

  const viewBox = (graph: SVGSVGElement) => graph.getAttribute("viewBox")!.split(" ").map(Number);

  it("preserves every drag delta even when moves and release are batched", async () => {
    const graph = await mountGraph();
    const before = viewBox(graph);
    const inverseScale = Math.max(before[2] / 800, before[3] / 480);
    await act(async () => {
      pointer(graph, "pointerdown", 100, 100);
      pointer(graph, "pointermove", 120, 110);
      pointer(graph, "pointermove", 150, 130);
      pointer(graph, "pointerup", 150, 130);
    });
    const after = viewBox(graph);
    expect(after[0]).toBeCloseTo(before[0] - 50 * inverseScale);
    expect(after[1]).toBeCloseTo(before[1] - 30 * inverseScale);
    expect(graph.setPointerCapture).toHaveBeenCalledWith(1);
    expect(graph.releasePointerCapture).toHaveBeenCalledWith(1);
    await act(async () => pointer(graph, "pointermove", 200, 200));
    expect(viewBox(graph)).toEqual(after);
  });

  it("pans by screen distance at the current zoom with aspect-ratio padding", async () => {
    const graph = await mountGraph();
    await act(async () => graph.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: -100 })));
    const before = viewBox(graph);
    const inverseScale = Math.max(before[2] / 800, before[3] / 480);
    await act(async () => pointer(graph, "pointerdown", 100, 100));
    await act(async () => pointer(graph, "pointermove", 125, 120));
    const after = viewBox(graph);
    expect(after[0]).toBeCloseTo(before[0] - 25 * inverseScale);
    expect(after[1]).toBeCloseTo(before[1] - 20 * inverseScale);
  });

  it.each(["pointercancel", "lostpointercapture"])("ignores other pointers and ends a drag on %s", async (ending) => {
    const graph = await mountGraph();
    const before = viewBox(graph);
    await act(async () => {
      pointer(graph, "pointerdown", 100, 100, 1, 2);
      pointer(graph, "pointermove", 150, 150);
    });
    expect(viewBox(graph)).toEqual(before);
    await act(async () => pointer(graph, "pointerdown", 100, 100));
    await act(async () => {
      pointer(graph, "pointerdown", 200, 200, 2);
      pointer(graph, "pointermove", 250, 250, 2);
      pointer(graph, "pointerup", 250, 250, 2);
    });
    expect(viewBox(graph)).toEqual(before);
    await act(async () => pointer(graph, "pointermove", 120, 120));
    const moved = viewBox(graph);
    expect(moved).not.toEqual(before);
    await act(async () => {
      pointer(graph, ending, 120, 120);
      pointer(graph, "pointermove", 180, 180);
    });
    expect(viewBox(graph)).toEqual(moved);
    await act(async () => {
      pointer(graph, "pointerdown", 180, 180);
      pointer(graph, "pointermove", 190, 190);
    });
    expect(viewBox(graph)).not.toEqual(moved);
  });
});

describe("interactive graph design", () => {
  async function mountInteractiveGraph(reducedMotion = true) {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: reducedMotion })));
    const fallback = respond;
    respond = (url, init) => url.pathname === "/knowledge_graph" ? Response.json(graphFixture) : fallback(url, init);
    await mount(<GraphPage />);
    return container.querySelector<SVGSVGElement>('svg[aria-label="Knowledge graph"]')!;
  }

  async function choose(label: string) {
    const node = container.querySelector<SVGGElement>(`g[aria-label="${label}"]`)!;
    expect(node).not.toBeNull();
    await act(async () => node.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    return node;
  }

  it("renders live groups, source documents and relationships instead of reference data", async () => {
    await mountInteractiveGraph();
    expect(container.textContent).toContain("2 source files, rendered locally");
    expect(container.textContent).toContain("Research team");
    expect(container.textContent).toContain("4 entities");
    expect(container.textContent).not.toContain("Q3 forecast.xlsx");
    const file = container.querySelector('g[aria-label="File: research.pdf"]')!;
    expect(file.querySelector("text")!.getAttribute("opacity")).toBe("0");
    const group = await choose("Group: Research team");
    expect(group.getAttribute("aria-pressed")).toBe("true");
    expect(file.querySelector("text")!.getAttribute("opacity")).toBe("1");
    expect(container.querySelector('g[aria-label="Entity: Audit"]')!.getAttribute("opacity")).toBe("0.08");
    expect(container.querySelector(".kg-ripple")).not.toBeNull();
    await choose("File: research.pdf");
    expect(container.textContent).toContain("Research team > research.pdf");
    expect(container.textContent).toContain("Entities in this source file");
    expect(container.querySelector('g[aria-label="Entity: Alice"]')!.getAttribute("opacity")).toBe("1");
  });

  it("respects reduced motion and resets on a repeated selection, Zoom out and Escape", async () => {
    const requestFrame = vi.fn();
    vi.stubGlobal("requestAnimationFrame", requestFrame);
    const graph = await mountInteractiveGraph();
    const overview = graph.getAttribute("viewBox");
    let group = await choose("Group: Research team");
    expect(graph.getAttribute("viewBox")).not.toBe(overview);
    expect(requestFrame).not.toHaveBeenCalled();
    await choose("Group: Research team");
    expect(graph.getAttribute("viewBox")).toBe(overview);
    expect(group.getAttribute("aria-pressed")).toBe("false");
    await choose("Group: Research team");
    await click("Zoom out");
    expect(graph.getAttribute("viewBox")).toBe(overview);
    group = await choose("Group: Research team");
    await act(async () => group.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(graph.getAttribute("viewBox")).toBe(overview);
  });

  it("animates the camera and can interrupt it safely with manual zoom", async () => {
    vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 16)));
    vi.stubGlobal("cancelAnimationFrame", vi.fn((handle: number) => window.clearTimeout(handle)));
    const graph = await mountInteractiveGraph(false);
    const overview = graph.getAttribute("viewBox");
    await choose("Group: Research team");
    expect(graph.getAttribute("viewBox")).toBe(overview);
    await advance(200);
    const partial = graph.getAttribute("viewBox");
    expect(partial).not.toBe(overview);
    await advance(1000);
    expect(graph.getAttribute("viewBox")).not.toBe(partial);
    await choose("Group: Finance");
    await advance(100);
    await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="Zoom in"]')!.click());
    expect(cancelAnimationFrame).toHaveBeenCalled();
    const manual = graph.getAttribute("viewBox");
    await advance(1200);
    expect(graph.getAttribute("viewBox")).toBe(manual);
  });

  it("supports Enter and Space on graph nodes and resets when switching workspaces", async () => {
    const graph = await mountInteractiveGraph();
    const overview = graph.getAttribute("viewBox");
    const group = container.querySelector<SVGGElement>('g[aria-label="Group: Research team"]')!;
    await act(async () => group.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(group.getAttribute("aria-pressed")).toBe("true");
    expect(group.getAttribute("tabindex")).toBe("0");
    await act(async () => group.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true })));
    expect(group.getAttribute("aria-pressed")).toBe("false");
    await choose("Entity: Alice");
    expect(container.textContent).toContain("Research lead");
    await act(async () => workspace.setWorkspace("another-workspace"));
    await advance();
    const newGraph = container.querySelector('svg[aria-label="Knowledge graph"]')!;
    expect(newGraph.getAttribute("viewBox")).toBe(overview);
    expect(container.querySelector('g[aria-pressed="true"]')).toBeNull();
  });

  it("prevents page scrolling when using the graph's wheel zoom", async () => {
    const graph = await mountInteractiveGraph();
    const overview = graph.getAttribute("viewBox");
    const event = new WheelEvent("wheel", { bubbles: true, cancelable: true, deltaY: -100 });
    await act(async () => graph.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true);
    expect(graph.getAttribute("viewBox")).not.toBe(overview);
  });

  it("keeps the selected view while build progress polls", async () => {
    let done = 1;
    await mountInteractiveGraph();
    const fallback = respond;
    respond = (url, init) => url.pathname === "/knowledge_graph" ? Response.json({ ...graphFixture, building: true,
      build: { state: "running", stage: "extracting", done, total: 5, error: null },
    }) : fallback(url, init);
    await act(async () => { await client.invalidateQueries({ queryKey: ["graph"] }); });
    await advance();
    await choose("Entity: Alice");
    const graph = container.querySelector('svg[aria-label="Knowledge graph"]')!;
    const focused = graph.getAttribute("viewBox");
    done = 2;
    await advance(1600);
    expect(container.textContent).toContain("2 of 5");
    expect(graph.getAttribute("viewBox")).toBe(focused);
    expect(container.querySelector('g[aria-label="Entity: Alice"]')!.getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps group hit targets at 44 screen pixels on narrow screens and while zoomed", async () => {
    const callbacks: Array<() => void> = [];
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { callbacks.push(callback); }
      observe() {}
      disconnect() {}
    });
    const graph = await mountInteractiveGraph();
    vi.spyOn(graph, "getBoundingClientRect").mockReturnValue({ width: 360, height: 340 } as DOMRect);
    await act(async () => callbacks.forEach((callback) => callback()));
    const hit = container.querySelector('g[aria-label="Group: Research team"] .kg-hit')!;
    expect(Number(hit.getAttribute("r")) * 0.4 * 2).toBeCloseTo(44);
    await choose("Group: Research team");
    const width = Number(graph.getAttribute("viewBox")!.split(" ")[2]);
    expect(Number(hit.getAttribute("r")) * (360 / width) * 2).toBeCloseTo(44);
  });
});

it("keeps graph build errors visible", async () => {
  const fallback = respond;
  respond = (url, init) => url.pathname === "/knowledge_graph" ? Response.json({ ...emptyGraph, build: { state: "error", stage: null, done: 0, total: 1, error: "Model ran out of memory" } }) : fallback(url, init);
  await mount(<GraphPage />);
  expect(container.textContent).toContain("Model ran out of memory");
});

it("changes only the graph model without changing the chat selection", async () => {
  const fallback = respond;
  let graphModel = "graph-small:1b";
  const changes: unknown[] = [];
  respond = (url, init) => {
    if (url.pathname === "/settings") {
      if (init?.method === "PUT") {
        const change = JSON.parse(init.body as string);
        changes.push(change);
        graphModel = change.graph_model;
      }
      return Response.json({ chat_model: "test-model", effective_chat_model: "test-model", graph_model: graphModel, effective_graph_model: graphModel });
    }
    return fallback(url, init);
  };
  await mount(<GraphPage />);
  const select = container.querySelector<HTMLSelectElement>("#graph-model")!;
  expect(select.value).toBe("graph-small:1b");
  await act(async () => {
    select.value = "test-model";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await advance();
  expect(changes).toEqual([{ graph_model: "test-model" }]);
  expect(select.value).toBe("test-model");
  expect(client.getQueryData(["settings"])).toMatchObject({ effective_chat_model: "test-model", effective_graph_model: "test-model" });
});

it.each([false, true])("downloads the graph model explicitly, without selecting it for chat (success=%s)", async (success) => {
  const fallback = respond;
  let installed = false;
  respond = (url, init) => {
    if (url.pathname === "/setup/status") return Response.json({ ...ready, models: installed ? [...ready.models, "graph-small:1b"] : ready.models });
    if (url.pathname === "/ollama/pull") {
      installed = success;
      return new Response(success ? '{"status":"success"}\n' : '{"status":"pulling manifest"}\n');
    }
    return fallback(url, init);
  };
  await mount(<GraphPage />);
  const buildButton = Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Build graph")!;
  expect(buildButton.disabled).toBe(true);
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/ollama/pull"))).toBe(false);
  await click(" Download graph model");
  await advance();
  const requests = vi.mocked(fetch).mock.calls;
  const pullRequest = requests.find(([url]) => String(url).includes("/ollama/pull"))!;
  expect(JSON.parse(pullRequest[1]!.body as string)).toEqual({ model: "graph-small:1b" });
  expect(requests.some(([url, init]) => String(url).endsWith("/settings") && init?.method === "PUT")).toBe(false);
  expect(buildButton.disabled).toBe(!success);
  if (!success) expect(container.textContent).toContain("interrupted before completion");
});

it("shows graph pause status with unchanged progress and resumes automatically", async () => {
  const fallback = respond;
  let paused = true;
  respond = (url, init) => url.pathname === "/knowledge_graph" ? Response.json({ ...emptyGraph, building: true,
    build: { state: "running", stage: "extracting", done: 3, total: 8, error: null, model: "graph-small:1b", paused_for_chat: paused },
  }) : fallback(url, init);
  await mount(<GraphPage />);
  expect(container.textContent).toContain("Paused for chat");
  expect(container.textContent).toContain("3 of 8");
  expect(container.querySelector<HTMLSelectElement>("#graph-model")!.disabled).toBe(true);
  paused = false;
  await advance(1500);
  expect(container.textContent).not.toContain("Paused for chat");
  expect(container.textContent).toContain("3 of 8");
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

const originalCitation = { index: 1, file_key: "file-1", file_name: "report.pdf", location: "page 3 of 10", full_text: "Saved source excerpt", source_type: "pdf", sender: null };

function originalTab() {
  const tab = { opener: {}, location: { replace: vi.fn() }, close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
  return tab;
}

it("opens a PDF original at its cited page without changing the excerpt toggle", async () => {
  const tab = originalTab();
  let body: unknown;
  const fallback = respond;
  respond = (url, init) => {
    if (url.pathname !== "/documents/original") return fallback(url, init);
    body = JSON.parse(init!.body as string);
    return Response.json({ path: "/documents/file-1/content?view_token=short", file_name: "report.pdf", previewable: true, matched_by_name: false });
  };
  await mount(<SourceList messageId="answer" citations={[originalCitation]} highlighted={null} />);
  await act(async () => workspace.setWorkspace("research"));
  await click("Open original");
  expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
  expect(tab.opener).toBeNull();
  expect(body).toEqual({ workspace_id: "research", file_key: "file-1", file_name: "report.pdf" });
  expect(tab.location.replace).toHaveBeenCalledWith("http://companion.test/documents/file-1/content?view_token=short#page=3");
  expect(tab.close).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Opened original in a new tab.");
  expect(container.textContent).not.toContain(originalCitation.full_text);
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-expanded]')!.click());
  expect(container.textContent).toContain(originalCitation.full_text);
});

it("warns when older citations use filename matching and retains the excerpt", async () => {
  const tab = originalTab();
  const citation = { ...originalCitation, file_key: undefined };
  respond = () => Response.json({ path: "/documents/file-1/content?view_token=short", file_name: "report.pdf", previewable: true, matched_by_name: true });
  await mount(<SourceList messageId="old" citations={[citation]} highlighted={1} />);
  await click("Open original");
  expect(tab.location.replace).toHaveBeenCalled();
  expect(container.textContent).toContain("stored file may be newer");
  expect(container.textContent).toContain(citation.full_text);
});

it("starts unsupported original downloads without a PDF page fragment", async () => {
  const tab = originalTab();
  respond = () => Response.json({ path: "/documents/file-1/content?view_token=short", file_name: "report.docx", previewable: false, matched_by_name: false });
  await mount(<SourceList messageId="word" citations={[{ ...originalCitation, file_name: "report.docx" }]} highlighted={null} />);
  await click("Open original");
  expect(tab.location.replace).toHaveBeenCalledWith("http://companion.test/documents/file-1/content?view_token=short");
  expect(container.textContent).toContain("Original download started");
});

it.each(["This source is no longer available", "More than one original matches this older citation"])("keeps excerpts usable when originals fail: %s", async (message) => {
  const tab = originalTab();
  respond = () => Response.json({ detail: message }, { status: 404 });
  await mount(<SourceList messageId="missing" citations={[originalCitation]} highlighted={null} />);
  await click("Open original");
  expect(tab.close).toHaveBeenCalledOnce();
  expect(tab.location.replace).not.toHaveBeenCalled();
  expect(container.querySelector('[role="alert"]')!.textContent).toContain(message);
  expect(container.textContent).toContain(originalCitation.full_text);
  expect(container.querySelector<HTMLButtonElement>('button[aria-label="Open original: report.pdf"]')!.disabled).toBe(false);
});

it("explains popup blocking and expands the excerpt without requesting a file", async () => {
  vi.spyOn(window, "open").mockReturnValue(null);
  await mount(<SourceList messageId="blocked" citations={[originalCitation]} highlighted={null} />);
  vi.mocked(fetch).mockClear();
  await click("Open original");
  expect(fetch).not.toHaveBeenCalled();
  expect(container.querySelector('[role="alert"]')!.textContent).toContain("blocked the new tab");
  expect(container.textContent).toContain(originalCitation.full_text);
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
