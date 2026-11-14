import { afterEach, describe, expect, it } from "vitest";
import { authRedirectUrl, prepareAuthCallback, validateAuthConfig } from "./supabase";

afterEach(() => window.history.replaceState(null, "", "/"));

describe("public auth configuration", () => {
  it("allows unconfigured local-only builds", () => {
    expect(validateAuthConfig()).toEqual({ configured: false, error: null });
  });

  it("requires both settings", () => {
    expect(validateAuthConfig("https://project.supabase.co").error).toContain("Set both");
  });

  it("accepts publishable keys and legacy anon keys", () => {
    expect(validateAuthConfig("https://project.supabase.co", "sb_publishable_example").configured).toBe(true);
    expect(validateAuthConfig("https://project.supabase.co", `header.${btoa(JSON.stringify({ role: "anon" }))}.signature`).configured).toBe(true);
  });

  it.each([
    ["https://project.supabase.co", "sb_secret_example"],
    ["https://project.supabase.co", "sb_publishable_"],
    ["https://project.supabase.co", `header.${btoa(JSON.stringify({ role: "service_role" }))}.signature`],
    ["http://project.supabase.co", "sb_publishable_example"],
    ["https://user:password@project.supabase.co", "sb_publishable_example"],
    ["not-a-url", "sb_publishable_example"],
  ])("refuses unsafe configuration %s", (url, key) => {
    expect(validateAuthConfig(url, key).configured).toBe(false);
  });
});

describe("hash-router email callbacks", () => {
  it("leaves app and pairing routes untouched", () => {
    window.history.replaceState(null, "", "/#/connect?token=pairing-token");
    expect(prepareAuthCallback()).toBeNull();
    expect(window.location.hash).toBe("#/connect?token=pairing-token");
  });

  it("makes same-origin redirect URLs without leaking the pairing key", () => {
    window.history.replaceState(null, "", "/Shakti/?tracking=1#/connect?token=private");
    expect(authRedirectUrl()).toBe(`${window.location.origin}/Shakti/?auth=callback`);
    expect(authRedirectUrl(true)).toBe(`${window.location.origin}/Shakti/?auth=recovery`);
  });

  it("captures then removes a confirmation code before the router starts", () => {
    window.history.replaceState(null, "", "/?auth=callback&code=one-use-code");
    expect(prepareAuthCallback()).toEqual({ code: "one-use-code", error: null, recovery: false });
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#/auth/callback");
    expect(prepareAuthCallback()).toBeNull();
  });

  it("routes password recovery and retains the deployment base path", () => {
    window.history.replaceState(null, "", "/Shakti/?auth=recovery&code=recovery-code");
    expect(prepareAuthCallback()).toEqual({ code: "recovery-code", error: null, recovery: true });
    expect(window.location.pathname).toBe("/Shakti/");
    expect(window.location.hash).toBe("#/auth/reset-password");
  });

  it("captures a PKCE flow identifier before removing it from the URL", () => {
    window.history.replaceState(null, "", "/?auth=callback&code=one-use-code&sb_flow_id=flow-123");
    expect(prepareAuthCallback()).toEqual({ code: "one-use-code", error: null, recovery: false, flowId: "flow-123" });
    expect(window.location.search).toBe("");
  });

  it("does not mislabel a directly opened callback as an expired email", () => {
    window.history.replaceState(null, "", "/?auth=callback");
    const callback = prepareAuthCallback();
    expect(callback?.error).toContain("No sign-in code was returned");
    expect(callback?.error).not.toContain("expired");
  });

  it("distinguishes an expired email from Google sign-in cancellation", () => {
    window.history.replaceState(null, "", "/?auth=callback&error=access_denied&error_code=otp_expired");
    expect(prepareAuthCallback()?.error).toContain("email link has expired");
  });

  it("handles OAuth cancellation without displaying provider details or tokens", () => {
    window.history.replaceState(null, "", "/?auth=callback&error=access_denied&error_description=private-provider-details");
    const callback = prepareAuthCallback();
    expect(callback?.error).toContain("Try signing in again");
    expect(callback?.error).not.toContain("private-provider-details");
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#/auth/callback");
  });

  it.each([
    "/?auth=callback&error=access_denied&error_description=secret-detail",
    "/?auth=recovery",
    "/#access_token=private-token&refresh_token=private-refresh&type=recovery",
    "/#error=access_denied&error_description=secret-detail",
  ])("handles invalid or legacy callbacks without retaining credentials: %s", (path) => {
    window.history.replaceState(null, "", path);
    expect(prepareAuthCallback()?.error).toContain("Request a new link");
    expect(window.location.href).not.toMatch(/private|secret-detail|access_token|refresh_token/);
  });
});
