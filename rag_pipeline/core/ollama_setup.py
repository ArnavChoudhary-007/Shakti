"""
core/ollama_setup.py
Detect, start, and install Ollama on the user's machine.

The website can't install software itself, but the companion can: it
downloads the official installer from ollama.com, checks its Authenticode
signature is a valid "Ollama Inc." signature, and launches it so the user
sees the normal setup wizard. Windows-only for now; other platforms get
manual instructions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from rag_pipeline import get_data_dir

logger = logging.getLogger(__name__)

WINDOWS_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
DOWNLOAD_PAGE_URL = "https://ollama.com/download"
_EXPECTED_SIGNER = "CN=Ollama Inc."

# Progress of the (single) install flow, polled by the UI.
_install_state: Dict[str, Any] = {"state": "idle", "downloaded": 0, "total": None, "error": None}
_install_task: Optional[asyncio.Task] = None


def platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _windows_install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"


def find_ollama_executable() -> Optional[str]:
    found = shutil.which("ollama")
    if found:
        return found
    candidates = []
    if platform_name() == "windows":
        candidates.append(_windows_install_dir() / "ollama.exe")
    elif platform_name() == "macos":
        candidates.append(Path("/Applications/Ollama.app/Contents/Resources/ollama"))
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


async def get_status(host: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Installed? Running? Which version? Never raises."""
    version = None
    running = False
    try:
        resp = await client.get(f"{host}/api/version", timeout=3.0)
        if resp.status_code == 200:
            running = True
            version = resp.json().get("version")
    except httpx.HTTPError:
        pass

    executable = find_ollama_executable()
    return {
        "platform": platform_name(),
        "installed": running or executable is not None,
        "running": running,
        "version": version,
        "executable": executable,
        "host": host,
        "download_url": DOWNLOAD_PAGE_URL,
        "can_auto_install": platform_name() == "windows",
        "install": dict(_install_state),
    }


def start_ollama() -> None:
    """Launch Ollama in the background. Raises RuntimeError if not installed."""
    if platform_name() == "windows":
        tray_app = _windows_install_dir() / "ollama app.exe"
        if tray_app.is_file():
            # The tray app starts the server and keeps it running across reboots.
            subprocess.Popen([str(tray_app)], creationflags=subprocess.DETACHED_PROCESS)
            return
    executable = find_ollama_executable()
    if not executable:
        raise RuntimeError("Ollama is not installed.")
    kwargs: Dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if platform_name() == "windows":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([executable, "serve"], **kwargs)


def _verify_signature(path: Path) -> None:
    """Refuse to run anything that isn't validly signed by Ollama Inc."""
    quoted = str(path).replace("'", "''")
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath '{quoted}'; "
        "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    status = lines[0] if lines else ""
    subject = lines[1] if len(lines) > 1 else ""
    if status != "Valid" or not subject.startswith(_EXPECTED_SIGNER):
        raise RuntimeError(f"Installer signature check failed (status={status!r}, signer={subject!r}).")


async def _run_install() -> None:
    target_dir = get_data_dir() / "downloads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "OllamaSetup.exe"
    partial = target.with_suffix(".part")
    try:
        _install_state.update(state="downloading", downloaded=0, total=None, error=None)
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0)) as client:
            async with client.stream("GET", WINDOWS_INSTALLER_URL) as resp:
                resp.raise_for_status()
                total = resp.headers.get("content-length")
                _install_state["total"] = int(total) if total else None
                with open(partial, "wb") as f:
                    async for chunk in resp.aiter_bytes(1024 * 256):
                        f.write(chunk)
                        _install_state["downloaded"] += len(chunk)
        partial.replace(target)

        _install_state["state"] = "verifying"
        await asyncio.to_thread(_verify_signature, target)

        _install_state["state"] = "installer_running"
        # Interactive install: the user sees Ollama's own setup wizard.
        await asyncio.to_thread(lambda: subprocess.run([str(target)], check=False))
        _install_state["state"] = "done"
    except Exception as e:
        logger.error("Ollama install failed: %s", e)
        _install_state.update(state="error", error=str(e))
        partial.unlink(missing_ok=True)


def begin_install() -> Dict[str, Any]:
    """Start the download+install flow in the background (idempotent)."""
    global _install_task
    if platform_name() != "windows":
        raise RuntimeError("Automatic install is only supported on Windows. Download Ollama from " + DOWNLOAD_PAGE_URL)
    if _install_task is None or _install_task.done():
        _install_task = asyncio.create_task(_run_install())
    return dict(_install_state)
