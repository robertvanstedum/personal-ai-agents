"""T8 the Agents light (agent-memory v0.4 §7) and the status files."""
import json
from datetime import timedelta

import pytest

from core.agent_memory.run import run_source
from core.agent_memory.status import (REASON_LIMIT, load_sources_status, memory_copy_state, memory_copy_states,
                                      update_status)
from core.agent_memory.sources import DirectorySource

from conftest import NOW, make_cfg, write_mac_manifest, write_tree


def iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ok(hours_ago, **extra):
    return {"enabled": True, "last_ok": True, "data_time": iso(hours_ago), "first_seen_at": iso(500), **extra}


@pytest.mark.parametrize("age,state", [(0, "green"), (36, "green"), (36.01, "yellow"), (72, "yellow"),
                                       (72.01, "red"), (200, "red")])
def test_age_thresholds(age, state):
    assert memory_copy_state(NOW, {"cos-agent-a": ok(age)})["state"] == state


def test_future_time_is_unknown():
    out = memory_copy_state(NOW, {"cos-agent-a": ok(-1)})
    assert out["state"] == "unknown"


def test_no_answer_is_unknown():
    assert memory_copy_state(NOW, None)["state"] == "unknown"
    assert memory_copy_state(NOW, {"cos-agent-a": None})["state"] == "unknown"
    assert memory_copy_state(NOW, {})["state"] == "unknown"


def test_mac_copy_40_hours_old_is_yellow_and_names_claude_code():
    out = memory_copy_state(NOW, {"cos-agent-a": ok(1), "claude-code": ok(40)})
    assert out == {"state": "yellow", "reason": "claude-code memory copy 40 h old"}
    assert len(out["reason"]) <= REASON_LIMIT


def test_worst_source_wins_and_disabled_sources_are_ignored():
    out = memory_copy_state(NOW, {"a": ok(1), "master-craftsman": ok(100), "claude-code": ok(40),
                                  "off": {"enabled": False, "data_time": None}})
    assert out["state"] == "red" and out["reason"].startswith("master-craftsman memory copy")
    assert set(memory_copy_states(NOW, {"a": ok(1), "off": {"enabled": False}})) == {"a"}


def test_enabled_never_succeeded():
    never = {"enabled": True, "last_ok": False, "data_time": None}
    assert memory_copy_state(NOW, {"s": dict(never, first_seen_at=iso(37))})["state"] == "red"
    assert memory_copy_state(NOW, {"s": dict(never, first_seen_at=iso(10))})["state"] == "yellow"
    assert memory_copy_state(NOW, {"s": {"enabled": True, "first_seen_at": iso(10)}})["state"] == "unknown"


def test_incomplete_last_run_is_at_least_yellow_and_old_data_stays_worse():
    assert memory_copy_state(NOW, {"s": ok(2, last_ok=False)}) == {"state": "yellow", "reason": "s memory copy incomplete"}
    assert memory_copy_state(NOW, {"s": ok(100, last_ok=False)})["state"] == "red"


def test_long_reason_is_truncated_to_the_light_limit():
    out = memory_copy_state(NOW, {"x" * 60: ok(40)})
    assert len(out["reason"]) == REASON_LIMIT and out["reason"].endswith("…")


def test_status_file_round_trip_and_staleness_drives_the_light(root, tmp_path):
    inbox = tmp_path / "inbox"
    write_tree(inbox, {"MEMORY.md": b"m"})
    write_mac_manifest(inbox, 1, copied_at=NOW - timedelta(hours=40))
    cfg = make_cfg("claude-code", source_kind="mac_folder", patterns=("*.md",))
    assert run_source(DirectorySource(inbox, require_copy_manifest=True), cfg, root, NOW).ok
    status = load_sources_status(root, ["claude-code", "missing-one"])
    assert status["missing-one"] is None
    assert memory_copy_state(NOW, {"claude-code": status["claude-code"]}) == \
        {"state": "yellow", "reason": "claude-code memory copy 40 h old"}
    # The Mac job dies; staging keeps running against the same stale manifest.
    assert run_source(DirectorySource(inbox, require_copy_manifest=True), cfg, root, NOW + timedelta(hours=40)).ok
    again = load_sources_status(root, ["claude-code"])["claude-code"]
    assert memory_copy_state(NOW + timedelta(hours=40), {"claude-code": again})["state"] == "red"   # 80 h


def test_status_file_holds_codes_times_counts_only(root, box):
    run_source(box.source, make_cfg(), root, NOW)
    doc = json.loads((root / "cos-agent-a" / "_status.json").read_text())
    assert set(doc) == {"schema_version", "source", "first_seen_at", "last_run_at", "last_ok", "last_code",
                        "last_complete", "last_success_at", "data_time", "last_snapshot", "counts"}
    assert "MEMORY.md" not in json.dumps(doc)


def test_failed_run_keeps_the_last_success_and_update_status_never_raises(root, tmp_path):
    folder = root / "s"
    assert update_status(folder, "s", NOW, ok=True, code=None, complete=True, counts={"files": 1})
    assert update_status(folder, "s", NOW + timedelta(days=1), ok=False, code="copy_incomplete", complete=False)
    doc = json.loads((folder / "_status.json").read_text())
    assert doc["last_ok"] is False and doc["last_code"] == "copy_incomplete"
    assert doc["data_time"] == "2026-10-03T12:00:00Z" and doc["counts"] == {"files": 1}
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert update_status(blocker / "sub", "s", NOW, ok=True, code=None, complete=True) is False
