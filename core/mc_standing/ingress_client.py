"""HTTP client for the standing event ingress (harness v0.3.1 §14.1, Codex's listener
``services/minimoi_actions/standing_ingress.py``). Used by the cycle runner and the learning job
as ``ingress``.

POST outcome (the caller decides what happens next):
  2xx with an exact ``mc-standing-ack/1`` for this event   → that ack
  2xx that is malformed, oversize or for another event     → IngressUncertain (may have committed)
  any 5xx, a timeout, or a connection lost after sending   → IngressUncertain: reconcile by GET
  4xx                                                      → {"state": "refused", "code": ...}
  3xx                                                      → refused (redirects are never followed,
                                                             so the producer token never leaves)
  connection refused before sending                        → ConnectionError: safe to retry later
GET: 200 needs an exact ack for this event (else ConnectionError, unknown); only the listener's
authenticated 404 (``{"error": "event_not_found"}``) is "verifiably absent" (None); anything else
is ConnectionError, never absence. The token comes from ``token_source()`` per call and is never
logged or returned.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid

from . import ack as ack_mod
from .runner import IngressUncertain

PATH = "/internal/mc/v1/standing/events"
MAX_RESPONSE = 64 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                     # surface the 3xx as an HTTPError; never re-send the token


class IngressClient:
    def __init__(self, base_url: str, token_source, *, timeout_s: float = 10.0, opener=None):
        if not base_url.startswith(("http://", "https://")) or "@" in base_url:
            raise ValueError("ingress_url_invalid")
        self.base = base_url.rstrip("/")
        self._token = token_source
        self.timeout = timeout_s
        self._open = opener or urllib.request.build_opener(_NoRedirect, urllib.request.HTTPHandler,
                                                           urllib.request.HTTPSHandler).open

    def _request(self, method, path, body=None):
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token()}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return self._open(req, timeout=self.timeout)

    @staticmethod
    def _json(resp_or_err) -> dict | None:
        """The JSON object, or None when the body is missing, malformed or oversize."""
        try:
            raw = resp_or_err.read(MAX_RESPONSE + 1)
        except OSError:
            return None
        if not raw or len(raw) > MAX_RESPONSE:
            return None
        try:
            doc = json.loads(raw)
        except ValueError:
            return None
        return doc if isinstance(doc, dict) else None

    def post(self, event: dict) -> dict:
        event_id = event.get("event_id")
        try:
            with self._request("POST", PATH, event) as resp:
                ack = ack_mod.check(self._json(resp), event_id)
        except urllib.error.HTTPError as err:
            if err.code >= 500:
                raise IngressUncertain() from None
            if 300 <= err.code < 400:
                return {"state": "refused", "code": "redirect_refused"}
            body = self._json(err) or {}
            code = body.get("error") or body.get("code")
            return {"state": "refused", "code": code if isinstance(code, str) and len(code) <= 64 else f"http_{err.code}"}
        except urllib.error.URLError as err:
            if isinstance(err.reason, ConnectionRefusedError):
                raise ConnectionError("ingress_unreachable") from None
            raise IngressUncertain() from None
        except (socket.timeout, TimeoutError, ConnectionResetError, OSError):
            raise IngressUncertain() from None
        if ack is None:
            raise IngressUncertain()    # a 2xx we cannot read may still have committed
        return ack

    def get(self, event_id: str) -> dict | None:
        if str(uuid.UUID(event_id)) != event_id:
            raise ValueError("event_id_invalid")
        try:
            with self._request("GET", f"{PATH}/{event_id}") as resp:
                ack = ack_mod.check(self._json(resp), event_id)
        except urllib.error.HTTPError as err:
            if err.code == 404 and (self._json(err) or {}).get("error") == "event_not_found":
                return None
            raise ConnectionError(f"ingress_readback_{err.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionResetError, OSError):
            raise ConnectionError("ingress_readback_unavailable") from None
        if ack is None:
            raise ConnectionError("ingress_readback_invalid")
        return ack
