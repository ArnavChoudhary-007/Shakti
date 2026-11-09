"""
rag_pipeline/__init__.py — Top-level package + config loader.

Where data lives
----------------
All runtime data (vector store, SQLite, uploads, settings, pairing token)
lives under one *data directory*:

  - ``SHAKTI_DATA_DIR`` env var if set (the installer sets this to
    ``%LOCALAPPDATA%\\Shakti``), otherwise
  - the repo root (development checkouts — keeps existing ./chroma_db etc.).

Relative paths in config.yaml are resolved against the data directory, never
against the process's current working directory, so launching from a
different folder can no longer silently create a second, empty database.

Config lookup order: ``<data_dir>/config.yaml`` → ``rag_pipeline/config.yaml``
→ ``rag_pipeline/config.example.yaml``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

__version__ = "0.2.0"

_PACKAGE_DIR = Path(__file__).parent
_REPO_ROOT = _PACKAGE_DIR.parent
_config_cache: dict[str, Any] | None = None

# (section, key) pairs in config.yaml that hold filesystem paths.
_PATH_KEYS = [
    ("vector_store", "chroma_persist_dir"),
    ("vector_store", "faiss_index_path"),
    ("structured_db", "sqlite_path"),
    ("sync", "sync_state_db"),
    ("logging", "log_file"),
]


def get_data_dir() -> Path:
    """Directory holding all runtime data. Created on first use."""
    env = os.environ.get("SHAKTI_DATA_DIR")
    data_dir = Path(env).expanduser() if env else _REPO_ROOT
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir.resolve()


def _find_config_file() -> Path:
    for candidate in (
        get_data_dir() / "config.yaml",
        _PACKAGE_DIR / "config.yaml",
        _PACKAGE_DIR / "config.example.yaml",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("No config.yaml or config.example.yaml found.")


def _resolve_paths(config: dict[str, Any], base: Path) -> None:
    for section, key in _PATH_KEYS:
        value = (config.get(section) or {}).get(key)
        if isinstance(value, str) and value and not Path(value).is_absolute():
            config[section][key] = str((base / value).resolve())


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """
    Load and cache config.yaml. Call this once at startup.
    Optionally override path for tests.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_file = Path(path) if path else _find_config_file()
    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    _resolve_paths(config, get_data_dir())
    _config_cache = config
    return _config_cache


def get_config() -> dict[str, Any]:
    """Return cached config, loading from default path if needed."""
    if _config_cache is None:
        return load_config()
    return _config_cache
