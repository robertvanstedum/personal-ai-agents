"""Master Craftsman on OpenClaw: the ``mc-agent`` entry in CoS Agent A's
OpenClaw container (MC spec v0.7 §1, §3, §8).

The Shop floor reaches it through the per-agent front door (N9, stage 2):
``MC_AGENT_A_URL`` (for example ``http://openclaw-agents:18789/v1``) and the
MC caller token ``MC_AGENT_A_TOKEN``. Neither exists in stage 1a, so this
backend reports "unavailable · not connected yet" and refuses every turn:
MC's own model key is absent in 1a as well, so a turn could only fail. The
portal never holds OpenClaw's own (full operator) token.

Rules carried from v0.5 §2:
* ``health()`` needs the runtime's ``/readyz`` AND the front door's
  ``/mc-ready`` (the self-check marker for this start); ``/readyz`` alone never
  opens MC turns.
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
    if status == 429:
        return "duplicate_in_progress", "one_turn_in_flight"
    if "budget" in text or "exceeded" in text:
        return "unavailable", "cap_reached"
    if "401" in text or "invalid api key" in text or "authentication" in text or "unauthorized" in text:
        return "unavailable", "key_refused"
    return "error", "runtime_error"


class OpenClawMasterCraftsman(MasterCraftsmanBackend):
    kind = "openclaw"

    def __init__(self, url: str | None, token: str | None, *, http_get=None, http_post=None):
        self.url = (url or "").rstrip("/")
        self._token = token or ""
        if self.url:
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("MC_AGENT_A_URL must be an http(s) URL")
        if http_get is None or http_post is None:
            import requests
            http_get = http_get or requests.get
            http_post = http_post or requests.post
        self._get, self._post = http_get, http_post

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        return cls(environ.get("MC_AGENT_A_URL"), environ.get("MC_AGENT_A_TOKEN"),
                   http_get=http_get, http_post=http_post)

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
            marker = self._get(f"{root}/mc-ready", timeout=HEALTH_TIMEOUT_S,
                               headers={"Authorization": f"Bearer {self._token}"})
        except Exception:
            return Health("unavailable", "not_ready")
        if getattr(marker, "status_code", None) != 200:
            return Health("unavailable", "starting")
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
