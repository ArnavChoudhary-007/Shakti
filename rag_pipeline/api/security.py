"""
api/security.py
Request guard for the local companion API.

The companion listens on 127.0.0.1 and is called from two kinds of page:

  * the UI it serves itself (http://localhost:8000) — trusted, no token;
  * the public website (e.g. https://<user>.github.io) — allowed only with
    the pairing token in the ``X-Shakti-Token`` header.

Every other origin is rejected outright. CORS alone is not enough: browsers
still *send* "simple" cross-site requests (form POSTs, multipart uploads) and
only hide the response, so the guard refuses them before they reach a route.
A Host-header check blocks DNS-rebinding attacks, where a hostile domain is
re-pointed at 127.0.0.1 to look same-origin.
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Iterable, List, Set

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

TOKEN_HEADER = "X-Shakti-Token"

# Paths the public website may call before it has been paired, so it can
# detect whether a companion is installed at all.
_UNPAIRED_PATHS = {"/version"}

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}

_DEFAULT_WEB_ORIGINS = ["https://arnavchoudhary-007.github.io"]


def local_origins(port: int) -> Set[str]:
    origins = set()
    for host in ("localhost", "127.0.0.1"):
        origins.add(f"http://{host}:{port}")
        origins.add(f"http://{host}:5173")   # Vite dev server
    return origins


def web_origins(config: Dict[str, Any]) -> Set[str]:
    configured: Iterable[str] = (config.get("server") or {}).get("web_origins") or _DEFAULT_WEB_ORIGINS
    extra = [o.strip() for o in os.environ.get("SHAKTI_WEB_ORIGINS", "").split(",") if o.strip()]
    return {o.rstrip("/") for o in [*configured, *extra]}


def server_port(config: Dict[str, Any]) -> int:
    return int(os.environ.get("SHAKTI_PORT") or (config.get("server") or {}).get("port", 8000))


def is_local_request(request: Request, local: Set[str]) -> bool:
    """True for same-origin page requests and non-browser clients."""
    origin = request.headers.get("origin")
    if origin is not None:
        return origin in local
    return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")


class CompanionGuard(BaseHTTPMiddleware):
    def __init__(self, app, *, local: Set[str], web: Set[str], token_provider, file_view_tokens=None):
        super().__init__(app)
        self.local = local
        self.web = web
        self.token_provider = token_provider
        self.file_view_tokens = file_view_tokens

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if hostname not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "Invalid Host header."}, status_code=400)

        if "view_token" in request.query_params:
            origin = request.headers.get("origin")
            if (
                self.file_view_tokens
                and (origin is None or origin in self.local | self.web)
                and self.file_view_tokens.permits(request)
            ):
                return await call_next(request)
            return JSONResponse({"detail": "This original-file link is invalid or expired. Open it again from Sources."}, status_code=403)

        if is_local_request(request, self.local):
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin in self.web:
            if request.url.path in _UNPAIRED_PATHS:
                return await call_next(request)
            supplied = request.headers.get(TOKEN_HEADER, "")
            if supplied and hmac.compare_digest(supplied, self.token_provider()):
                return await call_next(request)
            return JSONResponse(
                {"detail": "This website is not paired with your Shakti companion.", "code": "not_paired"},
                status_code=401,
            )

        return JSONResponse({"detail": "Origin not allowed."}, status_code=403)


def cors_origins(local: Set[str], web: Set[str]) -> List[str]:
    return sorted(local | web)
