import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, createApi, type Api, type Version } from "./api";
import { DEFAULT_COMPANION_URL } from "./config";

/**
 * Where is the companion, and may we talk to it?
 *
 *  - "companion" mode: this page is served by the companion itself
 *    (http://127.0.0.1:8000). Same origin, no token needed.
 *  - "remote" mode: this page is the public website (or the Vite dev
 *    server). It calls the companion at 127.0.0.1 and must be paired
 *    (token) unless it's a trusted local origin like the dev server.
 */
export type Phase = "checking" | "ready" | "missing" | "unpaired";

type ConnectionState = {
  phase: Phase;
  mode: "companion" | "remote";
  baseUrl: string;
  version: Version | null;
  api: Api;
  retry: () => void;
  setToken: (token: string | null) => void;
  setBaseUrl: (url: string) => void;
};

const TOKEN_KEY = "shakti.token";
const URL_KEY = "shakti.companionUrl";

const Ctx = createContext<ConnectionState | null>(null);

async function servedByCompanion(): Promise<Version | null> {
  try {
    const res = await fetch(new URL("version", window.location.href), { cache: "no-store" });
    if (!res.ok || !res.headers.get("content-type")?.includes("application/json")) return null;
    const body = await res.json();
    return body?.name === "shakti-companion" ? body : null;
  } catch {
    return null;
  }
}

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [mode, setMode] = useState<"companion" | "remote">("remote");
  const [version, setVersion] = useState<Version | null>(null);
  const [token, setTokenState] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY));
  const [remoteUrl, setRemoteUrl] = useState<string>(() => localStorage.getItem(URL_KEY) || DEFAULT_COMPANION_URL);
  const [attempt, setAttempt] = useState(0);

  const baseUrl = mode === "companion" ? "" : remoteUrl.replace(/\/+$/, "");
  const api = useMemo(() => createApi({ baseUrl, token: mode === "companion" ? null : token }), [baseUrl, token, mode]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const local = await servedByCompanion();
      if (cancelled) return;
      if (local) {
        setMode("companion");
        setVersion(local);
        setPhase("ready");
        return;
      }
      setMode("remote");
      const remote = createApi({ baseUrl: remoteUrl.replace(/\/+$/, ""), token });
      try {
        setVersion(await remote.version());
      } catch {
        if (!cancelled) setPhase("missing");
        return;
      }
      try {
        await remote.settings();
        if (!cancelled) setPhase("ready");
      } catch (e) {
        if (!cancelled) setPhase(e instanceof ApiError && (e.status === 401 || e.status === 403) ? "unpaired" : "missing");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt, remoteUrl, token]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const setToken = useCallback((t: string | null) => {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
    setTokenState(t);
  }, []);
  const setBaseUrl = useCallback((u: string) => {
    localStorage.setItem(URL_KEY, u);
    setRemoteUrl(u);
  }, []);

  const value = useMemo(
    () => ({ phase, mode, baseUrl, version, api, retry, setToken, setBaseUrl }),
    [phase, mode, baseUrl, version, api, retry, setToken, setBaseUrl],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConnection(): ConnectionState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useConnection must be used inside ConnectionProvider");
  return ctx;
}

export function useApi(): Api {
  return useConnection().api;
}
