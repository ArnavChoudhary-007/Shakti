import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Globe, Info, Link2 } from "lucide-react";
import { Alert, Button, Card, CardHeader, Input, PageHeader } from "@/components/ui";
import { useConnection } from "@/lib/connection";
import { DEFAULT_COMPANION_URL, PUBLIC_SITE_URL, REPO } from "@/lib/config";
import { useSetupStatus } from "@/lib/queries";

export default function SettingsPage() {
  const { mode, version, setBaseUrl, setToken, baseUrl } = useConnection();
  const status = useSetupStatus();

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6">
      <PageHeader title="Settings" />

      {mode === "companion" ? <PairWebsite /> : <RemoteConnection baseUrl={baseUrl} onSave={setBaseUrl} onForget={() => setToken(null)} />}

      <Card>
        <CardHeader title={<span className="inline-flex items-center gap-2"><Info className="h-4 w-4" /> About</span>} />
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 px-5 py-4 text-sm">
          <dt className="text-zinc-500">Shakti companion</dt>
          <dd>{version ? `v${version.version}` : "—"}</dd>
          <dt className="text-zinc-500">Ollama</dt>
          <dd>{status.data?.running ? `v${status.data.version ?? "?"} at ${status.data.host}` : "not running"}</dd>
          <dt className="text-zinc-500">Answering with</dt>
          <dd>{status.data?.chat_model ?? "—"}</dd>
          <dt className="text-zinc-500">Source code</dt>
          <dd>
            <a className="inline-flex items-center gap-1 text-brand-600 hover:underline dark:text-brand-500" href={`https://github.com/${REPO}`} target="_blank" rel="noreferrer">
              github.com/{REPO} <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </dd>
        </dl>
      </Card>
    </div>
  );
}

/** Shown in the companion's own UI: lets the user pair the public website. */
function PairWebsite() {
  const { api } = useConnection();
  const pair = useQuery({ queryKey: ["pair-token"], queryFn: api.pairToken, staleTime: Infinity });
  const link = pair.data ? `${PUBLIC_SITE_URL}#/connect?token=${encodeURIComponent(pair.data.token)}` : null;

  return (
    <Card>
      <CardHeader
        title={<span className="inline-flex items-center gap-2"><Globe className="h-4 w-4" /> Use Shakti from the website</span>}
        description="You can also open Shakti at its public web address. Connecting gives that website permission to talk to the Shakti app on this PC."
      />
      <div className="space-y-3 px-5 py-4 text-sm">
        <p className="text-zinc-600 dark:text-zinc-400">
          Your documents still never leave this PC — the website only runs in your browser and talks to this app directly.
        </p>
        {link ? (
          <a href={link} target="_blank" rel="noreferrer">
            <Button variant="outline"><Link2 className="h-4 w-4" /> Connect the website</Button>
          </a>
        ) : (
          pair.error && <Alert tone="danger">{(pair.error as Error).message}</Alert>
        )}
        <p className="text-xs text-zinc-500">
          The link contains a secret key. Don't share it — anyone with it could use the Shakti app on this PC from their browser while they're on it.
        </p>
      </div>
    </Card>
  );
}

function RemoteConnection({ baseUrl, onSave, onForget }: { baseUrl: string; onSave: (u: string) => void; onForget: () => void }) {
  const [url, setUrl] = useState(baseUrl || DEFAULT_COMPANION_URL);
  return (
    <Card>
      <CardHeader title="Connection" description="This website talks to the Shakti app running on your PC." />
      <form
        className="space-y-3 px-5 py-4"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(url.trim() || DEFAULT_COMPANION_URL);
        }}
      >
        <label className="block text-sm font-medium" htmlFor="companion-url">Shakti app address</label>
        <div className="flex gap-2">
          <Input id="companion-url" value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button type="submit" variant="outline">Save</Button>
        </div>
        <Button type="button" variant="ghost" size="sm" className="text-red-600 dark:text-red-400" onClick={onForget}>
          Disconnect this browser
        </Button>
      </form>
    </Card>
  );
}
