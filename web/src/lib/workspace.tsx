import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * The active workspace. Workspaces are separate collections of documents —
 * questions only search the active one. A workspace exists on the server
 * once it holds a document; until then it lives in this browser's list.
 */
type WorkspaceState = {
  workspace: string;
  setWorkspace: (w: string) => void;
  localWorkspaces: string[];
  addLocalWorkspace: (w: string) => void;
};

const Ctx = createContext<WorkspaceState | null>(null);
const CURRENT_KEY = "shakti.workspace";
const LIST_KEY = "shakti.workspaces";

export function normalizeWorkspaceName(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspace, setWorkspaceState] = useState(() => localStorage.getItem(CURRENT_KEY) || "default");
  const [localWorkspaces, setLocal] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(LIST_KEY) || "[]");
    } catch {
      return [];
    }
  });

  const setWorkspace = useCallback((w: string) => {
    localStorage.setItem(CURRENT_KEY, w);
    setWorkspaceState(w);
  }, []);

  const addLocalWorkspace = useCallback((w: string) => {
    setLocal((prev) => {
      const next = prev.includes(w) ? prev : [...prev, w];
      localStorage.setItem(LIST_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ workspace, setWorkspace, localWorkspaces, addLocalWorkspace }),
    [workspace, setWorkspace, localWorkspaces, addLocalWorkspace],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}
