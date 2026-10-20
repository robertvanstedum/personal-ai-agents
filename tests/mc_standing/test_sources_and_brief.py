"""Standing P0b: registry, read-only sources and the deterministic brief (spec v0.3.1 §§4, 7)."""
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from core.mc_standing import registry, render, sources
from core.mc_standing.observe import Observation, ObservationError

NOW = datetime(2026, 10, 21, 10, 40, tzinfo=timezone.utc)   # 05:40 Chicago
CYCLE = "prod:2026-10-21:p0:morning"


def watch_doc(**over):
    base = {
        "id": "backup-local", "version": 1, "label": "Local backup", "environment": "prod",
        "host_scope": "ec2", "adapter_id": "job_status", "source_binding": "jobs/backup-local.json",
        "stale_after_s": 93600, "max_read_bytes": 8192, "timeout_s": 3,
        "required_for_coverage": True, "approved_by": "Robert", "approval_ref": "plan-v0.3-kickoff",
        "review_by": "2027-01-31",
        "expected_run_rule": {"expected_every_s": 86400, "grace_s": 3600,
                              "missed_after_s": 172800, "stuck_after_s": 7200},
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def make_watch(**over):
    return registry.parse({"schema": registry.SCHEMA, "watches": [watch_doc(**over)]}).watches[0]


def write(root, rel, obj):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return path


def status_doc(state="ok", success=NOW - timedelta(hours=8), started=None):
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None
    return {"schema_version": 1, "job_id": "backup-local", "host": "ec2", "run_id": "r1",
            "state": state, "started_at": iso(started or success - timedelta(minutes=5)),
            "finished_at": iso(success) if state != "running" else None, "exit_code": 0,
            "summary": "", "results": {}, "next_due_by": None, "last_success_at": iso(success),
            "alert": None}


# ── registry ────────────────────────────────────────────────────────────────

def test_registry_rejects_unknown_fields_adapters_and_bindings():
    for bad in ({"shell": "rm -rf /"}, {"adapter_id": "exec"},
                {"source_binding": "/etc/passwd"}, {"source_binding": "../secrets/x"},
                {"source_binding": "https://x.test/a"}, {"source_binding": "jobs/a;rm"},
                {"max_read_bytes": 10**7}, {"review_by": "soon"}, {"approved_by": " "}):
        with pytest.raises(registry.RegistryError):
            registry.parse({"schema": registry.SCHEMA, "watches": [watch_doc(**bad)]})


def test_registry_job_status_needs_full_run_rule_and_unique_ids():
    with pytest.raises(registry.RegistryError):
        make_watch(expected_run_rule={"expected_every_s": 60})
    with pytest.raises(registry.RegistryError):
        registry.parse({"schema": registry.SCHEMA, "watches": [watch_doc(), watch_doc()]})


def test_registry_load_records_hash(tmp_path):
    path = write(tmp_path, "watchlist.json", {"schema": registry.SCHEMA, "watches": [watch_doc()]})
    wl = registry.load(path)
    assert len(wl.sha256) == 64 and wl.watches[0].id == "backup-local"


# ── sources ─────────────────────────────────────────────────────────────────

def test_job_status_ok_is_healthy(tmp_path):
    write(tmp_path, "jobs/backup-local.json", status_doc())
    o = sources.observe(CYCLE, make_watch(), NOW, tmp_path)
    assert (o.state, o.reason, o.coverage) == ("healthy", "job_ok", "complete")
    assert o.measurements["last_success_age_h"] == 8.0
    assert o.evidence[0][0] == "jobs/backup-local.json"


def test_job_status_missed_and_failed(tmp_path):
    write(tmp_path, "jobs/backup-local.json", status_doc(success=NOW - timedelta(days=3)))
    assert sources.observe(CYCLE, make_watch(), NOW, tmp_path).reason == "job_missed"
    write(tmp_path, "jobs/backup-local.json", status_doc(state="failed"))
    o = sources.observe(CYCLE, make_watch(), NOW, tmp_path)
    assert (o.state, o.reason) == ("failed", "job_failed")


def test_missing_corrupt_oversize_symlink_are_unknown(tmp_path):
    w = make_watch()
    assert sources.observe(CYCLE, w, NOW, tmp_path).reason == "source_missing"
    write(tmp_path, "jobs/backup-local.json", "{not json")
    assert sources.observe(CYCLE, w, NOW, tmp_path).reason == "source_malformed"
    write(tmp_path, "jobs/backup-local.json", "x" * 9000)
    assert sources.observe(CYCLE, w, NOW, tmp_path).reason == "source_oversize"
    (tmp_path / "jobs/backup-local.json").unlink()
    real = write(tmp_path, "elsewhere.json", status_doc())
    os.symlink(real, tmp_path / "jobs/backup-local.json")
    o = sources.observe(CYCLE, w, NOW, tmp_path)
    assert (o.state, o.reason) == ("unknown", "source_symlink_refused")


def test_future_dated_status_is_unknown(tmp_path):
    write(tmp_path, "jobs/backup-local.json", status_doc(success=NOW + timedelta(hours=2)))
    assert sources.observe(CYCLE, make_watch(), NOW, tmp_path).state == "unknown"


def health_watch():
    return make_watch(id="services", label="Services", adapter_id="health_snapshot",
                      source_binding="health/services.json", stale_after_s=3600, expected_run_rule=None)


def test_health_snapshot_states(tmp_path):
    snap = {"schema": "mc-health-snapshot/1", "environment": "production",
            "sampled_at": (NOW - timedelta(minutes=10)).isoformat(),
            "services": [{"name": "portal", "status": "ok"}, {"name": "curator", "status": "unreachable"}]}
    write(tmp_path, "health/services.json", snap)
    o = sources.observe(CYCLE, health_watch(), NOW, tmp_path)
    assert (o.state, o.measurements["services_failing"]) == ("failed", 1)
    snap["sampled_at"] = (NOW - timedelta(hours=5)).isoformat()
    write(tmp_path, "health/services.json", snap)
    assert sources.observe(CYCLE, health_watch(), NOW, tmp_path).reason == "sample_stale"
    snap.update(sampled_at=NOW.isoformat(), environment="dev")
    write(tmp_path, "health/services.json", snap)
    assert sources.observe(CYCLE, health_watch(), NOW, tmp_path).reason == "source_environment_mismatch"


def host_watch(rules):
    return make_watch(id="headroom", label="Disk and memory", adapter_id="host_snapshot",
                      source_binding="health/host.json", stale_after_s=3600, expected_run_rule=None,
                      severity_rules=rules)


def test_host_snapshot_thresholds_and_missing_threshold(tmp_path):
    write(tmp_path, "health/host.json", {"schema": "mc-host-snapshot/1", "sampled_at": NOW.isoformat(),
          "filesystems": [{"mount": "/", "total_bytes": 100, "avail_bytes": 15}],
          "mem_total_bytes": 100, "mem_available_bytes": 50})
    o = sources.observe(CYCLE, host_watch({"disk_used_pct_degraded": 80, "mem_used_pct_degraded": 85}),
                        NOW, tmp_path)
    assert (o.state, o.measurements["disk_used_pct"]) == ("degraded", 85.0)
    assert sources.observe(CYCLE, host_watch({}), NOW, tmp_path).reason == "threshold_missing"


def release_watch():
    return make_watch(id="deploy", label="Last deployment", adapter_id="release_status",
                      source_binding="release/status.json", stale_after_s=3600, expected_run_rule=None)


def test_release_requires_verification_and_flags_stuck_deploys(tmp_path):
    doc = {"format": "minimoi.release-observation/1", "observed_at": (NOW - timedelta(hours=9)).isoformat(),
           "status": "selected_images_observed", "requested_tag": "b05bb9b5"}
    write(tmp_path, "release/status.json", doc)
    o = sources.observe(CYCLE, release_watch(), NOW, tmp_path)
    assert (o.state, o.reason) == ("unknown", "deploy_unverified")
    doc["verification"] = {"checked_at": doc["observed_at"], "services": [{"name": "portal", "ok": True}]}
    write(tmp_path, "release/status.json", doc)
    assert sources.observe(CYCLE, release_watch(), NOW, tmp_path).state == "healthy"
    doc.update(status="deployment_in_progress")
    write(tmp_path, "release/status.json", doc)
    assert sources.observe(CYCLE, release_watch(), NOW, tmp_path).reason == "deploy_in_progress_stale"


def guest_watch(**rules):
    return make_watch(id="guests", label="Guest requests", adapter_id="guest_summary",
                      source_binding="unused/guests", stale_after_s=3600, expected_run_rule=None,
                      required_for_coverage=False, severity_rules=rules or None)


def test_guest_summary_counts_only_and_partial_is_unknown():
    full = lambda: {"coverage": "complete", "stores": {
        "guild": {"count": 2, "oldest": (NOW - timedelta(hours=30)).isoformat()},
        "pending": {"count": 0, "oldest": None}}}
    o = sources.observe(CYCLE, guest_watch(pending_age_degraded_h=24), NOW, None, guest_summary_fn=full)
    assert (o.state, o.measurements["pending_total"], o.measurements["oldest_age_h"]) == ("degraded", 2, 30.0)
    partial = lambda: {"coverage": "partial", "stores": {"guild": {"count": None, "error": "unavailable"},
                                                          "pending": {"count": 1, "oldest": None}}}
    o = sources.observe(CYCLE, guest_watch(), NOW, None, guest_summary_fn=partial)
    assert (o.state, o.coverage) == ("unknown", "partial")
    assert sources.observe(CYCLE, guest_watch(), NOW, None).reason == "source_not_configured"


def test_observation_refuses_green_without_full_coverage():
    with pytest.raises(ObservationError):
        Observation(CYCLE, "w", 1, "prod", "ec2", "healthy", "ok", "partial", "2026-10-21T10:40:00Z")
    with pytest.raises(ObservationError):
        Observation(CYCLE, "w", 1, "prod", "ec2", "failed", "bad reason!", "complete", "x")
    with pytest.raises(ObservationError):
        Observation(CYCLE, "w", 1, "prod", "ec2", "failed", "ok", "complete", "x",
                    measurements={"note": "a long sentence with spaces from a log"})


# ── brief ───────────────────────────────────────────────────────────────────

def _obs(watch, state, reason, coverage="complete", **m):
    return Observation(CYCLE, watch.id, 1, "prod", "ec2", state, reason, coverage,
                       "2026-10-21T10:40:00Z", measurements=m)


def test_quiet_day_is_three_lines_or_fewer():
    ws = [make_watch(), health_watch()]
    text = render.render_brief([_obs(ws[0], "healthy", "job_ok"), _obs(ws[1], "healthy", "services_ok")],
                               ws, local_date=date(2026, 10, 21), environment="prod")
    assert len(text.splitlines()) <= 3 and "Nothing needs you" in text


def test_unknown_is_never_a_green_day_and_missing_required_watch_counts():
    ws = [make_watch(), health_watch()]
    text = render.render_brief([_obs(ws[1], "healthy", "services_ok")], ws,
                               local_date=date(2026, 10, 21), environment="prod")
    assert "Could not check: Local backup" in text and "Nothing needs you" not in text
    text = render.render_brief([_obs(ws[0], "unknown", "source_missing", "unavailable"),
                                _obs(ws[1], "healthy", "services_ok")], ws,
                               local_date=date(2026, 10, 21), environment="prod")
    assert "Local backup: unknown (source missing)" in text
    assert "MC took no actions" in text


def test_attention_lines_carry_evidence_and_proposals_and_no_names():
    ws = [make_watch(), guest_watch()]
    text = render.render_brief([_obs(ws[0], "failed", "job_failed", last_success_age_h=50.0),
                                _obs(ws[1], "degraded", "guest_requests_waiting", pending_total=2,
                                     oldest_age_h=30.0)],
                               ws, local_date=date(2026, 10, 21), environment="prod",
                               details_link="https://minimoi.ai/guild-next/mc-operations")
    lines = text.splitlines()
    assert lines[2].startswith("- Local backup: failed (last success 50.0h ago)")
    assert "proposed:" in lines[2] and "[" in lines[2]
    assert "2 pending, oldest 30.0h" in text and "@" not in text
    assert lines[-1].startswith("Details: ")


def test_long_brief_is_capped_keeping_most_severe_first():
    ws = [make_watch(id=f"w{i:02d}", label=f"Watch {i:02d}") for i in range(30)]
    obs = [_obs(w, "failed" if i == 29 else "degraded", "job_late") for i, w in enumerate(ws)]
    text = render.render_brief(obs, ws, local_date=date(2026, 10, 21), environment="prod")
    lines = text.splitlines()
    assert len(lines) <= render.MAX_LINES
    assert "Watch 29: failed" in lines[2]
    assert lines[-1].startswith("... ")


def test_learning_line_only_when_nonzero():
    ws = [make_watch()]
    quiet = render.render_brief([_obs(ws[0], "healthy", "job_ok")], ws, local_date=date(2026, 10, 21),
                                environment="prod", learning_counts={"suggestions_pending": 0})
    assert "Learning" not in quiet
    busy = render.render_brief([_obs(ws[0], "healthy", "job_ok")], ws, local_date=date(2026, 10, 21),
                               environment="prod", learning_counts={"suggestions_pending": 2,
                                                                    "repeated_corrections": 1})
    assert "Learning: 2 learning suggestions pending · 1 repeated correction." in busy
