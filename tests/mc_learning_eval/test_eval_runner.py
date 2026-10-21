"""Dry-run orchestration of the independent learning evaluation with synthetic cases only.

No sealed case, fixture or key is used or referenced here; IDs below are synthetic (S01…).
Includes Codex's E-F1..E-F4 reproductions."""
import hashlib
import json
import os

import pytest

from core.mc_learning_eval import runner as ev

CONFIGS = [ev.Config("anthropic", "model-a", "low"), ev.Config("xai", "model-b", "low")]
SHOULD_NOT_FIRE = {"S04", "S05", "S06", "S09"}
HARD = {"S04", "S05", "S06", "S12", "SF1"}
BINDING = {"candidate": "c" * 64, "role": "r" * 64, "policy": "p" * 64, "spec": "s" * 64}


def manifest_doc(**over):
    cases = [{"id": f"S{i:02d}", "kind": "should_not_fire" if f"S{i:02d}" in SHOULD_NOT_FIRE else "adaptation",
              "hard": f"S{i:02d}" in HARD, "fixture_ref": f"synthetic/s{i:02d}", "synthetic": True}
             for i in range(1, 13)]
    cases.append({"id": "SF1", "kind": "adaptation", "hard": True, "held_out": False,
                  "fixture_ref": "synthetic/sf1", "synthetic": True})
    cases += [{"id": f"R{i}", "kind": "adaptation", "fixture_ref": f"synthetic/r{i}", "synthetic": True}
              for i in range(1, 3)]
    doc = {"schema": ev.MANIFEST_SCHEMA, "cases": cases, "reserve": ["R1", "R2"], "retired": [],
           "file_sha256": {}}
    doc.update(over)
    return doc


def write_manifest(tmp_path, doc=None, files=None):
    d = tmp_path / "sealed"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    for name, data in (files or {}).items():
        (d / name).write_bytes(data)
        os.chmod(d / name, 0o600)
    (d / "MANIFEST.json").write_text(json.dumps(doc or manifest_doc()))
    os.chmod(d / "MANIFEST.json", 0o600)
    return d / "MANIFEST.json"


class Executor:
    def __init__(self, status_for=None):
        self.calls, self.status_for = [], status_for or (lambda spec: "completed")

    def run(self, spec):
        self.calls.append(spec)
        return {"status": self.status_for(spec), "transcript_ref": f"t/{spec['run_id']}",
                "transcript_sha256": "0" * 64, "receipts": [{"id": "r1"}],
                "application_events": [{"kind": "learning.applied"}] if spec["learning"] == "enabled" else [],
                "cost": {"actual_usd": 0.01, "held_usd": 0.0}, "latency_ms": 1200,
                "hashes": {"spec": "s", "candidate": "c", "role": "r", "policy": "p"}}


class Allowance:
    def __init__(self, budget=1000):
        self.left = budget

    def check(self, spec, estimate):
        if self.left <= 0:
            return "refused"
        self.left -= 1
        return "admitted"


class Grader:
    version = "grader-1"

    def __init__(self, score=lambda case, rep: "PASS", run_score=lambda case, record: "PASS"):
        self.score, self.run_score, self.calls = score, run_score, 0

    def grade(self, case, enabled, disabled):
        self.calls += 1
        assert enabled["mode"] == "enabled" and disabled["mode"] == "disabled"
        assert enabled["rep"] == disabled["rep"] and enabled["config"] == disabled["config"]
        return {"score": self.score(case, enabled["rep"]), "reason": "cites synthetic evidence"}

    def grade_run(self, case, record):
        self.calls += 1
        assert record["mode"] == "disabled"
        return {"score": self.run_score(case, record), "reason": "baseline checked on its own"}


def setup(tmp_path, **kw):
    m = ev.load_manifest(write_manifest(tmp_path, kw.pop("doc", None)), mode="dry_run")
    out = tmp_path / "out"
    return m, ev.Ledger(out / "runs.jsonl"), ev.Ledger(out / "grades.jsonl")


def execute(m, ledger, executor=None, allowance=None, plan_id="plan-1", binding=BINDING, **kw):
    return ev.run_evaluation(manifest=m, configs=CONFIGS, executor=executor or Executor(),
                             allowance=allowance or Allowance(), ledger=ledger, plan_id=plan_id,
                             mode="dry_run", estimate_usd=0.05, binding=binding, **kw)


def gate(m, runs, grades, grader=None, plan_id="plan-1", binding=BINDING):
    return ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=grader or Grader(),
                             grades_ledger=grades, plan_id=plan_id, mode="dry_run", binding=binding)


def test_full_plan_runs_every_config_case_mode_and_repetition(tmp_path):
    m, runs, grades = setup(tmp_path)
    ex = Executor()
    out = execute(m, runs, ex)
    assert out["state"] == "executed" and out["ran"] == 2 * 13 * 2 * 2
    assert len({c["run_id"] for c in ex.calls}) == len(ex.calls)
    report = gate(m, runs, grades)
    assert report["gate"] == "pass"
    cfg = report["configs"]["anthropic/model-a/low"]
    assert cfg["repetitions"][1]["held_out_passes"] == 12 and cfg["cost"]["actual_usd"] == 0.52
    assert cfg["cost"]["attempts"] == 52 and cfg["cost"]["unknown_spend_attempts"] == 0


def test_rerun_skips_finished_runs_and_reuses_exact_grades(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    ex = Executor()
    assert execute(m, runs, ex)["ran"] == 0 and ex.calls == []
    g = Grader()
    gate(m, runs, grades, g)
    first = g.calls
    gate(m, runs, grades, g)
    assert g.calls == first


def test_ten_of_twelve_passes_with_neutral_counting_only_for_should_not_fire(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    score = lambda case, rep: "NEUTRAL" if case.id in ("S01", "S02", "S09") else "PASS"
    report = gate(m, runs, grades, Grader(score))
    assert report["configs"]["xai/model-b/low"]["repetitions"][2]["held_out_passes"] == 10
    assert report["gate"] == "pass"


def test_nine_of_twelve_in_one_repetition_fails_no_best_of_n(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    score = lambda case, rep: "FAIL" if rep == 2 and case.id in ("S01", "S02", "S03") else "PASS"
    report = gate(m, runs, grades, Grader(score))
    assert report["gate"] == "fail"
    assert report["configs"]["anthropic/model-a/low"]["repetitions"][1]["pass"] is True


def test_a_single_hard_case_failure_fails_the_gate(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    score = lambda case, rep: "NEUTRAL" if case.id == "SF1" and rep == 1 else "PASS"
    report = gate(m, runs, grades, Grader(score))
    assert report["gate"] == "fail"
    assert report["configs"]["anthropic/model-a/low"]["repetitions"][1]["hard_all_pass"] is False


def test_hard_case_disabled_baseline_is_graded_on_its_own(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    report = gate(m, runs, grades, Grader(run_score=lambda case, r: "FAIL" if case.id == "S12" else "PASS"))
    assert report["gate"] == "fail"


def test_interrupted_run_is_counted_and_not_silently_rerun(tmp_path):
    m, runs, grades = setup(tmp_path)

    class Crash(BaseException):
        pass

    class Crashing(Executor):
        def run(self, spec):
            if len(self.calls) == 5:
                raise Crash()
            return super().run(spec)
    with pytest.raises(Crash):
        execute(m, runs, Crashing())
    execute(m, runs, Executor())
    report = gate(m, runs, grades)
    assert report["gate"] == "incomplete"
    cfg = report["configs"]["anthropic/model-a/low"]
    assert len(cfg["interrupted"]) == 1 and cfg["cost"]["unknown_spend_attempts"] == 1
    ex2 = Executor()
    execute(m, runs, ex2, retry_interrupted=True)
    assert len(ex2.calls) == 1 and ex2.calls[0]["attempt"] == 2
    report = gate(m, runs, grades)
    assert report["gate"] == "pass"
    assert report["configs"]["anthropic/model-a/low"]["cost"]["unknown_spend_attempts"] == 1   # still accounted


def test_allowance_refusal_stops_before_the_run(tmp_path):
    m, runs, _ = setup(tmp_path)
    ex = Executor()
    out = execute(m, runs, ex, allowance=Allowance(budget=3))
    assert out == {"state": "stopped_allowance", "reason": "allowance_refused", "ran": 3} and len(ex.calls) == 3


@pytest.mark.parametrize("bad", ["authority_violation", "privacy_violation"])
def test_authority_or_privacy_violation_stops_everything(tmp_path, bad):
    m, runs, grades = setup(tmp_path)
    ex = Executor(lambda spec: bad if spec["case"] == "S03" else "completed")
    out = execute(m, runs, ex)
    assert out["state"] == "stopped" and out["reason"] == bad
    later = Executor()
    assert execute(m, runs, later)["state"] == "stopped" and later.calls == []


# ── E-F1: plan identity ────────────────────────────────────────────────────

def test_a_ledger_belongs_to_one_plan_and_another_plan_is_refused(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    ex = Executor()
    with pytest.raises(ev.EvalRefused, match="another_plan"):
        execute(m, runs, ex, plan_id="plan-2")            # previously executed 0 runs silently
    with pytest.raises(ev.EvalRefused, match="another_plan"):
        execute(m, runs, ex, binding=dict(BINDING, candidate="d" * 64))
    assert ex.calls == []


def test_grades_are_not_reused_after_the_grader_changes(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    g1 = Grader()
    gate(m, runs, grades, g1)
    g2 = Grader()
    g2.version = "grader-2"
    gate(m, runs, grades, g2)
    assert g2.calls == g1.calls                           # a new grader version regrades everything


def test_binding_must_be_complete(tmp_path):
    m, runs, _ = setup(tmp_path)
    with pytest.raises(ev.EvalRefused, match="binding_incomplete"):
        execute(m, runs, binding={"candidate": "c"})


# ── E-F2: violations and baselines ─────────────────────────────────────────

def test_a_disabled_run_violation_fails_the_gate_despite_an_always_pass_grader(tmp_path):
    m, runs, grades = setup(tmp_path)
    ex = Executor(lambda spec: "privacy_violation" if spec["case"] == "S12" and spec["learning"] == "disabled"
                  else "completed")
    execute(m, runs, ex)
    report = gate(m, runs, grades, Grader())
    assert report["gate"] == "fail" and report["violations"][0]["mode"] == "disabled"


def test_a_refused_baseline_is_not_a_completed_comparison(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs, Executor(lambda spec: "refused" if spec["case"] == "S07" and spec["learning"] == "disabled"
                              else "completed"))
    report = gate(m, runs, grades)
    assert report["gate"] == "incomplete"
    assert "S07" in report["configs"]["anthropic/model-a/low"]["repetitions"][1]["ungraded"]


# ── E-F3: durable ledger ───────────────────────────────────────────────────

def test_interior_damage_holds_instead_of_skipping(tmp_path):
    m, runs, _ = setup(tmp_path)
    execute(m, runs, allowance=Allowance(budget=4))
    lines = runs.path.read_bytes().splitlines(keepends=True)
    lines[3] = b'{"broken": \n'
    runs.path.write_bytes(b"".join(lines))
    with pytest.raises(ev.LedgerDamaged):
        execute(m, runs)


def test_a_rewritten_interior_record_breaks_the_chain(tmp_path):
    m, runs, _ = setup(tmp_path)
    execute(m, runs, allowance=Allowance(budget=4))
    lines = runs.path.read_bytes().splitlines(keepends=True)
    rec = json.loads(lines[2])
    rec["status"] = "refused"
    lines[2] = json.dumps(rec, sort_keys=True).encode() + b"\n"
    runs.path.write_bytes(b"".join(lines))
    with pytest.raises(ev.LedgerDamaged, match="chain_broken"):
        runs.records()


def test_torn_final_fragment_is_preserved_and_cut_back(tmp_path):
    m, runs, _ = setup(tmp_path)
    execute(m, runs, allowance=Allowance(budget=2))
    with open(runs.path, "ab") as fh:
        fh.write(b'{"event": "started", "half')
    records = runs.records()
    assert records[-1]["event"] == "finished"
    side = list(runs.path.parent.glob("runs.jsonl.torn-*"))
    assert len(side) == 1 and side[0].read_bytes().startswith(b'{"event": "started"')


def test_short_writes_are_completed(tmp_path, monkeypatch):
    m, runs, _ = setup(tmp_path)
    real = os.write
    monkeypatch.setattr(ev, "_write", lambda fd, view: real(fd, view[:7]))
    execute(m, runs, allowance=Allowance(budget=2))
    monkeypatch.undo()
    assert [r["event"] for r in runs.records()] == ["plan", "started", "finished", "started", "finished"]


def test_ledger_refuses_symlinks_and_shared_permissions(tmp_path):
    d = tmp_path / "ledgers"
    d.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    (d / "runs.jsonl").symlink_to(target)
    with pytest.raises(ev.EvalRefused):
        ev.Ledger(d / "runs.jsonl")
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    with pytest.raises(ev.EvalRefused, match="not_private"):
        ev.Ledger(shared / "runs.jsonl")


# ── E-F4: manifest binding ─────────────────────────────────────────────────

def _live_doc(**over):
    doc = manifest_doc(**over)
    for c in doc["cases"]:
        c["synthetic"] = False
    return doc


def test_live_manifest_needs_complete_private_hash_coverage(tmp_path):
    data = b"synthetic sealed body"
    good = {"CASES.md": hashlib.sha256(data).hexdigest()}
    path = write_manifest(tmp_path / "a", _live_doc(file_sha256=good), files={"CASES.md": data})
    assert ev.load_manifest(path, mode="live")["file_sha256"] == good
    path = write_manifest(tmp_path / "b", _live_doc(file_sha256={}), files={"CASES.md": data})
    with pytest.raises(ev.EvalRefused, match="file_hashes_missing"):
        ev.load_manifest(path, mode="live")
    path = write_manifest(tmp_path / "c", _live_doc(file_sha256=good), files={"CASES.md": data, "KEY.md": b"k"})
    with pytest.raises(ev.EvalRefused, match="incomplete"):
        ev.load_manifest(path, mode="live")
    path = write_manifest(tmp_path / "d", _live_doc(file_sha256={"../x": "0" * 64}), files={"CASES.md": data})
    with pytest.raises(ev.EvalRefused, match="file_hashes_invalid"):
        ev.load_manifest(path, mode="live")
    path = write_manifest(tmp_path / "e", _live_doc(file_sha256=good), files={"CASES.md": data})
    os.chmod(path.parent / "CASES.md", 0o644)
    with pytest.raises(ev.EvalRefused, match="permissions"):
        ev.load_manifest(path, mode="live")
    path = write_manifest(tmp_path / "f", _live_doc(file_sha256=good), files={"CASES.md": data})
    (path.parent / "CASES.md").write_bytes(b"tampered")
    os.chmod(path.parent / "CASES.md", 0o600)
    with pytest.raises(ev.EvalRefused, match="hash_mismatch"):
        ev.load_manifest(path, mode="live")
    path = write_manifest(tmp_path / "g", _live_doc(file_sha256=good), files={"CASES.md": data})
    os.chmod(path.parent, 0o755)
    with pytest.raises(ev.EvalRefused, match="sealed_dir_not_private"):
        ev.load_manifest(path, mode="live")


def test_live_mode_refuses_placeholder_fixtures_and_synthetic_cases(tmp_path):
    files = {"CASES.md": b"x"}
    hashes = {"CASES.md": hashlib.sha256(b"x").hexdigest()}
    doc = _live_doc(file_sha256=hashes)
    doc["cases"][0]["fixture_ref"] = "pending:S01"
    with pytest.raises(ev.EvalRefused, match="fixtures_pending"):
        ev.load_manifest(write_manifest(tmp_path / "a", doc, files=files), mode="live")
    with pytest.raises(ev.EvalRefused, match="live_refuses_synthetic"):
        ev.load_manifest(write_manifest(tmp_path / "b", manifest_doc(file_sha256=hashes), files=files), mode="live")


def test_booleans_are_strict(tmp_path):
    doc = manifest_doc()
    doc["cases"][0]["hard"] = "yes"
    with pytest.raises(ev.EvalRefused, match="boolean"):
        ev.load_manifest(write_manifest(tmp_path, doc), mode="dry_run")


def test_execution_requires_a_manifest_validated_for_its_own_mode(tmp_path):
    m, runs, _ = setup(tmp_path)
    with pytest.raises(ev.EvalRefused, match="not_validated_for_mode"):
        execute(dict(m, validated_mode="live"), runs)
    unsynthetic = dict(m, cases=[ev.Case(**dict(c.__dict__, synthetic=False)) for c in m["cases"]])
    with pytest.raises(ev.EvalRefused, match="synthetic"):
        execute(unsynthetic, runs)


def test_dry_run_refuses_non_synthetic_cases(tmp_path):
    doc = manifest_doc()
    doc["cases"][0]["synthetic"] = False
    with pytest.raises(ev.EvalRefused, match="dry_run_requires_synthetic_cases"):
        ev.load_manifest(write_manifest(tmp_path, doc), mode="dry_run")


def test_live_needs_spend_admission(tmp_path):
    m, runs, _ = setup(tmp_path)
    with pytest.raises(ev.EvalRefused):
        ev.run_evaluation(manifest=dict(m, validated_mode="live"), configs=CONFIGS, executor=Executor(),
                          allowance=Allowance(), ledger=runs, plan_id="p", mode="live", estimate_usd=0.05,
                          binding=BINDING)


def test_two_providers_are_required(tmp_path):
    m, runs, _ = setup(tmp_path)
    with pytest.raises(ev.EvalRefused, match="two_providers_required"):
        ev.run_evaluation(manifest=m, configs=[CONFIGS[0], ev.Config("anthropic", "model-c", "high")],
                          executor=Executor(), allowance=Allowance(), ledger=runs, plan_id="p",
                          mode="dry_run", estimate_usd=0.05, binding=BINDING)


def test_contaminated_case_is_replaced_from_reserve(tmp_path):
    m = ev.load_manifest(write_manifest(tmp_path, manifest_doc(retired=["S07"])), mode="dry_run")
    assert "S07" not in m["held_out"] and "R1" in m["held_out"] and len(m["held_out"]) == 12
    assert "R2" not in {c.id for c in m["cases"]}


def test_ungraded_or_invalid_grade_is_incomplete_not_pass(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)

    class Bad(Grader):
        def grade(self, case, en, dis):
            return {"score": "GREAT"} if case.id == "S08" else super().grade(case, en, dis)
    assert gate(m, runs, grades, Bad())["gate"] == "incomplete"


def test_public_summary_carries_counts_only(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    text = json.dumps(ev.public_summary(gate(m, runs, grades)))
    assert "S01" not in text and "reason" not in text and "transcript" not in text
    assert json.loads(text)["gate"] == "pass"


def test_runner_source_holds_no_sealed_case_ids_or_text():
    import inspect
    src = inspect.getsource(ev)
    assert not any(f"C{i:02d}" in src for i in range(1, 13)) and "F01" not in src


# ── Codex delta review of 267ec406 (E-F3 remainder) ─────────────────────────

def test_an_altered_final_record_is_detected(tmp_path):
    """Codex's reproduction: rewrite only the final record's status, keeping seq/prev."""
    m, runs, _ = setup(tmp_path)
    execute(m, runs, Executor(lambda spec: "privacy_violation"))
    lines = runs.path.read_bytes().splitlines(keepends=True)
    last = json.loads(lines[-1])
    assert last["event"] == "stopped"
    finished = json.loads(lines[-2])
    finished["status"] = "completed"
    lines[-2] = json.dumps(finished, sort_keys=True).encode() + b"\n"
    lines = lines[:-1]                                     # and drop the stop record entirely
    runs.path.write_bytes(b"".join(lines))
    with pytest.raises(ev.LedgerDamaged, match="acknowledged_tail_missing|acknowledged_record_altered"):
        runs.records()


def test_altering_only_the_last_line_is_detected(tmp_path):
    m, runs, _ = setup(tmp_path)
    execute(m, runs, allowance=Allowance(budget=1))
    lines = runs.path.read_bytes().splitlines(keepends=True)
    rec = json.loads(lines[-1])
    rec["status"] = "privacy_violation" if rec.get("status") == "completed" else "completed"
    lines[-1] = json.dumps(rec, sort_keys=True).encode() + b"\n"
    runs.path.write_bytes(b"".join(lines))
    with pytest.raises(ev.LedgerDamaged, match="acknowledged_record_altered"):
        runs.records()


def test_a_lost_acknowledged_final_line_is_detected(tmp_path):
    m, runs, _ = setup(tmp_path)
    execute(m, runs, allowance=Allowance(budget=2))
    lines = runs.path.read_bytes().splitlines(keepends=True)
    runs.path.write_bytes(b"".join(lines[:-1]))
    with pytest.raises(ev.LedgerDamaged, match="acknowledged_tail_missing"):
        execute(m, runs)


def test_concurrent_evaluations_never_execute_the_same_slot_twice(tmp_path):
    """Codex's reproduction: two callers raced to the same slot and both executed it."""
    import threading
    m, runs, _ = setup(tmp_path)
    barrier = threading.Barrier(2, timeout=5)
    calls, results = [], []

    class Slow(Executor):
        def run(self, spec):
            calls.append(spec["run_id"])
            return super().run(spec)

    class Gate(Allowance):
        def check(self, spec, estimate):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return super().check(spec, estimate)

    def go():
        results.append(execute(m, runs, Slow(), allowance=Gate(budget=1)))
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(calls) == len(set(calls))
    assert {"state": "refused", "reason": "evaluation_already_running"} in results


def test_a_write_that_makes_no_progress_is_refused(tmp_path, monkeypatch):
    m, runs, _ = setup(tmp_path)
    monkeypatch.setattr(ev, "_write", lambda fd, view: 0)
    with pytest.raises(ev.LedgerDamaged, match="no_progress"):
        execute(m, runs)


def test_an_oversize_ledger_holds(tmp_path, monkeypatch):
    m, runs, _ = setup(tmp_path)
    execute(m, runs, allowance=Allowance(budget=2))
    monkeypatch.setattr(ev, "MAX_LEDGER_BYTES", 100)
    with pytest.raises(ev.LedgerDamaged, match="too_large"):
        runs.records()
