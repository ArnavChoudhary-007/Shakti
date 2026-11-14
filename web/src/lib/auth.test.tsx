import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import { AuthProvider, useAuth } from "./auth";
import type { AuthCallback } from "./supabase";

const mock = vi.hoisted(() => ({
  getSession: vi.fn(), exchangeCodeForSession: vi.fn(), onAuthStateChange: vi.fn(),
  signInWithOAuth: vi.fn(), signInWithPassword: vi.fn(), signUp: vi.fn(), resetPasswordForEmail: vi.fn(),
  updateUser: vi.fn(), signOut: vi.fn(), unsubscribe: vi.fn(),
}));

vi.mock("./supabase", async (importOriginal) => ({
  ...await importOriginal<typeof import("./supabase")>(),
  authConfig: { configured: true, error: null },
  supabase: { auth: mock },
}));

const session = { user: { id: "user-1", email: "person@example.com" }, access_token: "account-token" } as Session;
let root: Root;
let container: HTMLDivElement;
let state: ReturnType<typeof useAuth>;
let notify: (event: AuthChangeEvent, session: Session | null) => void;

function Probe() {
  state = useAuth();
  return <p>Local document features stay mounted</p>;
}

async function mount(callback: AuthCallback | null = null) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<StrictMode><AuthProvider callback={callback}><Probe /></AuthProvider></StrictMode>));
}

beforeEach(() => {
  vi.clearAllMocks();
  mock.getSession.mockResolvedValue({ data: { session: null }, error: null });
  mock.exchangeCodeForSession.mockResolvedValue({ data: { session }, error: null });
  mock.signInWithPassword.mockResolvedValue({ error: null });
  mock.signInWithOAuth.mockResolvedValue({ error: null });
  mock.signUp.mockResolvedValue({ data: { session: null }, error: null });
  mock.resetPasswordForEmail.mockResolvedValue({ error: null });
  mock.updateUser.mockResolvedValue({ error: null });
  mock.signOut.mockResolvedValue({ error: null });
  mock.onAuthStateChange.mockImplementation((listener) => {
    notify = listener;
    return { data: { subscription: { unsubscribe: mock.unsubscribe } } };
  });
  window.history.replaceState(null, "", "/");
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
  vi.useRealTimers();
});

describe("account session lifecycle", () => {
  it("does not overwrite a fresh sign-in with a late restored session", async () => {
    let restore!: (value: unknown) => void;
    mock.getSession.mockReturnValue(new Promise((resolve) => { restore = resolve; }));
    await mount();
    await act(async () => notify("SIGNED_IN", session));
    expect(state.loading).toBe(false);
    await act(async () => restore({ data: { session: null }, error: null }));
    expect(state.session).toEqual(session);
  });

  it("does not resurrect a signed-out account when restoration finishes late", async () => {
    let restore!: (value: unknown) => void;
    mock.getSession.mockReturnValue(new Promise((resolve) => { restore = resolve; }));
    await mount();
    await act(async () => notify("SIGNED_OUT", null));
    await act(async () => restore({ data: { session }, error: null }));
    expect(state.session).toBeNull();
    expect(state.loading).toBe(false);
  });

  it("preserves an existing account session when a callback is invalid", async () => {
    mock.getSession.mockResolvedValue({ data: { session }, error: null });
    await mount({ code: null, error: "Invalid callback", recovery: false });
    expect(state.session).toEqual(session);
    expect(state.error).toBe("Invalid callback");
  });

  it("restores a session and tracks auth events without touching pairing", async () => {
    localStorage.setItem("shakti.token", "companion-token");
    mock.getSession.mockResolvedValue({ data: { session }, error: null });
    await mount();
    expect(state.session).toEqual(session);
    expect(state.loading).toBe(false);
    await act(async () => notify("SIGNED_OUT", null));
    expect(state.session).toBeNull();
    expect(localStorage.getItem("shakti.token")).toBe("companion-token");
    expect(mock.getSession).toHaveBeenCalledTimes(1);
  });

  it("exchanges a code only once under StrictMode", async () => {
    await mount({ code: "one-use-code", error: null, recovery: false });
    expect(mock.exchangeCodeForSession).toHaveBeenCalledExactlyOnceWith("one-use-code", undefined);
    expect(mock.getSession).not.toHaveBeenCalled();
    expect(state.session).toEqual(session);
  });

  it("shows actionable errors for expired codes", async () => {
    mock.exchangeCodeForSession.mockResolvedValue({ data: { session: null }, error: new Error("invalid grant") });
    await mount({ code: "expired", error: null, recovery: true });
    expect(state.error).toContain("same browser");
    expect(state.loading).toBe(false);
  });

  it("passes the captured PKCE flow identifier through after URL cleanup", async () => {
    await mount({ code: "one-use-code", error: null, recovery: false, flowId: "flow-123" });
    expect(mock.exchangeCodeForSession).toHaveBeenCalledExactlyOnceWith("one-use-code", { flowId: "flow-123" });
  });

  it("explains origin and storage problems instead of reporting an expired email", async () => {
    mock.exchangeCodeForSession.mockResolvedValue({ data: { session: null }, error: { code: "pkce_code_verifier_not_found" } });
    await mount({ code: "one-use-code", error: null, recovery: false });
    expect(state.error).toContain("same browser and site address");
    expect(state.error).toContain("ports 5173 and 8000");
    expect(state.error).not.toContain("expired");
  });

  it("preserves an existing session when exchanging a code fails", async () => {
    mock.getSession.mockResolvedValue({ data: { session }, error: null });
    mock.exchangeCodeForSession.mockResolvedValue({ data: { session: null }, error: new Error("invalid grant") });
    await mount({ code: "expired", error: null, recovery: false });
    expect(state.session).toEqual(session);
    expect(state.error).toContain("Couldn't complete sign-in");
  });

  it("stops showing an endless spinner if account restoration stalls", async () => {
    vi.useFakeTimers();
    mock.getSession.mockReturnValue(new Promise(() => {}));
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(state.loading).toBe(false);
    expect(state.error).toContain("too long");
    expect(container.textContent).toContain("Local document features stay mounted");
  });

  it("does not exchange malformed callbacks", async () => {
    await mount({ code: "invalid", error: "Expired email link", recovery: false });
    expect(mock.exchangeCodeForSession).not.toHaveBeenCalled();
    expect(state.error).toBe("Expired email link");
  });

  it("keeps local children available while account initialization is pending", async () => {
    mock.getSession.mockReturnValue(new Promise(() => {}));
    await mount();
    expect(state.loading).toBe(true);
    expect(container.textContent).toContain("Local document features stay mounted");
  });

  it("recovers from an offline initialization when the user signs in", async () => {
    mock.getSession.mockRejectedValue(new Error("network unavailable"));
    await mount();
    expect(state.error).toContain("continue locally");
    expect(state.loading).toBe(false);
    await act(async () => notify("SIGNED_IN", session));
    expect(state.error).toBeNull();
    expect(state.session).toEqual(session);
  });

  it("unsubscribes on unmount", async () => {
    await mount();
    expect(mock.unsubscribe).toHaveBeenCalledTimes(1);
    await act(async () => root.unmount());
    expect(mock.unsubscribe).toHaveBeenCalledTimes(2);
  });
});

describe("account actions", () => {
  it("starts Google OAuth with a same-origin PKCE callback and account selection", async () => {
    localStorage.setItem("shakti.token", "companion-token");
    await mount();
    await state.signInWithGoogle();
    expect(mock.signInWithOAuth).toHaveBeenCalledWith({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/?auth=callback`, queryParams: { prompt: "select_account" } },
    });
    expect(localStorage.getItem("shakti.token")).toBe("companion-token");
    expect(mock.signInWithPassword).not.toHaveBeenCalled();
  });

  it("reports failures starting Google OAuth", async () => {
    await mount();
    mock.signInWithOAuth.mockResolvedValue({ error: new Error("Google provider is disabled") });
    await expect(state.signInWithGoogle()).rejects.toThrow("Google provider is disabled");
  });

  it("uses same-origin confirmation and recovery redirects", async () => {
    await mount();
    await state.signUp("person@example.com", "strong-password");
    expect(mock.signUp).toHaveBeenCalledWith({ email: "person@example.com", password: "strong-password", options: { emailRedirectTo: `${window.location.origin}/?auth=callback` } });
    await state.requestPasswordReset("person@example.com");
    expect(mock.resetPasswordForEmail).toHaveBeenCalledWith("person@example.com", { redirectTo: `${window.location.origin}/?auth=recovery` });
  });

  it("passes login and password updates to Supabase", async () => {
    await mount();
    await state.signIn("person@example.com", "strong-password");
    expect(mock.signInWithPassword).toHaveBeenCalledWith({ email: "person@example.com", password: "strong-password" });
    await state.updatePassword("new-strong-password");
    expect(mock.updateUser).toHaveBeenCalledWith({ password: "new-strong-password" });
  });

  it("signs out only the current browser without deleting local data", async () => {
    localStorage.setItem("shakti.token", "paired");
    localStorage.setItem("shakti.workspace", "personal");
    await mount();
    await state.signOut();
    expect(mock.signOut).toHaveBeenCalledWith({ scope: "local" });
    expect(localStorage.getItem("shakti.token")).toBe("paired");
    expect(localStorage.getItem("shakti.workspace")).toBe("personal");
  });

  it("propagates Supabase action failures", async () => {
    await mount();
    mock.signInWithPassword.mockResolvedValue({ error: new Error("Invalid login credentials") });
    await expect(state.signIn("person@example.com", "wrong")).rejects.toThrow("Invalid login credentials");
    mock.updateUser.mockResolvedValue({ error: new Error("Password is too weak") });
    await expect(state.updatePassword("weak")).rejects.toThrow("Password is too weak");
  });
});
