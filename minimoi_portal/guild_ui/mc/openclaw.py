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
bounded reader (stream.py) reads it: a 310 s wall clock, 305 s idle (the read
timeout), 256 KB of text, a 64 KB line. ``stop()`` asks the relay to abort the
turn (POST /v1/turns/stop). A non-200 from the relay is classified exactly as
the non-streaming path's. Both portal paths outlast the relay's 300 s budget,
including silence while OpenClaw works through tools. Timeout remains uncertain;
no turn is automatically retried, with one exception.

Plan failover (Robert, 6 Nov 2026; docs/runbooks/model_routes_plan.md): a plan route that declares an API target moves to it,
once, within the turn, when the relay reports a declared plan-unavailable signal and that no tool began. Doubt about tools
stops the turn and says so. Any other error is reported as ever. The same session and request go to the target, the next turn
tries the plan again, and the conversation's choice is not touched.
"""
from __future__ import annotations

import hashlib
import re
import json
import time
from uuid import uuid4
from urllib.parse import urlparse

from collections import namedtuple

from core.model_routes import switch_words, switched_route

from .backend import Health, MasterCraftsmanBackend, TurnResult
from .stream import (PLAN_SIGNAL_WORDS, Delta, Failure, Finish, Limits, RunStarted, Served, Step, StreamRefused, Switched, Usage,
                     read_stream, served_fields, Terminal, Progress)

# What the header needs to remember about the last turn: its status and failure class, never its reply text, usage or trace
# (a Private answer must not stay in this process's memory after the request ends; Codex review, 7 Oct).
_Settled = namedtuple("_Settled", "status failure_class")

MODEL = "openclaw/mc-agent"
# The conversation lane's agent. The relay (v2) allows exactly these two; mc-chat is the lean, read-only one that proposes jobs.
# Unset, chat turns go to mc-agent exactly as before.
CHAT_MODELS = ("openclaw/mc-agent", "openclaw/mc-chat")
USER_PREFIX = "guild-mc:"
MAX_REQUEST_BODY_BYTES = 262_144
CONNECT_TIMEOUT_S = 5
IN_FLIGHT_WAIT_S = 45         # Master Craftsman does one turn at a time (Rooms turns count). A note that arrives meanwhile waits this long for its turn instead of failing at once.
IN_FLIGHT_POLL_S = 1.5
TURN_DEADLINE_S = 310
HEALTH_TIMEOUT_S = 5
# OpenClaw can send no headers or bytes while using tools. Keep the portal
# outside the relay's 300 s deadline so its classified failure can arrive first.
STREAM_LIMITS = Limits(deadline_s=310.0, idle_s=305.0, text_max=256 * 1024, line_max=64 * 1024)
STOP_TIMEOUT_S = 5
# OpenClaw 2026.9.6 (dist/openai-http: the non-streaming chat completion)
# answers 200 with this text when the agent run produced no reply text, and
# sets finish_reason to "stop", "length" or (pending client tools) "tool_calls".
OPENCLAW_NO_RESPONSE = "No response from OpenClaw."
ANSWER_FINISH_REASONS = (None, "stop", "length")
# Spec 164 §9: a direct-path voice consult (mc_voice_direct) is MC's typed turn carrying this server-held marker
# (api.run_mc_turn host_channel). It is an object, so no browser value can equal it; the adapter writes these words.
VOICE_MARKER = object()
VOICE_CHANNEL_WORDS = "voice: the owner's spoken words, as transcribed; the reply is spoken aloud"


def _root(url: str) -> str:
    """http://host:port/v1 -> http://host:port"""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


KEY_REFUSED_MARKERS = ("401", "invalid api key", "authentication", "unauthorized",
                       # LiteLLM with no key database (stages A and B): a non-master key
                       # cannot be looked up and is refused before routing.
                       "no connected db", "no_db_connection")


def parse_served(raw) -> dict | None:
    """The relay's X-MC-Served header (OpenClaw's own record of the run), checked as stream.parse_relay_line checks its event."""
    try:
        return served_fields(json.loads(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _plan_headers(response) -> tuple[str | None, str | None]:
    """What the relay judged about a non-200: its declared signal, and whether a tool may have begun."""
    headers = getattr(response, "headers", None) or {}
    signal = headers.get("X-MC-Plan-Signal")
    return (signal, headers.get("X-MC-Plan-Tools")) if signal in PLAN_SIGNAL_WORDS else (None, None)


def classify_failure(status: int, body: str) -> tuple[str, str]:
    """(turn status, failure class) for a non-200 answer from the runtime."""
    text = (body or "").lower()
    if "relay_route_refused" in text:
        return "unavailable", "route_refused"
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
                 http_get=None, http_post=None, chat_model: str | None = None):
        if chat_model and chat_model not in CHAT_MODELS:
            raise ValueError(f"MC_RUNTIME_CHAT_MODEL must be one of {', '.join(CHAT_MODELS)}")
        self.chat_model = chat_model or MODEL
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
        self.clock, self.sleep = time.monotonic, time.sleep
        self._last = None          # the last completed turn on this process (for honest "live")

    @classmethod
    def from_env(cls, environ, *, http_get=None, http_post=None):
        return cls(environ.get("MC_RUNTIME_URL"), environ.get("MC_RUNTIME_TOKEN"),
                   ready_path=environ.get("MC_RUNTIME_READY_PATH"), http_get=http_get, http_post=http_post,
                   chat_model=(environ.get("MC_RUNTIME_CHAT_MODEL") or "").strip() or None)

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

    def session_user(self, conversation_id: str) -> str:
        """The ``user`` a turn in this conversation is sent with; OpenClaw derives the session key
        from it, and MC's tool path binds tool calls through it (core/mc_tools/binding.py)."""
        return self._user(conversation_id)

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
            self._last = _Settled(result.status, result.failure_class)

    def _payload(self, req, *, stream: bool = False) -> bytes | TurnResult:
        context = {k: v for k, v in (req.context or {}).items() if v not in (None, "") and k != "host_channel"}
        # A host channel is a server-held object, never JSON: only this adapter's own voice marker is translated, into
        # plain words. Anything else under that key (another host's sentinel, a string) is dropped, never serialized.
        if (req.context or {}).get("host_channel") is VOICE_MARKER:
            context["channel"] = VOICE_CHANNEL_WORDS
        content = req.text if not context else f"[Shop floor context: {json.dumps(context, sort_keys=True)}]\n{req.text}"
        body = {"model": self.chat_model, "user": self._user(req.conversation_id),
                "messages": [{"role": "user", "content": content}]}
        if stream:
            body["stream"] = True
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BODY_BYTES:
            return TurnResult("refused", self.kind, failure_class="request_too_large",
                              message="The turn is larger than 256 KB; nothing was sent")
        return payload

    def _headers(self, req, route: dict | None = None) -> dict:
        headers = {**self._auth(), "Content-Type": "application/json"}
        if req.correlation_id:
            headers["X-MC-Correlation-Id"] = req.correlation_id
        route = route or req.route or {}
        if route.get("runtime_override"):                # the route id only: the relay alone maps it to a model
            headers["X-MC-Route"] = route["route_id"]
        return headers

    def stream_turn(self, req, cancel=None):
        """Pre-dispatch checks now (StreamRefused: nothing was sent); the
        returned iterator dispatches on its first next()."""
        if not self.connected:
            raise StreamRefused("not_connected", "Master Craftsman is not connected yet")
        payload = self._payload(req, stream=True)
        if isinstance(payload, TurnResult):
            raise StreamRefused(payload.failure_class, payload.message)
        if (req.route or {}).get("depth"):
            return self._stream_depth(req, payload, cancel)
        return self._stream_with_failover(req, payload, cancel)

    def _stream_depth(self, req, payload, cancel):
        from core.model_routes.depth_control import ControlBuffer, InvalidDepthControl, instruction, DEEP_ANSWER_INSTRUCTION
        from core.model_routes.runtime_proof import unused_completed
        depth = req.route["depth"]
        def deeper_payload():
            body = json.loads(payload)
            body["messages"].insert(0, {"role": "system", "content": DEEP_ANSWER_INSTRUCTION})
            encoded = json.dumps(body, ensure_ascii=False).encode()
            if len(encoded) > MAX_REQUEST_BODY_BYTES:
                return None
            return encoded
        if not depth.get("allow_continuation"):
            if depth["kind"] == "deep":
                payload = deeper_payload()
                if payload is None:
                    yield Failure("request_too_large", "refused")
                    return
                yield Progress("I’ll take a closer look at this.")
            yield from self._stream(payload, self._headers(req), cancel)
            return
        if not callable(req.prepare_depth):
            yield Failure("depth_binding_unavailable", "unavailable")
            return
        nonce = uuid4().hex
        body = json.loads(payload)
        body["messages"].insert(0, {"role": "system", "content": instruction(nonce)})
        first = json.dumps(body, ensure_ascii=False).encode()
        if len(first) > MAX_REQUEST_BODY_BYTES:
            yield Failure("request_too_large", "refused")
            return
        control = ControlBuffer(nonce)
        run_id, proof, finished, tail = None, None, False, []
        quick_attempt = {"route_id": req.route["route_id"], "run_id": None, "outcome": "dispatch_started",
                         "input_tokens": None, "output_tokens": None, "served": None}
        req.route["attempts"] = [quick_attempt]
        try:
            for event in self._stream(first, self._headers(req), cancel):
                if isinstance(event, RunStarted):
                    run_id = event.run_id
                    quick_attempt["run_id"] = run_id
                    yield event
                elif isinstance(event, Delta):
                    text = control.feed(event.text)
                    if text:
                        yield Delta(text)
                elif isinstance(event, Step):                 # shown as it happens, not held with the finish
                    yield event
                elif isinstance(event, Terminal):
                    proof = event.receipt
                elif isinstance(event, Failure):
                    quick_attempt["outcome"] = "uncertain"
                    yield event
                    return
                elif isinstance(event, Finish):
                    finished = event.reason == "stop"
                    quick_attempt["outcome"] = event.reason
                    tail.append(event)
                else:
                    if isinstance(event, Usage):
                        quick_attempt.update(input_tokens=event.prompt_tokens, output_tokens=event.completion_tokens)
                    elif isinstance(event, Served):
                        quick_attempt["served"] = event.fields
                    tail.append(event)
            signal = control.finish(completed=finished)
        except InvalidDepthControl:
            quick_attempt["outcome"] = "invalid_control"
            yield Failure("invalid_depth_control", "error")
            return
        if signal is None:
            yield from tail
            return
        quick_attempt["outcome"] = "depth_requested"
        if (not run_id or not unused_completed(proof) or proof["run"] != run_id
                or (cancel is not None and cancel.is_set())):
            yield Failure("depth_completion_uncertain", "error")
            return
        try:
            prepared = req.prepare_depth(run_id, nonce, proof)
        except Exception:
            prepared = False
        if not prepared:
            yield Failure("depth_transition_refused", "error")
            return
        if cancel is not None and cancel.is_set():
            yield Failure("stopped", "cancelled")
            return
        deep = {**depth["continuation_route"], "failover": None}
        first_usage = next((e for e in tail if isinstance(e, Usage)), None)
        deep["attempts"] = req.route["attempts"]
        deep["attempts"].append({"route_id": deep["route_id"], "run_id": None, "outcome": "dispatch_started",
                                 "input_tokens": None, "output_tokens": None, "served": None})
        yield Progress("I’ll take a closer look at this.")
        yield Switched(deep)
        second_usage = None
        deep_payload = deeper_payload()
        if deep_payload is None:
            yield Failure("request_too_large", "refused")
            return
        for event in self._stream(deep_payload, self._headers(req, deep), cancel):
            if isinstance(event, RunStarted):
                deep["attempts"][-1].update(run_id=event.run_id, outcome="started")
                yield RunStarted(event.run_id, replaces=run_id, required=True)
            elif isinstance(event, Usage):
                second_usage = event
                deep["attempts"][-1].update(input_tokens=event.prompt_tokens, output_tokens=event.completion_tokens)
            else:
                if isinstance(event, Served):
                    deep["attempts"][-1]["served"] = event.fields
                elif isinstance(event, Finish):
                    deep["attempts"][-1]["outcome"] = event.reason
                elif isinstance(event, Failure):
                    deep["attempts"][-1]["outcome"] = "uncertain"
                yield event
        def total(field):
            values = [getattr(u, field) if u else None for u in (first_usage, second_usage)]
            return sum(values) if all(v is not None for v in values) else None
        yield Usage(total("prompt_tokens"), total("completion_tokens"))

    def _stream_with_failover(self, req, payload: bytes, cancel):
        """One attempt, and at most one more. Nothing here can start a third: the moved route has no failover."""
        route, plan_run, down = req.route or {}, None, None
        for event in self._stream(payload, self._headers(req), cancel):
            if isinstance(event, RunStarted):
                plan_run = event.run_id
            if isinstance(event, Failure) and event.plan_signal:
                down = event
                break
            yield event
        if down is None:
            return
        target = route.get("failover")
        if not target:
            yield Failure(down.cls, down.turn_status, down.http_status)     # no switch declared: reported as ever
            return
        if down.tools != "none":
            yield Failure("plan_switch_uncertain", "error", down.http_status)
            return
        moved = switched_route(route, down.plan_signal)
        yield Switched(moved)
        answered, served, tail = False, None, []
        for event in self._stream(payload, self._headers(req, moved), cancel):
            if isinstance(event, RunStarted):
                yield RunStarted(event.run_id, replaces=plan_run or event.run_id)
            elif isinstance(event, Failure):
                refused = (event.http_status is not None and event.http_status not in (502, 504)      # those came after a call
                           and event.cls not in ("stopped", "not_ready", "one_turn_in_flight"))
                unused = not answered and (refused or ((event.plan_signal or event.cap) and event.tools == "none"))
                yield Failure("plan_and_api_unavailable", "unavailable", event.http_status) if unused else event
                return
            elif isinstance(event, (Finish, Usage, Served)):    # held: the sentence below goes in before the finish
                served = event.fields if isinstance(event, Served) else served
                tail.append(event)
            else:
                answered = answered or isinstance(event, Delta)
                yield event
        if answered and any(isinstance(event, Finish) for event in tail):
            words = switch_words(moved, served)
            if req.route_notice is None or req.route_notice(moved, words):
                yield Delta("\n\n" + words)
        yield from tail

    def _stream(self, payload: bytes, headers: dict, cancel):
        if cancel is not None and cancel.is_set():
            yield Failure("stopped", "cancelled")
            return
        waited_until = self.clock() + IN_FLIGHT_WAIT_S
        while True:
            try:
                response = self._post(f"{self.url}/chat/completions", data=payload, headers=headers, stream=True,
                                       timeout=(CONNECT_TIMEOUT_S, STREAM_LIMITS.idle_s))
            except Exception as exc:
                name = type(exc).__name__
                if "Timeout" in name and "Connect" not in name:
                    yield Failure("local_timeout", "timeout_uncertain")
                else:
                    yield Failure("not_ready", "unavailable")
                return
            # Only a "one turn at a time" answer is waited out, and only nothing-was-sent can be: the relay answered 429 before starting any run.
            if getattr(response, "status_code", 0) == 429 and self.clock() < waited_until \
                    and classify_failure(429, getattr(response, "text", "") or "") == ("duplicate_in_progress", "one_turn_in_flight"):
                close = getattr(response, "close", None)
                if close:
                    close()
                if cancel is not None and hasattr(cancel, "wait"):
                    stopped = cancel.wait(IN_FLIGHT_POLL_S)         # wakes at once if the owner stops the turn
                else:
                    self.sleep(IN_FLIGHT_POLL_S)
                    stopped = False
                if stopped:
                    yield Failure("stopped", "cancelled")
                    return
                continue
            break
        try:
            status = getattr(response, "status_code", 0)
            ctype = str((getattr(response, "headers", None) or {}).get("Content-Type", ""))
            if status != 200 or "ndjson" not in ctype:
                text = getattr(response, "text", "") or ""
                signal, tools = _plan_headers(response)
                if status != 200 and signal:
                    yield Failure(classify_failure(status, text)[1], classify_failure(status, text)[0], http_status=status,
                                  plan_signal=signal, tools=tools)
                    return
                if status == 409 and "relay_stopped" in text:
                    yield Failure("stopped", "cancelled", http_status=status)
                    return
                if status == 504 and "relay_timeout" in text:        # the relay's deadline or idle, before MC's headers
                    yield Failure("deadline", "timeout_uncertain", http_status=status)
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

    def run_end_receipt(self, run_id: str):
        """The runtime's signed end receipt for one run, from the relay, or None. Content-free; the caller verifies it."""
        if not self.connected or not isinstance(run_id, str) or not re.fullmatch(r"chatcmpl_[0-9a-f-]{36}", run_id):
            return None
        try:
            response = self._get(f"{self.url}/runs/{run_id}/terminal", headers=self._auth(),
                                 timeout=(CONNECT_TIMEOUT_S, STOP_TIMEOUT_S))
        except Exception:
            return None
        if getattr(response, "status_code", 0) != 200:
            return None
        try:
            return (response.json() or {}).get("receipt")
        except ValueError:
            return None

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
        """The buffered path's one attempt, and at most one more (the same rules as the stream's)."""
        route = req.route or {}
        result = self._attempt(req, cancel, route)
        signal = (result.trace or {}).get("plan_signal")
        target = route.get("failover")
        if not signal or not target:
            return result
        if result.trace.get("plan_tools") != "none":
            return TurnResult("error", self.kind, failure_class="plan_switch_uncertain", trace=result.trace,
                              message="The plan is unavailable and a tool may have started; nothing was sent again")
        moved = switched_route(route, signal)
        second = self._attempt(req, cancel, moved)
        trace = {**(second.trace or {}), "route": moved}
        if second.status == "answered":
            words = switch_words(moved, trace.get("served"))
            show = req.route_notice is None or req.route_notice(moved, words)
            return TurnResult("answered", self.kind, text=f"{second.text}\n\n{words}" if show else second.text,
                              usage=second.usage, trace=trace)
        if second.status in ("unavailable",) or trace.get("plan_signal"):
            return TurnResult("unavailable", self.kind, failure_class="plan_and_api_unavailable", trace=trace,
                              message="The plan and the API route are both unavailable")
        return TurnResult(second.status, self.kind, text=second.text, failure_class=second.failure_class,
                          message=second.message, usage=second.usage, trace=trace)

    def _attempt(self, req, cancel, route) -> TurnResult:
        if not self.connected:
            return TurnResult("unavailable", self.kind, failure_class="not_connected",
                              message="Master Craftsman is not connected yet")
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        payload = self._payload(req)
        if isinstance(payload, TurnResult):
            return payload
        headers = self._headers(req, route)
        try:
            response = self._post(f"{self.url}/chat/completions", data=payload, headers=headers,
                                  timeout=(CONNECT_TIMEOUT_S, TURN_DEADLINE_S))
        except Exception as exc:
            name = type(exc).__name__
            if "Timeout" in name and "Connect" not in name:
                return TurnResult("timeout_uncertain", self.kind, failure_class="local_timeout",
                                  message="No answer received before this page stopped waiting; the outcome is unknown")
            return TurnResult("unavailable", self.kind, failure_class="not_ready",
                              message="Master Craftsman's runtime did not answer")
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        status = getattr(response, "status_code", 0)
        text = getattr(response, "text", "") or ""
        echoed = (getattr(response, "headers", None) or {}).get("X-MC-Correlation-Id")
        trace = {"correlation_echo": echoed, "relay_status": status}
        served = parse_served((getattr(response, "headers", None) or {}).get("X-MC-Served"))
        if served:
            trace["served"] = served
        if status != 200:
            turn_status, failure = classify_failure(status, text)
            signal, tools = _plan_headers(response)
            if signal:
                trace.update(plan_signal=signal, plan_tools=tools)
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
        trace["finish_reason"] = str(finish) if finish else "none"            # server-side only; lets a caller tell a complete answer ("stop") from a cut one
        # Usage is deliberately ignored: OpenClaw 2026.9.6 always reports zeros
        # (usage None = not reported). The answer signals are below.
        if (data.get("error") or not isinstance(reply, str) or not reply.strip()
                or reply.strip() == OPENCLAW_NO_RESPONSE or finish not in ANSWER_FINISH_REASONS):
            return TurnResult("error", self.kind, failure_class="no_run_status",
                              message="the runtime's answer carried no reply; not treated as an answer", trace=trace)
        return TurnResult("answered", self.kind, text=reply, usage=None, trace=trace)
