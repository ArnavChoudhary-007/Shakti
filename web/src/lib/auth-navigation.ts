const RETURN_TO_KEY = "shakti.auth.returnTo";

export function safeAuthReturnTo(path: string | null) {
  return path && /^\/(?:chat|documents|graph|models|settings|setup)?(?:\?[^#\\]*)?$/.test(path) ? path : "/";
}

export function readAuthReturnTo() {
  try { return safeAuthReturnTo(sessionStorage.getItem(RETURN_TO_KEY)); }
  catch { return "/"; }
}

export function rememberAuthReturnTo(path: string) {
  try { sessionStorage.setItem(RETURN_TO_KEY, safeAuthReturnTo(path)); }
  catch { return; }
}

export function clearAuthReturnTo() {
  try { sessionStorage.removeItem(RETURN_TO_KEY); }
  catch { return; }
}
