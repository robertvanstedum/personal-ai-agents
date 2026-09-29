"""Master Craftsman on OpenClaw: MC's own OpenClaw container, reached only
through the one-way relay (MC spec v0.9 §4; separate-container plan stage B).

The portal talks to the relay (``MC_RUNTIME_URL``, for example
``http://mc-relay:8790/v1``) with the relay's caller token
(``MC_RUNTIME_TOKEN``). The relay holds MC's own full-operator OpenClaw token;
the portal never does. Neither token reaches the browser, HTML or logs.

Honest states:
* not connected (no URL or token): "unavailable · not connected yet", no turns;
* the relay's ``/readyz`` fails: "unavailable · its runtime is not answering";
* reachable but no answered turn yet on this portal process: "unavailable ·
  connected, no answer yet" (turns may be tried) — never "live" before a
  real answer (PR 249 review 3b);
* after an answered turn: ready ("live"); after a failed one: unavailable with
  that failure's reason (for example "its model key was refused", which is
  what every stage-A/B turn on staging ends in: MC's key is a placeholder).

Rules carried from spec v0.5 §2:
* Errors are never answers: ``answered`` only with HTTP 200, non-blank reply
  text, no ``error`` field, a normal ``finish_reason`` (stop/length, or none)
  and not OpenClaw's own "No response from OpenClaw." placeholder. OpenClaw
  2026.9.6 always reports ``usage`` as zeros, so usage is NOT a signal: it is
  kept as not reported (None); spend comes from the gateway's key record.
  Upstream failures reach here as non-200 (key refusal 400/401; budget, 500
  and 429 as a generic 500). 408 (also what a DNS failure to the model gateway looks like) is
  "unavailable · model gateway down"; a refused key or budget is unavailable
  with its class; a timeout is ``timeout_uncertain``; nothing is retried here.
* The request is refused before sending above 256 KB.

Streaming (spec v0.2 §3, v0.3 §2): ``stream_turn()`` sends the same body with
``stream: true`` to the relay, which answers with its own NDJSON. The portal's
bounded reader (stream.py) reads it: a 125 s wall clock, 30 s idle (the read
timeout), 256 KB of text, a 64 KB line. ``stop()`` asks the relay to abort the
turn (POST /v1/turns/stop). A non-200 from the relay is classified exactly as
the non-streaming path's.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlparse

from .backend import Health, MasterCraftsmanBackend, TurnResult
from .stream import Failure, Limits, StreamRefused, read_stream

MODEL = "openclaw/mc-agent"
USER_PREFIX = "guild-mc:"
MAX_REQUEST_BODY_BYTES = 262_144
CONNECT_TIMEOUT_S = 5
TURN_DEADLINE_S = 90
HEALTH_TIMEOUT_S = 5
STREAM_LIMITS = Limits(deadline_s=125.0, idle_s=30.0, text_max=256 * 1024, line_max=64 * 1024)
STOP_TIMEOUT_S = 5
# OpenClaw 2026.9.6 (dist/openai-http: the non-streaming chat completion)
# answers 200 with this text when the agent run produced no reply text, and
# sets finish_reason to "stop", "length" or (pending client tools) "tool_calls".
OPENCLAW_NO_RESPONSE = "No response from OpenClaw."
ANSWER_FINISH_REASONS = (None, "stop", "length")


def _root(url: str) -> str:
    """http://host:port/v1 -> http://host:port"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


KEY_REFUSED_MARKERS = ("401", "invalid api key", "authentication", "unauthorized",
                       # LiteLLM with no key database (stages A and B): a non-master key
                       # cannot be looked up and is refused before routing.
                       "no connected db", "no_db_connection")


def classify_failure(status: int, body: str) -> tuple[str, str]:
    """(turn status, failure class) for a non-200 answer from the runtime."""
    text = (body or "").lower()
    if status == 403 and "relay_" in text:
        return "unavailable", "caller_refused"
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
    if any(marker in text for marker in KEY_REFUSED_MARKERS):
        return "unavailable", "key_refused"
    return "error", "runtime_error"


class OpenClawMasterCraftsman(MasterCraftsmanBackend):
    kind = "openclaw"
    supports_streaming = True

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
        self._last = None          # the last completed turn on this process (for honest "live")

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        return cls(environ.get("MC_RUNTIME_URL"), environ.get("MC_RUNTIME_TOKEN"),
                   ready_path=environ.get("MC_RUNTIME_READY_PATH"), http_get=http_get, http_post=http_post)

    @property
    def connected(self) -> bool:
        return bool(self.url and self._token)

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    def health(self) -> Health:
        if not self.connected:
            return Health("unavailable", "not_connected")
        root = _root(self.url)
        try:
            ready = self._get(f"{root}/readyz", timeout=HEALTH_TIMEOUT_S, headers=self._auth())
            if getattr(ready, "status_code", None) != 200:
                return Health("unavailable", "not_ready")
            if self.ready_path:
                marker = self._get(f"{root}{self.ready_path}", timeout=HEALTH_TIMEOUT_S, headers=self._auth())
                if getattr(marker, "status_code", None) != 200:
                    return Health("unavailable", "starting")
        except Exception:
            return Health("unavailable", "not_ready")
        last = self._last
        if last is None:
            return Health("unavailable", "not_verified", reachable=True)
        if last.status == "answered":
            return Health("ready", reachable=True)
        return Health("unavailable", last.failure_class or "not_verified", reachable=True)

    def _user(self, conversation_id: str) -> str:
        return USER_PREFIX + hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]

    def turn(self, req, cancel=None) -> TurnResult:
        result = self._turn(req, cancel)
        self.settle(result)
        return result

    def settle(self, result: TurnResult) -> None:
        """Only an attempt that reached (or tried to reach) the runtime changes
        what the header says; a refused, cancelled or busy turn does not. The
        streaming path calls this too, so its header turns "live" alike."""
        if result.status in ("answered", "unavailable", "error", "timeout_uncertain") \
                and result.failure_class not in ("not_connected", "one_turn_in_flight"):
            self._last = result

    def _payload(self, req, *, stream: bool = False) -> bytes | TurnResult:
        context = {k: v for k, v in (req.context or {}).items() if v not in (None, "")}
        content = req.text if not context else f"[Shop floor context: {json.dumps(context, sort_keys=True)}]\n{req.text}"
        body = {"model": MODEL, "user": self._user(req.conversation_id),
                "messages": [{"role": "user", "content": content}]}
        if stream:
            body["stream"] = True
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BODY_BYTES:
            return TurnResult("refused", self.kind, failure_class="request_too_large",
                              message="The turn is larger than 256 KB; nothing was sent")
        return payload

    def _headers(self, req) -> dict:
        headers = {**self._auth(), "Content-Type": "application/json"}
        if req.correlation_id:
            headers["X-MC-Correlation-Id"] = req.correlation_id
        return headers

    def stream_turn(self, req, cancel=None):
        """Pre-dispatch checks now (StreamRefused: nothing was sent); the
        returned iterator dispatches on its first next()."""
        if not self.connected:
            raise StreamRefused("not_connected", "Master Craftsman is not connected yet")
        payload = self._payload(req, stream=True)
        if isinstance(payload, TurnResult):
            raise StreamRefused(payload.failure_class, payload.message)
        return self._stream(payload, self._headers(req), cancel)

    def _stream(self, payload: bytes, headers: dict, cancel):
        if cancel is not None and cancel.is_set():
            yield Failure("stopped", "cancelled")
            return
        try:
            response = self._post(f"{self.url}/chat/completions", data=payload, headers=headers, stream=True,
                                   timeout=(CONNECT_TIMEOUT_S, STREAM_LIMITS.idle_s))
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name and "Connect" not in name:
                yield Failure("idle", "timeout_uncertain")
            else:
                yield Failure("not_ready", "unavailable")
            return
        try:
            status = getattr(response, "status_code", 0)
            ctype = str((getattr(response, "headers", None) or {}).get("Content-Type", ""))
            if status != 200 or "ndjson" not in ctype:
                text = getattr(response, "text", "") or ""
                if status == 409 and "relay_stopped" in text:
                    yield Failure("stopped", "cancelled", http_status=status)
                    return
                turn_status, failure = classify_failure(status, text) if status != 200 \
                    else ("error", "malformed_answer")
                yield Failure(failure, turn_status, http_status=status)
                return
            yield from read_stream(response.iter_content(chunk_size=None), limits=STREAM_LIMITS, cancel=cancel)
        finally:
            close = getattr(response, "close", None)
            if close:
                close()

    def stop(self, correlation_id: str) -> bool:
        """Ask the relay to abort this turn's call to MC. True when it was running."""
        if not self.connected or not correlation_id:
            return False
        try:
            response = self._post(f"{self.url}/turns/stop", data=json.dumps({"correlation_id": correlation_id}),
                                  headers={**self._auth(), "Content-Type": "application/json"},
                                  timeout=(CONNECT_TIMEOUT_S, STOP_TIMEOUT_S))
        except Exception:
            return False
        return getattr(response, "status_code", 0) == 200

    def _turn(self, req, cancel=None) -> TurnResult:
        if not self.connected:
            return TurnResult("unavailable", self.kind, failure_class="not_connected",
                              message="Master Craftsman is not connected yet")
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        payload = self._payload(req)
        if isinstance(payload, TurnResult):
            return payload
        headers = self._headers(req)
        try:
            response = self._post(f"{self.url}/chat/completions", data=payload, headers=headers,
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
        echoed = (getattr(response, "headers", None) or {}).get("X-MC-Correlation-Id")
        trace = {"correlation_echo": echoed, "relay_status": status}
        if status != 200:
            turn_status, failure = classify_failure(status, text)
            return TurnResult(turn_status, self.kind, failure_class=failure, message=f"runtime answered {status}",
                              trace=trace)
        try:
            data = json.loads(text)
            choice = data["choices"][0]
            reply = choice["message"].get("content") or ""
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return TurnResult("error", self.kind, failure_class="malformed_answer", message="unreadable answer")
        trace["response_id"] = str(data.get("id") or "")[:80]
        # Usage is deliberately ignored: OpenClaw 2026.9.6 always reports zeros
        # (usage None = not reported). The answer signals are below.
        if (data.get("error") or not isinstance(reply, str) or not reply.strip()
                or reply.strip() == OPENCLAW_NO_RESPONSE or finish not in ANSWER_FINISH_REASONS):
            return TurnResult("error", self.kind, failure_class="no_run_status",
                              message="the runtime's answer carried no reply; not treated as an answer", trace=trace)
        return TurnResult("answered", self.kind, text=reply, usage=None, trace=trace)
