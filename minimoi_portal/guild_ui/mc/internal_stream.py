"""A buffered Master Craftsman turn that streams internally when MC's tools are on (spec v1.2 §7.1 step 1; Codex M1
review P2-3).

The tool path binds calls to the exact runtime run, which the portal learns only from the stream's opening run event.
A buffered frontend (the non-streaming /mc/turns route) therefore reads the backend's own stream to the end here,
attaches the run as soon as it appears, and returns the same TurnResult the buffered route already handles: nothing
is shown before the turn is complete. The answer rules are the streaming path's (a cut or empty reply is not an
answer), and the runtime-stream usage record is written exactly as for a streamed turn, so cost stays attributed.
If the backend refuses to stream before dispatch, the turn goes the ordinary buffered way and its tools stay refused.
"""
from __future__ import annotations

import logging
import time

from .backend import TurnResult
from .openclaw import OPENCLAW_NO_RESPONSE
from .stream import STREAM_ANSWER_FINISH_REASONS, Delta, Failure, Finish, RunStarted, StreamRefused, Usage

_log = logging.getLogger("guild_ui.mc")


def collect(backend, req, *, on_run, record_usage=None, clock=time.monotonic) -> TurnResult:
    try:
        events = backend.stream_turn(req)
    except StreamRefused:
        return backend.turn(req)                         # nothing was sent: the ordinary way, no run, no tools
    if record_usage is None:
        from .stream_usage import record_run as record_usage
    started, parts, finish, usage, failure, run_seen = clock(), [], None, None, None, False
    try:
        for event in events:
            if isinstance(event, RunStarted):
                if not run_seen:
                    run_seen = True
                    try:
                        on_run(event.run_id)
                    except Exception:
                        _log.warning("mc turn %s: run binding not recorded; tools refused for this turn",
                                     req.correlation_id)
            elif isinstance(event, Delta):
                parts.append(event.text)
            elif isinstance(event, Finish):
                finish = finish or event.reason
            elif isinstance(event, Usage):
                usage = event
            elif isinstance(event, Failure):
                failure = event
                break
    except Exception:
        _log.exception("mc turn %s: the internal stream failed", req.correlation_id)
        failure = failure or Failure("upstream")
    duration_ms = int((clock() - started) * 1000)
    text = "".join(parts)
    trace = {"finish_reason": finish or "none", "internal_stream": True}
    if failure is None and not (finish in STREAM_ANSWER_FINISH_REASONS and text.strip()
                                and text.strip() != OPENCLAW_NO_RESPONSE):
        failure = Failure("no_run_status") if finish is not None else Failure("truncated")
    if failure is None:
        result = TurnResult("answered", backend.kind, text=text, usage=None, trace=trace)
    else:
        status = failure.turn_status if failure.turn_status in ("error", "unavailable", "timeout_uncertain",
                                                                "cancelled") else "error"
        result = TurnResult(status, backend.kind, failure_class=failure.cls, trace=trace)
    try:
        backend.settle(result)
    except Exception:
        pass
    if failure is None or failure.cls != "not_ready":
        ok = failure is None
        record_usage(turn_id=req.correlation_id, status="ok" if ok else "error",
                     prompt_tokens=usage.prompt_tokens if usage and ok else None,
                     completion_tokens=usage.completion_tokens if usage and ok else None,
                     latency_ms=duration_ms, error_class=None if ok else failure.cls)
    return result
