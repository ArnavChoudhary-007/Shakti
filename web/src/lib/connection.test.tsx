import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { useQuery, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionProvider, useApi, useConnection } from "./connection";
import { UploadsProvider, useUploads } from "./uploads";

// The real ConnectionProvider, talking to two fake companions. Regression test:
// query keys don't name the backend, so switching the address used to keep
// serving the previous backend's cached documents and never ask the new one.

const A = "http://127.0.0.1:8001";
const B = "http://127.0.0.1:8002";
const DOCS: Record<string, string[]> = { [A]: ["from-a.pdf"], [B]: ["from-b.pdf"] };

let requests: string[];
let root: Root;
let container: HTMLDivElement;
let connection: ReturnType<typeof useConnection>;
let uploads: ReturnType<typeof useUploads>;

function Docs() {
  const api = useApi();
  const q = useQuery({ queryKey: ["documents", "default"], queryFn: () => api.documents("default") });
  connection = useConnection();
  uploads = useUploads();
  return (
    <>
      <p data-testid="docs">{q.data ? q.data.documents.map((d) => d.file_name).join(",") : "loading"}</p>
      <p data-testid="uploads">{uploads.uploads.map((u) => u.file.name).join(",")}</p>
    </>
  );
}

const job = (status: string) => ({
  job_id: "job-1", status, stage: "embedding", file_name: "slow.pdf", workspace_id: "default",
  doc_count: 0, chunk_count: 0, source_types: [], duplicate: false, replaced: 0, file_key: null, error: null,
});

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}

async function flush() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
}

beforeEach(async () => {
  requests = [];
  localStorage.setItem("shakti.companionUrl", A);
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
    requests.push(url.href);
    const origin = url.origin;
    // This page is not served by a companion, so it behaves like the public website.
    if (origin !== A && origin !== B) return new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } });
    if (url.pathname === "/version") return json({ name: "shakti-companion", version: "t", api: 1 });
    if (url.pathname === "/settings") return json({ chat_model: null });
    if (url.pathname === "/documents") {
      return json({ documents: DOCS[origin].map((file_name) => ({ file_key: file_name, file_name, workspace_id: "default", source_types: [], chunk_count: 1, size_bytes: 1, ingested_at: "", legacy: false })) });
    }
    // An upload that never finishes, so it is still in progress at the switch.
    if (url.pathname === "/ingest") return json(job("queued"));
    if (url.pathname.startsWith("/ingest/status/")) return json(job("processing"));
    return new Response("not found", { status: 404 });
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } })}>
        <ConnectionProvider><UploadsProvider><Docs /></UploadsProvider></ConnectionProvider>
      </QueryClientProvider>,
    );
  });
  await flush();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

const shown = () => container.querySelector('[data-testid="docs"]')!.textContent;

describe("ConnectionProvider", () => {
  it("shows the documents of the backend it is connected to", async () => {
    expect(shown()).toBe("from-a.pdf");
  });

  it("does not keep showing the previous backend's data after the address changes", async () => {
    expect(shown()).toBe("from-a.pdf");
    requests.length = 0;

    await act(async () => connection.setBaseUrl(B));
    await flush();

    expect(shown()).toBe("from-b.pdf");
    expect(requests.some((u) => u.startsWith(`${B}/documents`))).toBe(true);
  });

  it("drops uploads started against the previous backend and stops polling it", async () => {
    await act(async () => uploads.addFiles([new File(["x"], "slow.pdf")], "default"));
    await flush();
    expect(container.querySelector('[data-testid="uploads"]')!.textContent).toBe("slow.pdf");

    await act(async () => connection.setBaseUrl(B));
    await flush();
    requests.length = 0;
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1200)); // longer than one poll interval
    });

    expect(container.querySelector('[data-testid="uploads"]')!.textContent).toBe("");
    expect(requests.some((u) => u.startsWith(`${A}/ingest`))).toBe(false);
  });

  it("starts from an empty cache when the pairing token changes", async () => {
    requests.length = 0;
    await act(async () => connection.setToken("another-token"));
    await flush();
    expect(requests.some((u) => u.startsWith(`${A}/documents`))).toBe(true);
  });
});
