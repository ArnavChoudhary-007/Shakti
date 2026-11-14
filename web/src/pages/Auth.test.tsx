import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Session } from "@supabase/supabase-js";
import AuthPage from "./Auth";
import App from "@/App";
import type { useAuth } from "@/lib/auth";
import { rememberAuthReturnTo } from "@/lib/auth-navigation";
import { AccountLink } from "@/components/AccountLink";

let auth: ReturnType<typeof useAuth>;
let phase = "missing";
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/lib/connection", () => ({ useConnection: () => ({ phase, retry: vi.fn() }), useApi: vi.fn() }));

let root: Root;
let container: HTMLDivElement;

async function mount(path: string, content: ReactNode = <AuthPage />) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/" element={<p>Local app</p>} />
    <Route path="/documents" element={<p>Documents page</p>} />
    <Route path="*" element={content} />
  </Routes></MemoryRouter>));
}

async function fill(selector: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(selector)!;
  expect(input).not.toBeNull();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit() {
  await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

beforeEach(() => {
  phase = "missing";
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
  auth = {
    configured: true, loading: false, session: null, error: null,
    signIn: vi.fn().mockResolvedValue(undefined), signUp: vi.fn().mockResolvedValue(null),
    requestPasswordReset: vi.fn().mockResolvedValue(undefined), updatePassword: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn().mockResolvedValue(undefined),
    signInWithGoogle: vi.fn().mockResolvedValue(undefined),
    clearError: vi.fn(),
  };
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container?.remove();
});

it.each(["missing", "unpaired", "checking", "ready"])("allows account login when the companion is %s", async (connectionPhase) => {
  phase = connectionPhase;
  await mount("/auth/login", <App />);
  expect(container.querySelector("h1")?.textContent).toBe("Welcome back");
  expect(container.querySelector('input[type="email"]')).not.toBeNull();
});

it("keeps a local-only build usable without credentials", async () => {
  auth.configured = false;
  await mount("/auth/login");
  expect(container.textContent).toContain("not configured");
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).not.toContain("Continue with Google");
  const localLink = Array.from(container.querySelectorAll("a")).find((link) => link.textContent === "Continue without an account")!;
  await act(async () => localLink.click());
  expect(container.textContent).toBe("Local app");
});

it.each(["/auth/login", "/auth/signup"])("starts Google sign-in without email or password on %s", async (path) => {
  await mount(path);
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((element) => element.textContent === "Continue with Google")!;
  expect(button).toBeDefined();
  expect(button.type).toBe("button");
  await act(async () => button.click());
  expect(auth.signInWithGoogle).toHaveBeenCalledTimes(1);
  expect(auth.signIn).not.toHaveBeenCalled();
  expect(auth.signUp).not.toHaveBeenCalled();
});

it("shows Google sign-in errors and allows retry", async () => {
  vi.mocked(auth.signInWithGoogle).mockRejectedValue(new Error("Google provider is disabled"));
  await mount("/auth/login");
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((element) => element.textContent === "Continue with Google")!;
  await act(async () => button.click());
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Google provider is disabled");
  expect(button.disabled).toBe(false);
});

it("prevents duplicate sign-in requests while Google OAuth is starting", async () => {
  let complete!: () => void;
  vi.mocked(auth.signInWithGoogle).mockReturnValue(new Promise<void>((resolve) => { complete = resolve; }));
  await mount("/auth/login");
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((element) => element.textContent === "Continue with Google")!;
  await act(async () => button.click());
  expect(button.disabled).toBe(true);
  expect(container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  await act(async () => button.click());
  expect(auth.signInWithGoogle).toHaveBeenCalledTimes(1);
  await act(async () => complete());
});

it("returns to the local app after a successful Google callback", async () => {
  auth.session = { user: { email: "person@example.com", app_metadata: { provider: "google" } } } as Session;
  await mount("/auth/callback");
  expect(container.textContent).toBe("Local app");
});

it("does not strand an already signed-in user on the account page", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  await mount("/auth/login");
  expect(container.textContent).toBe("Local app");
});

it("returns to the page saved before Google sign-in", async () => {
  rememberAuthReturnTo("/documents");
  auth.session = { user: { email: "person@example.com" } } as Session;
  await mount("/auth/callback");
  expect(container.textContent).toBe("Documents page");
  expect(sessionStorage.getItem("shakti.auth.returnTo")).toBeNull();
});

it("returns to the requested app page after password sign-in", async () => {
  await mount("/auth/login?next=%2Fdocuments");
  await fill("#auth-email", "person@example.com");
  await fill("#auth-password", "strong-password");
  await submit();
  expect(container.textContent).toBe("Documents page");
});

it("clears a previous callback error when returning to sign-in", async () => {
  await mount("/auth/login");
  expect(auth.clearError).toHaveBeenCalled();
});

it("keeps a failed callback visible instead of treating an old session as success", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  auth.error = "Sign-in was cancelled";
  await mount("/auth/callback");
  expect(container.textContent).toContain("Sign-in was cancelled");
  expect(auth.clearError).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Continue to Shakti");
});

it("does not offer email password reset to Google-only accounts", async () => {
  auth.session = { user: { email: "person@example.com", app_metadata: { providers: ["google"] } } } as Session;
  await mount("/account");
  expect(container.textContent).toContain("Manage your password in your Google account");
  expect(container.textContent).not.toContain("Reset password by email");
});

it("remembers the current app page in the sign-in entry link", async () => {
  await mount("/chat", <AccountLink />);
  expect(container.querySelector("a")?.getAttribute("href")).toBe("/auth/login?next=%2Fchat");
});

it("keeps the app destination when opening account settings", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  await mount("/chat", <AccountLink />);
  expect(container.querySelector("a")?.getAttribute("href")).toBe("/account?next=%2Fchat");
});

it("labels the return-to-app link correctly for signed-in users", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  await mount("/account");
  expect(container.textContent).toContain("Continue to Shakti");
  expect(container.textContent).not.toContain("Continue without an account");
});

it("shows credential errors instead of silently navigating", async () => {
  vi.mocked(auth.signIn).mockRejectedValue(new Error("Invalid login credentials"));
  await mount("/auth/login");
  await fill("#auth-email", "person@example.com");
  await fill("#auth-password", "wrong-password");
  await submit();
  expect(auth.signIn).toHaveBeenCalledWith("person@example.com", "wrong-password");
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("Invalid login credentials");
});

it("checks signup password confirmation before calling Supabase", async () => {
  await mount("/auth/signup");
  await fill("#auth-email", "person@example.com");
  await fill("#auth-password", "strong-password");
  await fill("#auth-confirmation", "different-password");
  await submit();
  expect(auth.signUp).not.toHaveBeenCalled();
  expect(container.textContent).toContain("passwords do not match");
});

it("shows confirmation instructions and clears passwords after signup", async () => {
  await mount("/auth/signup");
  await fill("#auth-email", "person@example.com");
  await fill("#auth-password", "strong-password");
  await fill("#auth-confirmation", "strong-password");
  await submit();
  expect(auth.signUp).toHaveBeenCalledWith("person@example.com", "strong-password");
  expect(container.textContent).toContain("Check your inbox");
  expect(container.querySelector<HTMLInputElement>("#auth-password")?.value).toBe("");
});

it("sends a reset link with an account-enumeration-safe confirmation", async () => {
  await mount("/auth/forgot-password");
  await fill("#auth-email", "person@example.com");
  await submit();
  expect(auth.requestPasswordReset).toHaveBeenCalledWith("person@example.com");
  expect(container.textContent).toContain("If an account exists");
});

it("does not permit a password update without an authenticated session", async () => {
  await mount("/auth/reset-password");
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).toContain("Request a new password reset link");
});

it("updates a password for an authenticated recovery session", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  await mount("/auth/reset-password");
  await fill("#auth-password", "new-password");
  await fill("#auth-confirmation", "new-password");
  await submit();
  expect(auth.updatePassword).toHaveBeenCalledWith("new-password");
  expect(container.textContent).toContain("Your password has been updated");
});

it("does not show a password form after a failed callback even with an existing session", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  auth.error = "Expired link";
  await mount("/auth/reset-password");
  expect(container.querySelector("form")).toBeNull();
  expect(container.textContent).toContain("Expired link");
});

it.each(["missing", "unpaired", "checking", "ready"])("returns to the landing page after sign-out when companion is %s", async (connectionPhase) => {
  phase = connectionPhase;
  auth.session = { user: { email: "person@example.com" } } as Session;
  vi.mocked(auth.signOut).mockImplementation(async () => { auth.session = null; });
  rememberAuthReturnTo("/documents");
  await mount("/account", <App />);
  await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
  expect(auth.signOut).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("How it works");
  expect(container.textContent).toContain("Open the app");
  expect(sessionStorage.getItem("shakti.auth.returnTo")).toBeNull();
  if (connectionPhase === "ready" || connectionPhase === "unpaired") {
    expect(container.textContent).toContain("Shakti is running on this PC");
    const openLink = Array.from(container.querySelectorAll("a")).find((link) => link.textContent?.includes("Open the app"))!;
    await act(async () => openLink.click());
    expect(container.querySelector("h1")?.textContent).toBe("Welcome back");
  }
});

it.each(["missing", "unpaired", "checking", "ready"])("asks signed-out users to sign in from every landing CTA when companion is %s", async (connectionPhase) => {
  phase = connectionPhase;
  await mount("/landing", <App />);
  const openLinks = Array.from(container.querySelectorAll("a")).filter((link) => link.textContent?.includes("Open the app"));
  expect(openLinks.length).toBeGreaterThan(1);
  expect(openLinks.every((link) => link.getAttribute("href") === "#/auth/login?next=%2F")).toBe(true);
  await act(async () => openLinks[0].click());
  expect(container.querySelector("h1")?.textContent).toBe("Welcome back");
  await fill("#auth-email", "person@example.com");
  await fill("#auth-password", "strong-password");
  await submit();
  expect(auth.signIn).toHaveBeenCalledWith("person@example.com", "strong-password");
  expect(container.textContent).toBe("Local app");
});

it("opens the app without another sign-in for an existing session", async () => {
  phase = "ready";
  auth.session = { user: { email: "person@example.com" } } as Session;
  await mount("/landing", <App />);
  const openLink = Array.from(container.querySelectorAll("a")).find((link) => link.textContent?.includes("Open the app"))!;
  await act(async () => openLink.click());
  expect(container.textContent).toBe("Local app");
});

it("reports sign-out failures and keeps the account visible", async () => {
  auth.session = { user: { email: "person@example.com" } } as Session;
  vi.mocked(auth.signOut).mockRejectedValue(new Error("Network unavailable"));
  await mount("/account");
  await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
  expect(container.textContent).toContain("Network unavailable");
  expect(container.textContent).toContain("person@example.com");
});
