import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, type IngestJob } from "./api";
import { useApi } from "./connection";

export type Upload = {
  id: string;
  file: File;
  workspace: string;
  job: IngestJob | null;
  error: string | null;
  pollError: string | null;
};

type UploadsState = {
  uploads: Upload[];
  addFiles: (files: FileList | File[], workspace: string) => void;
  clearFinished: () => void;
};

const Ctx = createContext<UploadsState | null>(null);

function waitForPoll(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, delay);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

export function UploadsProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [uploads, setUploads] = useState<Upload[]>([]);
  const controllers = useRef(new Map<string, AbortController>());

  useEffect(() => {
    const active = controllers.current;
    return () => {
      active.forEach((controller) => controller.abort());
      active.clear();
    };
  }, []);

  const uploadOne = useCallback(async (upload: Upload) => {
    const controller = new AbortController();
    const { signal } = controller;
    controllers.current.set(upload.id, controller);
    const update = (patch: Partial<Upload>) => {
      if (!signal.aborted) setUploads((prev) => prev.map((item) => item.id === upload.id ? { ...item, ...patch } : item));
    };
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    };
    try {
      let job = await api.upload(upload.file, upload.workspace, signal);
      update({ job });
      let failures = 0;
      while (job.status === "queued" || job.status === "processing") {
        await waitForPoll(Math.min(1000 * 2 ** failures, 15000), signal);
        try {
          job = await api.ingestStatus(job.job_id, signal);
          failures = 0;
          update({ job, pollError: null });
        } catch (error) {
          if (signal.aborted) return;
          if (error instanceof ApiError && error.status === 404) {
            refresh();
            throw new Error("Upload status is no longer available. The companion may have restarted. Check the document list before uploading again.");
          }
          if (error instanceof ApiError && error.status < 500 && error.status !== 408 && error.status !== 429) {
            throw new Error(`Couldn't check upload status: ${error.message}. Check your connection and the document list before uploading again.`);
          }
          failures = Math.min(failures + 1, 4);
          update({ pollError: "Status temporarily unavailable. Retrying automatically; your upload may still be processing." });
        }
      }
      if (job.status === "done") refresh();
    } catch (error) {
      if (!signal.aborted) update({ error: error instanceof Error ? error.message : "Upload failed.", pollError: null });
    } finally {
      controllers.current.delete(upload.id);
    }
  }, [api, queryClient]);

  const addFiles = useCallback((files: FileList | File[], workspace: string) => {
    const fresh: Upload[] = Array.from(files).map((file) => ({ id: crypto.randomUUID(), file, workspace, job: null, error: null, pollError: null }));
    setUploads((prev) => [...fresh, ...prev]);
    fresh.forEach((upload) => void uploadOne(upload));
  }, [uploadOne]);

  const clearFinished = useCallback(() => {
    setUploads((prev) => prev.filter((upload) => !upload.error && (!upload.job || upload.job.status === "queued" || upload.job.status === "processing")));
  }, []);

  const value = useMemo(() => ({ uploads, addFiles, clearFinished }), [uploads, addFiles, clearFinished]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useUploads(): UploadsState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useUploads must be used inside UploadsProvider");
  return ctx;
}
