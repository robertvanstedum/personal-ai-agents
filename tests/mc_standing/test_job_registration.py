"""Every new standing job registers (standing §8a; scheduled-jobs contract), and the brief's entry
point writes the standard job status file with fixed phrases only."""
import json
import os
from pathlib import Path

from core.jobs import registry as jobs_registry, status as jobs_status
from tests.mc_standing.test_entrypoint import REPO, args, run_cycle

CANDIDATE = REPO / "config/mc_standing/scheduled_jobs.candidate.json"


def test_candidate_entries_pass_the_real_registry_loader_and_do_not_clash():
    candidate = jobs_registry.load(CANDIDATE)
    assert {j.id for j in candidate.jobs} == {"mc-standing-brief", "mc-learning-consolidation"}
    shared = json.loads((REPO / "config/scheduled_jobs.json").read_text())
    merged = dict(shared, jobs=shared["jobs"] + json.loads(CANDIDATE.read_text())["jobs"])
    jobs_registry.parse(merged)                     # no duplicate ids or status files once merged
    brief = candidate.get("mc-standing-brief")
    assert brief.schedule["at"] == "05:40" and candidate.get("mc-learning-consolidation").schedule["at"] == "04:40"


def _run(tmp_path, extra=()):
    a = args(tmp_path, **{"--environment": "dev"}) + ["--dev-fixture", "--jobs-root", str(tmp_path / "jobs"),
                                                      "--jobs-registry", str(CANDIDATE), *extra]
    return run_cycle.main(a)


def test_brief_run_writes_a_contract_status_file(tmp_path, capsys):
    assert _run(tmp_path) == 0                       # no control document: paused fail-safe
    doc = jobs_status.read_status(tmp_path / "jobs", "mc-standing-brief.json", "mc-standing-brief")
    assert doc is not None and doc["state"] == "warn" and doc["summary"] == "paused by owner"
    assert doc["results"] == {"brief": "paused"} and doc["host"] == "ec2"
    assert oct(os.stat(tmp_path / "jobs" / "mc-standing-brief.json").st_mode & 0o777) == "0o600"


def test_unregistered_job_id_is_a_config_error(tmp_path, capsys):
    assert _run(tmp_path, ["--job-id", "not-a-job"]) == 2
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["reason"] == "job_not_registered"


def test_outcome_mapping_is_fixed_phrases_only():
    assert run_cycle._job_outcome({"state": "delivered"}) == ("ok", "brief delivered", {"brief": "delivered"})
    assert run_cycle._job_outcome({"state": "delivered", "channels": {"site": "delivered", "telegram": "uncertain"}}) \
        == ("warn", "brief delivered; telegram copy uncertain", {"brief": "delivered", "telegram": "uncertain"})
    assert run_cycle._job_outcome({"state": "held"})[0] == "failed"
    assert run_cycle._job_outcome({"state": "config_error"})[:2] == ("failed", "brief cycle failed")
    for state in ("delivered", "paused", "skipped", "delivery_pending", "delivery_unknown", "held", "missed"):
        job_state, summary, results = run_cycle._job_outcome({"state": state})
        jobs_status.build(job_id="mc-standing-brief", host="ec2", run_id="r1", state=job_state,
                          started_at=jobs_status.parse_time("2026-10-21T10:40:00Z"),
                          finished_at=jobs_status.parse_time("2026-10-21T10:41:00Z"), exit_code=0,
                          summary=summary, results=results)      # raises if any phrase breaks the contract


def test_envelope_excess_makes_an_ok_run_warn():
    assert run_cycle._job_outcome({"state": "delivered", "envelope_exceeded": ["max_wall_ms"]}) == \
        ("warn", "brief delivered; resource envelope exceeded", {"brief": "delivered", "resources": "over_envelope"})
