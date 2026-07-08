"""
core/retriever.py
Hybrid retrieval: dense vector search + optional BM25 re-ranking.

Steps:
  1. Embed query with Embedder.
  2. Query VectorStore for top_k * 3 candidates.
  3. If BM25 enabled: re-rank using BM25 scores on the candidate set.
  4. Return top_k results with full metadata (for citation rendering).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Retriever:
    """
    Retrieves the most relevant chunks for a query.

    Usage:
        retriever = Retriever(vector_store, embedder, config)
        chunks = retriever.retrieve("What is Acme Corp's total invoice amount?", top_k=5)
    """

    def __init__(
        self,
        vector_store: Any,      # VectorStore instance
        embedder: Any,          # Embedder instance
        config: Optional[Dict[str, Any]] = None,
        use_bm25: bool = True,
    ):
        self.vector_store = vector_store
        self.embedder = embedder
        self.config = config or {}
        self.use_bm25 = use_bm25
        self._bm25_corpus: List[str] = []
        self._bm25_meta: List[Dict[str, Any]] = []

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        use_bm25_override: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve top_k relevant chunks for query.
        Returns list of chunk dicts with: chunk_id, text, metadata, score.
        """
        # Step 1: embed the query
        query_vec = self.embedder.encode_single(query)

        # Step 2: vector search (over-fetch for re-ranking)
        fetch_k = top_k * 4 if self.use_bm25 else top_k
        candidates = self.vector_store.query(
            embedding=query_vec,
            top_k=fetch_k,
            filters=filters,
        )

        if not candidates:
            return []

        # Step 3: BM25 re-rank
        do_bm25 = use_bm25_override if use_bm25_override is not None else self.use_bm25
        if do_bm25 and len(candidates) > 1:
            candidates = self._bm25_rerank(query, candidates, top_k)
        else:
            candidates = candidates[:top_k]

        return candidates

    def _bm25_rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        Re-rank candidates using BM25 scores, then combine with vector score.
        Final score = 0.6 * vector_score + 0.4 * normalised_bm25_score
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning("rank-bm25 not installed — skipping BM25 re-rank.")
            return candidates[:top_k]

        # Tokenise (simple whitespace + lowercase)
        tokenised_corpus = [c["text"].lower().split() for c in candidates]
        query_tokens = query.lower().split()

        bm25 = BM25Okapi(tokenised_corpus)
        bm25_scores = bm25.get_scores(query_tokens)

        # Normalise BM25 scores to [0, 1]
        max_bm25 = max(bm25_scores) if max(bm25_scores) > 0 else 1.0
        norm_bm25 = [s / max_bm25 for s in bm25_scores]

        # Combine scores
        for i, candidate in enumerate(candidates):
            vec_score = candidate.get("score", 0.0)
            combined = 0.6 * vec_score + 0.4 * norm_bm25[i]
            candidate["bm25_score"] = float(bm25_scores[i])
            candidate["combined_score"] = combined

        # Sort by combined score descending
        candidates.sort(key=lambda c: c.get("combined_score", c.get("score", 0)), reverse=True)
        return candidates[:top_k]
