import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

const api = createApi({ baseUrl: "http://companion.test", token: "paired-token" });

it("requests original-file grants with pairing and workspace identity", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ path: "/documents/file-1/content?view_token=temporary", file_name: "report.pdf", previewable: true, matched_by_name: false }));
  vi.stubGlobal("fetch", fetchMock);
  const result = await api.originalFile("workspace a", { index: 1, file_key: "file-1", file_name: "report.pdf", location: "page 3", full_text: "excerpt", source_type: "pdf", sender: null });
  expect(result.url).toBe("http://companion.test/documents/file-1/content?view_token=temporary");
  expect(fetchMock).toHaveBeenCalledWith("http://companion.test/documents/original", expect.objectContaining({
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Shakti-Token": "paired-token" },
    body: JSON.stringify({ workspace_id: "workspace a", file_key: "file-1", file_name: "report.pdf" }),
  }));
  expect(result.url).not.toContain("paired-token");
});

it("keeps original links same-origin for the companion-hosted UI", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ path: "/documents/file-1/content?view_token=temporary", file_name: "report.pdf", previewable: true, matched_by_name: true })));
  const result = await createApi({ baseUrl: "", token: null }).originalFile("default", { index: 1, file_name: "report.pdf", location: "", full_text: "", source_type: "pdf", sender: null });
  expect(result.url).toBe("/documents/file-1/content?view_token=temporary");
});

function streamResponse(...chunks: string[]) {
  return new Response(new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(new TextEncoder().encode(chunk)));
      controller.close();
    },
  }));
}

describe("model downloads", () => {
  it.each(["", '{"status":"pulling manifest"}\n', '{"status":"downloading","completed":20,"total":100}\n'])("rejects incomplete streams: %s", async (body) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(body)));
    await expect(api.pullModel("test-model", vi.fn())).rejects.toThrow("interrupted before completion");
  });

  it("requires explicit success and handles split chunks and a final line without a newline", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse('{"status":"pulling manifest"}\n{"sta', 'tus":"success"}'));
    vi.stubGlobal("fetch", fetchMock);
    const progress = vi.fn();
    await expect(api.pullModel("test-model", progress)).resolves.toBeUndefined();
    expect(progress.mock.calls.map(([message]) => message.status)).toEqual(["pulling manifest", "success"]);
    expect(fetchMock.mock.calls[0][1].headers["X-Shakti-Token"]).toBe("paired-token");
  });

  it("preserves Ollama errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse('{"error":"Not enough disk space"}\n')));
    await expect(api.pullModel("test-model", vi.fn())).rejects.toThrow("Not enough disk space");
  });

  it("preserves cancellation", async () => {
    const controller = new AbortController();
    vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
    })));
    const result = api.pullModel("test-model", vi.fn(), controller.signal);
    controller.abort();
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
  });
});

it("encodes namespaced model names when deleting", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ status: "deleted" }));
  vi.stubGlobal("fetch", fetchMock);
  await api.deleteModel("audit/model:latest");
  expect(fetchMock).toHaveBeenCalledWith("http://companion.test/ollama/models/audit%2Fmodel%3Alatest", expect.objectContaining({ method: "DELETE" }));
});
