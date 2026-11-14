import { createClient } from "@supabase/supabase-js";
import { validateAuthConfig } from "./auth-config";
export { validateAuthConfig } from "./auth-config";

const projectUrl = import.meta.env.VITE_SUPABASE_URL?.trim();
const publicKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim();
export const authConfig = validateAuthConfig(projectUrl, publicKey);
export const supabase = authConfig.configured ? createClient(projectUrl!, publicKey!, {
  auth: { flowType: "pkce", detectSessionInUrl: false, persistSession: true, autoRefreshToken: true },
}) : null;

export type AuthCallback = { code: string | null; error: string | null; recovery: boolean; flowId?: string };

export function authRedirectUrl(recovery = false) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("auth", recovery ? "recovery" : "callback");
  return url.href;
}

export function prepareAuthCallback(): AuthCallback | null {
  const url = new URL(window.location.href);
  const legacyTokens = new URLSearchParams(url.hash.slice(1));
  const legacyCallback = legacyTokens.has("access_token") || legacyTokens.has("error") || legacyTokens.has("error_description");
  if (!url.searchParams.has("auth") && !url.searchParams.has("code") && !url.searchParams.has("error") && !legacyCallback) return null;
  const recovery = url.searchParams.get("auth") === "recovery";
  const code = url.searchParams.get("code");
  const flowId = url.searchParams.get("sb_flow_id");
  const errorCode = url.searchParams.get("error_code") || legacyTokens.get("error_code");
  const providerError = url.searchParams.get("error") || legacyTokens.get("error");
  let error: string | null = null;
  if (errorCode === "otp_expired") {
    error = "This email link has expired or was already used. Request a new link and open it in the same browser that requested it.";
  } else if (providerError === "access_denied") {
    error = "Sign-in was cancelled or access was denied. Try signing in again. Request a new link if using email.";
  } else if (providerError || legacyCallback) {
    error = "Sign-in could not be completed. Try signing in again. Request a new link if using email.";
  } else if (!code) {
    error = "No sign-in code was returned. Start sign-in from Shakti rather than opening the callback URL directly. Request a new link if using email.";
  }
  url.search = "";
  url.hash = recovery ? "/auth/reset-password" : "/auth/callback";
  window.history.replaceState(window.history.state, "", url.href);
  return { code, error, recovery, ...(flowId ? { flowId } : {}) };
}
