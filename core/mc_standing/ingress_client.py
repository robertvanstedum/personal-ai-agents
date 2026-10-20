"""HTTP client for the standing event ingress (harness v0.3.1 §14.1, Codex's listener
``services/minimoi_actions/standing_ingress.py``). Used by the cycle runner as ``ingress``.

Status mapping (the runner decides what happens next):
  200 accepted / already_applied / deferred → returned as the ack
  400, 401, 403, 409, 413, 415              → {"state": "refused", "code": ...}: visible failure
  503, timeout or connection lost after the request was sent → IngressUncertain: reconcile by GET
  connection refused before sending          → ConnectionError: safe to try again later
GET 404 is "verifiably absent" (None). The producer token comes from ``token_source()`` per call
and is never logged or returned.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid

from .runner import IngressUncertain

PATH = "/internal/mc/v1/standing/events"
MAX_RESPONSE = 64 * 1024


class IngressClient:
    def __init__(self, base_url: str, token_source, *, timeout_s: float = 10.0, opener=None):
        if not base_url.startswith(("http://", "https://")) or "@" in base_url:
            raise ValueError("ingress_url_invalid")
        self.base = base_url.rstrip("/")
        self._token = token_source
        self.timeout = timeout_s
        self._open = opener or urllib.request.build_opener(urllib.request.HTTPHandler,
                                                           urllib.request.HTTPSHandler).open

    def _request(self, method, path, body=None):
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token()}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return self._open(req, timeout=self.timeout)

    @staticmethod
    def _json(resp_or_err) -> dict:
        try:
            raw = resp_or_err.read(MAX_RESPONSE + 1)
            doc = json.loads(raw) if raw and len(raw) <= MAX_RESPONSE else {}
            return doc if isinstance(doc, dict) else {}
        except (ValueError, OSError):
            return {}

    def post(self, event: dict) -> dict:
        try:
            with self._request("POST", PATH, event) as resp:
                return self._json(resp)
        except urllib.error.HTTPError as err:
            body = self._json(err)
            if err.code == 503:
                raise IngressUncertain() from None
            return {"state": "refused", "code": body.get("error") or body.get("code") or f"http_{err.code}"}
        except urllib.error.URLError as err:
            if isinstance(err.reason, ConnectionRefusedError):
                raise ConnectionError("ingress_unreachable") from None
            raise IngressUncertain() from None
        except (socket.timeout, TimeoutError, ConnectionResetError):
            raise IngressUncertain() from None

    def get(self, event_id: str) -> dict | None:
        if str(uuid.UUID(event_id)) != event_id:
            raise ValueError("event_id_invalid")
        try:
            with self._request("GET", f"{PATH}/{event_id}") as resp:
                return self._json(resp)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise ConnectionError(f"ingress_readback_{err.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionResetError):
            raise ConnectionError("ingress_readback_unavailable") from None
