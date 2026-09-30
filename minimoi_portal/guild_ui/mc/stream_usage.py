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

**Its own folder** (#275 review, finding 4). The portal writes only into
``usage/portal/`` (``MINIMOI_USAGE_PORTAL_DIR``, else ``$MINIMOI_USAGE_DIR/portal``),
the same v1 files and format, and never creates or appends to the shared
monthly file the gateway writes. The portal runs as root and the library
creates files 0600: had the portal created ``usage-YYYY-MM.jsonl`` first, the
non-root gateway could no longer append to it that month. On staging the
shared folder is mounted read-only into the portal and only ``usage/portal``
read-write, so the portal cannot touch the gateway's records either.
"""
from __future__ import annotations

import logging

import os

EMITTER = "runtime-stream"
ACTOR = "mc"
ROUTE = "openclaw/mc-agent"
_log = logging.getLogger("guild_ui.mc")


def portal_folder() -> str | None:
    """Where the portal's own records live (never the shared monthly file)."""
    explicit = os.environ.get("MINIMOI_USAGE_PORTAL_DIR")
    if explicit:
        return explicit
    shared = os.environ.get("MINIMOI_USAGE_DIR")
    return os.path.join(shared, "portal") if shared else None


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
    folder = folder or portal_folder()
    if not folder:
        return False
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)       # the portal's own folder, never the shared one
    except OSError:
        _log.warning("mc turn %s: the portal's usage folder cannot be made; no runtime-stream record", turn_id)
        return False
    try:
        return bool(usage_record.record(folder, **fields))
    except Exception:
        _log.warning("mc turn %s: runtime-stream usage record not written", turn_id)
        return False


def flush(timeout: float = 5.0) -> None:
    """Wait for queued records to reach the store (the operator probe reads them back)."""
    try:
        from services.usage import usage_record
        usage_record.flush(timeout)
    except Exception:
        pass


__all__ = ["record_run", "flush", "portal_folder", "EMITTER", "ACTOR", "ROUTE"]
