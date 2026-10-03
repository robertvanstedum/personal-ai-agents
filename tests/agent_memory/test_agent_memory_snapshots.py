"""T1 snapshots, T2 partial never deletes (agent-memory v0.4 §3.1, §3.6)."""
import json
import os
import stat
from datetime import timedelta

import pytest

from core.agent_memory import publish as publish_mod
from core.agent_memory.run import run_source
from core.agent_memory.snapshot import read_manifest, rebuild_state, snapshot_names
from core.agent_memory.sources import DirectorySource, TarSource

from conftest import NOW, Box, make_cfg, make_tar, tree, write_mac_manifest, write_tree


def src(root, name="cos-agent-a"):
    return root / name


def current_tree(root, name="cos-agent-a"):
    base = src(root, name) / "current"
    return {k: v for k, v in tree(base).items() if k != "_manifest.json"}


def test_first_run_snapshots_everything_and_layout_matches_spec(root, box):
    result = run_source(box.source, make_cfg(), root, NOW)
    assert result.ok and result.snapshot == "2026-10-03T120000Z"
    base = src(root)
    assert current_tree(root) == box.files
    manifest = read_manifest(base / "current")
    assert manifest["schema_version"] == 1 and manifest["captured_at"] == "2026-10-03T12:00:00Z"
    assert manifest["source"] == {"agent": "cos-agent-a", "runtime": "openclaw", "runtime_version": "test",
                                  "source_kind": "docker_workspace"}
    assert [f["path"] for f in manifest["files"]] == ["MEMORY.md", "memory/2026-10-01.md"]
    entry = manifest["files"][0]
    assert set(entry) == {"path", "sha256", "size", "sanitized", "source_sha256", "sanitizers", "redactions"}
    assert manifest["deleted"] == [] and "skipped" in manifest
    snap = read_manifest(base / "history" / result.snapshot)
    assert len(snap["files"]) == 2 and snap["run_id"] == manifest["run_id"]
    assert (base / "_status.json").exists()
    assert "/Users/" not in json.dumps(manifest)


def test_permissions_are_0600_and_0700(root, box):
    run_source(box.source, make_cfg(), root, NOW)
    for path in [root, *root.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600), path.name


def test_unchanged_files_make_no_snapshot(root, box, later):
    run_source(box.source, make_cfg(), root, NOW)
    again = run_source(box.source, make_cfg(), root, later(hours=24))
    assert again.ok and again.snapshot is None
    assert snapshot_names(src(root)) == ["2026-10-03T120000Z"]


def test_changed_file_goes_into_a_new_snapshot_alone(root, box, later):
    run_source(box.source, make_cfg(), root, NOW)
    box.files["MEMORY.md"] = b"# memory\nbeta\n"
    r = run_source(box.source, make_cfg(), root, later(hours=24))
    snap = read_manifest(src(root) / "history" / r.snapshot)
    assert [f["path"] for f in snap["files"]] == ["MEMORY.md"] and snap["deleted"] == []
    assert current_tree(root) == box.files


def test_deleted_file_is_listed_after_a_complete_scan(root, box, later):
    run_source(box.source, make_cfg(), root, NOW)
    del box.files["memory/2026-10-01.md"]
    r = run_source(box.source, make_cfg(), root, later(hours=24))
    snap = read_manifest(src(root) / "history" / r.snapshot)
    assert snap["deleted"] == ["memory/2026-10-01.md"] and snap["files"] == []
    assert read_manifest(src(root) / "current")["deleted"] == ["memory/2026-10-01.md"]
    assert current_tree(root) == box.files


def test_two_runs_on_one_day_make_two_snapshots(root, box, later):
    run_source(box.source, make_cfg(), root, NOW)
    box.files["MEMORY.md"] = b"changed"
    run_source(box.source, make_cfg(), root, later(minutes=5))
    box.files["MEMORY.md"] = b"changed again"
    run_source(box.source, make_cfg(), root, later(minutes=5))   # same second as previous clock reading
    assert len(snapshot_names(src(root))) == 3


def test_rebuilt_state_matches_each_runs_current(root, box, later):
    states = []
    for step in range(5):
        if step == 1:
            box.files["memory/new.md"] = b"new"
        if step == 2:
            box.files["MEMORY.md"] = b"edit"
        if step == 3:
            del box.files["memory/2026-10-01.md"]
        if step == 4:
            box.files["memory/2026-10-01.md"] = b"back again"   # deleted then re-added
        r = run_source(box.source, make_cfg(), root, later(hours=24 * step))
        assert r.ok
        states.append((r.snapshot, dict(box.files)))
        assert rebuild_state(src(root)) == current_tree(root) == box.files
    for name, files in states:
        assert rebuild_state(src(root), name) == files


# ---------------------------------------------------------------- T2


def _published(root, box):
    run_source(box.source, make_cfg(), root, NOW)
    return current_tree(root), snapshot_names(src(root))


def test_truncated_tar_publishes_nothing_and_deletes_nothing(root, box, later):
    before = _published(root, box)
    good = make_tar(box.files)
    for cut in (700, 1030, 2050):      # inside a header, and inside each file's data
        r = run_source(TarSource(lambda cut=cut: good[:cut]), make_cfg(), root, later(hours=24))
        assert not r.ok and r.code == "copy_incomplete"
        assert (current_tree(root), snapshot_names(src(root))) == before


def test_tar_cut_inside_a_skipped_member_is_still_incomplete(root, box):
    before = _published(root, box)
    files = dict(box.files, **{"memory/x.sqlite": b"\x01" * 4000})
    good = make_tar(files)
    r = run_source(TarSource(lambda: good[:2400]), make_cfg(), root, NOW + timedelta(days=1))
    assert not r.ok and (current_tree(root), snapshot_names(src(root))) == before


def test_empty_archive_is_not_a_complete_empty_scan(root, box):
    before = _published(root, box)
    r = run_source(TarSource(lambda: b""), make_cfg(), root, NOW + timedelta(days=1))
    assert not r.ok and (current_tree(root), snapshot_names(src(root))) == before


@pytest.mark.parametrize("manifest", ["missing", "incomplete", "count_mismatch", "garbage", "bad_time"])
def test_mac_copy_manifest_rules(root, box, tmp_path, manifest):
    before = _published(root, box)          # earlier state to protect (different source dir below)
    inbox = tmp_path / "inbox"
    write_tree(inbox, {"MEMORY.md": b"m", "feedback_a.md": b"a"})
    if manifest == "incomplete":
        write_mac_manifest(inbox, 2, complete=False)
    elif manifest == "count_mismatch":
        write_mac_manifest(inbox, 3)
    elif manifest == "garbage":
        (inbox / "_copy_manifest.json").write_text("{not json")
    elif manifest == "bad_time":
        (inbox / "_copy_manifest.json").write_text(json.dumps({"complete": True, "file_count": 2, "copied_at": "soon"}))
    cfg = make_cfg("claude-code", source_kind="mac_folder", patterns=("*.md",))
    # Seed a published state, then a bad manifest must not delete it.
    good = tmp_path / "good"
    write_tree(good, {"MEMORY.md": b"m", "feedback_a.md": b"a"})
    write_mac_manifest(good, 2)
    assert run_source(DirectorySource(good, require_copy_manifest=True), cfg, root, NOW).ok
    seeded = (current_tree(root, "claude-code"), snapshot_names(src(root, "claude-code")))
    r = run_source(DirectorySource(inbox, require_copy_manifest=True), cfg, root, NOW + timedelta(days=1))
    assert not r.ok and r.code == "copy_incomplete"
    assert (current_tree(root, "claude-code"), snapshot_names(src(root, "claude-code"))) == seeded
    assert len(seeded[0]) == 2


def test_mac_complete_copy_uses_copied_at_as_data_time(root, tmp_path):
    inbox = tmp_path / "inbox"
    write_tree(inbox, {"MEMORY.md": b"m"})
    write_mac_manifest(inbox, 1, copied_at=NOW - timedelta(hours=40))
    cfg = make_cfg("claude-code", source_kind="mac_folder", patterns=("*.md",))
    assert run_source(DirectorySource(inbox, require_copy_manifest=True), cfg, root, NOW).ok
    assert json.loads((src(root, "claude-code") / "_status.json").read_text())["data_time"] == "2026-10-01T20:00:00Z"


STEPS = ["tmp_written", "validated", "snapshot_staged", "snapshot_published", "current_moved_aside", "current_swapped"]


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("hard_crash", [False, True])
def test_crash_mid_publish_publishes_nothing_and_next_run_recovers(root, box, later, monkeypatch, step, hard_crash):
    before = _published(root, box)
    box.files["MEMORY.md"] = b"edited after the crash window"
    del box.files["memory/2026-10-01.md"]

    def boom(name):
        if name == step:
            raise RuntimeError("crash-canary-text")
    with monkeypatch.context() as m:
        m.setattr(publish_mod, "_checkpoint", boom)
        if hard_crash:           # a power cut: no inline cleanup either
            m.setattr(publish_mod, "recover", lambda *a, **k: None)
        r = run_source(box.source, make_cfg(), root, later(hours=24))
    assert not r.ok and r.code == "internal"
    if not hard_crash and step != "current_swapped":     # the last step is after the commit
        assert (current_tree(root), snapshot_names(src(root))) == before
    if hard_crash:
        assert any(p.name.startswith(".tmp-") for p in src(root).iterdir())   # the crash left its mess
    again = run_source(box.source, make_cfg(), root, later(hours=48))
    assert again.ok
    assert current_tree(root) == box.files
    assert rebuild_state(src(root)) == box.files
    leftovers = [p.name for p in src(root).iterdir() if p.name.startswith(".tmp-") or p.name == "current.prev"]
    assert leftovers == []
    assert len(snapshot_names(src(root))) == 2      # exactly one new snapshot, whichever step failed


def test_failed_first_run_leaves_no_current(root, box, monkeypatch):
    monkeypatch.setattr(publish_mod, "_checkpoint", lambda s: (_ for _ in ()).throw(OSError("x")) if s == "snapshot_published" else None)
    r = run_source(box.source, make_cfg(), root, NOW)
    assert not r.ok and r.code == "disk_write_failed"
    assert not (src(root) / "current").exists() and snapshot_names(src(root)) == []
