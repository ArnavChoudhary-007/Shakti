"""
core/model_catalog.py
Live hardware detection + Ollama model inventory for /system/recommendations.

No third-party catalog service (e.g. ollamadb.dev) — Ollama has no official
endpoint for the full remote model library, so "not yet installed" suggestions
come from a small curated data file (data/suggested_models.json) instead of
being fetched. Installed-model data (size, parameter count, quantization)
always comes live from Ollama's own /api/tags — never hardcoded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

import psutil
import requests

_SUGGESTED_MODELS_PATH = Path(__file__).parent.parent / "data" / "suggested_models.json"


def get_system_specs() -> Dict[str, Any]:
    """Real hardware readout — never hardcoded."""
    vm = psutil.virtual_memory()
    return {
        "total_ram_gb": round(vm.total / (1024 ** 3), 1),
        "available_ram_gb": round(vm.available / (1024 ** 3), 1),
        "cpu_cores": os.cpu_count(),
    }


def get_installed_models(ollama_host: str) -> List[Dict[str, Any]]:
    """
    Models actually present on this machine, straight from Ollama's own
    /api/tags. Raises requests.RequestException if Ollama isn't reachable —
    callers must not swallow this into a hardcoded fallback.
    """
    resp = requests.get(f"{ollama_host}/api/tags", timeout=5)
    resp.raise_for_status()
    return resp.json().get("models", [])


def _is_cloud(name: str) -> bool:
    return name.endswith("-cloud") or ":cloud" in name


def build_installed_view(installed: List[Dict[str, Any]], budget_gb: float) -> List[Dict[str, Any]]:
    """
    Shape raw /api/tags entries into the response format, flagging what fits
    the detected RAM budget. Cloud models run on Ollama's infra, not this
    machine, so local RAM never gates them — Ollama also reports a near-zero
    stub `size` for most cloud entries rather than the real remote size, so
    size_gb is not meaningful for them and is left null.
    """
    results = []
    for m in installed:
        name = m.get("name", "")
        is_cloud = _is_cloud(name)
        details = m.get("details", {}) or {}
        size_gb = None if is_cloud else round(m.get("size", 0) / (1024 ** 3), 2)

        results.append({
            "name": name,
            "size_gb": size_gb,
            "parameter_size": details.get("parameter_size") or None,
            "quantization": details.get("quantization_level") or None,
            "is_cloud": is_cloud,
            "fits_comfortably": True if is_cloud else size_gb <= budget_gb,
        })

    results.sort(key=lambda x: (x["size_gb"] is None, x["size_gb"] or 0))
    return results


def load_suggested_models() -> List[Dict[str, Any]]:
    """Curated 'not yet installed' suggestions — data, not code. Empty list
    (not a hardcoded model fallback) if the file is missing or malformed."""
    try:
        with open(_SUGGESTED_MODELS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def build_suggestions_view(
    suggested: List[Dict[str, Any]],
    installed_names: set,
    budget_gb: float,
) -> List[Dict[str, Any]]:
    """Cross-reference curated suggestions against what's installed and what
    actually fits the detected RAM budget."""
    results = []
    for m in suggested:
        size_gb = m.get("approx_size_gb")
        fits = size_gb is None or size_gb <= budget_gb
        if not fits:
            continue
        results.append({
            "name": m.get("name"),
            "approx_size_gb": size_gb,
            "description": m.get("description", ""),
            "tags": m.get("tags", []),
            "installed": m.get("name") in installed_names,
        })
    return results
