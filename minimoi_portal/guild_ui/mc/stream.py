"""The streaming seam's events and the shared bounded reader (streaming spec
v0.2 §4.1, reworded by v0.3 §7).

**The seam** a runtime implements is ``stream_turn()`` and these runtime-neutral
events: ``Delta(text)``, ``Finish(reason)``, ``Usage(prompt, completion)`` and
``Failure(cls)``. A non-OpenAI runtime implements ``stream_turn()`` with its own
parser and reuses only the limits.

**The bounded reader** is the OpenAI-compatible parser behind that seam, for
the relay's own NDJSON (MC, S1) and for SSE ``data:`` lines (CoS in S2):

* incremental UTF-8 decoding (a character split across chunks is kept whole);
* a wall-clock deadline and an idle limit (the caller's HTTP read timeout;
  any byte resets it), an emitted-text cap and a pending-line cap, set per
  caller; each trips as one ``Failure`` with its class;
* an allow-list: only text deltas, the finish reason and the trailing usage
  survive; everything else (ids, models, roles, tool calls) is dropped;
* a cancel event (the owner's Stop) ends the read as ``Failure("stopped")``.

Never raises: every problem is a ``Failure``.
"""
from __future__ import annotations

import codecs
import json
import re
import time
from dataclasses import dataclass
from typing import Iterable, Iterator

STREAM_ANSWER_FINISH_REASONS = ("stop", "length")     # a missing finish reason means truncated
# "deadline" and "idle" arrive from the RELAY (it cancelled its call to Master Craftsman). "local_timeout" is this
# portal giving up waiting (its own wall clock or read timeout): the relay was not told anything, so the run may
# still be going and the outcome is unknown. The two are never worded alike.
FAILURE_CLASSES = ("deadline", "idle", "too_large", "upstream", "stopped", "truncated", "local_timeout")


@dataclass(frozen=True)
class Delta:
    text: str


# OpenClaw's run id, which the MC relay forwards once, first (relay_v2 `{"t":"run"}`; MC spec v1.2 §7.1).
RUN_ID = re.compile(r"chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class RunStarted:
    """The runtime run this turn's request opened: the portal binds the turn's tool calls to it. ``replaces``: the run of the
    plan attempt this one takes over from, on a switched turn (openclaw.py)."""
    run_id: str
    replaces: str | None = None
    required: bool = False


@dataclass(frozen=True)
class Finish:
    reason: str


@dataclass(frozen=True)
class Terminal:
    receipt: dict


@dataclass(frozen=True)
class Progress:
    text: str


STEP_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
STEP_MS_MAX = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class Step:
    """One tool step of the run (relay ``{"t":"step"}``, issue #545): the tool's name, its place in the run, whether it
    began or ended, and how long it took. Never its arguments or result: the relay sends none, and nothing else is read."""
    n: int
    name: str
    phase: str                                 # "start" or "end"
    ok: bool | None = None
    ms: int | None = None


def _step(obj: dict):
    n, name, phase = obj.get("n"), obj.get("name"), obj.get("phase")
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 1000):
        return None
    if not (isinstance(name, str) and STEP_NAME.fullmatch(name)) or phase not in ("start", "end"):
        return None
    ms = obj.get("ms")
    ms = ms if isinstance(ms, int) and not isinstance(ms, bool) and 0 <= ms <= STEP_MS_MAX else None
    ok = obj.get("ok") if isinstance(obj.get("ok"), bool) else None
    return Step(n, name, phase, ok if phase == "end" else None, ms if phase == "end" else None)


@dataclass(frozen=True)
class Answer:
    """The run's final step alone, from OpenClaw's record (relay ``{"t":"answer"}``), sent before the finish only when the
    streamed text also carried narration between tool steps. The portal keeps it only if the stream ends with it."""
    text: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None
    completion_tokens: int | None
    source: str = "runtime"                    # "stub" for the scripted stub's usage


@dataclass(frozen=True)
class Failure:
    cls: str                                   # a FAILURE_CLASSES entry, or a runtime failure class
    turn_status: str = "error"                 # the TurnResult status this failure means
    http_status: int | None = None
    plan_signal: str | None = None             # the relay's declared plan-unavailable signal (core.model_routes.PLAN_SIGNALS)
    tools: str | None = None                   # with a signal: "none", "may_have_begun" or "unknown" (relay toolsSince)
    cap: bool = False                          # the runtime named a budget


class StreamRefused(Exception):
    """A pre-dispatch refusal: nothing was sent (not connected, too large)."""

    def __init__(self, failure_class: str, message: str = ""):
        super().__init__(failure_class)
        self.failure_class, self.message = failure_class, message


@dataclass(frozen=True)
class Limits:
    deadline_s: float = 125.0
    idle_s: float = 30.0                       # enforced by the caller's read timeout
    text_max: int = 256 * 1024                 # bytes of emitted text
    line_max: int = 64 * 1024                  # bytes of one pending line


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _runtime_count(value) -> int | None:
    """A token count reported by the runtime. Zero means "not reported": OpenClaw
    2026.9.6's stream usage chunk carries zeros, as its compat API does (#252,
    #253), so a 0 is unknown (null), never a count (staging, 2026-09-29: the
    stream said 0 while the gateway recorded 190 output tokens)."""
    count = _count(value)
    return count if count else None


SERVED_FIELD = re.compile(r"^[A-Za-z0-9._:/-]{1,80}$")


def served_fields(obj) -> dict | None:
    """OpenClaw's record of who served a run, as the relay read it: known fields of a plain shape, or nothing."""
    if not isinstance(obj, dict):
        return None
    fields = {k: obj[k] for k in ("provider", "model", "api", "harness")
              if isinstance(obj.get(k), str) and SERVED_FIELD.fullmatch(obj[k])}
    return fields if fields.get("provider") and fields.get("model") else None


@dataclass(frozen=True)
class Served:
    """Evidence of the model that served this turn. Consumed by the route recorder; never sent to the browser as a claim."""
    fields: dict


@dataclass(frozen=True)
class Switched:
    """This turn moved from its plan route to the declared API route. Consumed by the route recorder, which keeps the receipt
    against the route actually sent; the words for the owner arrive as text."""
    route: dict


PLAN_SIGNAL_WORDS = ("rate_limit", "plan_exhausted", "signin_expired")


def parse_relay_line(line: str):
    """One line of the MC relay's NDJSON -> an event, or None (dropped)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    kind = obj.get("t")
    if kind == "run" and isinstance(obj.get("id"), str) and RUN_ID.fullmatch(obj["id"]):
        return RunStarted(obj["id"])
    if kind == "delta" and isinstance(obj.get("text"), str) and obj["text"]:
        return Delta(obj["text"])
    if kind == "step":
        return _step(obj)
    if kind == "answer" and isinstance(obj.get("text"), str) and obj["text"].strip():
        return Answer(obj["text"])
    if kind == "finish" and isinstance(obj.get("reason"), str):
        return Finish(obj["reason"][:32])
    if kind == "terminal":
        from core.model_routes.runtime_proof import valid_receipt
        return Terminal(obj["receipt"]) if valid_receipt(obj.get("receipt")) else None
    if kind == "usage":
        return Usage(_runtime_count(obj.get("prompt_tokens")), _runtime_count(obj.get("completion_tokens")))
    if kind == "served":
        fields = served_fields(obj)
        return Served(fields) if fields else None
    if kind == "error" and isinstance(obj.get("class"), str):
        cls = obj["class"] if obj["class"] in FAILURE_CLASSES else "upstream"
        return Failure(cls, plan_signal=obj.get("signal") if obj.get("signal") in PLAN_SIGNAL_WORDS else None,
                       tools=obj.get("tools") if isinstance(obj.get("tools"), str) else None, cap=obj.get("cap") is True)
    return None


def parse_sse_line(line: str):
    """One SSE line of an OpenAI-compatible stream -> a list of events. "[DONE]"
    is the ``"done"`` marker."""
    if not line.startswith("data:"):
        return []
    data = line[5:].strip()
    if data == "[DONE]":
        return ["done"]
    try:
        chunk = json.loads(data)
    except ValueError:
        return []
    if not isinstance(chunk, dict):
        return []
    if chunk.get("error"):
        return [Failure("upstream")]
    events = []
    choices = chunk.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
    if isinstance(delta.get("content"), str) and delta["content"]:
        events.append(Delta(delta["content"]))
    if isinstance(choice.get("finish_reason"), str):
        events.append(Finish(choice["finish_reason"][:32]))
    usage = chunk.get("usage")
    if isinstance(usage, dict) and ("prompt_tokens" in usage or "completion_tokens" in usage):
        events.append(Usage(_runtime_count(usage.get("prompt_tokens")), _runtime_count(usage.get("completion_tokens"))))
    return events


def read_stream(chunks: Iterable[bytes], *, limits: Limits, fmt: str = "ndjson", cancel=None,
                clock=time.monotonic) -> Iterator:
    """Bounded events from raw byte chunks (see the module doc). Ends after a
    ``Failure``, after SSE "[DONE]", or when the chunks end."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    parse = parse_relay_line if fmt == "ndjson" else None
    start = clock()
    pending = ""
    emitted = 0

    def handle(line: str):
        nonlocal emitted
        line = line.rstrip("\r")
        events = [parse(line)] if parse else parse_sse_line(line)
        out = []
        for event in events:
            if event is None:
                continue
            if isinstance(event, Delta):
                emitted += len(event.text.encode("utf-8"))
                if emitted > limits.text_max:
                    return out + [Failure("too_large")]
            out.append(event)
        return out

    try:
        for chunk in chunks:
            if cancel is not None and cancel.is_set():
                yield Failure("stopped", "cancelled")
                return
            if clock() - start > limits.deadline_s:
                yield Failure("local_timeout", "timeout_uncertain")
                return
            if not chunk:
                continue
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                for event in handle(line):
                    if event == "done":
                        return
                    yield event
                    if isinstance(event, Failure):
                        return
            if len(pending.encode("utf-8")) > limits.line_max:
                yield Failure("too_large")
                return
        pending += decoder.decode(b"", final=True)
        if pending.strip():
            for event in handle(pending):
                if event == "done":
                    return
                yield event
                if isinstance(event, Failure):
                    return
        if cancel is not None and cancel.is_set():
            yield Failure("stopped", "cancelled")
    except Exception as exc:                  # a read timeout is the idle limit; anything else, upstream
        if cancel is not None and cancel.is_set():
            yield Failure("stopped", "cancelled")
        elif "timeout" in f"{type(exc).__name__} {exc}".lower():
            yield Failure("local_timeout", "timeout_uncertain")
        else:
            yield Failure("upstream")


__all__ = ["Delta", "Step", "RunStarted", "Finish", "Usage", "Failure", "Served", "Switched", "StreamRefused", "Limits", "read_stream", "parse_relay_line",
           "parse_sse_line", "STREAM_ANSWER_FINISH_REASONS", "FAILURE_CLASSES"]
