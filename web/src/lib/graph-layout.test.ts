import { describe, expect, it } from "vitest";
import { graphFixture } from "../test/graph-fixture";
import { createGraphLayout, GRAPH_HEIGHT, GRAPH_WIDTH, highlightedNodes } from "./graph-layout";


describe("graph layout", () => {
  it("builds groups, entities and source files only from backend data without mutating it", () => {
    const before = JSON.stringify(graphFixture);
    const layout = createGraphLayout(graphFixture);
    expect(layout.entityCount).toBe(4);
    expect(layout.fileCount).toBe(2);
    expect([...layout.groups.values()].map((group) => group.label)).toEqual(["Research team", "Finance"]);
    expect(layout.nodes.filter((node) => node.kind === "file").map((node) => node.label)).toEqual(["research.pdf", "budget.xlsx"]);
    expect(layout.links.filter((link) => link.kind === "relationship").map((link) => link.relation)).toEqual(["leads", "reviewed during", "funded by"]);
    expect(JSON.stringify(graphFixture)).toBe(before);
    for (const node of layout.nodes) {
      expect(node.x).toBeGreaterThanOrEqual(0);
      expect(node.x).toBeLessThanOrEqual(GRAPH_WIDTH);
      expect(node.y).toBeGreaterThanOrEqual(0);
      expect(node.y).toBeLessThanOrEqual(GRAPH_HEIGHT);
    }
    expect(createGraphLayout(graphFixture).nodes).toEqual(layout.nodes);
  });

  it("highlights a group's members and cross-group relationships", () => {
    const layout = createGraphLayout(graphFixture);
    const highlighted = highlightedNodes(layout, layout.groups.get("3")!);
    expect(highlighted.has("entity:alice")).toBe(true);
    expect(highlighted.has("file:research.pdf")).toBe(true);
    expect(highlighted.has("entity:budget")).toBe(true);
    expect(highlighted.has("entity:audit")).toBe(false);
  });

  it("highlights a source file and the entities actually extracted from it", () => {
    const layout = createGraphLayout(graphFixture);
    const selected = layout.nodes.find((node) => node.id === "file:research.pdf")!;
    const highlighted = highlightedNodes(layout, selected);
    expect(highlighted.has("entity:alice")).toBe(true);
    expect(highlighted.has("entity:research")).toBe(true);
    expect(highlighted.has("entity:budget")).toBe(true);
    expect(highlighted.has("entity:audit")).toBe(false);
  });

  it("handles empty graphs, missing communities and dangling edges without demo data", () => {
    expect(createGraphLayout({ nodes: [], edges: [], communities: [] }).nodes).toEqual([]);
    const layout = createGraphLayout({ nodes: [{ ...graphFixture.nodes[0], community: null }], communities: [], edges: [graphFixture.edges[0]] });
    expect(layout.fileCount).toBe(0);
    expect(layout.nodes.map((node) => node.label)).toEqual(["Alice", "Ungrouped entities"]);
    expect(layout.links.filter((link) => link.kind === "relationship")).toEqual([]);
  });

  it("bounds large graphs and marks the overview as limited", () => {
    const layout = createGraphLayout({
      nodes: Array.from({ length: 305 }, (_, index) => ({ ...graphFixture.nodes[0], id: `node:${index}`, centrality: index })),
      edges: Array.from({ length: 105 }, (_, index) => ({ ...graphFixture.edges[0], source: "node:304", target: "node:303", source_doc: `file:${index}.pdf` })),
      communities: graphFixture.communities,
    });
    expect(layout.entityCount).toBe(300);
    expect(layout.fileCount).toBe(100);
    expect(layout.truncated).toBe(true);
    expect(layout.nodes.some((node) => node.entityId === "node:0")).toBe(false);
  });
});
