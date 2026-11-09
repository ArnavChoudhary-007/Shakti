"""
rag_pipeline/settings.py
User-changeable runtime settings, persisted as JSON in the data directory.

config.yaml holds install-time tuning; settings.json holds choices the user
makes from the UI (e.g. which Ollama model to chat with), so the website can
change them without anyone editing YAML.
"""
from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from rag_pipeline import get_data_dir

_lock = threading.Lock()

_DEFAULTS: Dict[str, Any] = {
    "chat_model": None,      # None → fall back to config ollama.default_text_model
}


def _settings_path() -> Path:
    return get_data_dir() / "settings.json"


def load_settings() -> Dict[str, Any]:
    path = _settings_path()
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    return {**_DEFAULTS, **{k: v for k, v in data.items() if k in _DEFAULTS}}


def update_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        current = load_settings()
        current.update({k: v for k, v in changes.items() if k in _DEFAULTS})
        path = _settings_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp.replace(path)
        return current


def get_chat_model(config: Dict[str, Any]) -> str:
    """The model to use when a request doesn't name one."""
    chosen: Optional[str] = load_settings().get("chat_model")
    return chosen or config.get("ollama", {}).get("default_text_model", "llama3.2:3b")


def get_pairing_token() -> str:
    """
    Secret that a browser on a non-local origin (the public website) must
    send in the X-Shakti-Token header. Generated once, stored in the data dir.
    """
    path = get_data_dir() / "companion_token"
    with _lock:
        if path.is_file():
            token = path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="utf-8")
        return token
