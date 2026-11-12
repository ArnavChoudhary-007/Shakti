import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { Minus, Plus, RotateCcw } from "lucide-react";
import type { GraphData } from "@/lib/api";
import { createGraphLayout, GRAPH_HEIGHT, GRAPH_WIDTH, highlightedNodes, type GraphNode } from "@/lib/graph-layout";
import "./knowledge-graph.css";

type Camera = { x: number; y: number; k: number };
const OVERVIEW: Camera = { x: 0, y: 0, k: 1 };
const shorten = (label: string, limit: number) => label.length > limit ? `${label.slice(0, limit - 1)}\u2026` : label;

function cameraEase(progress: number) {
  if (progress === 0 || progress === 1) return progress;
  const bezier = (time: number, first: number, second: number) => 3 * (1 - time) ** 2 * time * first + 3 * (1 - time) * time ** 2 * second + time ** 3;
  let low = 0;
  let high = 1;
  for (let iteration = 0; iteration < 14; iteration++) {
    const middle = (low + high) / 2;
    if (bezier(middle, 0.3, 0.2) < progress) low = middle;
    else high = middle;
  }
  return bezier((low + high) / 2, 0.8, 1);
}

export function KnowledgeGraph({ data }: { data: GraphData }) {
  const layout = useMemo(() => createGraphLayout({ nodes: data.nodes, edges: data.edges, communities: data.communities }), [data.nodes, data.edges, data.communities]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [camera, setCamera] = useState<Camera>(OVERVIEW);
  const [moving, setMoving] = useState(false);
  const [screenScale, setScreenScale] = useState(1);
  const cameraRef = useRef(camera);
  const animation = useRef<number | null>(null);
  const drag = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const descriptionId = useId();
  const canvasRef = useRef<SVGSVGElement>(null);
  const selected = layout.nodes.find((node) => node.id === selectedId) ?? null;
  const highlighted = useMemo(() => selected ? highlightedNodes(layout, selected) : null, [layout, selected]);
  const activeGroup = selected ? layout.groups.get(selected.group) : null;
  const zoomed = selected !== null || camera.x !== 0 || camera.y !== 0 || camera.k !== 1;

  useEffect(() => { cameraRef.current = camera; }, [camera]);
  useEffect(() => () => { if (animation.current !== null) cancelAnimationFrame(animation.current); }, []);
  useEffect(() => {
    if (animation.current !== null) cancelAnimationFrame(animation.current);
    animation.current = null;
    drag.current = null;
    setMoving(false);
    setSelectedId(null);
    setCamera(OVERVIEW);
  }, [layout]);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (event.deltaY === 0) return;
      if (animation.current !== null) cancelAnimationFrame(animation.current);
      animation.current = null;
      setCamera((current) => ({ ...current, k: Math.min(4, Math.max(0.3, current.k * (event.deltaY < 0 ? 1.15 : 1 / 1.15))) }));
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      const bounds = canvas.getBoundingClientRect();
      setScreenScale(Math.min(bounds.width / GRAPH_WIDTH, bounds.height / GRAPH_HEIGHT) || 1);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(canvas);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, []);

  const stopAnimation = () => {
    if (animation.current !== null) cancelAnimationFrame(animation.current);
    animation.current = null;
  };
  const animateCamera = (target: Camera) => {
    stopAnimation();
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setCamera(target);
      return;
    }
    const start = cameraRef.current;
    let startedAt: number | null = null;
    const frame = (timestamp: number) => {
      startedAt ??= timestamp;
      const progress = Math.min(1, (timestamp - startedAt) / 1000);
      const eased = cameraEase(progress);
      setCamera({ x: start.x + (target.x - start.x) * eased, y: start.y + (target.y - start.y) * eased, k: start.k + (target.k - start.k) * eased });
      animation.current = progress < 1 ? requestAnimationFrame(frame) : null;
    };
    animation.current = requestAnimationFrame(frame);
  };
  const reset = () => { setSelectedId(null); animateCamera(OVERVIEW); };
  const selectNode = (node: GraphNode) => {
    if (selectedId === node.id) { reset(); return; }
    setSelectedId(node.id);
    const members = layout.nodes.filter((member) => member.group === node.group);
    const left = Math.min(...members.map((member) => member.x));
    const right = Math.max(...members.map((member) => member.x));
    const top = Math.min(...members.map((member) => member.y));
    const bottom = Math.max(...members.map((member) => member.y));
    const scale = Math.min(2.3, Math.max(1, Math.min(GRAPH_WIDTH / (right - left + 140), GRAPH_HEIGHT / (bottom - top + 110))));
    animateCamera({ x: (left + right - GRAPH_WIDTH) / 2, y: (top + bottom - GRAPH_HEIGHT) / 2, k: scale });
  };
  const zoomBy = (factor: number) => {
    stopAnimation();
    setCamera((current) => ({ ...current, k: Math.min(4, Math.max(0.3, current.k * factor)) }));
  };
  const onPointerDown = (event: PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0 || drag.current) return;
    stopAnimation();
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
    setMoving(true);
  };
  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const previous = drag.current;
    if (!previous || previous.pointerId !== event.pointerId) return;
    const matrix = event.currentTarget.getScreenCTM()?.inverse();
    if (!matrix) return;
    const deltaX = event.clientX - previous.x;
    const deltaY = event.clientY - previous.y;
    const offsetX = deltaX * matrix.a + deltaY * matrix.c;
    const offsetY = deltaX * matrix.b + deltaY * matrix.d;
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
    setCamera((current) => ({ ...current, x: current.x - offsetX, y: current.y - offsetY }));
  };
  const onPointerEnd = (event: PointerEvent<SVGSVGElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    setMoving(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const relatedLinks = selected ? layout.links.filter((link) => link.kind !== "membership" && (link.source === selected || link.target === selected)) : [];
  const groupMembers = activeGroup ? layout.nodes.filter((node) => node.group === activeGroup.group) : [];
  const groupEntityCount = groupMembers.filter((node) => node.kind === "entity").length;
  const groupFileCount = groupMembers.filter((node) => node.kind === "file").length;
  const status = selected ? selected.kind === "group"
    ? `${selected.label} - ${groupEntityCount} ${groupEntityCount === 1 ? "entity" : "entities"}, ${groupFileCount} source file${groupFileCount === 1 ? "" : "s"}`
    : `${activeGroup?.label} > ${selected.label}`
    : "Select a group to zoom into it, then explore its entities and source files";

  return (
    <section className="kg-card" aria-label="Interactive knowledge graph" onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); reset(); } }}>
      <header className="kg-header">
        <h2>Knowledge graph</h2>
        <span className="kg-offline"><span aria-hidden="true" />{layout.fileCount > 0 ? `${layout.fileCount} source files` : `${layout.entityCount} entities`}, rendered locally</span>
      </header>
      <div className="kg-stage">
        <svg
          ref={canvasRef}
          className={`kg-canvas${moving ? " kg-moving" : ""}`}
          viewBox={`${camera.x + (GRAPH_WIDTH - GRAPH_WIDTH / camera.k) / 2} ${camera.y + (GRAPH_HEIGHT - GRAPH_HEIGHT / camera.k) / 2} ${GRAPH_WIDTH / camera.k} ${GRAPH_HEIGHT / camera.k}`}
          role="group" aria-label="Knowledge graph" aria-describedby={descriptionId}
          onPointerDown={onPointerDown} onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd} onPointerCancel={onPointerEnd} onLostPointerCapture={onPointerEnd}
        >
          <desc id={descriptionId}>Groups and relationships extracted from your documents. Select a node with Enter or Space. Scroll to zoom, drag the background to pan, or press Escape to reset.</desc>
          <g className="kg-edges" aria-hidden="true">
            {layout.links.map((link) => {
              const active = highlighted?.has(link.source.id) && highlighted.has(link.target.id);
              return <line key={link.id} x1={link.source.x} y1={link.source.y} x2={link.target.x} y2={link.target.y}
                stroke={link.source.color} strokeWidth={active ? 1.5 : link.kind === "membership" ? 1.1 : 0.8}
                opacity={highlighted ? active ? 0.9 : 0.08 : link.source.group !== link.target.group ? 0.32 : 0.5}
                vectorEffect="non-scaling-stroke"><title>{link.source.label} - {link.relation} - {link.target.label}</title></line>;
            })}
          </g>
          <g className="kg-decorations" aria-hidden="true" pointerEvents="none">
            {[...layout.groups.values()].map((group) => <g key={group.id} transform={`translate(${group.x},${group.y})`} opacity={!highlighted || highlighted.has(group.id) ? 1 : 0.08}>
              <circle className="kg-halo" r={15} fill={group.color} />
              {activeGroup?.id === group.id && <circle className="kg-ripple" r={15} fill="none" stroke={group.color} vectorEffect="non-scaling-stroke" />}
            </g>)}
          </g>
          {layout.nodes.map((node, index) => {
            const active = !highlighted || highlighted.has(node.id);
            const isSelected = selected?.id === node.id;
            const groupIndex = [...layout.groups.keys()].indexOf(node.group);
            const labelSize = (node.kind === "group" ? 18 : node.kind === "entity" ? 10 : 8) / camera.k;
            return (
              <g key={node.id} transform={`translate(${node.x},${node.y})`} className={`kg-node kg-${node.kind}`} data-node-id={node.id} data-kind={node.kind}
                style={{ "--kg-delay": `${Math.min(groupIndex * 100 + index * 5, 950)}ms` } as CSSProperties}
                opacity={active ? 1 : 0.08} role="button" tabIndex={0}
                aria-label={`${node.kind === "group" ? "Group" : node.kind === "file" ? "File" : "Entity"}: ${node.label}`} aria-pressed={isSelected}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => selectNode(node)}
                onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectNode(node); } }}
              >
                <title>{node.label}{node.entityType ? ` (${node.entityType})` : ""}</title>
                <circle className="kg-hit" r={(node.kind === "group" ? 22 : node.kind === "entity" ? 13 : 10) / (camera.k * screenScale)} fill="transparent" />
                <g className="kg-node-enter">
                  <circle className="kg-dot" r={node.radius} fill={node.color} stroke={isSelected ? "#ffffff" : "#0b0d0e"} strokeWidth={isSelected ? 2 : 1} vectorEffect="non-scaling-stroke" />
                </g>
                <text className={`kg-label${node.kind === "file" ? " kg-file-label" : ""}`} y={-node.radius - 5 / camera.k}
                  textAnchor={node.kind === "group" && node.x > GRAPH_WIDTH - 160 ? "end" : node.kind === "group" && node.x < 160 ? "start" : "middle"}
                  fontSize={labelSize} opacity={node.kind === "file" && (!selected || !active) ? 0 : 1}>
                  {shorten(node.label, node.kind === "group" ? 30 : 28)}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="kg-tools" aria-label="Graph zoom controls">
          <button type="button" aria-label="Zoom in" onClick={() => zoomBy(1.25)} disabled={camera.k >= 4}><Plus size={16} /></button>
          <button type="button" aria-label="Zoom out one step" onClick={() => zoomBy(0.8)} disabled={camera.k <= 0.3}><Minus size={16} /></button>
          <button type="button" aria-label="Reset graph view" onClick={reset} disabled={!zoomed}><RotateCcw size={15} /></button>
        </div>
      </div>
      <footer className="kg-footer">
        <p role="status" aria-live="polite">{status}</p>
        {zoomed && <button type="button" className="kg-reset" onClick={reset}>Zoom out</button>}
      </footer>
      <div className="kg-caption">
        <span>{layout.entityCount} entities &middot; {data.edges.length} relationships &middot; {layout.groups.size} groups{layout.truncated ? " (showing a limited overview)" : ""}</span>
        <span>Scroll to zoom &middot; drag to pan &middot; Esc to reset</span>
      </div>
      {selected && <details className="kg-details" key={selected.id}>
        <summary>{selected.kind === "file" ? "Entities in this source file" : selected.kind === "group" ? "Explore this group" : "Entity details and connections"}</summary>
        {selected.description && <p>{selected.description}</p>}
        {selected.entityType && <p className="kg-detail-type">{selected.entityType}</p>}
        <ul>{(selected.kind === "group" ? layout.links.filter((link) => link.kind === "membership" && link.source === selected) : relatedLinks).slice(0, 40).map((link) => {
          const other = link.source === selected ? link.target : link.source;
          return <li key={link.id}><span>{link.relation}</span><button type="button" onClick={() => selectNode(other)}>{other.label}</button></li>;
        })}</ul>
        {relatedLinks.length === 0 && selected.kind !== "group" && <p>No connections in the displayed overview.</p>}
      </details>}
    </section>
  );
}
