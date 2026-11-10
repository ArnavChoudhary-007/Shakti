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
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None

from rag_pipeline.core.search_models import load_kwargs, repo_id

logger = logging.getLogger(__name__)

# Loaded once and shared. A failed load is remembered for a while instead of
# being retried (and re-timing-out) on every question.
_reranker = None
_reranker_error: Optional[str] = None
_reranker_failed_at = 0.0
_reranker_lock = threading.Lock()
RERANKER_RETRY_SECONDS = 300.0


def get_reranker(model_name: str):
    global _reranker, _reranker_error, _reranker_failed_at
    if _reranker is not None:
        return _reranker
    if CrossEncoder is None:
        if _reranker_error is None:
            _reranker_error = "sentence-transformers is not installed"
            logger.warning("sentence_transformers not installed. Reranking disabled.")
        return None
    with _reranker_lock:
        if _reranker is not None:
            return _reranker
        if _reranker_error and time.monotonic() - _reranker_failed_at < RERANKER_RETRY_SECONDS:
            return None
        logger.info("Loading cross-encoder reranker: %s...", model_name)
        try:
            _reranker = CrossEncoder(model_name, **load_kwargs(repo_id(model_name, "reranker")))
            _reranker_error = None
            logger.info("Reranker loaded successfully.")
        except Exception as e:
            _reranker_error = str(e)
            _reranker_failed_at = time.monotonic()
            logger.error("Failed to load reranker %s: %s (retrying in %.0fs at the earliest)",
                         model_name, e, RERANKER_RETRY_SECONDS)
            return None
    return _reranker


def reset_reranker_failure() -> None:
    """Allow an immediate retry, e.g. right after the model has been downloaded."""
    global _reranker_error, _reranker_failed_at
    with _reranker_lock:
        _reranker_error = None
        _reranker_failed_at = 0.0


def reranker_state() -> Dict[str, Any]:
    return {"loaded": _reranker is not None, "error": _reranker_error}


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
        top_k: Optional[int] = None,
        candidate_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        use_bm25_override: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks for query using a two-stage process:
        1. Vector search + BM25 (candidate_k)
        2. Cross-Encoder reranking (top_k)
        """
        retrieval_cfg = self.config.get("retrieval", {})
        final_k = top_k if top_k is not None else retrieval_cfg.get("final_k", 5)
        cand_k = candidate_k if candidate_k is not None else retrieval_cfg.get("candidate_k", final_k * 4)

        # Step 1: embed the query
        query_vec = self.embedder.encode_single(query)

        # Step 2: vector search (over-fetch for re-ranking)
        fetch_k = cand_k * 4 if self.use_bm25 else cand_k
        candidates = self.vector_store.query(
            embedding=query_vec,
            top_k=fetch_k,
            filters=filters,
        )

        if not candidates:
            return []

        # Step 3: BM25 re-rank (narrows down to cand_k)
        do_bm25 = use_bm25_override if use_bm25_override is not None else self.use_bm25
        if do_bm25 and len(candidates) > 1:
            candidates = self._bm25_rerank(query, candidates, cand_k)
        else:
            candidates = candidates[:cand_k]

        # Step 4: Cross-Encoder Reranking
        reranker_model = retrieval_cfg.get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        reranker = get_reranker(reranker_model)
        
        if reranker is not None:
            pairs = [(query, r.get("text", "")) for r in candidates]
            try:
                scores = reranker.predict(pairs)
                for r, score in zip(candidates, scores):
                    r["rerank_score"] = float(score)
                candidates = sorted(candidates, key=lambda r: r.get("rerank_score", 0.0), reverse=True)
                
                # Debug logging
                logger.debug("--- Reranking Scores ---")
                for i, r in enumerate(candidates[:final_k]):
                    logger.debug(f"[{i+1}] Score: {r.get('rerank_score'):.3f} | Chunk: {r.get('text', '')[:60]}...")
            except Exception as e:
                logger.error(f"Reranking failed: {e}. Falling back to BM25/Vector scores.")
        
        return candidates[:final_k]

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
