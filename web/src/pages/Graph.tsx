import { useMemo, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationNodeDatum } from "d3-force";
import { Network, RefreshCw, X } from "lucide-react";
import { Alert, Badge, Button, Card, EmptyState, PageHeader, Spinner } from "@/components/ui";
import type { GraphData } from "@/lib/api";
import { useApi } from "@/lib/connection";
import { useWorkspace } from "@/lib/workspace";

const PALETTE = ["#4f46e5", "#0891b2", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0d9488", "#dc2626", "#2563eb", "#65a30d"];
const MAX_NODES = 300;

type Node = SimulationNodeDatum & { id: string; label: string; type: string | null; community: number | null; centrality: number; degree: number };
type Link = { source: Node; target: Node; relation: string };

function useLayout(data: GraphData | undefined) {
  return useMemo(() => {
    if (!data || data.nodes.length === 0) return null;
    // Keep the most central nodes so large graphs stay readable and fast.
    const nodes: Node[] = [...data.nodes]
      .sort((a, b) => (b.centrality ?? 0) - (a.centrality ?? 0))
      .slice(0, MAX_NODES)
      .map((n) => ({ id: n.id, label: n.label, type: n.type, community: n.community, centrality: n.centrality ?? 0, degree: 0 }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links: Link[] = [];
    for (const e of data.edges) {
      const s = byId.get(e.source);
      const t = byId.get(e.target);
      if (s && t && s !== t) {
        links.push({ source: s, target: t, relation: e.relation });
        s.degree++;
        t.degree++;
      }
    }
    const sim = forceSimulation(nodes)
      .force("link", forceLink<Node, Link>(links).distance(60).strength(0.4))
      .force("charge", forceManyBody().strength(-120))
      .force("center", forceCenter(0, 0))
      .force("collide", forceCollide<Node>().radius((n) => radius(n) + 4))
      .stop();
    for (let i = 0; i < 300; i++) sim.tick();

    const xs = nodes.map((n) => n.x ?? 0);
    const ys = nodes.map((n) => n.y ?? 0);
    const pad = 60;
    const box = { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
    return { nodes, links, box, truncated: data.nodes.length > MAX_NODES };
  }, [data]);
}

const radius = (n: Node) => 4 + Math.min(14, Math.sqrt(n.degree) * 3);
const color = (n: Node) => (n.community == null ? "#71717a" : PALETTE[n.community % PALETTE.length]);

export default function GraphPage() {
  const api = useApi();
  const queryClient = useQueryClient();
  const { workspace } = useWorkspace();
  const graph = useQuery({ queryKey: ["graph", workspace], queryFn: () => api.graph(workspace) });
  const build = useMutation({
    mutationFn: () => api.buildGraph(workspace),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["graph", workspace] }),
  });
  const layout = useLayout(graph.data);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<{ x: number; y: number; k: number }>({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number } | null>(null);

  const communities = new Map((graph.data?.communities ?? []).map((c) => [c.id, c.label]));
  const pending = graph.data?.pending_docs ?? 0;
  const building = build.isPending || graph.data?.building;

  const selectedNode = layout?.nodes.find((n) => n.id === selected) ?? null;
  const neighbours = selectedNode
    ? (graph.data?.edges ?? []).filter((e) => e.source === selected || e.target === selected)
    : [];

  const onWheel = (e: WheelEvent) => {
    const k = Math.min(4, Math.max(0.3, view.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    setView({ ...view, k });
  };
  const onPointerDown = (e: PointerEvent) => {
    drag.current = { x: e.clientX, y: e.clientY };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent) => {
    if (!drag.current || !layout) return;
    const scale = layout.box.w / ((e.currentTarget as SVGElement).clientWidth || 1) / view.k;
    setView((v) => ({ ...v, x: v.x - (e.clientX - drag.current!.x) * scale, y: v.y - (e.clientY - drag.current!.y) * scale }));
    drag.current = { x: e.clientX, y: e.clientY };
  };

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col px-4 py-8 sm:px-6">
      <PageHeader
        title="Knowledge graph"
        description="People, organizations and ideas in your documents, and how they connect. Built by your local model."
        action={
          <Button onClick={() => build.mutate()} loading={!!building} disabled={!!building}>
            {!building && <RefreshCw className="h-4 w-4" />}
            {building ? "Building…" : layout ? "Update graph" : "Build graph"}
          </Button>
        }
      />

      {pending > 0 && !building && (
        <div className="mb-4">
          <Alert tone="brand">
            {pending} new document part{pending === 1 ? "" : "s"} haven't been added to the graph yet. Click <strong>{layout ? "Update graph" : "Build graph"}</strong> — it can take a few minutes on a large workspace.
          </Alert>
        </div>
      )}
      {building && (
        <div className="mb-4">
          <Alert tone="neutral">Reading your documents with the AI model to find entities and relationships. Keep this tab open…</Alert>
        </div>
      )}
      {build.error && <div className="mb-4"><Alert tone="danger">{(build.error as Error).message}</Alert></div>}

      <Card className="relative min-h-[480px] flex-1 overflow-hidden">
        {graph.isLoading ? (
          <div className="flex items-center gap-2 p-6 text-sm text-zinc-500"><Spinner /> Loading graph…</div>
        ) : !layout ? (
          <EmptyState icon={<Network className="h-6 w-6" />} title="No graph yet">
            {pending > 0 ? "Build the graph to see how the things in your documents connect." : "Upload documents first, then build the graph."}
          </EmptyState>
        ) : (
          <>
            <svg
              className="h-full min-h-[480px] w-full cursor-grab touch-none select-none active:cursor-grabbing"
              viewBox={`${layout.box.x + view.x + (layout.box.w - layout.box.w / view.k) / 2} ${layout.box.y + view.y + (layout.box.h - layout.box.h / view.k) / 2} ${layout.box.w / view.k} ${layout.box.h / view.k}`}
              onWheel={onWheel}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={() => (drag.current = null)}
              role="img"
              aria-label="Knowledge graph"
            >
              <g stroke="currentColor" className="text-zinc-300 dark:text-zinc-700">
                {layout.links.map((l, i) => {
                  const active = selected && (l.source.id === selected || l.target.id === selected);
                  return (
                    <line
                      key={i}
                      x1={l.source.x} y1={l.source.y} x2={l.target.x} y2={l.target.y}
                      strokeWidth={active ? 1.8 : 0.8}
                      className={active ? "text-brand-500" : undefined}
                      stroke={active ? "currentColor" : undefined}
                      opacity={selected && !active ? 0.25 : 1}
                    />
                  );
                })}
              </g>
              {layout.nodes.map((n) => {
                const dim = selected && n.id !== selected && !neighbours.some((e) => e.source === n.id || e.target === n.id);
                return (
                  <g
                    key={n.id}
                    transform={`translate(${n.x},${n.y})`}
                    opacity={dim ? 0.25 : 1}
                    className="cursor-pointer"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={() => setSelected(n.id === selected ? null : n.id)}
                  >
                    <circle r={radius(n)} fill={color(n)} stroke="white" strokeWidth={1.5} className="dark:stroke-zinc-900" />
                    {(n.degree >= 3 || n.id === selected) && (
                      <text y={-radius(n) - 4} textAnchor="middle" className="fill-zinc-700 text-[10px] font-medium dark:fill-zinc-300" style={{ paintOrder: "stroke" }} stroke="white" strokeWidth={3} strokeOpacity={0.6}>
                        {n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label}
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>

            <div className="pointer-events-none absolute left-3 top-3 flex max-w-[60%] flex-wrap gap-1.5">
              {[...communities.entries()].slice(0, 8).map(([id, label]) => (
                <span key={id} className="inline-flex items-center gap-1.5 rounded-full bg-white/90 px-2 py-0.5 text-xs shadow-sm dark:bg-zinc-900/90">
                  <span className="h-2 w-2 rounded-full" style={{ background: PALETTE[id % PALETTE.length] }} /> {label}
                </span>
              ))}
            </div>
            <p className="pointer-events-none absolute bottom-3 left-3 text-xs text-zinc-400">
              {layout.nodes.length} entities · {layout.links.length} connections{layout.truncated ? ` (showing the ${MAX_NODES} most connected)` : ""} · scroll to zoom, drag to move
            </p>

            {selectedNode && (
              <aside className="absolute inset-y-3 right-3 w-72 overflow-y-auto rounded-xl border border-zinc-200 bg-white p-4 shadow-lg dark:border-zinc-800 dark:bg-zinc-900">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold">{selectedNode.label}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {selectedNode.type && <Badge>{selectedNode.type}</Badge>}
                      {selectedNode.community != null && communities.get(selectedNode.community) && <Badge tone="brand">{communities.get(selectedNode.community)}</Badge>}
                    </div>
                  </div>
                  <Button variant="ghost" size="icon" aria-label="Close" onClick={() => setSelected(null)}><X className="h-4 w-4" /></Button>
                </div>
                <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-zinc-500">Connections</p>
                <ul className="mt-2 space-y-2 text-sm">
                  {neighbours.slice(0, 40).map((e, i) => {
                    const other = e.source === selected ? e.target : e.source;
                    const otherLabel = graph.data?.nodes.find((n) => n.id === other)?.label ?? other;
                    return (
                      <li key={i}>
                        <button className="text-left hover:underline" onClick={() => setSelected(other)}>
                          <span className="text-zinc-500">{e.source === selected ? e.relation : `← ${e.relation}`}</span>{" "}
                          <strong>{otherLabel}</strong>
                        </button>
                        {e.source_doc && <p className="truncate text-xs text-zinc-400">from {e.source_doc}</p>}
                      </li>
                    );
                  })}
                </ul>
              </aside>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
