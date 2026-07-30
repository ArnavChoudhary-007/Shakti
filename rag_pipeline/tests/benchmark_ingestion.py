"""
tests/benchmark_ingestion.py
Repeatable ingestion benchmark against a running rag_pipeline server.

Usage:
    # Start the server first: uvicorn rag_pipeline.api.main:app --port 8000
    python tests/benchmark_ingestion.py --label phase1_baseline

Measures wall-clock time from upload start until the server reports the
ingest job as "done" (i.e. including any background work tied to that job,
not just the time the HTTP request takes to return). Also pulls the
per-stage timing breakdown (parse/chunk/kg/embed/store) that the server now
reports in the job status payload.

Results are written to tests/benchmark_results/<label>.json so successive
phases can be diffed against the same fixed sample set.

The sample set lives in tests/benchmark_files/ and is committed to the repo
so every run — and every person running it — uses identical inputs:
    bench_small.pdf        (2-page PDF)
    bench_medium.pdf       (33-page PDF)
    bench_large.pdf        (49-page PDF)
    bench_spreadsheet.csv  (~900KB real-world CSV)
    bench_chat_whatsapp.txt (341-message WhatsApp export)

No audio file is included — none was available in the repo at benchmark
creation time. Drop a short .mp3/.wav into tests/benchmark_files/ named
bench_audio.* and it will be picked up automatically.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

_HERE = Path(__file__).parent
_SAMPLE_DIR = _HERE / "benchmark_files"
_RESULTS_DIR = _HERE / "benchmark_results"
_WORKSPACE_ID = "benchmark"

_FIXED_FILES = [
    "bench_small.pdf",
    "bench_medium.pdf",
    "bench_large.pdf",
    "bench_spreadsheet.csv",
    "bench_chat_whatsapp.txt",
]


def _find_audio_file() -> Optional[Path]:
    for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
        p = _SAMPLE_DIR / f"bench_audio{ext}"
        if p.exists():
            return p
    return None


def _check_server(client: httpx.Client, base_url: str) -> None:
    try:
        resp = client.get(f"{base_url}/health", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERROR: cannot reach server at {base_url} ({e}).")
        print("Start it first: uvicorn rag_pipeline.api.main:app --port 8000")
        sys.exit(1)
    if data.get("ollama") != "ok":
        print(f"WARNING: Ollama not reachable ({data.get('ollama')}). KG timings will be skewed/failing.")


def _clear_workspace(client: httpx.Client, base_url: str) -> None:
    try:
        client.delete(f"{base_url}/system/clear", params={"workspace_id": _WORKSPACE_ID}, timeout=30.0)
    except Exception as e:
        print(f"WARNING: could not clear benchmark workspace before run: {e}")


def _ingest_one(client: httpx.Client, base_url: str, file_path: Path, poll_interval: float = 1.0, timeout: float = 900.0) -> Dict[str, Any]:
    wall_start = time.perf_counter()
    with open(file_path, "rb") as f:
        resp = client.post(
            f"{base_url}/ingest",
            files={"file": (file_path.name, f, "application/octet-stream")},
            data={"workspace_id": _WORKSPACE_ID},
            timeout=120.0,
        )
    resp.raise_for_status()
    payload = resp.json()

    job_id = payload.get("job_id")
    if not job_id:
        # Synchronous response — already done.
        wall_time = time.perf_counter() - wall_start
        return {
            "file": file_path.name,
            "status": payload.get("status", "done"),
            "wall_time_s": round(wall_time, 3),
            "doc_count": payload.get("doc_count"),
            "chunk_count": payload.get("chunk_count"),
            "source_types": payload.get("source_types"),
            "timings": payload.get("timings"),
        }

    # Backoff from a short delay up to poll_interval so fast (post-fix) jobs
    # aren't dominated by fixed poll granularity, while slow jobs don't hammer
    # the server with requests.
    deadline = time.perf_counter() + timeout
    delay = min(0.2, poll_interval)
    while time.perf_counter() < deadline:
        time.sleep(delay)
        delay = min(delay * 1.5, poll_interval)
        r = client.get(f"{base_url}/ingest/status/{job_id}", timeout=15.0)
        r.raise_for_status()
        job = r.json()
        if job.get("status") in ("done", "failed"):
            wall_time = time.perf_counter() - wall_start
            return {
                "file": file_path.name,
                "status": job.get("status"),
                "error": job.get("error"),
                "wall_time_s": round(wall_time, 3),
                "doc_count": job.get("doc_count"),
                "chunk_count": job.get("chunk_count"),
                "source_types": job.get("source_types"),
                "timings": job.get("timings"),
            }

    return {
        "file": file_path.name,
        "status": "timeout",
        "wall_time_s": round(time.perf_counter() - wall_start, 3),
    }


def _print_summary(results: List[Dict[str, Any]]) -> None:
    print()
    header = f"{'file':<28}{'status':<10}{'wall_s':>9}{'parse':>8}{'chunk':>8}{'kg':>8}{'embed':>8}{'store':>8}{'chunks':>8}"
    print(header)
    print("-" * len(header))
    total_wall = 0.0
    for r in results:
        t = r.get("timings") or {}
        total_wall += r.get("wall_time_s") or 0.0
        print(
            f"{r['file']:<28}{r['status']:<10}"
            f"{r.get('wall_time_s', 0):>9.2f}"
            f"{t.get('parse', 0):>8.2f}"
            f"{t.get('chunk', 0):>8.2f}"
            f"{t.get('kg', 0):>8.2f}"
            f"{t.get('embed', 0):>8.2f}"
            f"{t.get('store', 0):>8.2f}"
            f"{r.get('chunk_count', '-'):>8}"
        )
    print("-" * len(header))
    print(f"TOTAL wall-clock across all files (sequential, includes poll delay): {total_wall:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark rag_pipeline ingestion end-to-end.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--label", required=True, help="Name for this run, e.g. phase1_baseline")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--skip-clear", action="store_true", help="Don't clear the benchmark workspace first")
    parser.add_argument("--only", nargs="+", default=None, help="Subset of fixture filenames to run")
    parser.add_argument("--timeout", type=float, default=900.0, help="Per-file poll timeout in seconds")
    args = parser.parse_args()

    client = httpx.Client()
    _check_server(client, args.base_url)
    if not args.skip_clear:
        _clear_workspace(client, args.base_url)

    fixed = args.only if args.only else _FIXED_FILES
    files = [_SAMPLE_DIR / name for name in fixed]
    missing = [f for f in files if not f.exists()]
    if missing:
        print(f"ERROR: missing benchmark fixture(s): {missing}")
        sys.exit(1)

    audio = _find_audio_file()
    if audio and not args.only:
        files.append(audio)
    elif not args.only:
        print("No audio fixture found (tests/benchmark_files/bench_audio.*) — skipping audio.")

    print(f"Running ingestion benchmark '{args.label}' against {args.base_url} "
          f"({len(files)} files, workspace={_WORKSPACE_ID})\n")

    results = []
    run_start = time.perf_counter()
    for f in files:
        print(f"Ingesting {f.name} ...")
        r = _ingest_one(client, args.base_url, f, poll_interval=args.poll_interval, timeout=args.timeout)
        results.append(r)
        print(f"  -> {r['status']} in {r.get('wall_time_s', 0):.2f}s "
              f"({r.get('chunk_count', '?')} chunks)")
    run_total = time.perf_counter() - run_start
    client.close()

    _print_summary(results)

    _RESULTS_DIR.mkdir(exist_ok=True)
    out_path = _RESULTS_DIR / f"{args.label}.json"
    out_path.write_text(json.dumps({
        "label": args.label,
        "base_url": args.base_url,
        "run_total_wall_s": round(run_total, 3),
        "files": [f.name for f in files],
        "results": results,
    }, indent=2))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
