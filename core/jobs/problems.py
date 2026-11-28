"""Owner-visible problems from evaluated alert state: open, track, close only on verified recovery (WS3 C3.1, first slice).

MC's ops helper feeds this module evaluated alert state and it keeps one problem per alert fingerprint. The state can
come from Grafana's alert API, Alertmanager, CloudWatch, or, in tests and the first slice, a fixture evaluator. Every
change to a problem is written through an injected **notice adapter**: the audited Guildhall write path in
production, a recording stub in tests. No remediation of any kind happens here.

    tracker = ProblemTracker(state_path, notices, clock)
    tracker.observe(evaluation)            -> list of notice events written (or queued) this time

An ``Evaluation`` is one authoritative evaluation of the rules: its own identity and evaluation time (the provider's,
never the time it was fetched), the rule-set version, whether the listing was complete, the firing alerts, and the
times at which each alert's underlying signal was last observed healthy.

**Three observation states** (Codex review of plan v0.1, finding 1; build acceptance items 2 and 4):

* ``firing``: the alert is in a valid evaluation. Opens a problem, or updates it, or re-opens one that recovered
  within ``REOPEN_S`` (same problem id).
* ``verified_recovered``: the alert is absent from **two distinct** valid evaluations (different identities, strictly
  later evaluation times) **and** its underlying signal was seen healthy after the problem was last seen firing and
  within ``FRESH_S`` of now. Only this closes a problem.
* ``observation_unknown``: an evaluation that is incomplete, older than ``FRESH_S``, dated in the future, under a
  changed rule-set version, or the same evaluation seen again. It never closes anything and never counts toward
  recovery; open problems record since when they cannot be seen.

Notice writes are idempotent (each carries a stable key) and queued in the state file until the adapter accepts
them, so a failed write is retried and a retry or a restart never creates a second problem or a second notice.
State lives in one JSON file (``state/1-2/ops-problems.json`` in production, inside the Tier 1 backup).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from core.jobs import status

FRESH_S = 600            # an evaluation or a healthy signal older than this proves nothing about now
SKEW_S = 60              # an evaluation dated beyond this in the future is not evidence
REOPEN_S = 3600          # re-firing within this of a recovery re-opens the same problem
UNKNOWN_NOTICE_S = 1800  # a problem that cannot be seen for this long gets a "cannot see" notice (once per episode)
RECOVERY_SAMPLES = 2     # distinct valid evaluations without the alert, needed to close
STATE_VERSION = 1


@dataclass(frozen=True)
class Firing:
    severity: str                       # red | yellow
    owner: str
    reason: str                         # a fixed code, e.g. "no_progress"
    value: str = ""                     # the last value, already a fixed alias or a number as text
    runbook: str = ""


@dataclass(frozen=True)
class Evaluation:
    evaluation_id: str
    evaluated_at: datetime
    rules_version: str
    complete: bool
    firing: dict = field(default_factory=dict)          # fingerprint -> Firing
    healthy_at: dict = field(default_factory=dict)      # fingerprint -> datetime the underlying signal was last healthy


def fingerprint(*parts: str) -> str:
    """A stable fingerprint from fixed alias parts (rule name, job id, host)."""
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class ProblemTracker:
    def __init__(self, state_path: str | Path, notices: Callable[[dict], bool],
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.path = Path(state_path)
        self.notices = notices
        self.clock = clock
        self.state = self._load()

    # ---- state ------------------------------------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            doc = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict) or doc.get("schema_version") != STATE_VERSION:
            doc = {"schema_version": STATE_VERSION, "rules_version": None, "last_evaluation": None,
                   "problems": {}, "outbox": [], "seq": 0}
        return doc

    def _save(self) -> None:
        status.write_json_atomic(self.path.parent, self.path.name, self.state)

    def problems(self) -> dict:
        return self.state["problems"]

    # ---- one observation --------------------------------------------------------------------------------------------

    def observe(self, ev: Evaluation) -> list[dict]:
        now = self.clock()
        written = self._flush()
        verdict = self._classify(ev, now)
        if verdict != "valid":
            for p in self.state["problems"].values():
                if p["status"] != "open":
                    continue
                if not p.get("unknown_since"):
                    p["unknown_since"] = status.iso(now)
                p["unknown_reason"] = verdict
                since = status.parse_time(p["unknown_since"])
                if not p.get("unknown_noticed") and (now - since).total_seconds() >= UNKNOWN_NOTICE_S:
                    p["unknown_noticed"] = True
                    self._queue(p, "cannot_see", now)
            if verdict == "rules_changed":
                self.state["rules_version"] = ev.rules_version     # adopted; this evaluation itself still proves nothing
            self._save()
            return written
        self.state["rules_version"] = ev.rules_version
        self.state["last_evaluation"] = {"id": ev.evaluation_id, "at": status.iso(ev.evaluated_at)}
        for fp, firing in ev.firing.items():
            self._firing(fp, firing, ev, now)
        for fp, p in self.state["problems"].items():
            if p["status"] != "open" or fp in ev.firing:
                continue
            for k in ("unknown_since", "unknown_reason", "unknown_noticed"):
                p.pop(k, None)
            healthy = ev.healthy_at.get(fp)
            seen = status.parse_time(p["last_seen_at"])
            if healthy is None or healthy <= seen or (now - healthy).total_seconds() > FRESH_S:
                p["absent_unverified"] = p.get("absent_unverified", 0) + 1     # absent, but no healthy evidence: open
                continue
            p["clear_samples"] = p.get("clear_samples", 0) + 1
            if p["clear_samples"] >= RECOVERY_SAMPLES:
                p["status"] = "recovered"
                p["recovered_at"] = status.iso(now)
                p["history"].append({"opened_at": p["opened_at"], "recovered_at": p["recovered_at"]})
                self._queue(p, "close", now)
        self._save()
        return written + self._flush()

    def _classify(self, ev: Evaluation, now: datetime) -> str:
        if not ev.complete:
            return "incomplete"
        age = (now - ev.evaluated_at).total_seconds()
        if age > FRESH_S:
            return "stale"
        if age < -SKEW_S:
            return "future"
        last = self.state.get("last_evaluation") or {}
        last_at = status.parse_time(last.get("at"))
        if last.get("id") == ev.evaluation_id or (last_at is not None and ev.evaluated_at <= last_at):
            return "same_evaluation"
        if self.state.get("rules_version") not in (None, ev.rules_version):
            return "rules_changed"
        return "valid"

    def _firing(self, fp: str, firing: Firing, ev: Evaluation, now: datetime) -> None:
        p = self.state["problems"].get(fp)
        if p and p["status"] == "recovered":
            recovered = status.parse_time(p.get("recovered_at"))
            if recovered is not None and (now - recovered).total_seconds() <= REOPEN_S:
                p.update(status="open", reopened_at=status.iso(now), clear_samples=0, absent_unverified=0)
                p.pop("recovered_at", None)
                self._queue(p, "reopen", now)
            else:
                p = None                                                 # an old, closed problem: a new one starts
        if p is None:
            p = {"id": f"{fp}-{status.iso(now)}", "fingerprint": fp, "status": "open", "opened_at": status.iso(now),
                 "history": [], "clear_samples": 0, "absent_unverified": 0}
            self.state["problems"][fp] = p
            p.update(_fields(firing), first_seen_at=status.iso(ev.evaluated_at), last_seen_at=status.iso(ev.evaluated_at))
            self._queue(p, "open", now)
            return
        changed = any(p.get(k) != v for k, v in _fields(firing).items())
        p.update(_fields(firing), last_seen_at=status.iso(ev.evaluated_at), clear_samples=0)
        for k in ("unknown_since", "unknown_reason", "unknown_noticed"):
            p.pop(k, None)
        if changed:
            self._queue(p, "update", now)                               # only when severity, reason or value moved

    # ---- notices ----------------------------------------------------------------------------------------------------

    def _queue(self, p: dict, event: str, now: datetime) -> None:
        self.state["seq"] += 1
        episode = p.get("unknown_since") if event == "cannot_see" else (p.get("reopened_at") or p["opened_at"])
        key = f"{p['id']}:{event}:{self.state['seq']}" if event == "update" else f"{p['id']}:{event}:{episode}"
        if any(n["key"] == key for n in self.state["outbox"]):
            return
        self.state["outbox"].append({"key": key, "problem_id": p["id"], "event": event, "at": status.iso(now),
                                     "severity": p.get("severity"), "owner": p.get("owner"), "reason": p.get("reason"),
                                     "value": p.get("value"), "runbook": p.get("runbook")})

    def _flush(self) -> list[dict]:
        sent, keep = [], []
        for notice in self.state["outbox"]:
            try:
                ok = bool(self.notices(dict(notice)))
            except Exception:                                    # noqa: BLE001
                ok = False
            (sent if ok else keep).append(notice)
        self.state["outbox"] = keep
        if sent:
            self._save()
        return sent


def _fields(f: Firing) -> dict:
    return {"severity": f.severity, "owner": f.owner, "reason": f.reason, "value": f.value, "runbook": f.runbook}
