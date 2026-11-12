import type { GraphData } from "../lib/api";

export const graphFixture: GraphData = {
  nodes: [
    { id: "alice", label: "Alice", type: "Person", description: "Research lead", community: 3, centrality: 1 },
    { id: "research", label: "Research", type: "Concept", description: null, community: 3, centrality: 0.8 },
    { id: "budget", label: "Budget", type: "Concept", description: null, community: 7, centrality: 0.5 },
    { id: "audit", label: "Audit", type: "Process", description: null, community: 7, centrality: 0.2 },
  ],
  edges: [
    { source: "alice", target: "research", relation: "leads", description: null, source_doc: "research.pdf" },
    { source: "budget", target: "audit", relation: "reviewed during", description: null, source_doc: "budget.xlsx" },
    { source: "research", target: "budget", relation: "funded by", description: null, source_doc: "research.pdf" },
  ],
  communities: [{ id: 3, label: "Research team" }, { id: 7, label: "Finance" }],
  pending_docs: 0,
  building: false,
};
