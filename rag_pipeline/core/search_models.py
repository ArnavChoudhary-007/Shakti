"""
core/search_models.py
The two Hugging Face models document search needs: the embedder and the
reranker.

- Downloaded once, with byte-level progress, from the setup page.
- After that they always load from the local cache (`local_files_only`), so
  the companion makes no network calls and works offline.
- One download job at a time; its state is polled through /setup/status.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Alternative weight formats a repo may ship; sentence-transformers only needs
# the PyTorch weights, so skipping these keeps the download small.
_IGNORE_PATTERNS = [
    "onnx/*", "openvino/*", "coreml/*", "*.onnx", "*.h5", "*.msgpack", "*.ot",
    "tf_model*", "flax_model*", "rust_model*", "*.tflite", "*.mlmodel",
]
_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")

DOWNLOAD_ERROR = (
    "Couldn't download the search models. Check your internet connection and try again."
)


def repo_id(name: str, kind: str) -> str:
    """Hub repo for a configured model name (same rule sentence-transformers uses)."""
    if "/" in name or Path(name).exists():
        return name
    return f"sentence-transformers/{name}" if kind == "embedding" else f"cross-encoder/{name}"


def configured_models(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """[(kind, model name)] for the embedder and the reranker."""
    return [
        ("embedding", config.get("embedding", {}).get("model_name", "all-MiniLM-L6-v2")),
        ("reranker", config.get("retrieval", {}).get("reranker_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")),
    ]


def cached_path(repo: str) -> Optional[str]:
    """Local snapshot folder if the model is fully downloaded, else None. No network."""
    if Path(repo).exists():
        return repo
    try:
        from huggingface_hub import snapshot_download
        folder = snapshot_download(repo, local_files_only=True)
    except Exception:
        return None
    return folder if any((Path(folder) / f).exists() for f in _WEIGHT_FILES) else None


def load_kwargs(repo: str) -> Dict[str, Any]:
    """Extra kwargs for SentenceTransformer/CrossEncoder: stay offline once cached."""
    return {"local_files_only": True} if cached_path(repo) else {}


# ── Download job ──────────────────────────────────────────────

_job: Dict[str, Any] = {"state": "idle", "downloaded": 0, "total": None, "error": None}
_job_lock = threading.Lock()


def job_state() -> Dict[str, Any]:
    with _job_lock:
        return dict(_job)


def _plan(repo: str) -> Tuple[List[str], int]:
    """Files to fetch and their total size in bytes."""
    from huggingface_hub import HfApi
    from huggingface_hub.utils import filter_repo_objects

    info = HfApi().model_info(repo, files_metadata=True)
    sizes = {s.rfilename: (s.size or 0) for s in info.siblings or []}
    ignore = list(_IGNORE_PATTERNS)
    if "model.safetensors" in sizes:
        ignore.append("pytorch_model.bin")
    files = list(filter_repo_objects(list(sizes), ignore_patterns=ignore))
    return files, sum(sizes[f] for f in files)


def _blob_bytes(repo: str) -> int:
    """Bytes on disk for a repo, including partial (.incomplete) downloads."""
    from huggingface_hub import constants
    from huggingface_hub.file_download import repo_folder_name

    blobs = Path(constants.HF_HUB_CACHE) / repo_folder_name(repo_id=repo, repo_type="model") / "blobs"
    try:
        return sum(p.stat().st_size for p in blobs.iterdir() if p.is_file())
    except OSError:
        return 0


def _set(**fields: Any) -> None:
    with _job_lock:
        _job.update(fields)


def _download(repos: List[str]) -> None:
    from huggingface_hub import snapshot_download

    plans = [(repo, *_plan(repo)) for repo in repos]
    total = sum(size for _, _, size in plans)
    _set(total=total, downloaded=0)

    done_bytes = 0
    for repo, files, size in plans:
        stop = threading.Event()
        baseline = min(_blob_bytes(repo), size)

        def watch(repo: str = repo, size: int = size, start: int = done_bytes) -> None:
            while not stop.wait(0.5):
                _set(downloaded=start + min(_blob_bytes(repo), size))

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            logger.info("Downloading %s (%d files, %.0f MB, %.0f MB already present)",
                        repo, len(files), size / 1e6, baseline / 1e6)
            snapshot_download(repo, allow_patterns=files)
        finally:
            stop.set()
            watcher.join()
        done_bytes += size
        _set(downloaded=done_bytes)


def start_prepare(config: Dict[str, Any], load: Callable[[], None]) -> Dict[str, Any]:
    """
    Download whatever is missing, then call `load` to bring both models into
    memory. Returns the job state immediately; the work runs in a thread.
    """
    with _job_lock:
        if _job["state"] in ("downloading", "loading"):
            return dict(_job)
        _job.update(state="downloading", downloaded=0, total=None, error=None, started_at=time.time())

    def run() -> None:
        try:
            missing = [repo_id(name, kind) for kind, name in configured_models(config)
                       if not cached_path(repo_id(name, kind))]
            if missing:
                try:
                    _download(missing)
                except Exception as e:
                    logger.error("Search model download failed: %s", e)
                    _set(state="error", error=DOWNLOAD_ERROR)
                    return
            _set(state="loading")
            load()
            _set(state="ready")
        except Exception as e:
            logger.exception("Loading search models failed")
            _set(state="error", error=f"The search models downloaded but couldn't be loaded: {e}")

    threading.Thread(target=run, name="search-models", daemon=True).start()
    return job_state()


def status(config: Dict[str, Any]) -> Dict[str, Any]:
    """Downloaded state of each model plus the current job. Filesystem only."""
    models = []
    for kind, name in configured_models(config):
        repo = repo_id(name, kind)
        models.append({"kind": kind, "name": name, "repo": repo, "downloaded": cached_path(repo) is not None})
    return {
        "ready": all(m["downloaded"] for m in models),
        "models": models,
        "job": job_state(),
    }
