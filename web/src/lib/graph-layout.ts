import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";
import type { GraphData } from "./api";

export const GRAPH_WIDTH = 900;
export const GRAPH_HEIGHT = 620;
export const GRAPH_COLORS = ["#ee8fb0", "#e8a07e", "#4fc3ae", "#b79cf2", "#e3c35a", "#7fb2f0"];
const MAX_ENTITIES = 300;
const MAX_FILES = 100;

export type GraphNode = {
  id: string;
  label: string;
  kind: "group" | "entity" | "file";
  group: string;
  color: string;
  x: number;
  y: number;
  radius: number;
  entityId?: string;
  description?: string | null;
  entityType?: string | null;
};

export type GraphLink = {
  id: string;
  source: GraphNode;
  target: GraphNode;
  kind: "relationship" | "membership" | "source";
  relation: string;
};

export type GraphLayout = {
  nodes: GraphNode[];
  links: GraphLink[];
  groups: Map<string, GraphNode>;
  entityCount: number;
  fileCount: number;
  truncated: boolean;
};

export function createGraphLayout(data: Pick<GraphData, "nodes" | "edges" | "communities">): GraphLayout {
  const entities = [...data.nodes].sort((first, second) => (second.centrality ?? 0) - (first.centrality ?? 0)).slice(0, MAX_ENTITIES);
  const groupKeys = [...new Set(entities.map((node) => String(node.community ?? "ungrouped")))].sort();
  const groupLabels = new Map(data.communities.map((group) => [String(group.id), group.label]));
  const groups = new Map<string, GraphNode>();
  for (const [index, key] of groupKeys.entries()) {
    const angle = index * Math.PI * 2 / groupKeys.length - Math.PI / 2;
    groups.set(key, {
      id: `group:${key}`, label: groupLabels.get(key) || (key === "ungrouped" ? "Ungrouped entities" : `Group ${key}`),
      kind: "group", group: key, color: GRAPH_COLORS[index % GRAPH_COLORS.length], radius: 7.5,
      x: GRAPH_WIDTH / 2 + (groupKeys.length > 1 ? Math.cos(angle) * 240 : 0),
      y: GRAPH_HEIGHT / 2 + (groupKeys.length > 1 ? Math.sin(angle) * 175 : 0),
    });
  }
  const byEntity = new Map<string, GraphNode>();
  const groupSizes = new Map<string, number>();
  for (const entity of entities) {
    const group = groups.get(String(entity.community ?? "ungrouped"))!;
    const index = groupSizes.get(group.id) ?? 0;
    groupSizes.set(group.id, index + 1);
    const angle = index * 2.399963;
    byEntity.set(entity.id, {
      id: `entity:${entity.id}`, entityId: entity.id, label: entity.label, description: entity.description,
      entityType: entity.type, kind: "entity", group: group.group, color: group.color, radius: 4.6,
      x: group.x + Math.cos(angle) * (35 + Math.sqrt(index) * 10),
      y: group.y + Math.sin(angle) * (35 + Math.sqrt(index) * 10),
    });
  }
  const links: GraphLink[] = [];
  const fileEntities = new Map<string, Set<GraphNode>>();
  for (const [index, edge] of data.edges.entries()) {
    const source = byEntity.get(edge.source);
    const target = byEntity.get(edge.target);
    if (!source || !target) continue;
    if (source !== target) links.push({ id: `relationship:${index}`, source, target, relation: edge.relation, kind: "relationship" });
    if (edge.source_doc) {
      const members = fileEntities.get(edge.source_doc) ?? new Set<GraphNode>();
      members.add(source);
      members.add(target);
      fileEntities.set(edge.source_doc, members);
    }
  }
  for (const entity of byEntity.values()) {
    links.push({ id: `membership:${entity.id}`, source: groups.get(entity.group)!, target: entity, relation: "Contains entity", kind: "membership" });
  }
  const files: GraphNode[] = [];
  for (const [name, members] of [...fileEntities].slice(0, MAX_FILES)) {
    const parent = [...members][0];
    const angle = files.length * 2.399963;
    const file: GraphNode = {
      id: `file:${name}`, label: name, kind: "file", group: parent.group, color: parent.color, radius: 2.9,
      x: parent.x + Math.cos(angle) * 28, y: parent.y + Math.sin(angle) * 28,
    };
    files.push(file);
    for (const entity of members) links.push({ id: `source:${name}:${entity.id}`, source: entity, target: file, relation: "Source document", kind: "source" });
  }
  const nodes = [...files, ...byEntity.values(), ...groups.values()];
  if (nodes.length > 0) {
    const simulation = forceSimulation(nodes)
      .force("link", forceLink<GraphNode, GraphLink>(links).distance((link) => link.kind === "source" ? 28 : link.kind === "membership" ? 65 : 85).strength((link) => link.kind === "membership" ? 0.65 : 0.15))
      .force("charge", forceManyBody().strength((node) => (node as GraphNode).kind === "file" ? -12 : -55))
      .force("collide", forceCollide<GraphNode>().radius((node) => node.kind === "group" ? 36 : node.kind === "entity" ? 20 : 10))
      .force("x", forceX<GraphNode>((node) => groups.get(node.group)!.x).strength(0.045))
      .force("y", forceY<GraphNode>((node) => groups.get(node.group)!.y).strength(0.045))
      .stop();
    for (let tick = 0; tick < 240; tick++) simulation.tick();
    const left = Math.min(...nodes.map((node) => node.x));
    const top = Math.min(...nodes.map((node) => node.y));
    const width = Math.max(...nodes.map((node) => node.x)) - left;
    const height = Math.max(...nodes.map((node) => node.y)) - top;
    const scale = Math.min((GRAPH_WIDTH - 180) / Math.max(width, 1), (GRAPH_HEIGHT - 100) / Math.max(height, 1), 2);
    for (const node of nodes) {
      node.x = (node.x - left - width / 2) * scale + GRAPH_WIDTH / 2;
      node.y = (node.y - top - height / 2) * scale + GRAPH_HEIGHT / 2;
    }
  }
  return { nodes, links, groups, entityCount: entities.length, fileCount: files.length, truncated: data.nodes.length > MAX_ENTITIES || fileEntities.size > MAX_FILES };
}

export function highlightedNodes(layout: GraphLayout, selected: GraphNode): Set<string> {
  const highlighted = new Set([selected.id, layout.groups.get(selected.group)!.id]);
  if (selected.kind === "group") {
    for (const node of layout.nodes) if (node.group === selected.group) highlighted.add(node.id);
    for (const link of layout.links) {
      if (link.kind !== "relationship") continue;
      if (link.source.group === selected.group) highlighted.add(link.target.id);
      if (link.target.group === selected.group) highlighted.add(link.source.id);
    }
  } else {
    for (const link of layout.links) {
      if (link.source === selected) highlighted.add(link.target.id);
      if (link.target === selected) highlighted.add(link.source.id);
    }
  }
  return highlighted;
}
