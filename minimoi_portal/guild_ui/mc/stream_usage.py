"""The portal's one usage-store writer (streaming spec v0.3 §4): a streamed
Master Craftsman run's own usage, as a ``runtime-stream`` record in usage
record v1 (services/usage/usage_record.py, the shared library).

The portal may write only this: ``emitter: "runtime-stream"``, ``actor: "mc"``,
``kind: "model"``, ``route: "openclaw/mc-agent"``, keyed by the turn id
(``correlation_id``). A test fails on any other emitter or actor from the
portal, and on any other module of the portal that writes to the store.

Cost is never computed here (``cost_usd`` null, ``cost_source: "none"``): the
gateway's record (U1) stays the primary source for cost and tokens, and
reports never sum across emitters. An interrupted or stopped run is
``status: "error"`` with null tokens, so a paid partial leaves a trace.
Fire and forget: nothing here ever fails a turn.
"""
from __future__ import annotations

import logging

EMITTER = "runtime-stream"
ACTOR = "mc"
ROUTE = "openclaw/mc-agent"
_log = logging.getLogger("guild_ui.mc")


def record_run(*, turn_id: str, status: str, prompt_tokens: int | None = None,
               completion_tokens: int | None = None, latency_ms: int | None = None,
               error_class: str | None = None, folder: str | None = None) -> bool:
    try:
        from services.usage import usage_record
    except Exception:                           # the library is not in this image: nothing is written
        _log.warning("mc turn %s: the usage library is unavailable; no runtime-stream record", turn_id)
        return False
    ok = status == "ok"
    fields = {"emitter": EMITTER, "actor": ACTOR, "kind": "model", "route": ROUTE,
              "status": "ok" if ok else "error", "cost_usd": None, "cost_source": "none",
              "correlation_id": turn_id, "latency_ms": latency_ms,
              "input_tokens": prompt_tokens if ok else None, "output_tokens": completion_tokens if ok else None,
              "error_class": None if ok else (error_class or "interrupted")}
    try:
        return bool(usage_record.record(folder, **fields))
    except Exception:
        _log.warning("mc turn %s: runtime-stream usage record not written", turn_id)
        return False


__all__ = ["record_run", "EMITTER", "ACTOR", "ROUTE"]
