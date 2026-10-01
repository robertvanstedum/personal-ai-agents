"""A streamed Master Craftsman turn on the Shop floor (streaming spec v0.2 §3,
§5, §6; v0.3 §2, §5, §6): the worker, the browser events, Stop, and the
dispatched-set.

**One dispatch per turn.** The route's guards run first; every refusal is a
normal JSON answer. Then the browser gets ``ack``, and only after ``ack`` does
the worker dispatch (the backend's iterator sends on its first ``next()``).
The dispatched-set refuses a second dispatch of the same ``request_id`` on
either endpoint for 10 minutes (``already_dispatched``, with the result when
there is one). Nothing here ever retries.

**The worker** gets everything it needs at request time (``StreamContext``):
it never reads ``request``, ``current_app``, ``cfg()`` or the session. It owns
the in-flight lock, handed over by the route, and releases it only when the
runtime's stream has settled or been stopped. A browser that leaves does not
stop it: the worker reads to the end within the deadline and keeps a real
answer, so a reload shows it. Only the limits and the owner's Stop end a run
early.

**Keeping.** The reply is kept at the finish reason (``stop`` or ``length``),
with non-blank text that is not OpenClaw's placeholder; a missing finish
reason is a truncated stream. A partial or failed stream is never kept and
never called an answer. The text that persists is ``done``'s.

**Browser events** (NDJSON, one object per line, all built here, so no token,
URL or runtime id can pass): ``ack`` (the portal's own turn id, for Stop),
``delta`` (text only), ``render`` (the server-rendered, sanitised HTML of the
text so far: at most every 500 ms, only when it changed, none past 32 KB),
``ping`` (every 10 s of silence), then one ``done`` or ``error``.

**Traces.** Every streamed turn writes its ``mc_turns.jsonl`` line (interrupted
and stopped ones too) and, for a real backend, a ``runtime-stream`` usage
record (stream_usage.py); a paid partial is then explainable.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..adapters.contract import now_iso
from .backend import REAL_KINDS, NotAnAnswer, TurnResult, keep_reply
from .openclaw import OPENCLAW_NO_RESPONSE
from .stream import STREAM_ANSWER_FINISH_REASONS, Delta, Failure, Finish, Usage

PING_S = 10.0
RENDER_EVERY_S = 0.5
RENDER_MAX_BYTES = 32 * 1024
DISPATCHED_TTL_S = 600.0
QUEUE_MAX = 4096
_log = logging.getLogger("guild_ui.mc")

# What the owner reads when a streamed turn does not end in an answer. Stop's
# spend limits are stated plainly (spec v0.3 §2). The no-spend probe P2
# (stage_b.py, 2026-09-29, OpenClaw 2026.9.6) showed OpenClaw closes its own
# call to the model endpoint when the relay aborts, so the line does not say
# the run may have finished; what was already generated may still be billed.
MESSAGES = {
    "answered": "Master Craftsman answered · kept on the record",
    "stopped": "Stopped · you may still be billed for what was already generated. Nothing was kept.",
    "deadline": "No complete answer within the deadline · it may still have run and been paid. Nothing was kept.",
    "idle": "Master Craftsman stopped sending · it may still have run and been paid. Nothing was kept.",
    "too_large": "The answer passed the size limit and was cut · it may have been paid. Nothing was kept.",
    "truncated": "The answer ended before it was complete · it may have been paid. Nothing was kept.",
    "upstream": "Master Craftsman's run failed part way · it may have been paid. Nothing was kept.",
    "no_run_status": "Master Craftsman's answer carried no reply, so it was not kept.",
    "not_kept": "Master Craftsman answered, but the answer could not be kept, so it is not shown. Treat it as unknown.",
}
PARTIAL_CLASSES = ("stopped", "deadline", "idle", "too_large", "truncated", "upstream")
RELAY_AFTER_DISPATCH = (409, 502, 504)       # relay answers that come after it called MC


# ── the dispatched-set and the running turns ──────────────────────────────────

class DispatchedSet:
    """``request_id``s dispatched in the last 10 minutes (per process)."""

    def __init__(self, ttl_s: float = DISPATCHED_TTL_S, clock=time.monotonic):
        self.ttl_s, self.clock = ttl_s, clock
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _purge(self):
        cutoff = self.clock() - self.ttl_s
        for rid in [r for r, e in self._items.items() if e["at"] < cutoff]:
            del self._items[rid]

    def claim(self, request_id: str, principal: str, turn_id: str) -> dict | None:
        """None when claimed now; otherwise a copy of the earlier dispatch."""
        with self._lock:
            self._purge()
            earlier = self._items.get(request_id)
            if earlier is not None:
                return dict(earlier)
            self._items[request_id] = {"principal": principal, "turn_id": turn_id, "state": "running",
                                       "result": None, "at": self.clock()}
            return None

    def finish(self, request_id: str | None, result: dict) -> None:
        with self._lock:
            if request_id in self._items:
                self._items[request_id].update(state="done", result=result)

    def forget(self, request_id: str | None) -> None:
        with self._lock:
            self._items.pop(request_id, None)

    def refusal(self, earlier: dict, principal: str) -> dict:
        """The already_dispatched answer (with the finished result for its owner)."""
        mine = earlier.get("principal") == principal
        return {"error": "already_dispatched", "state": earlier.get("state"),
                "result": earlier.get("result") if mine else None,
                "message": ("This note was already sent to Master Craftsman; nothing was sent again."
                            + (" It is still running." if earlier.get("state") == "running" else ""))}


class Runs:
    """The streamed turns running now, by turn id (for Stop)."""

    def __init__(self):
        self._runs: dict[str, "StreamRun"] = {}
        self._lock = threading.Lock()

    def add(self, run: "StreamRun"):
        with self._lock:
            self._runs[run.ctx.turn_id] = run

    def remove(self, turn_id: str):
        with self._lock:
            self._runs.pop(turn_id, None)

    def get(self, turn_id: str) -> "StreamRun | None":
        with self._lock:
            return self._runs.get(turn_id)


DISPATCHED = DispatchedSet()
RUNS = Runs()


# ── the worker ────────────────────────────────────────────────────────────────

@dataclass
class StreamContext:
    """Everything the worker needs, captured by the route at request time."""
    turn_id: str
    principal: str
    backend: object
    events: object                      # the backend's iterator (dispatches on its first next())
    floor: object
    reply_key: str
    context: dict                        # {area, item_ref, page} of the note
    release: Callable[[], None]          # releases the in-flight lock
    request_id: str | None = None
    turn_log: object = None
    mc_health: object = None
    header: Callable[[], dict] = lambda: {}       # the header state after the turn (no request context)
    touch: Callable[[], None] = lambda: None      # the conversation's "last used"
    annotate: Callable[[dict], None] = lambda note: None   # the reply's footer and html
    usage_folder: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


class StreamRun:
    def __init__(self, ctx: StreamContext, *, clock=time.monotonic, record_usage=None):
        self.ctx = ctx
        self.clock = clock
        self.queue: queue.Queue = queue.Queue(QUEUE_MAX)
        self.consumer_gone = threading.Event()
        self.done = threading.Event()
        self.started = False
        self.outcome: dict | None = None
        if record_usage is None:
            from .stream_usage import record_run as record_usage
        self._record_usage = record_usage
        self._thread: threading.Thread | None = None

    # The route hands the lock to the run; the worker releases it. If the
    # browser left before the worker started, the response's close does.
    def start(self):
        if self.started:
            return
        self.started = True
        RUNS.add(self)
        self._thread = threading.Thread(target=self._run, name=f"mc-stream-{self.ctx.turn_id[:8]}", daemon=True)
        self._thread.start()

    def abandon_if_not_started(self):
        """The browser left before ack was sent: nothing was dispatched."""
        if not self.started:
            self.started = True
            DISPATCHED.forget(self.ctx.request_id)
            self.ctx.release()
            self.done.set()

    def stop(self) -> bool:
        """The owner's Stop: end MiniMoi's side now, and ask the runtime to abort."""
        self.ctx.cancel.set()
        try:
            return bool(self.ctx.backend.stop(self.ctx.turn_id))
        except Exception:
            return False

    def join(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def _put(self, item, essential: bool = False):
        if self.consumer_gone.is_set():
            return
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            if essential:                       # the terminal event always finds room
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.queue.put_nowait(item)
                except queue.Full:
                    pass

    def _keep(self, text: str):
        ctx = self.ctx
        result = TurnResult("answered", ctx.backend.kind, text=text)
        try:
            kept = keep_reply(ctx.floor, result, request_id=ctx.reply_key, area=ctx.context.get("area"),
                              item_ref=ctx.context.get("item_ref"), page=ctx.context.get("page"))
        except Exception as exc:                # a store failure, or not an answer
            _log.warning("mc turn %s answered but not kept (%s)", ctx.turn_id, type(exc).__name__)
            return result, None
        if getattr(kept, "outcome", None) != "kept" or not getattr(kept, "value", None):
            _log.warning("mc turn %s answered but not kept (%s)", ctx.turn_id, getattr(kept, "outcome", None))
            return result, None
        try:
            ctx.touch()
        except Exception:
            pass
        return result, kept.value

    def _run(self):
        ctx = self.ctx
        started = self.clock()
        parts: list[str] = []
        usage: Usage | None = None
        failure: Failure | None = None
        final_at = None
        answered: TurnResult | None = None
        reply_note = None
        _log.info("mc turn %s stream start backend=%s", ctx.turn_id, ctx.backend.kind)
        try:
            for event in ctx.events:
                if isinstance(event, Delta):
                    parts.append(event.text)
                    self._put(("delta", event.text))
                elif isinstance(event, Finish):
                    if final_at is None:
                        final_at = self.clock()
                        text = "".join(parts)
                        if (event.reason in STREAM_ANSWER_FINISH_REASONS and text.strip()
                                and text.strip() != OPENCLAW_NO_RESPONSE):
                            answered, reply_note = self._keep(text)      # kept now: a disconnect cannot lose it
                        else:
                            failure = Failure("no_run_status")
                            break
                elif isinstance(event, Usage):
                    usage = event
                elif isinstance(event, Failure):
                    failure = event
                    break
            else:
                if final_at is None:
                    failure = Failure("stopped", "cancelled") if ctx.cancel.is_set() else Failure("truncated")
        except Exception:
            _log.exception("mc turn %s: the stream failed", ctx.turn_id)
            failure = failure or Failure("upstream")
        finally:
            try:
                self._finish(started, final_at, parts, usage, failure, answered, reply_note)
            finally:
                RUNS.remove(ctx.turn_id)
                ctx.release()
                self.done.set()

    def _finish(self, started, final_at, parts, usage, failure, answered, reply_note):
        ctx = self.ctx
        ended = self.clock()
        duration_ms = int(((final_at if final_at is not None else ended) - started) * 1000)
        kind = ctx.backend.kind
        if answered is not None and reply_note is not None:
            status, cls = "answered", None
        elif answered is not None:
            status, cls = "error", "not_kept"
        else:
            failure = failure or Failure("truncated")
            cls = failure.cls
            status = ("stopped" if cls == "stopped"
                      else "interrupted" if cls in PARTIAL_CLASSES and parts else failure.turn_status)
        # The header, as turn() does (a cancelled turn does not change it).
        settle = answered if answered is not None else TurnResult(
            failure.turn_status if failure.turn_status in ("error", "unavailable", "timeout_uncertain", "cancelled")
            else "error", kind, failure_class=cls)
        try:
            ctx.backend.settle(settle)
        except Exception:
            pass
        if ctx.mc_health is not None:
            ctx.mc_health.invalidate()
        if ctx.turn_log is not None:
            ctx.turn_log.record(turn_id=ctx.turn_id, status=status, backend_kind=kind, duration_ms=duration_ms,
                                failure_class=cls, reply_request_id=ctx.reply_key if status == "answered" else None,
                                mode="stream")
        # Not dispatched: the relay was never reached (not_ready), or it refused
        # before calling MC (a caller, body or busy refusal). A 409 (stopped
        # before MC's headers), 502 or 504 comes after the relay called MC, so
        # the run may have been paid and gets its trace (#275 review, finding 6).
        dispatched = failure is None or (failure.cls != "not_ready" and (
            failure.http_status is None or failure.http_status in RELAY_AFTER_DISPATCH))
        if kind in REAL_KINDS and dispatched:
            ok = status == "answered"
            self._record_usage(turn_id=ctx.turn_id, status="ok" if ok else "error",
                               prompt_tokens=usage.prompt_tokens if usage and ok else None,
                               completion_tokens=usage.completion_tokens if usage and ok else None,
                               latency_ms=duration_ms, error_class=None if ok else (cls or status),
                               folder=ctx.usage_folder)
        header = {}
        try:
            header = ctx.header() or {}
        except Exception:
            pass
        base = {"turn_id": ctx.turn_id, "backend_kind": kind, "observed_at": now_iso(),
                "mc_state": header.get("state"), "mc_header": header.get("header")}
        if status == "answered":
            try:
                ctx.annotate(reply_note)
            except Exception:
                pass
            output = usage.completion_tokens if usage is not None else None
            turn = reply_note.get("turn") if isinstance(reply_note.get("turn"), dict) else None
            if turn is not None and isinstance(output, int):
                from .usage_join import tokens_text
                turn["output_tokens"], turn["tokens_text"] = output, tokens_text(output)
                if usage.source == "stub":
                    turn["tokens_text"] += " (stub)"
            outcome = {**base, "t": "done", "status": "answered", "reply_note": reply_note,
                       "elapsed_s": round(duration_ms / 1000, 1), "output_tokens": output,
                       "message": MESSAGES["answered"]}
        else:
            message = MESSAGES.get(cls or "") or (
                f"{header.get('header') or 'Master Craftsman did not answer'}. Your note is kept; "
                "Master Craftsman did not answer.")
            outcome = {**base, "t": "error", "status": status, "failure_class": cls, "partial": bool(parts),
                       "reply_note": None, "message": message}
        self.outcome = outcome
        _log.info("mc turn %s stream end status=%s class=%s caller_gone=%s", ctx.turn_id, status, cls,
                  self.consumer_gone.is_set())
        DISPATCHED.finish(ctx.request_id, {k: v for k, v in outcome.items() if k != "t"})
        self._put(("end", outcome), essential=True)


# ── what the browser reads ────────────────────────────────────────────────────

def _line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def browser_events(run: StreamRun, *, render: Callable[[str], str], clock=time.monotonic, ping_s: float = PING_S):
    """The NDJSON the browser reads. ``ack`` first; the worker starts (and so
    dispatches) only after it. A browser that leaves ends this generator, not
    the run."""
    try:
        yield _line({"t": "ack", "turn_id": run.ctx.turn_id, "observed_at": now_iso()})
        run.start()
        text = ""
        deltas = 0
        last_render = None
        rendered = -1
        while True:
            try:
                kind, payload = run.queue.get(timeout=ping_s)
            except queue.Empty:
                yield _line({"t": "ping"})
                continue
            if kind == "delta":
                text += payload
                deltas += 1
                yield _line({"t": "delta", "text": payload})
                now = clock()
                if ((last_render is None or now - last_render >= RENDER_EVERY_S) and deltas != rendered
                        and len(text.encode("utf-8")) <= RENDER_MAX_BYTES):
                    last_render, rendered = now, deltas
                    yield _line({"t": "render", "html": render(text), "deltas": deltas})
                continue
            yield _line(payload)
            return
    finally:
        run.consumer_gone.set()


__all__ = ["DISPATCHED", "RUNS", "DispatchedSet", "Runs", "StreamContext", "StreamRun", "browser_events",
           "MESSAGES", "RENDER_EVERY_S", "RENDER_MAX_BYTES", "PING_S"]
