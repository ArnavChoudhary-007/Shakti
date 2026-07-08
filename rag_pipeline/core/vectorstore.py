"""
core/vectorstore.py
Abstract VectorStore interface + concrete Chroma and FAISS implementations.

Design goals:
  - Calling code ONLY uses VectorStore.add() / VectorStore.query().
  - get_vector_store(config) returns the right backend — Chroma or FAISS.
  - Swapping backend = change one line in config.yaml.
  - Every chunk stored with FULL metadata for citation rendering.

Metadata stored per chunk:
    doc_id, source_type, file_name, file_path,
    page_or_timestamp, sender, title, chunk_index, total_chunks
"""
from __future__ import annotations

import logging
import os
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Abstract interface ────────────────────────────────────────

class VectorStore(ABC):
    """
    Minimal interface both backends implement.
    Calling code never imports ChromaVectorStore / FaissVectorStore directly.
    """

    @abstractmethod
    def add(self, chunks: List[Dict[str, Any]]) -> None:
        """
        Add chunks to the store.
        Each chunk dict must have:
            chunk_id: str
            text: str
            embedding: list[float]
            metadata: dict   (source_type, file_name, page_or_timestamp, ...)
        """
        ...

    @abstractmethod
    def query(
        self,
        embedding: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return top_k nearest chunks.
        Each result dict has: chunk_id, text, metadata, score (distance or similarity).
        """
        ...

    @abstractmethod
    def count(self) -> int:
        """Return number of stored chunks."""
        ...

    @abstractmethod
    def delete_by_doc_id(self, doc_id: str) -> None:
        """Remove all chunks belonging to a document (for re-ingestion)."""
        ...

    @abstractmethod
    def delete_by_file_path(self, file_path: str) -> None:
        """Remove all chunks belonging to a specific file path."""
        ...


# ── Chroma implementation ─────────────────────────────────────

class ChromaVectorStore(VectorStore):
    """
    Chroma persistent client.
    All metadata stored alongside embeddings so retrieval is self-contained.
    """

    def __init__(self, persist_dir: str, collection_name: str = "rag_chunks"):
        self.persist_dir = str(Path(persist_dir).resolve())
        self.collection_name = collection_name
        self._client = None
        self._collection = None

    def _get_collection(self):
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise RuntimeError("chromadb not installed. Install with: pip install chromadb")

        Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self.persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "Chroma collection %r at %s (count=%d)",
            self.collection_name, self.persist_dir, self._collection.count()
        )
        return self._collection

    def add(self, chunks: List[Dict[str, Any]]) -> None:
        if not chunks:
            return
        col = self._get_collection()

        ids = [c["chunk_id"] for c in chunks]
        embeddings = [c["embedding"] for c in chunks]
        documents = [c["text"] for c in chunks]
        metadatas = [_sanitise_metadata(c.get("metadata", {})) for c in chunks]

        # Chroma upserts by ID — safe for re-ingestion
        col.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.debug("Upserted %d chunks into Chroma.", len(chunks))

    def query(
        self,
        embedding: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        col = self._get_collection()
        where = _build_chroma_where(filters) if filters else None

        kwargs: Dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": min(top_k, max(col.count(), 1)),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

        output: List[Dict[str, Any]] = []
        for i in range(len(results["ids"][0])):
            output.append({
                "chunk_id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "score": 1.0 - results["distances"][0][i],  # cosine similarity
            })
        return output

    def count(self) -> int:
        return self._get_collection().count()

    def delete_by_doc_id(self, doc_id: str) -> None:
        col = self._get_collection()
        col.delete(where={"doc_id": {"$eq": doc_id}})
        logger.info("Deleted chunks for doc_id=%r from Chroma.", doc_id)

    def delete_by_file_path(self, file_path: str) -> None:
        col = self._get_collection()
        col.delete(where={"file_path": {"$eq": file_path}})
        logger.info("Deleted chunks for file_path=%r from Chroma.", file_path)


# ── FAISS implementation ──────────────────────────────────────

class FaissVectorStore(VectorStore):
    """
    FAISS flat index (exact search, cosine via normalised L2).
    Metadata stored in a companion pickle file since FAISS is vector-only.

    Index is persisted to disk after every add() call.
    """

    def __init__(self, index_path: str, dim: int = 384):
        self.index_path = str(Path(index_path).resolve())
        self.meta_path = self.index_path.replace(".faiss", "_meta.pkl")
        self.dim = dim
        self._index = None
        self._meta: List[Dict[str, Any]] = []   # parallel: metadata + text
        self._ids: List[str] = []               # parallel: chunk_ids
        self._vecs: List[List[float]] = []      # parallel: raw vectors (for upsert rebuild)
        self._load()

    def _load(self) -> None:
        try:
            import faiss
        except ImportError:
            raise RuntimeError("faiss-cpu not installed. Install with: pip install faiss-cpu")

        if Path(self.index_path).exists():
            import faiss
            self._index = faiss.read_index(self.index_path)
            with open(self.meta_path, "rb") as f:
                saved = pickle.load(f)
                self._meta = saved.get("meta", [])
                self._ids = saved.get("ids", [])
                self._vecs = saved.get("vecs", [])
            logger.info("FAISS index loaded from %s (%d vectors)", self.index_path, self._index.ntotal)
        else:
            import faiss
            self._index = faiss.IndexFlatIP(self.dim)
            self._vecs = []
            logger.info("New FAISS IndexFlatIP created (dim=%d)", self.dim)

    def _save(self) -> None:
        import faiss
        Path(self.index_path).parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, self.index_path)
        with open(self.meta_path, "wb") as f:
            pickle.dump({"meta": self._meta, "ids": self._ids, "vecs": self._vecs}, f)

    def add(self, chunks: List[Dict[str, Any]]) -> None:
        if not chunks:
            return
        import numpy as np, faiss

        # Remove existing chunks with same IDs (upsert semantics)
        new_ids = {c["chunk_id"] for c in chunks}
        keep_mask = [i for i, cid in enumerate(self._ids) if cid not in new_ids]

        if len(keep_mask) < len(self._ids):
            # Rebuild from stored raw vectors
            kept_vecs = [self._vecs[i] for i in keep_mask]
            self._index = faiss.IndexFlatIP(self.dim)
            if kept_vecs:
                arr = np.array(kept_vecs, dtype="float32")
                faiss.normalize_L2(arr)
                self._index.add(arr)
            self._meta = [self._meta[i] for i in keep_mask]
            self._ids = [self._ids[i] for i in keep_mask]
            self._vecs = kept_vecs

        raw_vecs = [c["embedding"] for c in chunks]
        vecs = np.array(raw_vecs, dtype="float32")
        faiss.normalize_L2(vecs)
        self._index.add(vecs)

        for i, c in enumerate(chunks):
            self._ids.append(c["chunk_id"])
            self._meta.append({
                "text": c["text"],
                "metadata": _sanitise_metadata(c.get("metadata", {})),
            })
            self._vecs.append(raw_vecs[i])

        self._save()
        logger.debug("Added %d vectors to FAISS (total=%d).", len(chunks), self._index.ntotal)

    def query(
        self,
        embedding: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        import numpy as np, faiss

        if self._index.ntotal == 0:
            return []

        vec = np.array([embedding], dtype="float32")
        faiss.normalize_L2(vec)
        k = min(top_k * 3, self._index.ntotal)  # over-fetch to allow post-filtering
        scores, indices = self._index.search(vec, k)

        output: List[Dict[str, Any]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            entry = self._meta[idx]
            meta = entry["metadata"]

            # Apply post-query filters (FAISS has no built-in metadata filtering)
            if filters and not _match_filters(meta, filters):
                continue

            output.append({
                "chunk_id": self._ids[idx],
                "text": entry["text"],
                "metadata": meta,
                "score": float(score),
            })
            if len(output) >= top_k:
                break

        return output

    def count(self) -> int:
        return self._index.ntotal if self._index else 0

    def delete_by_doc_id(self, doc_id: str) -> None:
        self._delete_by_condition(lambda m: m.get("metadata", {}).get("doc_id") == doc_id)
        logger.info("Deleted chunks for doc_id=%r from FAISS.", doc_id)

    def delete_by_file_path(self, file_path: str) -> None:
        self._delete_by_condition(lambda m: m.get("metadata", {}).get("file_path") == file_path)
        logger.info("Deleted chunks for file_path=%r from FAISS.", file_path)

    def _delete_by_condition(self, condition_fn) -> None:
        keep_mask = [i for i, m in enumerate(self._meta) if not condition_fn(m)]
        if len(keep_mask) == len(self._ids):
            return  # nothing to delete
        import numpy as np, faiss
        kept_vecs = [self._vecs[i] for i in keep_mask]
        self._index = faiss.IndexFlatIP(self.dim)
        if kept_vecs:
            arr = np.array(kept_vecs, dtype="float32")
            faiss.normalize_L2(arr)
            self._index.add(arr)
        self._meta = [self._meta[i] for i in keep_mask]
        self._ids = [self._ids[i] for i in keep_mask]
        self._vecs = kept_vecs
        self._save()


# ── Helpers ───────────────────────────────────────────────────

def _sanitise_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Chroma requires metadata values to be str/int/float/bool.
    Coerce lists and None to strings.
    """
    clean: Dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            clean[k] = ""
        elif isinstance(v, (str, int, float, bool)):
            clean[k] = v
        elif isinstance(v, list):
            clean[k] = ", ".join(str(x) for x in v)
        else:
            clean[k] = str(v)
    return clean


def _build_chroma_where(filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert {key: value} filters to Chroma where clause."""
    if not filters:
        return None
    if len(filters) == 1:
        k, v = next(iter(filters.items()))
        return {k: {"$eq": str(v)}}
    return {"$and": [{k: {"$eq": str(v)}} for k, v in filters.items()]}


def _match_filters(meta: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    """Simple exact-match filter for FAISS post-query filtering."""
    for k, v in filters.items():
        if str(meta.get(k, "")) != str(v):
            return False
    return True


# ── Factory ───────────────────────────────────────────────────

def get_vector_store(config: Dict[str, Any], embedder_dim: int = 384) -> VectorStore:
    """
    Factory function — returns Chroma or FAISS instance from config.
    Calling code imports only this function, never the concrete classes.
    """
    vs_cfg = config.get("vector_store", {})
    backend = vs_cfg.get("backend", "chroma").lower()

    if backend == "chroma":
        persist_dir = vs_cfg.get("chroma_persist_dir", "./chroma_db")
        collection = vs_cfg.get("collection_name", "rag_chunks")
        return ChromaVectorStore(persist_dir=persist_dir, collection_name=collection)

    elif backend == "faiss":
        index_path = vs_cfg.get("faiss_index_path", "./faiss_index/index.faiss")
        return FaissVectorStore(index_path=index_path, dim=embedder_dim)

    else:
        raise ValueError(
            f"Unknown vector_store.backend {backend!r}. "
            "Valid options: 'chroma', 'faiss'"
        )
