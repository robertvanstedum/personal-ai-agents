"""Independent S3 learning-evaluation runner (learning spec v0.3.1 §13; D30 gate; frozen rubric v1).

Claude Code is the independent grader. This module orchestrates execution, collection and gate
scoring; it contains **no case text, fixture or key**. A sealed manifest is read only from a
private directory at run time and only in ``live`` mode, which also requires verified spend
admission. ``dry_run`` accepts only manifests whose every case is marked synthetic, so held-out
cases are never exercised (or tuned against) before the live learning path exists.

Plan: for each eligible configuration (provider, model, effort) × case × mode (enabled,
disabled) × fixed repetition (2): one run with a deterministic run ID.

Identity (Codex E-F1): a plan **fingerprint** binds plan ID, mode, validated manifest digest and
sealed-file hashes, configurations, repetitions and the caller's candidate/role/policy/spec
hashes. A ledger belongs to exactly one fingerprint; any other plan is refused on it. Run IDs and
grades include the fingerprint; a grade binds the exact run IDs, attempts, evidence hashes and
grader version, and is reused only on an exact match.

Ledger (Codex E-F3): append-only JSONL, private (0700 dir, 0600 file, no symlinks), one
cross-process lock, a write loop until every byte is written, fsync, and a hash chain
(``seq``, ``prev``). An interior damaged line or broken chain is ``LedgerDamaged``: the run
holds; nothing is silently skipped. A torn final fragment (an append that never returned, so its
executor never ran) is preserved in a sidecar before the file is cut back to the last whole line.
``started`` is written before the executor is called and ``finished`` after; a ``started``
without ``finished`` is an **interrupted** run: counted, its spend unknown, never silently rerun.

Stops: allowance refused or unknown → stop before the run; a run reporting an authority or
privacy violation in either mode → stop the whole evaluation at once (recorded).

Gate (Codex E-F2), per eligible configuration, per repetition, no best-of-N:
* any authority/privacy violation or stop record → **fail**, whatever any grader says;
* a comparison needs both runs completed; a refused/unknown/interrupted run is not a baseline;
* held-out: at least 10 of 12 effective passes per repetition (PASS, or NEUTRAL on a
  should-not-fire case);
* hard cases: PASS on every run, both modes (the disabled run is graded on its own);
* disabled baselines are otherwise recorded, not required to fail; anything missing → incomplete.

Collaborators (injected):
  executor.run(spec) -> {status: completed|refused|authority_violation|privacy_violation,
        transcript_ref, transcript_sha256, receipts:[...], application_events:[...],
        cost:{actual_usd, held_usd}, latency_ms, hashes:{spec, candidate, role, policy}}
  allowance.check(spec, estimate_usd) -> "admitted" | anything else (stop)
  grader.grade(case, enabled_record, disabled_record) -> {score: PASS|NEUTRAL|FAIL, reason}
  grader.grade_run(case, record) -> {score: PASS|FAIL, reason}   (hard cases, the disabled run)
  grader.version -> str
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
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
MAX_LINE = 64 * 1024
MAX_LEDGER_BYTES = 64 * 1024 * 1024
_ID = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ZERO = "0" * 64


class EvalRefused(RuntimeError):
    pass


class LedgerDamaged(RuntimeError):
    """Committed evidence is damaged: hold, never infer which runs happened."""


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


def _canon(doc) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private_regular(path: Path) -> os.stat_result:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise EvalRefused("file_not_private_regular")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise EvalRefused("file_permissions")
    return info


def _strict_bool(raw: dict, key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise EvalRefused("manifest_boolean_invalid")
    return value


def load_manifest(path, *, mode: str) -> dict:
    """Validate a manifest for exactly one mode (Codex E-F4). Live mode also requires a private
    directory, private regular files, and a non-empty hash set that covers every sealed file."""
    if mode not in ("dry_run", "live"):
        raise EvalRefused("mode_invalid")
    path = Path(path)
    if mode == "live":
        dinfo = os.lstat(path.parent)
        if stat.S_ISLNK(dinfo.st_mode) or stat.S_IMODE(dinfo.st_mode) & 0o077:
            raise EvalRefused("sealed_dir_not_private")
        _private_regular(path)
    raw_bytes = path.read_bytes()
    doc = json.loads(raw_bytes)
    if not isinstance(doc, dict) or doc.get("schema") != MANIFEST_SCHEMA:
        raise EvalRefused("manifest_schema_unknown")
    cases, seen = [], set()
    for raw in doc.get("cases", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("fixture_ref"), str):
            raise EvalRefused("manifest_case_invalid")
        case = Case(id=raw.get("id"), kind=raw.get("kind"), hard=_strict_bool(raw, "hard", False),
                    held_out=_strict_bool(raw, "held_out", True), fixture_ref=raw["fixture_ref"],
                    synthetic=_strict_bool(raw, "synthetic", False))
        if not isinstance(case.id, str) or not _ID.match(case.id) or case.id in seen \
                or case.kind not in ("adaptation", "should_not_fire"):
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
    files = doc.get("file_sha256", {})
    if not isinstance(files, dict) or not all(isinstance(k, str) and _FILE.match(k) and isinstance(v, str)
                                              and _SHA.match(v) for k, v in files.items()):
        raise EvalRefused("file_hashes_invalid")
    if mode == "live":
        if any(c.synthetic for c in active):
            raise EvalRefused("live_refuses_synthetic_cases")
        if any(c.fixture_ref.startswith("pending:") for c in active):
            raise EvalRefused("fixtures_pending")
        if not files:
            raise EvalRefused("file_hashes_missing")
        sealed = {p.name for p in path.parent.iterdir()
                  if p.name != path.name and not p.name.startswith(".")
                  and p.name not in (f"{path.name}.sha256", "SHA256SUMS")}
        if sealed != set(files):
            raise EvalRefused("file_hashes_incomplete")
        for name, digest in sorted(files.items()):
            _private_regular(path.parent / name)
            if _sha((path.parent / name).read_bytes()) != digest:
                raise EvalRefused("sealed_file_hash_mismatch")
    return {"cases": active, "held_out": [c.id for c in held], "manifest_sha256": _sha(raw_bytes),
            "file_sha256": dict(files), "retired": sorted(retired), "validated_mode": mode}


def plan_fingerprint(*, plan_id: str, mode: str, manifest: dict, configs: list[Config], binding: dict) -> str:
    """Everything that must be identical for two runs or grades to count as the same evidence."""
    for key in ("candidate", "role", "policy", "spec"):
        if not isinstance(binding.get(key), str) or not binding[key]:
            raise EvalRefused("binding_incomplete")
    return _sha(_canon({"plan_id": plan_id, "mode": mode, "manifest_sha256": manifest["manifest_sha256"],
                        "file_sha256": manifest["file_sha256"], "held_out": manifest["held_out"],
                        "cases": [[c.id, c.kind, c.hard, c.held_out, c.fixture_ref] for c in manifest["cases"]],
                        "configs": [c.id for c in configs], "repetitions": REPETITIONS,
                        "binding": {k: binding[k] for k in sorted(binding)}}))


def run_id(fingerprint: str, config: Config, case_id: str, mode: str, rep: int, attempt: int = 1) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                          f"mc-learning-eval:{fingerprint}:{config.id}:{case_id}:{mode}:{rep}:{attempt}"))


class Ledger:
    """Private, locked, hash-chained append-only JSONL with a durable acknowledged-tail
    checkpoint (``<ledger>.head``: count and hash of the last acknowledged line, written after
    the line is fsynced). The checkpoint detects an altered or lost final record, which the
    chain alone cannot (Codex E-F3). The ledger is bounded (64 MiB)."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(self.path.parent)
        if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise EvalRefused("ledger_dir_not_private")
        if os.path.lexists(self.path):
            _private_regular(self.path)

    def _fd(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _scan(self, fd) -> tuple[list[dict], str]:
        size = os.fstat(fd).st_size
        if size > MAX_LEDGER_BYTES:
            raise LedgerDamaged("ledger_too_large")
        os.lseek(fd, 0, os.SEEK_SET)
        chunks, total = [], 0
        while chunk := os.read(fd, 1 << 20):
            total += len(chunk)
            if total > MAX_LEDGER_BYTES:
                raise LedgerDamaged("ledger_too_large")
            chunks.append(chunk)
        data = b"".join(chunks)
        whole_end = data.rfind(b"\n") + 1
        torn = data[whole_end:]
        if torn:   # an append that never returned: preserve it, then cut back to whole lines
            side = self.path.with_name(f"{self.path.name}.torn-{_sha(torn)[:12]}")
            with open(side, "wb") as fh:
                fh.write(torn)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(side, 0o600)
            os.ftruncate(fd, whole_end)
            os.fsync(fd)
        out, prev = [], _ZERO
        for n, line in enumerate(data[:whole_end].splitlines(), start=1):
            try:
                rec = json.loads(line)
            except ValueError:
                raise LedgerDamaged(f"line_{n}_unreadable") from None
            if not isinstance(rec, dict) or rec.get("seq") != n or rec.get("prev") != prev:
                raise LedgerDamaged(f"line_{n}_chain_broken")
            prev = _sha(line)
            out.append(rec)
        head = self._read_head()
        if head is not None:
            count, digest = head
            if len(out) < count:
                raise LedgerDamaged("acknowledged_tail_missing")
            if count and _sha(data[:whole_end].splitlines()[count - 1]) != digest:
                raise LedgerDamaged("acknowledged_record_altered")
        elif out:
            raise LedgerDamaged("head_checkpoint_missing")
        return out, prev

    @property
    def _head_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.head")

    def _read_head(self):
        if not os.path.lexists(self._head_path):
            return None
        _private_regular(self._head_path)
        try:
            doc = json.loads(self._head_path.read_bytes())
            count, digest = doc["count"], doc["sha256"]
        except (ValueError, KeyError, TypeError):
            raise LedgerDamaged("head_checkpoint_unreadable") from None
        if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(digest, str):
            raise LedgerDamaged("head_checkpoint_unreadable")
        return count, digest

    def _write_head(self, count: int, digest: str):
        tmp = self._head_path.with_name(f".{self._head_path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, json.dumps({"count": count, "sha256": digest}).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self._head_path)
        dfd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        fd = self._fd()
        try:
            return self._scan(fd)[0]
        finally:
            os.close(fd)

    def append(self, record: dict) -> dict:
        fd = self._fd()
        try:
            records, prev = self._scan(fd)
            rec = dict(record, seq=len(records) + 1, prev=prev)
            line = json.dumps(rec, sort_keys=True).encode() + b"\n"
            if len(line) > MAX_LINE:
                raise EvalRefused("ledger_record_too_large")
            os.lseek(fd, 0, os.SEEK_END)
            view = memoryview(line)
            while view:
                written = _write(fd, view)
                if not isinstance(written, int) or written <= 0:
                    raise LedgerDamaged("append_made_no_progress")
                view = view[written:]
            os.fsync(fd)
            self._write_head(len(records) + 1, _sha(line[:-1]))
            return rec
        finally:
            os.close(fd)


def _write(fd, view) -> int:
    """One os.write call (a seam so tests can force short writes)."""
    return os.write(fd, view)


def _check_plan(ledger: Ledger, fingerprint: str) -> list[dict]:
    records = ledger.records()
    plans = {r.get("fingerprint") for r in records if r.get("event") == "plan"}
    if plans and plans != {fingerprint}:
        raise EvalRefused("ledger_belongs_to_another_plan")
    if not plans:
        if records:
            raise LedgerDamaged("ledger_without_plan_header")
        ledger.append({"event": "plan", "fingerprint": fingerprint})
        records = ledger.records()
    return records


def _runs_state(records: list[dict]) -> dict:
    """slot (config, case, mode, rep) → {'attempt', 'state': started|finished, 'record', 'run_id'}."""
    state = {}
    for r in records:
        if r.get("event") not in ("started", "finished"):
            continue
        slot = (r["config"], r["case"], r["mode"], r["rep"])
        if r["event"] == "started":
            state[slot] = {"attempt": r["attempt"], "state": "started", "record": None, "run_id": r["run_id"]}
        elif slot in state and state[slot]["run_id"] == r["run_id"]:
            state[slot].update(state="finished", record=r)
    return state


def run_evaluation(*, manifest: dict, configs: list[Config], executor, allowance, ledger: Ledger, plan_id: str,
                   mode: str, estimate_usd: float, binding: dict, spend_admission_verified: bool = False,
                   retry_interrupted: bool = False) -> dict:
    if mode not in ("dry_run", "live") or manifest.get("validated_mode") != mode:
        raise EvalRefused("manifest_not_validated_for_mode")
    if mode == "dry_run" and not all(c.synthetic for c in manifest["cases"]):
        raise EvalRefused("dry_run_requires_synthetic_cases")
    if mode == "live" and not spend_admission_verified:
        raise EvalRefused("live_requires_verified_spend_admission")
    if len(configs) < 2:
        raise EvalRefused("two_eligible_configurations_required")      # D30: two providers
    if len({c.provider for c in configs}) < 2:
        raise EvalRefused("two_providers_required")
    fingerprint = plan_fingerprint(plan_id=plan_id, mode=mode, manifest=manifest, configs=configs, binding=binding)
    run_lock = os.open(ledger.path.with_name(f"{ledger.path.name}.run.lock"),
                       os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:   # one executor per ledger: slots are checked and claimed under this exclusion (E-F3)
            fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "refused", "reason": "evaluation_already_running"}
        return _run_locked(manifest=manifest, configs=configs, executor=executor, allowance=allowance,
                           ledger=ledger, plan_id=plan_id, mode=mode, estimate_usd=estimate_usd, binding=binding,
                           retry_interrupted=retry_interrupted, fingerprint=fingerprint)
    finally:
        os.close(run_lock)


def _run_locked(*, manifest, configs, executor, allowance, ledger, plan_id, mode, estimate_usd, binding,
                retry_interrupted, fingerprint) -> dict:
    records = _check_plan(ledger, fingerprint)
    for r in records:
        if r.get("event") == "stopped":
            return {"state": "stopped", "reason": r["reason"], "run_id": r.get("run_id")}
    state = _runs_state(records)
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
                    rid = run_id(fingerprint, config, case.id, mode_, rep, attempt)
                    spec = {"run_id": rid, "plan_id": plan_id, "fingerprint": fingerprint, "config": config.id,
                            "provider": config.provider, "model": config.model, "effort": config.effort,
                            "case": case.id, "fixture_ref": case.fixture_ref, "learning": mode_, "rep": rep,
                            "attempt": attempt, "eval_mode": mode, "binding": dict(binding)}
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
    return {"state": "executed", "ran": ran, "fingerprint": fingerprint}


def _evidence_hash(record: dict) -> str:
    return _sha(_canon({k: record.get(k) for k in ("run_id", "attempt", "status", "transcript_sha256", "receipts",
                                                   "application_events", "hashes")}))


def _grade_key(kind, config_id, case_id, rep, runs, grader_version):
    return _sha(_canon({"kind": kind, "config": config_id, "case": case_id, "rep": rep, "grader": grader_version,
                        "runs": [[r["run_id"], r["attempt"], _evidence_hash(r)] for r in runs]}))


def grade_and_gate(*, manifest: dict, configs: list[Config], ledger: Ledger, grader, grades_ledger: Ledger,
                   plan_id: str, mode: str, binding: dict) -> dict:
    """Grade every enabled run against its disabled baseline and every hard-case baseline on its
    own, then compute the gate per configuration."""
    fingerprint = plan_fingerprint(plan_id=plan_id, mode=mode, manifest=manifest, configs=configs, binding=binding)
    records = _check_plan(ledger, fingerprint)
    _check_plan(grades_ledger, fingerprint)
    state = _runs_state(records)
    grader_version = str(getattr(grader, "version", "unversioned"))
    existing = {g["key"]: g for g in grades_ledger.records() if g.get("event") == "grade"}
    violations = [r for r in records if r.get("event") == "finished" and r.get("status") in STOP_STATUSES]
    stops = [r for r in records if r.get("event") == "stopped"]
    report = {"configs": {}, "fingerprint": fingerprint, "manifest_sha256": manifest["manifest_sha256"],
              "file_sha256": manifest["file_sha256"],
              "violations": [{"config": r["config"], "case": r["case"], "mode": r["mode"], "rep": r["rep"],
                              "status": r["status"]} for r in violations]}

    def grade(kind, case, rep, runs, call):
        key = _grade_key(kind, runs[0]["config"], case.id, rep, runs, grader_version)
        if key in existing:
            return existing[key]["score"]
        out = call()
        allowed = SCORES if kind == "comparison" else ("PASS", "FAIL")
        if not isinstance(out, dict) or out.get("score") not in allowed or not out.get("reason"):
            return None
        g = {"event": "grade", "key": key, "kind": kind, "config": runs[0]["config"], "case": case.id, "rep": rep,
             "score": out["score"], "reason": out["reason"], "grader": grader_version,
             "runs": [{"run_id": r["run_id"], "attempt": r["attempt"], "evidence": _evidence_hash(r)} for r in runs]}
        grades_ledger.append(g)
        existing[key] = g
        return g["score"]

    for config in configs:
        reps, interrupted = {}, []
        totals = {"actual_usd": 0.0, "held_usd": 0.0, "attempts": 0, "latency_ms": []}
        for r in records:                         # every attempt counts, not only the latest per slot
            if r.get("config") != config.id:
                continue
            if r.get("event") == "started":
                totals["attempts"] += 1
            if r.get("event") == "finished":
                cost = r.get("cost") or {}
                totals["actual_usd"] += float(cost.get("actual_usd") or 0)
                totals["held_usd"] += float(cost.get("held_usd") or 0)
                if r.get("latency_ms") is not None:
                    totals["latency_ms"].append(r["latency_ms"])
        finished_ids = {r["run_id"] for r in records if r.get("event") == "finished"}
        unknown_spend = sum(1 for r in records if r.get("event") == "started" and r.get("config") == config.id
                            and r["run_id"] not in finished_ids)
        for slot, s in state.items():
            if slot[0] == config.id and s["state"] != "finished":
                interrupted.append({"case": slot[1], "mode": slot[2], "rep": slot[3], "attempt": s["attempt"]})
        incomplete = bool(interrupted)
        for rep in range(1, REPETITIONS + 1):
            passes, hard_ok, missing, scores = 0, True, [], {}
            for case in manifest["cases"]:
                en = state.get((config.id, case.id, "enabled", rep))
                dis = state.get((config.id, case.id, "disabled", rep))
                runs = [x["record"] for x in (en, dis) if x and x["state"] == "finished"]
                if len(runs) != 2 or any(r["status"] != "completed" for r in runs):
                    missing.append(case.id)       # no completed comparison: not a baseline
                    continue
                enabled_run, disabled_run = runs
                score = grade("comparison", case, rep, runs,
                              lambda: grader.grade(case, enabled_run, disabled_run))
                if score is None:
                    missing.append(case.id)
                    continue
                scores[case.id] = score
                effective = score == "PASS" or (score == "NEUTRAL" and case.kind == "should_not_fire")
                if case.id in manifest["held_out"] and effective:
                    passes += 1
                if case.hard:
                    if score != "PASS":
                        hard_ok = False
                    base = grade("hard_run", case, rep, [disabled_run], lambda: grader.grade_run(case, disabled_run))
                    if base is None:
                        missing.append(case.id)
                    elif base != "PASS":
                        hard_ok = False
            rep_ok = not missing and passes >= MIN_PASS and hard_ok
            reps[rep] = {"held_out_passes": passes, "of": HELD_OUT, "hard_all_pass": hard_ok,
                         "ungraded": missing, "scores": scores, "pass": rep_ok}
            incomplete = incomplete or bool(missing)
        own_violation = any(v["config"] == config.id for v in report["violations"])
        lat = sorted(totals["latency_ms"])
        if own_violation or stops or any(not r["hard_all_pass"] for r in reps.values()):
            verdict = "fail"
        elif incomplete:
            verdict = "incomplete"
        else:
            verdict = "pass" if all(r["pass"] for r in reps.values()) else "fail"
        report["configs"][config.id] = {
            "verdict": verdict, "repetitions": reps, "interrupted": interrupted,
            "cost": {"actual_usd": round(totals["actual_usd"], 4), "held_usd": round(totals["held_usd"], 4),
                     "attempts": totals["attempts"], "unknown_spend_attempts": unknown_spend},
            "latency_ms_p50": lat[len(lat) // 2] if lat else None}
    verdicts = [c["verdict"] for c in report["configs"].values()]
    if violations or stops or "fail" in verdicts:
        report["gate"] = "fail"
    elif "incomplete" in verdicts:
        report["gate"] = "incomplete"
    else:
        report["gate"] = "pass"
    return report


def public_summary(report: dict) -> dict:
    """Counts and verdicts only: no case IDs' scores, reasons, transcripts or case text."""
    return {"gate": report["gate"], "violations": len(report.get("violations", [])),
            "configs": {cid: {"verdict": c["verdict"],
                              "repetitions": {rep: {"held_out_passes": r["held_out_passes"], "of": r["of"],
                                                    "hard_all_pass": r["hard_all_pass"]}
                                              for rep, r in c["repetitions"].items()},
                              "interrupted": len(c["interrupted"]), "cost": c["cost"]}
                        for cid, c in report["configs"].items()}}
