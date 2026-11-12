import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Network, RefreshCw } from "lucide-react";
import { Alert, Button, Card, EmptyState, PageHeader, Progress, Spinner } from "@/components/ui";
import type { GraphBuild } from "@/lib/api";
import { KnowledgeGraph } from "@/components/KnowledgeGraph";
import { useApi } from "@/lib/connection";
import { useWorkspace } from "@/lib/workspace";
import { useSetupStatus } from "@/lib/queries";
import { usePulls } from "@/lib/pulls";

const STAGE_TEXT: Record<string, string> = {
  starting: "Starting…",
  extracting: "Reading your documents with the AI model to find entities and relationships",
  clustering: "Grouping related entities",
  labelling: "Naming the groups",
};

function BuildProgress({ build }: { build: GraphBuild | undefined }) {
  if (build?.paused_for_chat) {
    return <Alert tone="neutral" title="Paused for chat">
      Graph model requests are paused while answering questions. The build resumes automatically when all active answers finish.
      {build.total > 0 && <p className="mt-1 text-xs">Progress saved: {build.done} of {build.total} calls completed.</p>}
    </Alert>;
  }
  const stage = build?.stage ?? "starting";
  const counted = build && build.total > 0 && (stage === "extracting" || stage === "labelling");
  return (
    <Alert tone="neutral">
      <div className="space-y-2">
        <p className="flex items-center gap-2"><Spinner /> {STAGE_TEXT[stage] ?? "Building…"}{counted ? ` (${build.done} of ${build.total})` : "…"}</p>
        {counted && <Progress value={build.done / build.total} />}
        <p className="text-xs text-zinc-500">This runs on your PC and can take a few minutes. You can use other pages meanwhile.</p>
      </div>
    </Alert>
  );
}

export default function GraphPage() {
  const api = useApi();
  const queryClient = useQueryClient();
  const { workspace } = useWorkspace();
  const status = useSetupStatus();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const { pulls, startPull, cancelPull } = usePulls();
  const chooseModel = useMutation({
    mutationFn: (name: string) => api.updateSettings({ graph_model: name }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
  const graphModel = settings.data?.effective_graph_model;
  const installedModels = status.data?.models ?? [];
  const modelInstalled = !!graphModel && installedModels.includes(graphModel);
  const modelOptions = Array.from(new Set([...(graphModel ? [graphModel] : []), ...installedModels]));
  const pull = graphModel ? pulls[graphModel] : undefined;
  const downloading = !!pull && !pull.done;
  const graph = useQuery({
    queryKey: ["graph", workspace],
    queryFn: () => api.graph(workspace),
    // Poll while a build runs in the background; the graph appears when it finishes.
    refetchInterval: (q) => (q.state.data?.building ? 1500 : false),
  });
  const build = useMutation({
    mutationFn: () => api.buildGraph(workspace),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["graph", workspace] }),
  });
  const hasGraph = !!graph.data?.nodes.length;
  const pending = graph.data?.pending_docs ?? 0;
  const building = build.isPending || graph.data?.building;
  const buildState = graph.data?.build;

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col px-4 py-8 sm:px-6">
      <PageHeader
        title="Knowledge graph"
        description="People, organizations and ideas in your documents, and how they connect. Built by your local model."
        action={
          <Button onClick={() => build.mutate()} loading={!!building} disabled={!!building || chooseModel.isPending || !modelInstalled || !status.data?.running}>
            {!building && <RefreshCw className="h-4 w-4" />}
            {building ? "Building…" : hasGraph ? "Update graph" : "Build graph"}
          </Button>
        }
      />

      <Card className="mb-4 p-4">
        <label htmlFor="graph-model" className="block text-sm font-semibold">Graph model (separate from chat)</label>
        <p className="mt-1 text-xs text-zinc-500">Choose a smaller model for graph extraction and group labels. Your chat model stays unchanged. Graph requests pause automatically while you chat.</p>
        {settings.isLoading || status.isLoading ? <p className="mt-3 flex items-center gap-2 text-sm"><Spinner /> Loading models...</p> : settings.error || status.error ? (
          <div className="mt-3">
            <Alert tone="danger">{(settings.error || status.error)?.message}</Alert>
            <Button className="mt-2" variant="outline" onClick={() => { void settings.refetch(); void status.refetch(); }}>Retry model settings</Button>
          </div>
        ) : !graphModel ? <div className="mt-3"><Alert tone="warning">Update the companion to configure a separate graph model.</Alert></div> : (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <select id="graph-model" value={graphModel} disabled={!!building || chooseModel.isPending || downloading} onChange={(event) => chooseModel.mutate(event.target.value)} className="h-9 rounded-lg border border-zinc-300 bg-transparent px-2 text-sm dark:border-zinc-700">
                {modelOptions.map((name) => <option key={name} value={name}>{name}{installedModels.includes(name) ? "" : " (not installed)"}</option>)}
              </select>
              {!modelInstalled && <Button variant="outline" size="sm" disabled={!!building || downloading || !status.data?.running} onClick={() => void startPull(graphModel)}><Download className="h-4 w-4" /> Download graph model</Button>}
              {downloading && <Button variant="ghost" size="sm" onClick={() => cancelPull(graphModel)}>Cancel download</Button>}
            </div>
            {!modelInstalled && !downloading && <p className="text-xs text-zinc-500">Download this model or choose an installed one before building. Downloads start only when you click the button.</p>}
            {downloading && <><Progress value={pull.total ? pull.completed / pull.total : null} /><p className="text-xs text-zinc-500">{pull.status}</p></>}
            {pull?.error && <Alert tone="danger">Download failed: {pull.error}</Alert>}
            {buildState?.model && building && <p className="text-xs text-zinc-500">Current build uses {buildState.model}. Model changes apply to the next build.</p>}
          </div>
        )}
        {chooseModel.error && <div className="mt-2"><Alert tone="danger">{chooseModel.error.message}</Alert></div>}
      </Card>

      {pending > 0 && !building && (
        <div className="mb-4">
          <Alert tone="brand">
            {pending} document{pending === 1 ? " hasn't" : "s haven't"} been added to the graph yet. Click <strong>{hasGraph ? "Update graph" : "Build graph"}</strong> — it can take a few minutes on a large workspace.
          </Alert>
        </div>
      )}
      {building && <div className="mb-4"><BuildProgress build={buildState} /></div>}
      {!building && buildState?.state === "error" && (
        <div className="mb-4"><Alert tone="danger" title="The graph wasn't fully built">{buildState.error}</Alert></div>
      )}
      {build.error && <div className="mb-4"><Alert tone="danger">{(build.error as Error).message}</Alert></div>}

      {graph.data && hasGraph && !graph.error ? (
        <KnowledgeGraph key={workspace} data={graph.data} />
      ) : (
        <Card className="relative min-h-[360px] overflow-hidden">
          {graph.isLoading ? (
            <div className="flex items-center gap-2 p-6 text-sm text-zinc-500"><Spinner /> Loading graph...</div>
          ) : graph.error ? (
            <div className="p-5">
              <Alert tone="danger" title="Couldn't load the graph">{graph.error.message}</Alert>
              <Button className="mt-3" loading={graph.isFetching} onClick={() => void graph.refetch()}>Retry</Button>
            </div>
          ) : (
            <EmptyState icon={<Network className="h-6 w-6" />} title="No graph yet">
              {pending > 0 ? "Build the graph to see how the things in your documents connect." : "Upload documents first, then build the graph."}
            </EmptyState>
          )}
        </Card>
      )}
    </div>
  );
}
