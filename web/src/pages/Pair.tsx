import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, ExternalLink, Link2 } from "lucide-react";
import { Logo } from "@/components/Layout";
import { Button, Card, Spinner } from "@/components/ui";
import { useConnection } from "@/lib/connection";
import { DEFAULT_COMPANION_URL } from "@/lib/config";

/** Public website, companion found, but this browser isn't paired yet. */
export function PairPage() {
  const { version } = useConnection();
  return (
    <div className="grid min-h-full place-items-center px-6 py-12">
      <Card className="w-full max-w-lg p-8 text-center">
        <Logo className="justify-center text-lg" />
        <CheckCircle2 className="mx-auto mt-6 h-10 w-10 text-emerald-500" />
        <h1 className="mt-3 text-xl font-semibold">Shakti is installed on this PC</h1>
        <p className="mt-1 text-sm text-zinc-500">{version ? `Version ${version.version}` : ""}</p>
        <p className="mt-4 text-sm text-zinc-600 dark:text-zinc-400">
          Open the Shakti app to continue. To use Shakti from this website instead, open the app, go to <strong>Settings</strong> and click{" "}
          <strong>Connect the website</strong>.
        </p>
        <a href={DEFAULT_COMPANION_URL} className="mt-6 inline-block">
          <Button size="lg"><ExternalLink className="h-4 w-4" /> Open Shakti</Button>
        </a>
      </Card>
    </div>
  );
}

/** Handles #/connect?token=… links created by the companion's Settings page. */
export function ConnectPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { setToken, phase, retry } = useConnection();
  const token = params.get("token");

  useEffect(() => {
    if (token) {
      setToken(token);
      retry();
    }
  }, [token, setToken, retry]);

  useEffect(() => {
    if (phase === "ready") navigate("/", { replace: true });
  }, [phase, navigate]);

  return (
    <div className="grid min-h-full place-items-center px-6">
      <Card className="max-w-md p-8 text-center">
        <Link2 className="mx-auto h-8 w-8 text-brand-600" />
        <h1 className="mt-3 text-lg font-semibold">{token ? "Connecting to your Shakti app…" : "This connect link is missing its key"}</h1>
        {token && phase !== "unpaired" && phase !== "missing" && <Spinner className="mx-auto mt-4" />}
        {phase === "unpaired" && <p className="mt-3 text-sm text-red-600">The key didn't work. Create a new link from Shakti's Settings page.</p>}
        {phase === "missing" && <p className="mt-3 text-sm text-red-600">Couldn't reach the Shakti app. Make sure it's running, then try the link again.</p>}
      </Card>
    </div>
  );
}
