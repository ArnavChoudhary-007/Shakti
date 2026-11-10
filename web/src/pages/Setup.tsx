import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, ExternalLink, Play, RotateCcw, ShieldCheck } from "lucide-react";
import { ModelPicker } from "@/components/ModelPicker";
import { Alert, Button, Card, PageHeader, Progress, Spinner } from "@/components/ui";
import { searchModelsReady, type SearchModelsStatus, type SetupStatus } from "@/lib/api";
import { useApi } from "@/lib/connection";
import { useSetupStatus } from "@/lib/queries";
import { cn, formatBytes } from "@/lib/utils";

function Step({ n, title, done, active, children }: { n: number; title: string; done: boolean; active: boolean; children?: ReactNode }) {
  return (
    <Card className={cn("overflow-hidden", !active && !done && "opacity-60")}>
      <div className="flex items-center gap-3 px-5 py-4">
        <span
          className={cn(
            "grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-semibold",
            done ? "bg-emerald-500 text-white" : active ? "bg-brand-600 text-white" : "bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
          )}
        >
          {done ? <CheckCircle2 className="h-5 w-5" /> : n}
        </span>
        <h2 className="font-semibold">{title}</h2>
      </div>
      {active && children && <div className="border-t border-zinc-100 px-5 py-5 dark:border-zinc-800">{children}</div>}
    </Card>
  );
}

function OllamaStep({ status }: { status: SetupStatus }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["setup-status"] });
  const install = useMutation({ mutationFn: api.installOllama, onSettled: refresh });
  const start = useMutation({ mutationFn: api.startOllama, onSettled: refresh });
  const inst = status.install;

  if (status.installed && !status.running) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">Ollama is installed but not running. Start it and Shakti will connect automatically.</p>
        <Button onClick={() => start.mutate()} loading={start.isPending}>
          <Play className="h-4 w-4" /> Start Ollama
        </Button>
        {start.isSuccess && <p className="flex items-center gap-2 text-sm text-zinc-500"><Spinner /> Waiting for Ollama to start…</p>}
        {start.error && <Alert tone="danger">{(start.error as Error).message}</Alert>}
      </div>
    );
  }

  const busy = ["downloading", "verifying", "installer_running"].includes(inst.state);
  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Shakti uses <strong>Ollama</strong>, a free app that runs AI models on your own computer. Nothing you ask or upload is sent to the internet.
      </p>

      {status.can_auto_install ? (
        <>
          {!busy && inst.state !== "done" && (
            <Button onClick={() => install.mutate()} loading={install.isPending}>
              <Download className="h-4 w-4" /> Install Ollama for me
            </Button>
          )}
          {inst.state === "downloading" && (
            <div className="space-y-1.5">
              <Progress value={inst.total ? inst.downloaded / inst.total : null} />
              <p className="text-xs text-zinc-500">
                Downloading the official installer… {formatBytes(inst.downloaded)}
                {inst.total ? ` of ${formatBytes(inst.total)}` : ""}
              </p>
            </div>
          )}
          {inst.state === "verifying" && <p className="flex items-center gap-2 text-sm"><Spinner /> Checking the installer's digital signature…</p>}
          {inst.state === "installer_running" && (
            <Alert tone="brand" title="The Ollama installer is open">
              Look for the Ollama Setup window (it may be behind this one) and click <strong>Install</strong>. This page continues by itself when it's done.
            </Alert>
          )}
          {inst.state === "done" && !status.running && (
            <p className="flex items-center gap-2 text-sm"><Spinner /> Waiting for Ollama to start…</p>
          )}
          {inst.state === "error" && <Alert tone="danger" title="Install didn't finish">{inst.error}</Alert>}
          <p className="flex items-start gap-2 text-xs text-zinc-500">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Shakti downloads the installer from ollama.com and only runs it if it's signed by Ollama Inc.
          </p>
        </>
      ) : null}

      <p className="text-sm">
        {status.can_auto_install ? "Prefer to do it yourself? " : "Download and install Ollama for your system, then come back here: "}
        <a className="inline-flex items-center gap-1 font-medium text-brand-600 hover:underline dark:text-brand-500" href={status.download_url} target="_blank" rel="noreferrer">
          ollama.com/download <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </p>
    </div>
  );
}

function SearchModelsStep({ status }: { status: SearchModelsStatus }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const prepare = useMutation({
    mutationFn: api.prepareSearchModels,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["setup-status"] }),
  });
  const job = status.job;
  const busy = job.state === "downloading" || job.state === "loading" || prepare.isPending;

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Shakti uses two small models to find the right passages in your documents. They're downloaded once (about
        180 MB) and then run offline on your PC.
      </p>
      {job.state === "downloading" && (
        <div className="space-y-1.5">
          <Progress value={job.total ? job.downloaded / job.total : null} />
          <p className="text-xs text-zinc-500">
            Downloading… {formatBytes(job.downloaded)}
            {job.total ? ` of ${formatBytes(job.total)}` : ""}
          </p>
        </div>
      )}
      {job.state === "loading" && <p className="flex items-center gap-2 text-sm"><Spinner /> Loading the models…</p>}
      {job.state === "error" && <Alert tone="danger" title="Download didn't finish">{job.error}</Alert>}
      {prepare.error && <Alert tone="danger">{(prepare.error as Error).message}</Alert>}
      {!busy && (
        <Button onClick={() => prepare.mutate()}>
          {job.state === "error" ? <RotateCcw className="h-4 w-4" /> : <Download className="h-4 w-4" />}
          {job.state === "error" ? "Try again" : "Download search models"}
        </Button>
      )}
    </div>
  );
}

export default function SetupPage() {
  const { data: status, isLoading, error } = useSetupStatus();

  if (isLoading) return <div className="flex items-center gap-2 p-8 text-sm text-zinc-500"><Spinner /> Checking your setup…</div>;
  if (error || !status) return <div className="p-8"><Alert tone="danger">Couldn't check setup: {(error as Error)?.message}</Alert></div>;

  const ollamaDone = status.running;
  const modelDone = ollamaDone && status.chat_model_installed;
  const searchDone = searchModelsReady(status);

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <PageHeader title="Set up Shakti" description="A few quick steps and you can start asking questions about your documents." />
      <div className="space-y-4">
        <Step n={1} title={ollamaDone ? `Ollama is running${status.version ? ` (v${status.version})` : ""}` : "Install Ollama"} done={ollamaDone} active={!ollamaDone}>
          <OllamaStep status={status} />
        </Step>
        <Step n={2} title={modelDone ? `Using ${status.chat_model}` : "Choose an AI model"} done={modelDone} active={ollamaDone}>
          <ModelPicker compact />
        </Step>
        {status.search_models && (
          // Independent of Ollama, so it can download while a chat model does.
          <Step n={3} title={searchDone ? "Document search is ready" : "Download the search models"} done={searchDone} active={!searchDone}>
            <SearchModelsStep status={status.search_models} />
          </Step>
        )}
        <Step n={status.search_models ? 4 : 3} title="Add your documents" done={false} active={modelDone && searchDone}>
          <div className="flex flex-wrap items-center gap-3">
            <p className="flex-1 text-sm text-zinc-600 dark:text-zinc-400">
              You're all set. Upload PDFs, Word files, spreadsheets, emails, chat exports or audio, then ask anything about them.
            </p>
            <Link to="/documents"><Button>Upload documents</Button></Link>
            <Link to="/chat"><Button variant="outline">Start asking</Button></Link>
          </div>
        </Step>
      </div>
    </div>
  );
}
