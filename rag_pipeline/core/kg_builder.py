import networkx as nx
import community as community_louvain
import httpx
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

async def build_graph_layout(workspace_id: str, db, host: str = "http://localhost:11434", model: str = "llama3.2:1b", min_edge_weight: int = 1) -> None:
    """
    Offline processing for Knowledge Graph:
    1. Load all raw edges.
    2. Build networkx graph, calculate degree centrality.
    3. Community detection (Louvain).
    4. Label communities using local LLM.
    5. Edge pruning based on weight threshold.
    6. Save computed layout back to DB.
    """
    logger.info(f"Building KG layout for workspace {workspace_id}...")

    # 1. Load data
    with db._connect() as conn:
        edges_rows = conn.execute("SELECT source, target, relation FROM kg_edges WHERE workspace_id = ?", (workspace_id,)).fetchall()
        nodes_rows = conn.execute("SELECT id, label FROM kg_nodes WHERE id IN (SELECT source FROM kg_edges WHERE workspace_id = ? UNION SELECT target FROM kg_edges WHERE workspace_id = ?)", (workspace_id, workspace_id)).fetchall()

    if not nodes_rows or not edges_rows:
        logger.info("No graph data found. Skipping layout build.")
        return

    nodes_dict = {r['id']: r['label'] for r in nodes_rows}
    
    # 2. Build graph and edge weights
    G = nx.Graph()
    for n_id in nodes_dict:
        G.add_node(n_id, label=nodes_dict[n_id])
        
    edge_weights = {} # (source, target) -> count
    raw_edges = []
    
    for r in edges_rows:
        s, t = r['source'], r['target']
        if s == t: continue
        
        # Undirected edge key
        key = tuple(sorted([s, t]))
        edge_weights[key] = edge_weights.get(key, 0) + 1
        raw_edges.append((s, t, r['relation']))
        G.add_edge(s, t)
        
    # 3. Centrality
    centrality = nx.degree_centrality(G)
    
    # 4. Community Detection
    partition = community_louvain.best_partition(G)
    
    # Group nodes by community
    comm_nodes = {}
    for node, comm_id in partition.items():
        comm_nodes.setdefault(comm_id, []).append(node)
        
    # 5. Label Communities with LLM
    communities_out = []
    
    async with httpx.AsyncClient() as client:
        for comm_id, members in comm_nodes.items():
            # Get top 5 members by centrality for context
            sorted_members = sorted(members, key=lambda x: centrality.get(x, 0.0), reverse=True)
            top_members = sorted_members[:5]
            top_labels = [nodes_dict.get(n, n) for n in top_members]
            
            prompt = (
                f"You are an ontology expert. These entities form a tight cluster in a knowledge graph: {', '.join(top_labels)}. "
                "Provide a short 2-4 word label for this cluster that describes their common theme. "
                "Do not add quotes, explanations, or any other text. Output ONLY the label."
            )
            
            try:
                resp = await client.post(
                    f"{host}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                    timeout=30.0
                )
                label = resp.json().get("response", "").strip().strip('"').strip()
                if not label:
                    label = f"Cluster {comm_id}"
            except Exception as e:
                logger.warning(f"Failed to generate label for community {comm_id}: {e}")
                label = f"Cluster {comm_id}"
                
            communities_out.append({"id": comm_id, "label": label})
            
    # Prepare node updates
    nodes_updates = []
    for node, comm_id in partition.items():
        nodes_updates.append({
            "id": node,
            "community": comm_id,
            "centrality": centrality.get(node, 0.0)
        })
        
    # Prepare edge updates (pruning)
    edges_updates = []
    for s, t, rel in raw_edges:
        key = tuple(sorted([s, t]))
        weight = edge_weights.get(key, 0)
        is_pruned = 1 if weight < min_edge_weight else 0
        
        edges_updates.append({
            "source": s,
            "target": t,
            "relation": rel,
            "is_pruned": is_pruned
        })
        
    # 6. Save back to DB
    db.save_graph_layout(workspace_id, nodes_updates, edges_updates, communities_out)
    logger.info(f"KG layout built: {len(nodes_updates)} nodes, {len(edges_updates)} edges, {len(communities_out)} communities.")
