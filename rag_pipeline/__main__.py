"""
python -m rag_pipeline — start the Shakti companion and open it in the browser.

    python -m rag_pipeline [--data-dir DIR] [--port 8000] [--no-browser]

If a companion is already running on the port, just opens the browser.
The installer's Start-menu shortcut runs this with pythonw (no console),
so logs go to <data-dir>/shakti.log.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _companion_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/version", timeout=2) as resp:
            return json.load(resp).get("name") == "shakti-companion"
    except Exception:
        return False


def _open_when_ready(url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _companion_running(url):
            webbrowser.open(f"{url}/")
            return
        time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shakti", description="Run the Shakti companion.")
    parser.add_argument("--data-dir", help="Where documents, indexes and settings are stored.")
    parser.add_argument("--port", type=int, help="Port to listen on (default 8000).")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser.")
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["SHAKTI_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    if args.port:
        os.environ["SHAKTI_PORT"] = str(args.port)

    # Import after the env is set: config and data paths resolve at import.
    from rag_pipeline import get_config, get_data_dir
    from rag_pipeline.api.security import server_port

    port = server_port(get_config())
    url = f"http://127.0.0.1:{port}"

    if _companion_running(url):
        if not args.no_browser:
            webbrowser.open(f"{url}/")
        print(f"Shakti is already running at {url}")
        return 0

    # pythonw has no console: send logs to a file in the data dir.
    if sys.stdout is None or not sys.stdout.isatty():
        log_file = get_data_dir() / "shakti.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        sys.stdout = sys.stdout or open(os.devnull, "w")
        sys.stderr = sys.stderr or open(os.devnull, "w")

    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()

    import uvicorn
    uvicorn.run("rag_pipeline.api.main:app", host="127.0.0.1", port=port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
