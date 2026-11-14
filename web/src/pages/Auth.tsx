import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "@/components/Layout";
import { Alert, Button, Card, Input, Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { clearAuthReturnTo, readAuthReturnTo, rememberAuthReturnTo, safeAuthReturnTo } from "@/lib/auth-navigation";

export default function AuthPage() {
  const { pathname } = useLocation();
  return <AuthScreen key={pathname} path={pathname} />;
}

function AuthScreen({ path }: { path: string }) {
  const auth = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeAuthReturnTo(params.get("next") ?? readAuthReturnTo());
  const nextQuery = next === "/" ? "" : `?next=${encodeURIComponent(next)}`;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const signup = path === "/auth/signup";
  const forgot = path === "/auth/forgot-password";
  const reset = path === "/auth/reset-password";
  const callback = path === "/auth/callback";
  const account = path === "/account";
  const login = !signup && !forgot && !reset && !callback && !account;
  const title = account ? "Your account" : signup ? "Create your account" : forgot ? "Reset your password" : reset ? "Choose a new password" : callback ? "Account verification" : "Welcome back";
  const providers = auth.session?.user.app_metadata?.providers;
  const googleOnly = Array.isArray(providers) ? providers.includes("google") && !providers.includes("email") : auth.session?.user.app_metadata?.provider === "google";

  useEffect(() => {
    if (auth.configured && !callback && !reset) auth.clearError();
  }, [auth.configured, auth.clearError, callback, reset]);

  if (!auth.loading && auth.session && (login || signup || (callback && !auth.error))) return <SignedInRedirect to={next} />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setMessage(null);
    if ((signup || reset) && password !== confirmation) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    auth.clearError();
    rememberAuthReturnTo(next);
    try {
      if (forgot) {
        await auth.requestPasswordReset(email.trim());
        setMessage("If an account exists for that email, you will receive a password reset link. Open it in this browser.");
      } else if (reset) {
        await auth.updatePassword(password);
        setPassword("");
        setConfirmation("");
        setMessage("Your password has been updated.");
      } else if (signup) {
        const session = await auth.signUp(email.trim(), password);
        setPassword("");
        setConfirmation("");
        if (session) { clearAuthReturnTo(); navigate(next, { replace: true }); }
        else setMessage("Check your inbox for a confirmation link. Open it in this browser, then return to Shakti. If you already have an account, sign in or reset your password.");
      } else {
        await auth.signIn(email.trim(), password);
        clearAuthReturnTo();
        navigate(next, { replace: true });
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The account request failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const canShowForm = auth.configured && !auth.loading && !account && !callback && (!reset || (auth.session && !auth.error));

  return (
    <main className="flex min-h-full items-center justify-center overflow-y-auto bg-zinc-50 px-5 py-12 dark:bg-zinc-950">
      <Card className="w-full max-w-md space-y-5 p-7">
        <Link to="/" aria-label="Shakti home"><Logo className="mb-3" /></Link>
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm text-zinc-500">An optional Shakti account. Your documents and AI processing stay on this PC.</p>
        </div>
        {!auth.configured && <Alert title="Local mode">{auth.error || "Account login is not configured for this build. You can still use all local document features."}</Alert>}
        {auth.loading && <div className="flex items-center gap-2 text-sm" role="status"><Spinner /> Checking your account...</div>}
        {auth.configured && auth.error && <Alert tone="danger">{auth.error}</Alert>}
        {error && <Alert tone="danger">{error}</Alert>}
        {message && <Alert tone="success">{message}</Alert>}
        {auth.configured && !auth.loading && account && (
          auth.session ? <div className="space-y-4">
            <p className="break-all text-sm">Signed in as <strong>{auth.session.user.email}</strong></p>
            <p className="text-sm text-zinc-500">Signing out only ends this browser's account session. It does not disconnect the companion, erase documents, or protect local files from other people using this PC.</p>
            {googleOnly ? <p className="text-sm text-zinc-500">You sign in with Google. Manage your password in your Google account.</p>
              : <Link className="block text-sm text-brand-600 underline" to={`/auth/forgot-password${nextQuery}`}>Reset password by email</Link>}
            <Button variant="outline" loading={busy} onClick={async () => {
              setBusy(true);
              setError(null);
              try { await auth.signOut(); clearAuthReturnTo(); navigate("/landing", { replace: true }); }
              catch (failure) { setError(failure instanceof Error ? failure.message : "Couldn't sign out. Please try again."); }
              finally { setBusy(false); }
            }}>Sign out</Button>
          </div> : <Link className="text-brand-600 underline" to={`/auth/login${nextQuery}`}>Sign in to your account</Link>
        )}
        {auth.configured && !auth.loading && callback && !auth.error && (
          auth.session ? <Alert tone="success">You are signed in. <Link className="underline" to="/account">Continue to your account</Link></Alert>
            : <Alert>Start Google sign-in or open your email confirmation link. <Link className="underline" to="/auth/login">Back to sign in</Link>.</Alert>
        )}
        {auth.configured && !auth.loading && reset && (!auth.session || auth.error) && <Link className="block text-brand-600 underline" to="/auth/forgot-password">Request a new password reset link</Link>}
        {auth.configured && !auth.loading && (login || signup) && <div className="space-y-4">
          <Button type="button" variant="outline" className="w-full" loading={busy} onClick={async () => {
            if (busy) return;
            setBusy(true);
            setError(null);
            setMessage(null);
            auth.clearError();
            rememberAuthReturnTo(next);
            try { await auth.signInWithGoogle(); }
            catch (failure) { setError(failure instanceof Error ? failure.message : "Couldn't start Google sign-in. Please try again."); }
            finally { setBusy(false); }
          }}>Continue with Google</Button>
          <p className="text-center text-xs text-zinc-500">No separate Shakti password or confirmation email required for Google sign-in.</p>
          <div className="flex items-center gap-3 text-xs text-zinc-500"><span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />or use email<span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" /></div>
        </div>}
        {canShowForm && <form className="space-y-4" onSubmit={(event) => void submit(event)}>
          {!reset && <div>
            <label htmlFor="auth-email" className="mb-1.5 block text-sm font-medium">Email address</label>
            <Input id="auth-email" type="email" autoComplete="email" required maxLength={254} value={email} disabled={busy} onChange={(event) => setEmail(event.target.value)} />
          </div>}
          {!forgot && <div>
            <label htmlFor="auth-password" className="mb-1.5 block text-sm font-medium">{reset ? "New password" : "Password"}</label>
            <Input id="auth-password" type="password" autoComplete={signup || reset ? "new-password" : "current-password"} required minLength={signup || reset ? 8 : undefined} value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} />
            {(signup || reset) && <p className="mt-1 text-xs text-zinc-500">Use at least 8 characters.</p>}
          </div>}
          {(signup || reset) && <div>
            <label htmlFor="auth-confirmation" className="mb-1.5 block text-sm font-medium">Confirm password</label>
            <Input id="auth-confirmation" type="password" autoComplete="new-password" required minLength={8} value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} />
          </div>}
          <Button type="submit" className="w-full" loading={busy}>{signup ? "Create account" : forgot ? "Send reset link" : reset ? "Update password" : "Sign in"}</Button>
        </form>}
        {auth.configured && !account && <div className="flex flex-wrap justify-between gap-3 text-sm text-brand-600">
          {!login && <Link to={`/auth/login${nextQuery}`}>Back to sign in</Link>}
          {login && <><Link to={`/auth/signup${nextQuery}`}>Create an account</Link><Link to={`/auth/forgot-password${nextQuery}`}>Forgot password?</Link></>}
        </div>}
        <div className="border-t border-zinc-200 pt-4 text-sm dark:border-zinc-800">
          <Link className="font-medium text-brand-600" to={next} onClick={clearAuthReturnTo}>{auth.session ? "Continue to Shakti" : "Continue without an account"}</Link>
          <p className="mt-2 text-xs leading-relaxed text-zinc-500">Account access requires internet and sends your email and authentication data to Supabase. Google sign-in also uses Google's authentication service. Login does not replace local companion pairing.</p>
        </div>
      </Card>
    </main>
  );
}

function SignedInRedirect({ to }: { to: string }) {
  useEffect(() => clearAuthReturnTo(), []);
  return <Navigate to={to} replace />;
}
