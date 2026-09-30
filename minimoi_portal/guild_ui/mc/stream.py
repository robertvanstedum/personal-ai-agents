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
import time
from dataclasses import dataclass
from typing import Iterable, Iterator

STREAM_ANSWER_FINISH_REASONS = ("stop", "length")     # a missing finish reason means truncated
FAILURE_CLASSES = ("deadline", "idle", "too_large", "upstream", "stopped", "truncated")


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class Finish:
    reason: str


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


def parse_relay_line(line: str):
    """One line of the MC relay's NDJSON -> an event, or None (dropped)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    kind = obj.get("t")
    if kind == "delta" and isinstance(obj.get("text"), str) and obj["text"]:
        return Delta(obj["text"])
    if kind == "finish" and isinstance(obj.get("reason"), str):
        return Finish(obj["reason"][:32])
    if kind == "usage":
        return Usage(_runtime_count(obj.get("prompt_tokens")), _runtime_count(obj.get("completion_tokens")))
    if kind == "error" and isinstance(obj.get("class"), str):
        cls = obj["class"] if obj["class"] in FAILURE_CLASSES else "upstream"
        return Failure(cls)
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
                yield Failure("deadline", "timeout_uncertain")
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
            yield Failure("idle", "timeout_uncertain")
        else:
            yield Failure("upstream")


__all__ = ["Delta", "Finish", "Usage", "Failure", "StreamRefused", "Limits", "read_stream", "parse_relay_line",
           "parse_sse_line", "STREAM_ANSWER_FINISH_REASONS", "FAILURE_CLASSES"]
