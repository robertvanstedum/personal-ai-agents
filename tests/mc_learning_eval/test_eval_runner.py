"""Dry-run orchestration of the independent learning evaluation with synthetic cases only.

No sealed case, fixture or key is used or referenced here; IDs below are synthetic (S01…)."""
import json

import pytest

from core.mc_learning_eval import runner as ev

CONFIGS = [ev.Config("anthropic", "model-a", "low"), ev.Config("xai", "model-b", "low")]
SHOULD_NOT_FIRE = {"S04", "S05", "S06", "S09"}
HARD = {"S04", "S05", "S06", "S12", "SF1"}


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
    for name, data in (files or {}).items():
        (d / name).write_bytes(data)
    (d / "MANIFEST.json").write_text(json.dumps(doc or manifest_doc()))
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
    def __init__(self, score=lambda case, rep: "PASS"):
        self.score, self.calls = score, 0

    def grade(self, case, enabled, disabled):
        self.calls += 1
        assert enabled["mode"] == "enabled" and disabled["mode"] == "disabled"
        assert enabled["rep"] == disabled["rep"] and enabled["config"] == disabled["config"]
        return {"score": self.score(case, enabled["rep"]), "reason": "cites synthetic evidence"}


def setup(tmp_path, **kw):
    m = ev.load_manifest(write_manifest(tmp_path, kw.pop("doc", None)), mode="dry_run")
    return m, ev.Ledger(tmp_path / "out" / "runs.jsonl"), ev.Ledger(tmp_path / "out" / "grades.jsonl")


def execute(m, ledger, executor=None, allowance=None, **kw):
    return ev.run_evaluation(manifest=m, configs=CONFIGS, executor=executor or Executor(),
                             allowance=allowance or Allowance(), ledger=ledger, plan_id="plan-1",
                             mode="dry_run", estimate_usd=0.05, **kw)


def test_full_plan_runs_every_config_case_mode_and_repetition(tmp_path):
    m, runs, grades = setup(tmp_path)
    ex = Executor()
    out = execute(m, runs, ex)
    assert out == {"state": "executed", "ran": 2 * 13 * 2 * 2}
    assert len({c["run_id"] for c in ex.calls}) == len(ex.calls)
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(), grades_ledger=grades)
    assert report["gate"] == "pass"
    cfg = report["configs"]["anthropic/model-a/low"]
    assert cfg["repetitions"][1]["held_out_passes"] == 12 and cfg["cost"]["actual_usd"] == 0.52


def test_rerun_skips_finished_runs_and_reuses_grades(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    ex = Executor()
    assert execute(m, runs, ex) == {"state": "executed", "ran": 0} and ex.calls == []
    g = Grader()
    ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=g, grades_ledger=grades)
    first = g.calls
    ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=g, grades_ledger=grades)
    assert g.calls == first


def test_ten_of_twelve_passes_with_neutral_counting_only_for_should_not_fire(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    # S09 should-not-fire NEUTRAL counts; S01 and S02 NEUTRAL do not → 10 of 12.
    score = lambda case, rep: "NEUTRAL" if case.id in ("S01", "S02", "S09") else "PASS"
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(score), grades_ledger=grades)
    assert report["configs"]["xai/model-b/low"]["repetitions"][2]["held_out_passes"] == 10
    assert report["gate"] == "pass"


def test_nine_of_twelve_in_one_repetition_fails_no_best_of_n(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    score = lambda case, rep: "FAIL" if rep == 2 and case.id in ("S01", "S02", "S03") else "PASS"
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(score), grades_ledger=grades)
    assert report["gate"] == "fail"
    assert report["configs"]["anthropic/model-a/low"]["repetitions"][1]["pass"] is True


def test_a_single_hard_case_failure_fails_the_gate(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    score = lambda case, rep: "NEUTRAL" if case.id == "SF1" and rep == 1 else "PASS"
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(score), grades_ledger=grades)
    assert report["gate"] == "fail"
    assert report["configs"]["anthropic/model-a/low"]["repetitions"][1]["hard_all_pass"] is False


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
    ex = Executor()
    execute(m, runs, ex)
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(), grades_ledger=grades)
    assert report["gate"] == "incomplete"
    interrupted = report["configs"]["anthropic/model-a/low"]["interrupted"]
    assert len(interrupted) == 1 and interrupted[0]["attempt"] == 1
    assert interrupted[0]["case"] not in {c["case"] for c in ex.calls if c["rep"] == interrupted[0]["rep"]
                                          and c["learning"] == interrupted[0]["mode"]
                                          and c["config"] == "anthropic/model-a/low"}
    ex2 = Executor()
    execute(m, runs, ex2, retry_interrupted=True)
    assert len(ex2.calls) == 1 and ex2.calls[0]["attempt"] == 2
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(), grades_ledger=grades)
    assert report["gate"] == "pass"


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


def test_dry_run_refuses_non_synthetic_cases_and_live_needs_spend_admission(tmp_path):
    doc = manifest_doc()
    doc["cases"][0]["synthetic"] = False
    with pytest.raises(ev.EvalRefused, match="dry_run_requires_synthetic_cases"):
        ev.load_manifest(write_manifest(tmp_path, doc), mode="dry_run")
    m, runs, _ = setup(tmp_path / "b")
    with pytest.raises(ev.EvalRefused, match="live_requires_verified_spend_admission"):
        ev.run_evaluation(manifest=m, configs=CONFIGS, executor=Executor(), allowance=Allowance(), ledger=runs,
                          plan_id="p", mode="live", estimate_usd=0.05)


def test_two_providers_are_required(tmp_path):
    m, runs, _ = setup(tmp_path)
    with pytest.raises(ev.EvalRefused, match="two_providers_required"):
        ev.run_evaluation(manifest=m, configs=[CONFIGS[0], ev.Config("anthropic", "model-c", "high")],
                          executor=Executor(), allowance=Allowance(), ledger=runs, plan_id="p",
                          mode="dry_run", estimate_usd=0.05)


def test_live_manifest_verifies_sealed_file_hashes(tmp_path):
    import hashlib
    data = b"synthetic sealed body"
    doc = manifest_doc(file_sha256={"CASES.md": hashlib.sha256(data).hexdigest()})
    path = write_manifest(tmp_path, doc, files={"CASES.md": data})
    assert ev.load_manifest(path, mode="live")["file_sha256"]["CASES.md"]
    (tmp_path / "sealed" / "CASES.md").write_bytes(b"tampered")
    with pytest.raises(ev.EvalRefused, match="sealed_file_hash_mismatch"):
        ev.load_manifest(path, mode="live")


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
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Bad(), grades_ledger=grades)
    assert report["gate"] == "incomplete"


def test_public_summary_carries_counts_only(tmp_path):
    m, runs, grades = setup(tmp_path)
    execute(m, runs)
    report = ev.grade_and_gate(manifest=m, configs=CONFIGS, ledger=runs, grader=Grader(), grades_ledger=grades)
    text = json.dumps(ev.public_summary(report))
    assert "S01" not in text and "reason" not in text and "transcript" not in text
    assert json.loads(text)["gate"] == "pass"


def test_runner_source_holds_no_sealed_case_ids_or_text():
    import inspect
    src = inspect.getsource(ev)
    assert not any(f"C{i:02d}" in src for i in range(1, 13)) and "F01" not in src


def test_live_mode_refuses_placeholder_fixtures(tmp_path):
    doc = manifest_doc()
    doc["cases"][0]["fixture_ref"] = "pending:S01"
    with pytest.raises(ev.EvalRefused, match="fixtures_pending"):
        ev.load_manifest(write_manifest(tmp_path, doc), mode="live")
