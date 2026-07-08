"""
core/embedder.py
Batched sentence-transformers embedder.

- Model name always read from config — never hardcoded.
- Model is lazy-loaded on first call (avoids loading at import time).
- Supports batched encoding with configurable batch_size.
- Returns list[list[float]] — plain Python for portability with both
  Chroma and FAISS backends.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Embedder:
    """
    Wraps sentence-transformers SentenceTransformer for batched encoding.

    Usage:
        embedder = Embedder.from_config(config)
        vecs = embedder.encode(["text one", "text two"])
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: str = "cpu",
        batch_size: int = 64,
        normalize_embeddings: bool = True,
    ):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.normalize_embeddings = normalize_embeddings
        self._model = None          # lazy-loaded

    # ── Factory ───────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "Embedder":
        cfg = config.get("embedding", {})
        return cls(
            model_name=cfg.get("model_name", "all-MiniLM-L6-v2"),
            device=cfg.get("device", "cpu"),
            batch_size=int(cfg.get("batch_size", 64)),
        )

    # ── Lazy model load ───────────────────────────────────────

    def _load_model(self) -> None:
        if self._model is not None:
            return
        logger.info("Loading sentence-transformers model %r on %s ...", self.model_name, self.device)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "sentence-transformers not installed.\n"
                "Install with: pip install sentence-transformers"
            )
        self._model = SentenceTransformer(self.model_name, device=self.device)
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info("Model loaded. Embedding dimension: %d", self._dim)

    @property
    def dimension(self) -> int:
        """Return embedding dimension, loading model if needed."""
        self._load_model()
        return self._dim

    # ── Encoding ──────────────────────────────────────────────

    def encode(self, texts: List[str]) -> List[List[float]]:
        """
        Encode a list of strings into dense vectors.
        Returns list[list[float]] with length == len(texts).
        Empty strings are replaced with zero vectors.
        """
        if not texts:
            return []

        self._load_model()

        # Filter out empty texts, track positions
        indices: List[int] = []
        non_empty: List[str] = []
        for i, t in enumerate(texts):
            if t and t.strip():
                indices.append(i)
                non_empty.append(t)

        dim = self._dim
        results: List[List[float]] = [[0.0] * dim for _ in range(len(texts))]

        if not non_empty:
            return results

        # Batch encode
        import numpy as np
        embeddings = self._model.encode(
            non_empty,
            batch_size=self.batch_size,
            show_progress_bar=len(non_empty) > 200,
            normalize_embeddings=self.normalize_embeddings,
            convert_to_numpy=True,
        )

        # Place results back at correct positions
        for pos, idx in enumerate(indices):
            results[idx] = embeddings[pos].tolist()

        return results

    def encode_single(self, text: str) -> List[float]:
        """Convenience wrapper for encoding a single string."""
        return self.encode([text])[0]
