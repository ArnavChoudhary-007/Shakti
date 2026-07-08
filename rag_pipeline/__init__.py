"""
rag_pipeline/__init__.py — Top-level package + config loader.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_config_cache: dict[str, Any] | None = None


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """
    Load and cache config.yaml. Call this once at startup.
    Optionally override path for tests.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_file = Path(path) if path else _CONFIG_PATH
    with open(config_file, "r", encoding="utf-8") as f:
        _config_cache = yaml.safe_load(f)
    return _config_cache


def get_config() -> dict[str, Any]:
    """Return cached config, loading from default path if needed."""
    if _config_cache is None:
        return load_config()
    return _config_cache
