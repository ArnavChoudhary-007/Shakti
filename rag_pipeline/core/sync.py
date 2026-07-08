"""
core/sync.py
Incremental Sync Daemon.

Continuously polls a configured directory (e.g. `data/ingest`) and incrementally
ingests new or modified files. Uses `StructuredDB.sync_state` table to track
file modifications (via mtime) to avoid re-ingesting unchanged files.

If a file is modified, it clears old vectors and structured data via
`delete_by_file_path` before re-ingesting.

Usage:
  python -m rag_pipeline.core.sync
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Set

from rag_pipeline import get_config
from rag_pipeline.api.main import (
    _get_struct_db,
    _get_vector_store,
    _ingest_file_path,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# Supported extensions (matches frontend and connectors)
SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv",
    ".eml", ".mbox", ".txt", ".json",
    ".mp3", ".wav", ".m4a", ".ogg"
}


async def run_sync_loop(watch_dir: str, poll_interval: int = 10) -> None:
    """Run an infinite polling loop over the watch directory."""
    watch_path = Path(watch_dir).resolve()
    watch_path.mkdir(parents=True, exist_ok=True)

    db = _get_struct_db()
    vs = _get_vector_store()

    logger.info("Starting sync daemon. Watching: %s", watch_path)

    while True:
        try:
            await _sync_once(watch_path, db, vs)
        except Exception as e:
            logger.error("Error during sync iteration: %s", e)

        await asyncio.sleep(poll_interval)


async def _sync_once(watch_path: Path, db, vs) -> None:
    """Perform a single sync pass over the directory."""
    current_files: Set[Path] = set()

    for root, _, files in os.walk(watch_path):
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            
            # Skip hidden files
            if name.startswith("."):
                continue

            current_files.add(path)

            try:
                mtime = str(path.stat().st_mtime)
            except OSError:
                continue

            file_path_str = str(path)
            cursor = db.get_sync_cursor(source_type="file", source_path=file_path_str)

            if cursor == mtime:
                # Unchanged
                continue

            logger.info("Detected new/modified file: %s", file_path_str)

            if cursor is not None:
                # File was modified. Delete existing records before re-ingest
                logger.info("File modified. Clearing old records for %s", file_path_str)
                vs.delete_by_file_path(file_path_str)
                db.delete_by_file_path(file_path_str)

            # Ingest
            try:
                res = await _ingest_file_path(file_path_str, original_name=path.name)
                logger.info("Ingested %s: %d docs, %d chunks", path.name, res.doc_count, res.chunk_count)
                
                # Update sync state
                db.update_sync_state(source_type="file", source_path=file_path_str, cursor=mtime)
            except Exception as e:
                logger.error("Failed to ingest %s: %s", file_path_str, e)


def main():
    config = get_config()
    # Default to data/ingest relative to project root
    default_ingest_dir = str(Path(__file__).parent.parent.parent / "data" / "ingest")
    
    sync_config = config.get("sync", {})
    watch_dir = sync_config.get("watch_dir", default_ingest_dir)
    poll_interval = int(sync_config.get("poll_interval", 10))

    try:
        asyncio.run(run_sync_loop(watch_dir, poll_interval))
    except KeyboardInterrupt:
        logger.info("Sync daemon stopped by user.")


if __name__ == "__main__":
    main()
