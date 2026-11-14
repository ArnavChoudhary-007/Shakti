export function validateAuthConfig(projectUrl?: string, publicKey?: string) {
  if (!projectUrl && !publicKey) return { configured: false, error: null };
  if (!projectUrl || !publicKey) {
    return { configured: false, error: "Set both VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY, then restart or rebuild the website." };
  }
  try {
    const url = new URL(projectUrl);
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error();
    if (!/^sb_publishable_[A-Za-z0-9_-]+$/.test(publicKey)) {
      const payload = JSON.parse(atob(publicKey.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      if (payload.role !== "anon") throw new Error();
    }
  } catch {
    return { configured: false, error: "Use an HTTPS Supabase project URL and a publishable or legacy anon key. Never use a secret or service_role key in the website." };
  }
  return { configured: true, error: null };
}
