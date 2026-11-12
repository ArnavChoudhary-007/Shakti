"""
core/kg_builder.py
Builds the knowledge graph from the extraction queue filled during ingestion.

1. Extract entities/relationships for every queued file. A file's queued
   pieces (pages, chat windows, sheets...) are packed into a few
   context-sized prompts, so a 500-message chat costs a handful of model
   calls instead of 500.
2. Centrality + Louvain communities — CPU work, run in a worker thread.
3. Label the larger communities with the model; small ones are named after
   their most central entity.
4. Save the layout.

Ollama runs requests one at a time on most PCs, so the calls stay sequential;
progress is reported through `on_progress(stage, done, total)`.
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import community as community_louvain
import httpx
import networkx as nx

from rag_pipeline.core.generator import GenerationError, extract_kg_relationships

logger = logging.getLogger(__name__)
InferenceRunner = Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]


async def _run_inference(operation: Callable[[], Awaitable[Any]], runner: Optional[InferenceRunner]):
    return await runner(operation) if runner else await operation()

# ~2,000 tokens of text per extraction call: fits comfortably in num_ctx 8192
# with the instructions and the JSON answer.
PIECE_CHARS = 8000
MAX_CALLS_PER_FILE = 12
MIN_LABELLED_COMMUNITY = 3     # smaller communities are named after their top entity
MAX_LABELLED_COMMUNITIES = 15  # bounds the number of labelling calls

Progress = Callable[[str, int, int], None]


def _noop(stage: str, done: int, total: int) -> None:
    pass


def pack_pieces(texts: List[str], piece_chars: int = PIECE_CHARS,
                max_pieces: int = MAX_CALLS_PER_FILE) -> List[str]:
    """
    Concatenate a file's texts into pieces of at most `piece_chars`; long
    texts are split. If that gives more than `max_pieces`, keep an evenly
    spread sample so the graph still covers the start, middle and end.
    """
    pieces: List[str] = []
    buf = ""
    for text in texts:
        text = text.strip()
        while text:
            room = piece_chars - len(buf) - (2 if buf else 0)
            if room <= 0:
                pieces.append(buf)
                buf = ""
                continue
            part, text = text[:room], text[room:]
            buf = f"{buf}\n\n{part}" if buf else part
    if buf:
        pieces.append(buf)
    if len(pieces) > max_pieces:
        step = (len(pieces) - 1) / (max_pieces - 1) if max_pieces > 1 else 0
        pieces = [pieces[round(i * step)] for i in range(max_pieces)]
    return pieces


async def _extract_pending_kg(
    workspace_id: str, db, host: str, model: str, timeout: float = 300.0,
    *, keep_alive: Optional[str] = None, num_ctx: Optional[int] = None,
    on_progress: Progress = _noop,
    run_inference: Optional[InferenceRunner] = None,
) -> None:
    """
    Extract every queued file. A file's queue rows are deleted only when all of
    its pieces were extracted and saved, so a failure (Ollama down, a slow
    model, invalid output) keeps it queued for the next build.
    """
    pending = await asyncio.to_thread(db.get_pending_kg_docs, workspace_id)
    if not pending:
        return

    files: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for row in pending:
        files.setdefault(row["source_doc"], []).append(row)
    plans = [(name, rows, pack_pieces([r["text"] for r in rows])) for name, rows in files.items()]
    total = sum(len(pieces) for _, _, pieces in plans)
    logger.info("Extracting KG for %d file(s): %d queued pieces -> %d model calls (workspace %s)",
                len(plans), len(pending), total, workspace_id)

    finished = 0          # calls accounted for, including skipped ones of failed files
    failed_files = 0
    on_progress("extracting", 0, total)
    for name, rows, pieces in plans:
        source_type = rows[0]["source_type"] or "doc"
        edges: List[Dict[str, Any]] = []
        try:
            for i, piece in enumerate(pieces, start=1):
                edges.extend(await _run_inference(lambda: extract_kg_relationships(
                    piece, model=model, host=host, source_type=source_type, timeout=timeout,
                    keep_alive=keep_alive, num_ctx=num_ctx,
                ), run_inference))
                on_progress("extracting", finished + i, total)
            if edges:
                await asyncio.to_thread(lambda: db.upsert_kg_data(
                    {"edges": edges}, source_doc=name, workspace_id=workspace_id))
            await asyncio.to_thread(db.delete_kg_pending_docs, [r["id"] for r in rows])
        except Exception as e:
            failed_files += 1
            logger.warning("KG extraction failed for %s: %s", name, e)
        finished += len(pieces)
        on_progress("extracting", finished, total)

    logger.info("KG extraction done: %d/%d file(s) succeeded.", len(plans) - failed_files, len(plans))
    if failed_files:
        raise GenerationError(
            f"Graph extraction failed for {failed_files} document(s). Their jobs are still queued. "
            "Check Ollama and the selected model, then build the graph again to retry."
        )


def _compute_layout(edges_rows: List[Any], nodes_rows: List[Any], min_edge_weight: int) -> Dict[str, Any]:
    """Centrality, communities and pruning. Pure CPU work — runs in a thread."""
    labels = {r["id"]: r["label"] for r in nodes_rows}
    graph = nx.Graph()
    graph.add_nodes_from(labels)

    weights: Dict[Tuple[str, str], int] = {}
    raw_edges = []
    for r in edges_rows:
        s, t = r["source"], r["target"]
        if s == t:
            continue
        key = tuple(sorted((s, t)))
        weights[key] = weights.get(key, 0) + 1
        raw_edges.append((s, t, r["relation"]))
        graph.add_edge(s, t)

    centrality = nx.degree_centrality(graph)
    partition = community_louvain.best_partition(graph, random_state=42)  # stable between builds

    members: Dict[int, List[str]] = {}
    for node, comm_id in partition.items():
        members.setdefault(comm_id, []).append(node)
    for nodes in members.values():
        nodes.sort(key=lambda n: centrality.get(n, 0.0), reverse=True)

    return {
        "labels": labels,
        "members": members,
        "nodes": [{"id": n, "community": c, "centrality": centrality.get(n, 0.0)} for n, c in partition.items()],
        "edges": [
            {"source": s, "target": t, "relation": rel,
             "is_pruned": 1 if weights[tuple(sorted((s, t)))] < min_edge_weight else 0}
            for s, t, rel in raw_edges
        ],
    }


async def _label_communities(layout: Dict[str, Any], host: str, model: str, timeout: float,
                             keep_alive: Optional[str], num_ctx: Optional[int],
                             on_progress: Progress, run_inference: Optional[InferenceRunner] = None) -> List[Dict[str, Any]]:
    labels, members = layout["labels"], layout["members"]
    by_size = sorted(members, key=lambda c: len(members[c]), reverse=True)
    to_label = [c for c in by_size if len(members[c]) >= MIN_LABELLED_COMMUNITY][:MAX_LABELLED_COMMUNITIES]

    names = {c: labels.get(members[c][0], members[c][0]) for c in members}   # fallback: top entity
    options: Dict[str, Any] = {"temperature": 0.0}
    if num_ctx:
        options["num_ctx"] = num_ctx
    on_progress("labelling", 0, len(to_label))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        for i, comm_id in enumerate(to_label, start=1):
            top = [labels.get(n, n) for n in members[comm_id][:5]]
            prompt = (
                f"You are an ontology expert. These entities form a tight cluster in a knowledge graph: {', '.join(top)}. "
                "Provide a short 2-4 word label for this cluster that describes their common theme. "
                "Do not add quotes, explanations, or any other text. Output ONLY the label."
            )
            body: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "options": options}
            if keep_alive:
                body["keep_alive"] = keep_alive
            try:
                resp = await _run_inference(lambda: client.post(f"{host}/api/generate", json=body), run_inference)
                resp.raise_for_status()
                label = resp.json().get("response", "").strip().strip('"').strip()
                if label:
                    names[comm_id] = label[:60]
            except Exception as e:
                logger.warning("Failed to label community %s: %s", comm_id, e)
            on_progress("labelling", i, len(to_label))
    return [{"id": c, "label": names[c]} for c in members]


async def build_graph_layout(
    workspace_id: str, db, host: str = "http://localhost:11434", model: str = "llama3.2:1b",
    min_edge_weight: int = 1, timeout: float = 300.0, *,
    keep_alive: Optional[str] = None, num_ctx: Optional[int] = None,
    on_progress: Progress = _noop,
    run_inference: Optional[InferenceRunner] = None,
) -> None:
    logger.info("Building KG layout for workspace %s...", workspace_id)
    await _extract_pending_kg(workspace_id, db, host, model, timeout=timeout,
                              keep_alive=keep_alive, num_ctx=num_ctx, on_progress=on_progress, run_inference=run_inference)

    def load():
        with db._connect() as conn:
            edges = conn.execute(
                "SELECT source, target, relation FROM kg_edges WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
            nodes = conn.execute(
                "SELECT id, label FROM kg_nodes WHERE workspace_id = ? AND id IN "
                "(SELECT source FROM kg_edges WHERE workspace_id = ? "
                " UNION SELECT target FROM kg_edges WHERE workspace_id = ?)",
                (workspace_id, workspace_id, workspace_id),
            ).fetchall()
        return edges, nodes

    edges_rows, nodes_rows = await asyncio.to_thread(load)
    if not nodes_rows or not edges_rows:
        logger.info("No graph data found. Skipping layout build.")
        return

    on_progress("clustering", 0, 1)
    layout = await asyncio.to_thread(_compute_layout, edges_rows, nodes_rows, min_edge_weight)
    communities = await _label_communities(layout, host, model, timeout, keep_alive, num_ctx, on_progress, run_inference)
    await asyncio.to_thread(db.save_graph_layout, workspace_id, layout["nodes"], layout["edges"], communities)
    logger.info("KG layout built: %d nodes, %d edges, %d communities.",
                len(layout["nodes"]), len(layout["edges"]), len(communities))
