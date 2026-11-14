import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useApi } from "./connection";

/**
 * Model downloads, kept above the router so a download keeps its progress
 * when the user switches pages.
 */
export type PullState = {
  status: string;
  completed: number;
  total: number;
  error: string | null;
  done: boolean;
};

type PullsState = {
  pulls: Record<string, PullState>;
  startPull: (model: string) => Promise<boolean>;
  cancelPull: (model: string) => void;
};

const Ctx = createContext<PullsState | null>(null);

export function PullsProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [pulls, setPulls] = useState<Record<string, PullState>>({});
  const controllers = useRef<Record<string, AbortController>>({});

  // Unmounted (e.g. the backend changed): stop downloads instead of letting
  // them run on, unseen, against the previous backend.
  useEffect(() => {
    const active = controllers.current;
    return () => Object.values(active).forEach((c) => c.abort());
  }, []);

  const update = (model: string, patch: Partial<PullState>) =>
    setPulls((prev) => {
      const base: PullState = prev[model] ?? { status: "starting", completed: 0, total: 0, error: null, done: false };
      return { ...prev, [model]: { ...base, ...patch } };
    });

  const startPull = useCallback(
    async (model: string) => {
      if (controllers.current[model]) return false;
      const controller = new AbortController();
      controllers.current[model] = controller;
      update(model, { status: "starting", completed: 0, total: 0, error: null, done: false });
      try {
        await api.pullModel(
          model,
          (p) => update(model, { status: p.status, ...(p.total ? { total: p.total, completed: p.completed ?? 0 } : {}) }),
          controller.signal,
        );
        update(model, { status: "success", done: true });
        await queryClient.invalidateQueries();
        return true;
      } catch (e) {
        const aborted = controller.signal.aborted;
        update(model, { error: aborted ? "Cancelled" : (e as Error).message, done: true });
        return false;
      } finally {
        delete controllers.current[model];
      }
    },
    [api, queryClient],
  );

  const cancelPull = useCallback((model: string) => controllers.current[model]?.abort(), []);

  const value = useMemo(() => ({ pulls, startPull, cancelPull }), [pulls, startPull, cancelPull]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePulls(): PullsState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePulls must be used inside PullsProvider");
  return ctx;
}
