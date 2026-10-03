"""The scheduler side of the agent-memory copier (Spec 160 §6, §7; M1).

Runs inside ``cos-scheduler`` only, and only when ``AGENT_MEMORY_COPIER=1`` is
set in that container's own ``environment:`` block (never the shared ``.env``),
so importing the CoS module in ``cos-bot`` starts nothing. Everything here is
best effort: failures are fixed codes, never text, and nothing can stop a chat
turn. ``run_all`` copies each enabled source that Robert has approved (the
dry-run approval record); ``status_payload`` is what ``GET /agent-memory/status``
serves: codes, times and counts only, never a file name or any content.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.agent_memory import status as copier_status
from core.agent_memory.config import ConfigError, build_source, load_config
from core.agent_memory.fsio import ensure_dir
from core.agent_memory.run import is_approved, run_source
from core.agent_memory.snapshot import iso_utc

CONFIG_ENV = "AGENT_MEMORY_CONFIG"
DEFAULT_CONFIG = "config/agent_memory_sources.json"


def enabled() -> bool:
    return os.environ.get("AGENT_MEMORY_COPIER", "").strip() == "1"


def _load():
    return load_config(os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)


def _stamp_first_seen(root: Path, names: list[str], now: datetime) -> None:
    """Give every enabled source a clock, so one that never runs turns red after 36 h."""
    for name in names:
        folder = root / name
        status = copier_status.read_status(folder)
        if status is None:
            try:
                ensure_dir(folder)
                copier_status.write_first_seen(folder, name, now)
            except Exception:  # noqa: BLE001 - the light stays unknown until it can write
                pass


def run_all(now: datetime | None = None) -> dict:
    """One daily pass over every enabled, approved source. Never raises."""
    now = now or datetime.now(timezone.utc)
    out: dict[str, str] = {}
    try:
        config = _load()
    except (ConfigError, OSError):
        return {"_config": "unreadable"}
    root = Path(config.data_root)
    enabled_names = [n for n, c in config.sources.items() if c.enabled]
    _stamp_first_seen(root, enabled_names, now)
    for name in enabled_names:
        cfg = config.sources[name]
        if not is_approved(root, name):
            out[name] = "awaiting_approval"
            continue
        try:
            result = run_source(build_source(cfg), cfg, root, now, headroom=config.headroom)
            out[name] = "ok" if result.ok else (result.code or "internal")
        except Exception:  # noqa: BLE001 - run_source never raises; this is belt and braces
            out[name] = "internal"
    return out


def _turn_log_status() -> dict:
    """The per-container turn-log status files: codes and times only."""
    from domains.cos import private_mode
    root = private_mode.turns_dir()
    out: dict[str, dict] = {}
    if root is None:
        return out
    try:
        for path in sorted((root / "_status").glob("*.json")):
            try:
                doc = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                out[path.stem] = {"readable": False}
                continue
            if isinstance(doc, dict):
                out[path.stem] = {k: doc.get(k) for k in ("last_success_at", "last_failure_at", "last_failure_code")}
    except OSError:
        pass
    return out


def status_payload(now: datetime | None = None) -> dict:
    """What the portal reads. Unknown (never a made-up green) when anything cannot be read."""
    now = now or datetime.now(timezone.utc)
    if not enabled():
        return {"enabled": False, "state": "unknown", "reason": "memory copy off", "sources": {},
                "turn_log": _turn_log_status(), "as_of": iso_utc(now)}
    try:
        config = _load()
        names = [n for n, c in config.sources.items() if c.enabled]
        statuses = copier_status.load_sources_status(Path(config.data_root), names)
        for name in names:                       # approval is part of the picture
            if not is_approved(Path(config.data_root), name) and statuses.get(name) is not None:
                statuses[name] = {**statuses[name], "awaiting_approval": True}
        light = copier_status.memory_copy_state(now, statuses)
        rows = copier_status.memory_copy_states(now, statuses)
        return {"enabled": True, "state": light["state"], "reason": light["reason"],
                "sources": {n: {"state": rows[n]["state"], "reason": rows[n]["reason"]} for n in rows},
                "turn_log": _turn_log_status(), "as_of": iso_utc(now)}
    except Exception:  # noqa: BLE001
        return {"enabled": True, "state": "unknown", "reason": "memory copy no answer", "sources": {},
                "turn_log": {}, "as_of": iso_utc(now)}


__all__ = ["enabled", "run_all", "status_payload"]
