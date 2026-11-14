"""#585: when the production deploy cannot refresh MC's repository copy (REPO_COPY_STATUS.txt says status=skipped), or the
copy is not at the commit the deploy wanted, job health says so where MC and Robert read it, not only in the deploy log.
The watchdog reads the record where the deploy already writes it; the clock is injected; synthetic files only."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.jobs import health, registry

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
A, B = "a" * 40, "b" * 40


def entry(path, **kw):
    row = {"id": "mc-repo-copy-deploy", "name": "MC repository copy", "host": "ec2",
           "schedule": {"text": "each deploy", "kind": "interval", "every_s": 86400},
           "expected_every_s": 86400, "missed_after_s": 86400, "stuck_after_s": 3600,
           "evidence": {"kind": "repo_copy", "path": str(path)}, "owner": "robert", "runbook": "x"}
    row.update(kw)
    return registry.parse({"schema_version": 1, "jobs": [row]})


def seen(path, text):
    if text is not None:
        Path(path).write_text(text)
    return health.observe(entry(path).jobs[0], {}, NOW)


@pytest.mark.parametrize("text,state,code", [
    (f"status=ok commit={A} wanted={A} at=2026-11-14T10:00:00Z\n", "green", "ok"),
    (f"status=skipped commit={B} wanted={A} at=2026-11-14T10:00:00Z\n", "yellow", "repo_skipped"),
    (f"status=skipped commit= wanted={A} at=2026-11-14T10:00:00Z\n", "yellow", "repo_skipped"),
    (f"status=ok commit={B} wanted={A} at=2026-11-14T10:00:00Z\n", "yellow", "repo_behind"),
    ("garbage\n", "yellow", "repo_unreadable"),
    ("status=ok " + "x" * 2000, "yellow", "repo_unreadable"),
    (None, "unknown", "not_tracked"),                                 # no record yet: not watched, never healthy
])
def test_the_record_is_judged(tmp_path, text, state, code):
    doc, verdict = seen(tmp_path / "REPO_COPY_STATUS.txt", text)
    assert (verdict.state, verdict.code) == (state, code)


def test_a_skipped_refresh_reads_as_watch_with_commits_and_since_when(tmp_path):
    path = tmp_path / "REPO_COPY_STATUS.txt"
    path.write_text(f"status=skipped commit={B} wanted={A} at=2026-11-14T10:00:00Z\n")
    report = health.host_report(entry(path), "ec2", {}, NOW)
    [item] = health.daily_items({"ec2": report}, NOW)
    assert item["severity"] == health.WATCH and item["off_since"] == "2026-11-14T10:00:00Z"
    words = health.words([item], hosts=["ec2"])
    assert "could not refresh MC's repository copy" in words and "for 5 h" in words
    assert f"copy at {B[:12]}, deploy wanted {A[:12]}" in words


def test_only_commit_ids_reach_the_report(tmp_path):
    doc, _ = seen(tmp_path / "s.txt", "status=skipped commit=<script>x wanted=ghp_secret at=bad\n")
    assert "script" not in doc["summary"] and "ghp" not in doc["summary"] and "unknown" in doc["summary"]


def test_a_fail_marker_is_refused_for_this_kind(tmp_path):
    with pytest.raises(registry.RegistryError):
        entry(tmp_path / "s.txt", evidence={"kind": "repo_copy", "path": str(tmp_path / "s.txt"), "fail_marker": "X"})


def test_the_shipped_list_reads_the_file_where_the_deploy_writes_it():
    job = registry.load().get("mc-repo-copy-deploy")
    assert job.host == "ec2" and job.evidence == {"kind": "repo_copy", "path": "/opt/minimoi/mc/work/REPO_COPY_STATUS.txt"}
    assert "S=/opt/minimoi/mc/work/REPO_COPY_STATUS.txt" in (REPO / ".github/workflows/deploy.yml").read_text()


def test_mc_job_health_block_shows_it(tmp_path):
    from minimoi_portal.guild_ui import jobs_health
    path = tmp_path / "REPO_COPY_STATUS.txt"
    path.write_text(f"status=skipped commit={B} wanted={A} at=2026-11-14T10:00:00Z\n")
    reg = entry(path)
    folder = tmp_path / "health"
    folder.mkdir()
    (folder / "health-ec2.json").write_text(json.dumps(health.host_report(reg, "ec2", {}, NOW)))
    text = jobs_health.block("robert", environ={"MINIMOI_SUPERVISION_OWNER": "robert"}, now=NOW, root=str(folder),
                             registry_loader=lambda: reg)
    assert "[watch] MC repository copy (ec2): the deploy could not refresh MC's repository copy" in text
