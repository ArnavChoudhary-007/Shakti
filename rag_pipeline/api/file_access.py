from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time


class FileViewTokens:
    def __init__(self, ttl: int = 600):
        self.ttl = ttl
        self._secret = secrets.token_bytes(32)

    def _signature(self, file_key: str, workspace_id: str, expiry: int) -> str:
        payload = json.dumps([file_key, workspace_id, expiry], separators=(",", ":")).encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue(self, file_key: str, workspace_id: str) -> str:
        expiry = int(time.time()) + self.ttl
        return f"{expiry}.{self._signature(file_key, workspace_id, expiry)}"

    def valid(self, token: str, file_key: str, workspace_id: str) -> bool:
        if not re.fullmatch(r"[0-9]{1,12}\.[0-9a-f]{64}", token):
            return False
        expiry_text, signature = token.split(".")
        expiry = int(expiry_text)
        now = int(time.time())
        if not now < expiry <= now + self.ttl:
            return False
        return hmac.compare_digest(signature, self._signature(file_key, workspace_id, expiry))

    def permits(self, request) -> bool:
        match = re.fullmatch(r"/documents/([a-zA-Z0-9_-]{1,80})/content", request.url.path)
        return bool(
            match
            and request.method in {"GET", "HEAD"}
            and self.valid(
                request.query_params.get("view_token", ""),
                match.group(1),
                request.query_params.get("workspace_id", "default"),
            )
        )
