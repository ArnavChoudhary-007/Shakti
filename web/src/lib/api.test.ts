import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

const api = createApi({ baseUrl: "http://companion.test", token: "paired-token" });

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
