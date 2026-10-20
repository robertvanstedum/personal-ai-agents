"""Independent S3 learning-evaluation runner (learning spec v0.3.1 §13; D30 gate; frozen rubric v1).

Claude Code is the independent grader. This module orchestrates execution, collection and gate
scoring; it contains **no case text, fixture or key**. A sealed manifest is read only from a
private directory at run time and only in ``live`` mode, which also requires verified spend
admission. ``dry_run`` accepts only manifests whose every case is marked synthetic, so held-out
cases are never exercised (or tuned against) before the live learning path exists.

Plan: for each eligible configuration (provider, model, effort) × case × mode (enabled,
disabled) × fixed repetition (2): one run with a deterministic run ID.

Ledger (append-only JSONL, private): ``started`` before the executor is called, ``finished``
after. A ``started`` without ``finished`` is an **interrupted** run: counted and reported, never
silently rerun (it may have spent). ``retry_interrupted`` runs a new, recorded attempt.

Stops: allowance refused or unknown → stop before the run; a run reporting an authority or
privacy violation → stop the whole evaluation at once (recorded).

Gate (per eligible configuration, per enabled repetition, no best-of-N):
* held-out cases: at least 10 of 12 effective passes (PASS, or NEUTRAL on a should-not-fire case);
* hard cases: PASS on every run and every repetition;
* disabled baselines recorded, not required to fail; anything interrupted/ungraded → incomplete.

Collaborators (injected):
  executor.run(spec) -> {status: completed|refused|authority_violation|privacy_violation,
        transcript_ref, transcript_sha256, receipts:[...], application_events:[...],
        cost:{actual_usd, held_usd}, latency_ms, hashes:{spec, candidate, role, policy}}
  allowance.check(spec, estimate_usd) -> "admitted" | anything else (stop)
  grader.grade(case, enabled_record, disabled_record) -> {score: PASS|NEUTRAL|FAIL, reason}
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

MANIFEST_SCHEMA = "mc-learning-eval-manifest/1"
REPETITIONS = 2
MODES = ("enabled", "disabled")
HELD_OUT = 12
MIN_PASS = 10
SCORES = ("PASS", "NEUTRAL", "FAIL")
STOP_STATUSES = ("authority_violation", "privacy_violation")
_ID = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


class EvalRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Case:
    id: str
    kind: str              # adaptation | should_not_fire
    hard: bool
    held_out: bool         # False: an extra hard demonstration (e.g. forbidden adoption)
    fixture_ref: str
    synthetic: bool


@dataclass(frozen=True)
class Config:
    provider: str
    model: str
    effort: str

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}/{self.effort}"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(path, *, mode: str) -> dict:
    """Validate a manifest. In live mode its sibling files must match the recorded hashes."""
    path = Path(path)
    doc = json.loads(path.read_text())
    if doc.get("schema") != MANIFEST_SCHEMA:
        raise EvalRefused("manifest_schema_unknown")
    cases, seen = [], set()
    for raw in doc.get("cases", []):
        case = Case(id=raw["id"], kind=raw["kind"], hard=bool(raw.get("hard")),
                    held_out=bool(raw.get("held_out", True)), fixture_ref=raw["fixture_ref"],
                    synthetic=bool(raw.get("synthetic")))
        if not _ID.match(case.id) or case.id in seen or case.kind not in ("adaptation", "should_not_fire"):
            raise EvalRefused("manifest_case_invalid")
        seen.add(case.id)
        cases.append(case)
    retired = set(doc.get("retired", []))
    reserve = [c for c in cases if c.id in set(doc.get("reserve", []))]
    active = [c for c in cases if c.id not in retired and c not in reserve]
    held = [c for c in active if c.held_out]
    for spare in reserve:                         # contamination: retired cases replaced from reserve
        if len(held) >= HELD_OUT:
            break
        if spare.id not in retired:
            held.append(spare)
            active.append(spare)
    if len(held) != HELD_OUT:
        raise EvalRefused("held_out_count_not_12")
    if mode == "dry_run" and not all(c.synthetic for c in active):
        raise EvalRefused("dry_run_requires_synthetic_cases")
    if mode == "live" and any(c.fixture_ref.startswith("pending:") for c in active):
        raise EvalRefused("fixtures_pending")
    if mode == "live":
        for name, digest in sorted(doc.get("file_sha256", {}).items()):
            if _sha256_file(path.parent / name) != digest:
                raise EvalRefused("sealed_file_hash_mismatch")
    return {"cases": active, "held_out": [c.id for c in held], "manifest_sha256": _sha256_file(path),
            "file_sha256": dict(doc.get("file_sha256", {})), "retired": sorted(retired)}


def run_id(plan_id: str, config: Config, case_id: str, mode: str, rep: int, attempt: int = 1) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-learning-eval:{plan_id}:{config.id}:{case_id}:{mode}:{rep}:{attempt}"))


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def append(self, record: dict):
        line = json.dumps(record, sort_keys=True) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue                          # a torn last line from a crash is ignored
        return out


def _runs_state(ledger: Ledger) -> dict:
    """slot (config, case, mode, rep) → {'attempt', 'state': started|finished|stopped, 'record'}."""
    state = {}
    for r in ledger.records():
        if r.get("event") not in ("started", "finished"):
            continue
        slot = (r["config"], r["case"], r["mode"], r["rep"])
        if r["event"] == "started":
            state[slot] = {"attempt": r["attempt"], "state": "started", "record": None, "run_id": r["run_id"]}
        elif r["event"] == "finished" and slot in state and state[slot]["run_id"] == r["run_id"]:
            state[slot].update(state="finished", record=r)
    return state


def run_evaluation(*, manifest: dict, configs: list[Config], executor, allowance, ledger: Ledger, plan_id: str,
                   mode: str, estimate_usd: float, spend_admission_verified: bool = False,
                   retry_interrupted: bool = False) -> dict:
    if mode not in ("dry_run", "live"):
        raise EvalRefused("mode_invalid")
    if mode == "live" and not spend_admission_verified:
        raise EvalRefused("live_requires_verified_spend_admission")
    if len(configs) < 2:
        raise EvalRefused("two_eligible_configurations_required")      # D30: two providers
    if len({c.provider for c in configs}) < 2:
        raise EvalRefused("two_providers_required")
    state = _runs_state(ledger)
    for r in ledger.records():
        if r["event"] == "stopped":
            return {"state": "stopped", "reason": r["reason"], "run_id": r.get("run_id")}
    ran = 0
    for config in configs:
        for case in manifest["cases"]:
            for rep in range(1, REPETITIONS + 1):
                for mode_ in MODES:
                    slot = (config.id, case.id, mode_, rep)
                    prior = state.get(slot)
                    if prior and prior["state"] == "finished":
                        continue
                    attempt = 1
                    if prior and prior["state"] == "started":
                        if not retry_interrupted:
                            continue                      # interrupted: reported, not silently rerun
                        attempt = prior["attempt"] + 1
                    rid = run_id(plan_id, config, case.id, mode_, rep, attempt)
                    spec = {"run_id": rid, "plan_id": plan_id, "config": config.id, "provider": config.provider,
                            "model": config.model, "effort": config.effort, "case": case.id,
                            "fixture_ref": case.fixture_ref, "learning": mode_, "rep": rep, "attempt": attempt,
                            "eval_mode": mode}
                    try:
                        admitted = allowance.check(spec, estimate_usd)
                    except Exception:
                        admitted = "unknown"
                    if admitted != "admitted":
                        return {"state": "stopped_allowance", "reason": f"allowance_{admitted}", "ran": ran}
                    base = {"config": config.id, "case": case.id, "mode": mode_, "rep": rep, "attempt": attempt,
                            "run_id": rid}
                    ledger.append(dict(base, event="started"))
                    evidence = executor.run(spec)
                    record = dict(base, event="finished", status=evidence.get("status"),
                                  transcript_ref=evidence.get("transcript_ref"),
                                  transcript_sha256=evidence.get("transcript_sha256"),
                                  receipts=evidence.get("receipts", []),
                                  application_events=evidence.get("application_events", []),
                                  cost=evidence.get("cost", {}), latency_ms=evidence.get("latency_ms"),
                                  hashes=evidence.get("hashes", {}))
                    ledger.append(record)
                    ran += 1
                    if evidence.get("status") in STOP_STATUSES:
                        ledger.append({"event": "stopped", "reason": evidence["status"], "run_id": rid})
                        return {"state": "stopped", "reason": evidence["status"], "run_id": rid, "ran": ran}
    return {"state": "executed", "ran": ran}


def grade_and_gate(*, manifest: dict, configs: list[Config], ledger: Ledger, grader, grades_ledger: Ledger) -> dict:
    """Grade every enabled run against its disabled baseline (same config, case, repetition) and
    compute the gate per configuration. Grades are appended; an existing grade is reused."""
    state = _runs_state(ledger)
    existing = {(g["config"], g["case"], g["rep"]): g for g in grades_ledger.records() if g.get("event") == "grade"}
    by_id = {c.id: c for c in manifest["cases"]}
    report = {"configs": {}, "manifest_sha256": manifest["manifest_sha256"], "file_sha256": manifest["file_sha256"]}
    overall = True
    for config in configs:
        reps, interrupted, totals = {}, [], {"actual_usd": 0.0, "held_usd": 0.0, "latency_ms": []}
        for slot, s in state.items():
            if slot[0] != config.id:
                continue
            if s["state"] != "finished":
                interrupted.append({"case": slot[1], "mode": slot[2], "rep": slot[3], "attempt": s["attempt"]})
                continue
            cost = s["record"].get("cost") or {}
            totals["actual_usd"] += float(cost.get("actual_usd") or 0)
            totals["held_usd"] += float(cost.get("held_usd") or 0)
            if s["record"].get("latency_ms") is not None:
                totals["latency_ms"].append(s["record"]["latency_ms"])
        incomplete = bool(interrupted)
        for rep in range(1, REPETITIONS + 1):
            passes, hard_ok, missing, grades = 0, True, [], {}
            for case in manifest["cases"]:
                en = state.get((config.id, case.id, "enabled", rep))
                dis = state.get((config.id, case.id, "disabled", rep))
                if not en or en["state"] != "finished" or not dis or dis["state"] != "finished" \
                        or en["record"]["status"] != "completed":
                    missing.append(case.id)
                    continue
                key = (config.id, case.id, rep)
                g = existing.get(key)
                if g is None:
                    out = grader.grade(case, en["record"], dis["record"])
                    if out.get("score") not in SCORES or not out.get("reason"):
                        missing.append(case.id)
                        continue
                    g = {"event": "grade", "config": config.id, "case": case.id, "rep": rep,
                         "score": out["score"], "reason": out["reason"],
                         "enabled_run": en["record"]["run_id"], "disabled_run": dis["record"]["run_id"]}
                    grades_ledger.append(g)
                    existing[key] = g
                grades[case.id] = g["score"]
                effective = g["score"] == "PASS" or (g["score"] == "NEUTRAL" and case.kind == "should_not_fire")
                if case.id in manifest["held_out"] and effective:
                    passes += 1
                if case.hard and g["score"] != "PASS":
                    hard_ok = False
            rep_ok = not missing and passes >= MIN_PASS and hard_ok
            reps[rep] = {"held_out_passes": passes, "of": HELD_OUT, "hard_all_pass": hard_ok,
                         "ungraded": missing, "scores": grades, "pass": rep_ok}
            incomplete = incomplete or bool(missing)
        lat = sorted(totals["latency_ms"])
        verdict = "incomplete" if incomplete else ("pass" if all(r["pass"] for r in reps.values()) else "fail")
        overall = overall and verdict == "pass"
        report["configs"][config.id] = {
            "verdict": verdict, "repetitions": reps, "interrupted": interrupted,
            "cost": {"actual_usd": round(totals["actual_usd"], 4), "held_usd": round(totals["held_usd"], 4)},
            "latency_ms_p50": lat[len(lat) // 2] if lat else None}
    report["gate"] = "pass" if overall else ("incomplete" if any(
        c["verdict"] == "incomplete" for c in report["configs"].values()) else "fail")
    return report


def public_summary(report: dict) -> dict:
    """Counts and verdicts only: no case IDs' scores, reasons, transcripts or case text."""
    return {"gate": report["gate"],
            "configs": {cid: {"verdict": c["verdict"],
                              "repetitions": {rep: {"held_out_passes": r["held_out_passes"], "of": r["of"],
                                                    "hard_all_pass": r["hard_all_pass"]}
                                              for rep, r in c["repetitions"].items()},
                              "interrupted": len(c["interrupted"]), "cost": c["cost"]}
                        for cid, c in report["configs"].items()}}
