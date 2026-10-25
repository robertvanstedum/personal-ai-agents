"""The limits registry (#296, slice 1): every cap the platform enforces, in one audited place that Operate shows and Master Craftsman can read.

Definitions and HARD BOUNDS live here, in code: a value in the data file can never go outside them, and the file cannot add a limit or widen a bound. The data
file ``limits.json`` holds only the owner's chosen values (``{"version": 1, "values": {"<id>": <number>}}``); a missing, unreadable or damaged file means the defaults
(never "no limit"), and says so. Changes are appended to ``audit.jsonl`` (who, what, before, after, why, when). Nothing here calls a model or the network.

Units: ``usd_notional`` is Claude Code's own cost estimate on the subscription (not a bill); ``usd_real`` would be actual API money. The relay entries are shown for
reference (``editable: False``): their source of truth is the relay's own environment until a later slice reads them from here.
"""
from __future__ import annotations

import json
import os
import time

VERSION = 1
FILE = "limits.json"
AUDIT = "audit.jsonl"

# id -> definition. ``editable`` False rows are information only in this slice. ``hard_min``/``hard_max`` bound every value, whoever sets it.
DEFINITIONS: dict[str, dict] = {
    "claude.session_cap_usd": {
        "label": "Claude Code session cap", "unit": "usd_notional", "default": 5.0, "hard_min": 0.5, "hard_max": 25.0, "editable": True,
        "what": "The most one Claude Code process may spend (Claude's own estimate) before it is restarted. It is passed to the process when it starts, so a change applies at the next restart.",
        "enforced_by": "connection manager (claude-stream provider)", "reload": "next restart", "mc_may_propose": True},
    "claude.daily_ceiling_usd": {
        "label": "Claude Code daily ceiling", "unit": "usd_notional", "default": 20.0, "hard_min": 1.0, "hard_max": 200.0, "editable": True,
        "what": "The most the persistent Claude Code session may spend in a day (Claude's own estimate). At the ceiling, new requests wait and the owner is told; they are not lost.",
        "enforced_by": "connection manager", "reload": "live", "mc_may_propose": True},
    "claude.daily_headroom_fraction": {
        "label": "Claude Code daily reserve", "unit": "fraction", "default": 0.1, "hard_min": 0.0, "hard_max": 0.5, "editable": True,
        "what": "The share of the daily ceiling kept back: new work stops being accepted once the day's spend reaches the ceiling minus this reserve, so a turn already running cannot carry the day far past the ceiling.",
        "enforced_by": "connection manager", "reload": "live", "mc_may_propose": True},
    "voice.daily_cap_usd": {
        "label": "Voice daily allowance", "unit": "usd_reserved", "default": 20.0, "hard_min": 1.0, "hard_max": 50.0, "editable": True,
        "what": "The most paid speech (reading answers aloud, and live transcription for voice and dictation) that may be started in one America/Chicago day. It is a RESERVED allowance counted when a "
                "session or reading starts (about three times the provider's published estimate), not a bill. Lowering it applies at once; raising it applies from the next day.",
        "enforced_by": "the shared voice budget (core.realtime_voice.budget), before any provider call", "reload": "live (lower) / next day (raise)", "mc_may_propose": False},
    "mc.relay_idle_s": {
        "label": "Master Craftsman silence limit", "unit": "seconds", "default": 300.0, "hard_min": 30.0, "hard_max": 300.0, "editable": False,
        "what": "How long the relay waits without a single word from Master Craftsman before it cancels a turn (its model may think and read for a while first).",
        "enforced_by": "MC relay (launchd environment MC_RELAY_IDLE_MS)", "reload": "relay restart", "mc_may_propose": False},
    "mc.relay_deadline_s": {
        "label": "Master Craftsman turn deadline", "unit": "seconds", "default": 300.0, "hard_min": 30.0, "hard_max": 300.0, "editable": False,
        "what": "The longest any one chat turn may take, however active.",
        "enforced_by": "MC relay", "reload": "relay restart", "mc_may_propose": False},
}


class Limits:
    def __init__(self, directory: str | None):
        self.directory = directory
        self.error = ""                                        # why the file is not being used, when it is not (shown, never silent)

    # ── reading ──
    def _path(self, name: str) -> str | None:
        return os.path.join(self.directory, name) if self.directory else None

    def _values(self) -> dict:
        path = self._path(FILE)
        self.error = ""
        if not path:
            self.error = "no limits folder configured; defaults in use"
            return {}
        try:
            with open(path, "rb") as fh:
                raw = fh.read(256 * 1024)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            self.error = f"limits file unreadable ({type(exc).__name__}); defaults in use"
            return {}
        try:
            doc = json.loads(raw.decode("utf-8"))
            if doc.get("version") != VERSION or not isinstance(doc.get("values"), dict):
                raise ValueError("shape")
            return doc["values"]
        except (ValueError, UnicodeDecodeError, AttributeError):
            self.error = "limits file damaged; defaults in use"
            return {}

    @staticmethod
    def _clamp(limit_id: str, value) -> float:
        d = DEFINITIONS[limit_id]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            return float(d["default"])
        return float(min(d["hard_max"], max(d["hard_min"], value)))

    def value(self, limit_id: str) -> float:
        """The effective value: the file's number clamped into the hard bounds, or the default. Never raises for a known id."""
        d = DEFINITIONS[limit_id]
        if not d["editable"]:
            return float(d["default"])
        return self._clamp(limit_id, self._values().get(limit_id, d["default"]))

    def snapshot(self) -> dict:
        """Everything Operate and Master Craftsman need: each limit's definition and effective value, and any file problem."""
        vals = self._values()
        rows = []
        for limit_id, d in DEFINITIONS.items():
            row = {"id": limit_id, **{k: d[k] for k in ("label", "unit", "what", "enforced_by", "reload", "editable", "hard_min", "hard_max", "mc_may_propose")}}
            row["default"] = d["default"]
            row["value"] = self._clamp(limit_id, vals.get(limit_id, d["default"])) if d["editable"] else float(d["default"])
            row["is_default"] = (not d["editable"]) or limit_id not in vals
            rows.append(row)
        return {"version": VERSION, "limits": rows, "error": self.error}

    # ── changing (owner actions; the portal's Operate screen calls this in a later slice, the operator can today) ──
    def set(self, limit_id: str, value, *, by: str, reason: str = "") -> dict:
        d = DEFINITIONS.get(limit_id)
        if d is None or not d["editable"]:
            raise ValueError("that limit cannot be changed here")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not (d["hard_min"] <= value <= d["hard_max"]):
            raise ValueError(f"a value between {d['hard_min']:g} and {d['hard_max']:g} is needed")
        if not self.directory:
            raise ValueError("no limits folder configured")
        os.makedirs(self.directory, exist_ok=True)
        vals = dict(self._values())
        before = self._clamp(limit_id, vals.get(limit_id, d["default"]))
        vals[limit_id] = float(value)
        tmp = self._path(FILE) + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps({"version": VERSION, "values": vals}, indent=2).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._path(FILE))
        self._audit({"event": "set", "id": limit_id, "before": before, "after": float(value), "by": str(by)[:60], "reason": str(reason)[:300]})
        return {"id": limit_id, "before": before, "after": float(value)}

    def _audit(self, event: dict) -> None:
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}, separators=(",", ":"), sort_keys=True)
        fd = os.open(self._path(AUDIT), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
