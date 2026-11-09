import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Cloud, Download, HardDrive, Sparkles, Trash2, X } from "lucide-react";
import type { InstalledModel, SuggestedModel } from "@/lib/api";
import { useApi } from "@/lib/connection";
import { usePulls } from "@/lib/pulls";
import { useRecommendations, useSetupStatus } from "@/lib/queries";
import { cn, formatBytes } from "@/lib/utils";
import { Alert, Badge, Button, Input, Progress, Spinner } from "./ui";

function PullProgress({ model }: { model: string }) {
  const { pulls, cancelPull } = usePulls();
  const p = pulls[model];
  if (!p || (p.done && !p.error)) return null;
  if (p.error) return <p className="mt-2 text-xs text-red-600 dark:text-red-400">Download failed: {p.error}</p>;
  const fraction = p.total ? p.completed / p.total : null;
  return (
    <div className="mt-3 space-y-1.5">
      <Progress value={fraction} />
      <div className="flex items-center justify-between text-xs text-zinc-500">
        <span className="truncate">
          {p.total ? `${formatBytes(p.completed)} of ${formatBytes(p.total)}` : p.status}
        </span>
        <button className="inline-flex items-center gap-1 hover:text-zinc-900 dark:hover:text-zinc-100" onClick={() => cancelPull(model)}>
          <X className="h-3 w-3" /> Cancel
        </button>
      </div>
    </div>
  );
}

/** Pick which installed model answers questions, and download new ones. */
export function ModelPicker({ compact = false }: { compact?: boolean }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const status = useSetupStatus();
  const running = !!status.data?.running;
  const recs = useRecommendations(running);
  const { pulls, startPull } = usePulls();
  const [custom, setCustom] = useState("");

  const select = useMutation({
    mutationFn: (name: string) => api.updateSettings({ chat_model: name }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const remove = useMutation({
    mutationFn: (name: string) => api.deleteModel(name),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const download = async (name: string) => {
    const ok = await startPull(name);
    // First model on this PC? Use it straight away.
    if (ok && !status.data?.chat_model_installed) select.mutate(name);
  };

  if (!running) return <Alert tone="neutral">Start Ollama first — then your models will appear here.</Alert>;
  if (recs.isLoading) return <div className="flex items-center gap-2 text-sm text-zinc-500"><Spinner /> Checking your PC and models…</div>;
  if (recs.isError || !recs.data) return <Alert tone="danger">Couldn't load models: {(recs.error as Error)?.message}</Alert>;

  const { specs, ram_budget_gb, installed_models, suggested_models } = recs.data;
  const current = status.data?.chat_model;
  const notInstalled = suggested_models.filter((m) => !m.installed);
  const recommended = [...notInstalled].filter((m) => m.tags.includes("recommended")).sort((a, b) => (b.approx_size_gb ?? 0) - (a.approx_size_gb ?? 0))[0];

  return (
    <div className="space-y-6">
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Your PC has <strong>{specs.total_ram_gb} GB</strong> of memory and <strong>{specs.cpu_cores}</strong> CPU cores, so models up to about{" "}
        <strong>{ram_budget_gb} GB</strong> will run comfortably. Smaller models answer faster; larger ones answer better.
      </p>

      {installed_models.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">On this PC</h3>
          <ul className="divide-y divide-zinc-100 rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
            {installed_models.map((m) => (
              <InstalledRow
                key={m.name}
                model={m}
                current={m.name === current}
                onUse={() => select.mutate(m.name)}
                onDelete={compact ? undefined : () => {
                  if (window.confirm(`Delete ${m.name} from this PC? You can download it again later.`)) remove.mutate(m.name);
                }}
                busy={select.isPending && select.variables === m.name}
              />
            ))}
          </ul>
        </section>
      )}

      {notInstalled.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">{installed_models.length ? "Download more" : "Choose a model to download"}</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            {notInstalled.map((m) => (
              <SuggestionCard
                key={m.name}
                model={m}
                highlighted={m.name === recommended?.name}
                pulling={!!pulls[m.name] && !pulls[m.name].done}
                onDownload={() => download(m.name)}
              />
            ))}
          </div>
        </section>
      )}

      {!compact && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Another model</h3>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const name = custom.trim();
              if (name) void download(name);
            }}
          >
            <Input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Any model name from ollama.com/library, e.g. mistral:7b" />
            <Button type="submit" variant="outline" disabled={!custom.trim()}>
              <Download className="h-4 w-4" /> Download
            </Button>
          </form>
          {custom.trim() && <PullProgress model={custom.trim()} />}
        </section>
      )}

      {(select.error || remove.error) && <Alert tone="danger">{((select.error || remove.error) as Error).message}</Alert>}
    </div>
  );
}

function InstalledRow({ model, current, onUse, onDelete, busy }: { model: InstalledModel; current: boolean; onUse: () => void; onDelete?: () => void; busy: boolean }) {
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <div className="grid h-9 w-9 place-items-center rounded-lg bg-zinc-100 dark:bg-zinc-800">
        {model.is_cloud ? <Cloud className="h-4 w-4" /> : <HardDrive className="h-4 w-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{model.name}</p>
        <p className="text-xs text-zinc-500">
          {model.is_cloud ? "Runs on Ollama's servers — your question text leaves this PC" : [model.size_gb && `${model.size_gb} GB`, model.parameter_size, model.quantization].filter(Boolean).join(" · ")}
          {!model.fits_comfortably && " · may be slow on this PC"}
        </p>
      </div>
      {current ? (
        <Badge tone="success"><Check className="h-3 w-3" /> In use</Badge>
      ) : (
        <Button size="sm" variant="outline" onClick={onUse} loading={busy}>Use this</Button>
      )}
      {onDelete && !current && (
        <Button size="icon" variant="ghost" aria-label={`Delete ${model.name}`} onClick={onDelete}>
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </li>
  );
}

function SuggestionCard({ model, highlighted, pulling, onDownload }: { model: SuggestedModel; highlighted: boolean; pulling: boolean; onDownload: () => void }) {
  return (
    <div className={cn("flex flex-col rounded-xl border p-4", highlighted ? "border-brand-500 ring-1 ring-brand-500/30" : "border-zinc-200 dark:border-zinc-800")}>
      <div className="flex items-start justify-between gap-2">
        <p className="font-medium">{model.name}</p>
        {highlighted && <Badge tone="brand"><Sparkles className="h-3 w-3" /> Best fit</Badge>}
      </div>
      <p className="mt-1 flex-1 text-sm text-zinc-500 dark:text-zinc-400">{model.description}</p>
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="text-xs text-zinc-500">{model.approx_size_gb ? `~${model.approx_size_gb} GB download` : ""}</span>
        <Button size="sm" variant={highlighted ? "primary" : "outline"} onClick={onDownload} loading={pulling}>
          {!pulling && <Download className="h-4 w-4" />} {pulling ? "Downloading" : "Download"}
        </Button>
      </div>
      <PullProgress model={model.name} />
    </div>
  );
}
