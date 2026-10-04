"""Scheduler side of the copier (Spec 160 §6, T7): off by default, approved sources only,
codes in the status endpoint and never names or text."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.agent_memory.run import record_approval
from domains.cos import agent_memory_job as job

NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
MARKER = "TOPSECRET-MARKER-9Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "ws"
    (home / "memory").mkdir(parents=True)
    (home / "MEMORY.md").write_text(f"# mem\n{MARKER}\n")
    root = tmp_path / "agent-memory"
    cfg = {"schema_version": 1, "data_root": str(root), "headroom": {"min_free_bytes": 1, "min_free_fraction": None},
           "sources": {"claude-code": {"source_kind": "mac_folder", "agent": "claude-code", "runtime": "claude-code",
                                       "enabled": True, "inbox_env": "AM_TEST_INBOX", "patterns": ["*.md"]}}}
    cfg_path = tmp_path / "sources.json"
    cfg_path.write_text(json.dumps(cfg))
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "note.md").write_text("a memory note\n")
    (inbox / "_copy_manifest.json").write_text(json.dumps({"complete": True, "file_count": 1,
                                                          "copied_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    monkeypatch.setenv("AM_TEST_INBOX", str(inbox))
    monkeypatch.setenv(job.CONFIG_ENV, str(cfg_path))
    monkeypatch.setenv("AGENT_MEMORY_COPIER", "1")
    monkeypatch.delenv("COS_TURNS_DIR", raising=False)
    return root


def approve(env, **changes):
    cfg = job._load().sources["claude-code"]
    record_approval(env, "claude-code", "a" * 64, NOW, cfg.fingerprint())


def test_off_unless_the_scheduler_container_turns_it_on(monkeypatch):
    monkeypatch.delenv("AGENT_MEMORY_COPIER", raising=False)
    assert job.enabled() is False
    assert job.status_payload(NOW)["enabled"] is False


def test_an_unapproved_source_waits_and_copies_nothing(env):
    assert job.run_all(NOW) == {"claude-code": "awaiting_approval"}
    assert not (env / "claude-code" / "current").exists()


def test_an_approved_source_is_copied_and_the_status_has_no_names_or_text(env):
    approve(env)
    assert job.run_all(NOW) == {"claude-code": "ok"}
    assert (env / "claude-code" / "current" / "note.md").exists()
    payload = job.status_payload(NOW + timedelta(hours=1))
    blob = json.dumps(payload)
    assert payload["state"] == "green" and "note.md" not in blob and MARKER not in blob


def test_a_source_that_never_ran_turns_red_after_36_hours(env):
    job.run_all(NOW)                                       # stamps first seen; awaiting approval
    assert job.status_payload(NOW + timedelta(hours=10))["state"] == "unknown"
    late = job.status_payload(NOW + timedelta(hours=40))
    assert late["state"] == "red" and "never succeeded" in late["reason"]


def test_a_dead_job_shows_stale_not_green(env):
    approve(env)
    job.run_all(NOW)
    assert job.status_payload(NOW + timedelta(hours=40))["state"] == "yellow"
    assert job.status_payload(NOW + timedelta(hours=80))["state"] == "red"


def test_an_unreadable_config_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MEMORY_COPIER", "1")
    monkeypatch.setenv(job.CONFIG_ENV, str(tmp_path / "missing.json"))
    assert job.run_all(NOW) == {"_config": "unreadable"}
    assert job.status_payload(NOW)["state"] == "unknown"


def test_turn_log_status_files_are_included_as_codes_only(env, tmp_path, monkeypatch):
    folder = tmp_path / "cos-turns"
    (folder / "_status").mkdir(parents=True)
    (folder / "_status" / "cos-scheduler.json").write_text(json.dumps(
        {"last_success_at": "2026-10-03T11:00:00Z", "last_failure_code": "disk_low", "secret": MARKER}))
    monkeypatch.setenv("COS_TURNS_DIR", str(folder))
    payload = job.status_payload(NOW)
    assert payload["turn_log"]["cos-scheduler"]["last_failure_code"] == "disk_low"
    assert MARKER not in json.dumps(payload)                # only whitelisted keys are passed on


# ── approval is bound to the source it was given for (Codex review of M1, 2026-10-04) ──────────────────────

def rewrite_config(monkeypatch, tmp_path, **changes):
    path = tmp_path / "sources.json"
    doc = json.loads(path.read_text())
    doc["sources"]["claude-code"].update(changes)
    path.write_text(json.dumps(doc))


def test_an_approval_survives_ordinary_content_changes(env, tmp_path):
    approve(env)
    (tmp_path / "inbox" / "note.md").write_text("a changed memory note\n")
    assert job.run_all(NOW) == {"claude-code": "ok"}


@pytest.mark.parametrize("change", [{"include": ["docs/*"]}, {"never_copy": ["private/*"]}, {"patterns": ["*.md", "*.txt"]},
                                    {"agent": "someone-else"}, {"container": "another-container"},
                                    {"workspace_path": "/elsewhere"}, {"inbox_env": "AM_OTHER_INBOX"}])
def test_changing_what_a_source_is_or_may_copy_invalidates_its_approval(env, tmp_path, monkeypatch, change):
    approve(env)
    rewrite_config(monkeypatch, tmp_path, **change)
    assert job.run_all(NOW) == {"claude-code": "awaiting_approval"}
    assert not (env / "claude-code" / "current").exists()


def test_pointing_the_same_inbox_variable_at_another_folder_invalidates_the_approval(env, tmp_path, monkeypatch):
    approve(env)
    other = tmp_path / "other-inbox"
    other.mkdir()
    (other / "x.md").write_text("elsewhere\n")
    (other / "_copy_manifest.json").write_text(json.dumps({"complete": True, "file_count": 1,
                                                          "copied_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    monkeypatch.setenv("AM_TEST_INBOX", str(other))
    assert job.run_all(NOW) == {"claude-code": "awaiting_approval"}


def test_turning_a_source_off_and_on_does_not_change_its_approval(env, tmp_path, monkeypatch):
    approve(env)
    rewrite_config(monkeypatch, tmp_path, runtime_version="9.9", enabled=True)
    assert job.run_all(NOW) == {"claude-code": "ok"}


def test_an_approval_written_before_fingerprints_existed_does_not_count(env):
    record_approval(env, "claude-code", "a" * 64, NOW)                 # no fingerprint: the old format
    assert job.run_all(NOW) == {"claude-code": "awaiting_approval"}
