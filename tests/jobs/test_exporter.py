"""WS3 C1.1: the trap exporter publishes only fixed aliases and numbers, atomically, with its own snapshot age."""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.jobs import health, registry, status

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 28, 7, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
SAMPLE = re.compile(r'^[a-z_]+(\{([a-z_]+="[A-Za-z0-9_.:-]+",?)*\})? -?[0-9.e+]+$')


def _load():
    spec = importlib.util.spec_from_file_location("exporter_t", REPO / "scripts/jobs/exporter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ex = _load()
ROWS = [
    {"id": "vault-meaning-seed", "name": "Seed", "host": "ec2",
     "schedule": {"text": "x", "kind": "interval", "every_s": 900}, "expected_every_s": 86400,
     "missed_after_s": 172800, "stuck_after_s": 7200, "status_file": "seed.json", "owner": "robert", "runbook": "x",
     "active_from": "2026-11-26T06:00:00Z", "progress_required": True, "idle_allowed_s": 3600},
    {"id": "vault-apply", "name": "Apply", "host": "ec2",
     "schedule": {"text": "x", "kind": "interval", "every_s": 900}, "expected_every_s": 900,
     "missed_after_s": 7200, "stuck_after_s": 3600, "status_file": "vault-apply.json", "owner": "robert", "runbook": "x",
     "active_from": "2026-11-13T15:00:00Z"},
]


def setup(tmp_path, *, extra_item=None, summary="budget_spent"):
    reg = registry.parse({"schema_version": 1, "jobs": ROWS})
    jobs, hdir = tmp_path / "jobs", tmp_path / "health"
    seed = reg.get("vault-meaning-seed")
    status.write_status(jobs, seed.status_file, status.build(
        job_id=seed.id, host="ec2", run_id="r1", state="ok", started_at=NOW - 2 * H, finished_at=NOW - H, exit_code=0,
        summary=summary, last_success_at=NOW - H,
        progress={"work_due": 1, "last_progress_at": status.iso(NOW - 2 * H), "phase": "active"},
        last_refusal_at=NOW - 30 * timedelta(minutes=1), last_refusal_reason="apply_busy"))
    report = health.host_report(reg, "ec2", {"jobs": str(jobs)}, NOW)
    if extra_item:
        report["items"].append(extra_item)
    health.publish(hdir, "health-ec2.json", report)
    return reg, jobs, hdir


def build(tmp_path, **kw):
    reg, jobs, hdir = setup(tmp_path, **kw)
    return ex.build(reg, hdir, {"jobs": str(jobs)}, "ec2", ex.Aliases.load(), NOW)


def test_every_registry_job_has_a_reviewed_public_alias():
    aliases = json.loads((REPO / "config/metrics_public_aliases.json").read_text())
    missing = sorted({j.id for j in registry.load().jobs} - set(aliases["job_ids"]))
    assert not missing, f"add these ids to config/metrics_public_aliases.json after review: {missing}"


def test_the_snapshot_has_the_expected_samples_and_valid_syntax(tmp_path):
    text = build(tmp_path)
    samples = [l for l in text.splitlines() if not l.startswith("#")]
    assert all(SAMPLE.match(l) for l in samples), [l for l in samples if not SAMPLE.match(l)]
    assert f"minimoi_exporter_snapshot_timestamp_seconds {int(NOW.timestamp())}" in samples
    assert 'minimoi_job_work_due{host="ec2",job_id="vault-meaning-seed"} 1' in samples
    assert 'minimoi_job_phase{host="ec2",job_id="vault-meaning-seed",phase="active"} 1' in samples
    assert any(l.startswith('minimoi_job_last_refusal_timestamp_seconds{host="ec2",job_id="vault-meaning-seed",reason="apply_busy"}')
               for l in samples)
    assert 'minimoi_job_threshold_seconds{job_id="vault-meaning-seed",kind="idle_allowed_s"} 3600' in samples
    assert any(l.startswith('minimoi_job_light{host="ec2",job_id="vault-apply"} ') for l in samples)
    assert "minimoi_exporter_dropped_total 0" in samples


def test_private_looking_safe_values_are_dropped_and_counted(tmp_path):
    """Codex finding 4: values that pass a safe-character regex can still be private names."""
    text = build(tmp_path, extra_item={"id": "acme-merger-call", "name": "Acme merger call", "host": "ec2",
                                       "light": "red", "code": "client_acme", "off_since": status.iso(NOW - H)})
    assert "acme" not in text.lower() and "merger" not in text.lower()
    dropped = int(re.search(r"^minimoi_exporter_dropped_total (\d+)$", text, re.M).group(1))
    assert dropped >= 2


def test_no_free_text_ever_leaves(tmp_path):
    text = build(tmp_path, summary="secret plan for client")
    assert "secret" not in text and "client" not in text and "/" not in text.replace("# HELP", "")


def test_publish_is_atomic_and_leaves_no_temp_file(tmp_path):
    out = tmp_path / "metrics" / "minimoi.prom"
    ex.publish("a 1\n", out)
    ex.publish("a 2\n", out)
    assert out.read_text() == "a 2\n" and [p.name for p in out.parent.iterdir()] == ["minimoi.prom"]


def test_run_writes_the_snapshot_from_the_environment(tmp_path):
    reg, jobs, hdir = setup(tmp_path)
    regfile = tmp_path / "reg.json"
    regfile.write_text(json.dumps({"schema_version": 1, "jobs": ROWS}))
    out = tmp_path / "m" / "minimoi.prom"
    code = ex.run(environ={"MINIMOI_JOBS_REGISTRY": str(regfile), "MINIMOI_JOBS_ROOT": str(jobs),
                           "MINIMOI_JOBS_HEALTH_DIR": str(hdir), "MINIMOI_JOBS_HOST": "ec2",
                           "MINIMOI_METRICS_FILE": str(out)}, now=lambda: NOW)
    assert code == 0 and "minimoi_exporter_snapshot_timestamp_seconds" in out.read_text()


def test_an_unreadable_alias_table_refuses_to_publish(tmp_path):
    code = ex.run(environ={"MINIMOI_METRICS_ALIASES": str(tmp_path / "missing.json"),
                           "MINIMOI_METRICS_FILE": str(tmp_path / "x.prom")}, now=lambda: NOW)
    assert code == 2 and not (tmp_path / "x.prom").exists()
