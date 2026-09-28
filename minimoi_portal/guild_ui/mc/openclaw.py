"""Master Craftsman on OpenClaw: MC's own OpenClaw container, separate from
CoS Agent A's (separate-container plan; spec v0.8).

The portal reaches it server-side only: ``MC_RUNTIME_URL`` (for example
``http://mc-agent:18789/v1``) and MC's own OpenClaw token ``MC_RUNTIME_TOKEN``.
The browser never receives either. Neither is set anywhere yet (dormant), so
this backend reports "unavailable · not connected yet" and refuses every turn.
``MC_RUNTIME_READY_PATH`` (optional) names an extra readiness path the runtime
must answer 200 on, for example a start-up self-check marker.

Rules carried from spec v0.5 §2:
* Errors are never answers: ``answered`` only with reply text and a ``usage``
  block. 408 (also what a DNS failure to the model gateway looks like) is
  "unavailable · model gateway down"; a refused key or budget is unavailable
  with its class; a timeout is ``timeout_uncertain``; nothing is retried here.
* The request is refused before sending above 256 KB.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

from .backend import Health, MasterCraftsmanBackend, TurnResult

MODEL = "openclaw/mc-agent"
USER_PREFIX = "guild-mc:"
MAX_REQUEST_BODY_BYTES = 262_144
CONNECT_TIMEOUT_S = 5
TURN_DEADLINE_S = 90
HEALTH_TIMEOUT_S = 5


def _root(url: str) -> str:
    """http://host:port/v1 -> http://host:port"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def classify_failure(status: int, body: str) -> tuple[str, str]:
    """(turn status, failure class) for a non-200 answer from the runtime."""
    text = (body or "").lower()
    if status == 408 or "upstream provider timeout" in text:
        return "unavailable", "model_gateway_down"
    # OpenClaw 9.6 answers 401 "authentication_error" when the MODEL key was
    # refused (MC's key in 1a is a placeholder), and 401 "unauthorized" when
    # the caller's own token was refused (probe, 2026-09-28).
    if status == 401 and "authentication_error" in text:
        return "unavailable", "key_refused"
    if status in (401, 403):
        return "unavailable", "caller_refused"
    if "budget" in text or "exceeded" in text:
        return "unavailable", "cap_reached"
    if status == 429 and ("rate limit" in text or "ratelimit" in text or "rpm" in text or "tpm" in text):
        return "unavailable", "rate_limited"
    if status == 429:
        return "duplicate_in_progress", "one_turn_in_flight"
    if "401" in text or "invalid api key" in text or "authentication" in text or "unauthorized" in text:
        return "unavailable", "key_refused"
    return "error", "runtime_error"


class OpenClawMasterCraftsman(MasterCraftsmanBackend):
    kind = "openclaw"

    def __init__(self, url: str | None, token: str | None, *, ready_path: str | None = None,
                 http_get=None, http_post=None):
        self.url = (url or "").rstrip("/")
        self._token = token or ""
        self.ready_path = ready_path or None
        if self.ready_path and not self.ready_path.startswith("/"):
            raise ValueError("MC_RUNTIME_READY_PATH must start with /")
        if self.url:
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("MC_RUNTIME_URL must be an http(s) URL")
        if http_get is None or http_post is None:
            import requests
            http_get = http_get or requests.get
            http_post = http_post or requests.post
        self._get, self._post = http_get, http_post

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        return cls(environ.get("MC_RUNTIME_URL"), environ.get("MC_RUNTIME_TOKEN"),
                   ready_path=environ.get("MC_RUNTIME_READY_PATH"), http_get=http_get, http_post=http_post)

    @property
    def connected(self) -> bool:
        return bool(self.url and self._token)

    def health(self) -> Health:
        if not self.connected:
            return Health("unavailable", "not_connected")
        root = _root(self.url)
        try:
            ready = self._get(f"{root}/readyz", timeout=HEALTH_TIMEOUT_S)
            if getattr(ready, "status_code", None) != 200:
                return Health("unavailable", "not_ready")
            if self.ready_path:
                marker = self._get(f"{root}{self.ready_path}", timeout=HEALTH_TIMEOUT_S,
                                   headers={"Authorization": f"Bearer {self._token}"})
                if getattr(marker, "status_code", None) != 200:
                    return Health("unavailable", "starting")
        except Exception:
            return Health("unavailable", "not_ready")
        return Health("ready")

    def _user(self, conversation_id: str) -> str:
        return USER_PREFIX + hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]

    def turn(self, req, cancel=None) -> TurnResult:
        if not self.connected:
            return TurnResult("unavailable", self.kind, failure_class="not_connected",
                              message="Master Craftsman is not connected yet")
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        context = {k: v for k, v in (req.context or {}).items() if v not in (None, "")}
        content = req.text if not context else f"[Shop floor context: {json.dumps(context, sort_keys=True)}]\n{req.text}"
        body = {"model": MODEL, "user": self._user(req.conversation_id),
                "messages": [{"role": "user", "content": content}]}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BODY_BYTES:
            return TurnResult("refused", self.kind, failure_class="request_too_large",
                              message="The turn is larger than 256 KB; nothing was sent")
        try:
            response = self._post(f"{self.url}/chat/completions", data=payload,
                                  headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                                  timeout=(CONNECT_TIMEOUT_S, TURN_DEADLINE_S))
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name and "Connect" not in name:
                return TurnResult("timeout_uncertain", self.kind, failure_class="deadline",
                                  message="No answer within the deadline; it may still have run")
            return TurnResult("unavailable", self.kind, failure_class="not_ready",
                              message="Master Craftsman's runtime did not answer")
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        status = getattr(response, "status_code", 0)
        text = getattr(response, "text", "") or ""
        if status != 200:
            turn_status, failure = classify_failure(status, text)
            return TurnResult(turn_status, self.kind, failure_class=failure, message=f"runtime answered {status}")
        try:
            data = json.loads(text)
            reply = data["choices"][0]["message"].get("content") or ""
            usage = data.get("usage")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return TurnResult("error", self.kind, failure_class="malformed_answer", message="unreadable answer")
        if data.get("error") or not isinstance(usage, dict) or not reply.strip():
            return TurnResult("error", self.kind, failure_class="no_run_status",
                              message="the runtime's answer carried no run status; not treated as an answer")
        return TurnResult("answered", self.kind, text=reply, usage=usage)
