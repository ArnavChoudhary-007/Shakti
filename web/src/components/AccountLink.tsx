import { Link, useLocation } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { safeAuthReturnTo } from "@/lib/auth-navigation";

export function AccountLink({ className }: { className?: string }) {
  const { configured, session } = useAuth();
  const location = useLocation();
  if (!configured) return null;
  const next = safeAuthReturnTo(location.pathname + location.search);
  const suffix = next === "/" ? "" : `?next=${encodeURIComponent(next)}`;
  return <Link className={className} to={session ? `/account${suffix}` : `/auth/login${suffix}`}>{session ? "Account" : "Sign in"}</Link>;
}
