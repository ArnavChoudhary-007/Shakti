import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { authConfig, authRedirectUrl, supabase, type AuthCallback } from "./supabase";

function requireClient() {
  if (!supabase) throw new Error(authConfig.error || "Account login is not configured for this build. You can still use Shakti locally.");
  return supabase;
}

const actions = {
  async signInWithGoogle() {
    const { error } = await requireClient().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: authRedirectUrl(), queryParams: { prompt: "select_account" } },
    });
    if (error) throw error;
  },
  async signIn(email: string, password: string) {
    const { error } = await requireClient().auth.signInWithPassword({ email, password });
    if (error) throw error;
  },
  async signUp(email: string, password: string) {
    const { data, error } = await requireClient().auth.signUp({ email, password, options: { emailRedirectTo: authRedirectUrl() } });
    if (error) throw error;
    return data.session;
  },
  async requestPasswordReset(email: string) {
    const { error } = await requireClient().auth.resetPasswordForEmail(email, { redirectTo: authRedirectUrl(true) });
    if (error) throw error;
  },
  async updatePassword(password: string) {
    const { error } = await requireClient().auth.updateUser({ password });
    if (error) throw error;
  },
  async signOut() {
    const { error } = await requireClient().auth.signOut({ scope: "local" });
    if (error) throw error;
  },
};

type AuthState = typeof actions & {
  configured: boolean;
  loading: boolean;
  session: Session | null;
  error: string | null;
  clearError: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children, callback = null }: { children: ReactNode; callback?: AuthCallback | null }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(Boolean(supabase));
  const [error, setError] = useState<string | null>(authConfig.error);
  const initialization = useRef<Promise<{ session: Session | null; error: string | null }> | null>(null);
  const sessionRevision = useRef(0);
  const initializationRevision = useRef(0);
  const clearError = useCallback(() => setError(authConfig.error), []);

  useEffect(() => {
    if (!loading) return;
    const timeout = window.setTimeout(() => {
      setLoading(false);
      setError("Checking your account took too long. Try signing in again, or continue locally while the account service is unavailable.");
    }, 15_000);
    return () => window.clearTimeout(timeout);
  }, [loading]);

  useEffect(() => {
    if (!supabase) return;
    const client = supabase;
    let active = true;
    const { data: { subscription } } = client.auth.onAuthStateChange((event, nextSession) => {
      if (!active) return;
      if (event === "INITIAL_SESSION" && sessionRevision.current > 0) return;
      setSession(nextSession);
      if (event !== "INITIAL_SESSION") {
        sessionRevision.current += 1;
        setLoading(false);
        setError(null);
      }
    });
    if (!initialization.current) initializationRevision.current = sessionRevision.current;
    initialization.current ??= (async () => {
      try {
        if (callback?.error) {
          const restored = await client.auth.getSession();
          return { session: restored.data.session, error: callback.error };
        }
        const result = callback?.code
          ? await client.auth.exchangeCodeForSession(callback.code, callback.flowId ? { flowId: callback.flowId } : undefined)
          : await client.auth.getSession();
        if (result.error) {
          const restored = callback ? await client.auth.getSession() : null;
          if (callback && result.error.code === "pkce_code_verifier_not_found") {
            return { session: restored?.data.session ?? null, error: "Sign-in could not find its browser verification data. Start sign-in again in the same browser and site address. In Supabase Redirect URLs, allow the exact callback address where sign-in started: localhost and 127.0.0.1, and ports 5173 and 8000, are separate sites. Browser storage must remain enabled." };
          }
          return { session: restored?.data.session ?? null, error: callback
            ? "Couldn't complete sign-in or email verification. Try Google sign-in again or request a new email link in the same browser that started the request."
            : "Couldn't restore your account session. You can sign in again or continue locally." };
        }
        return { session: result.data.session, error: null };
      } catch {
        return { session: null, error: "Couldn't reach the account service. Check your internet connection or continue locally." };
      }
    })();
    void initialization.current.then((result) => {
      if (!active) return;
      if (sessionRevision.current === initializationRevision.current) {
        setSession(result.session);
        setError(result.error);
      }
      setLoading(false);
    });
    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [callback]);

  return <AuthContext.Provider value={{ ...actions, configured: authConfig.configured, loading, session, error, clearError }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
