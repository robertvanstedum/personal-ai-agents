"""The Mac copy script (Spec 160 §4, T2, §4 headroom): publishes only a complete copy,
a failed run never touches the previous inbox, low disk writes nothing. Synthetic folders only."""
import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "staging" / "agent-memory-copy"


def run(src, dest, **extra):
    env = {**os.environ, "AGENT_MEMORY_MAC_SOURCE": str(src), "AGENT_MEMORY_MAC_INBOX": str(dest),
           "AGENT_MEMORY_MIN_FREE_KB": "1", **extra}
    return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)


def make_source(tmp_path):
    src = tmp_path / "memory"
    src.mkdir()
    (src / "MEMORY.md").write_text("# index\n")
    (src / "feedback_a.md").write_text("note\n")
    (src / "secret.json").write_text("{}")
    (src / "big.md").write_bytes(b"x" * (600 * 1024))
    (src / "link.md").symlink_to(src / "MEMORY.md")
    (src / "sub").mkdir()
    (src / "sub" / "deep.md").write_text("no subfolders\n")
    return src


def test_copies_only_small_regular_top_level_markdown_and_writes_the_manifest_last(tmp_path):
    src, dest = make_source(tmp_path), tmp_path / "inbox"
    result = run(src, dest)
    assert result.returncode == 0, result.stdout + result.stderr
    names = sorted(p.name for p in dest.iterdir())
    assert names == ["MEMORY.md", "_copy_manifest.json", "feedback_a.md"]       # no json, no big, no link, no subfolder
    manifest = json.loads((dest / "_copy_manifest.json").read_text())
    assert manifest["complete"] is True and manifest["file_count"] == 2 and manifest["copied_at"].endswith("Z")
    assert oct((dest / "MEMORY.md").stat().st_mode & 0o777) == "0o600"


def test_a_second_run_replaces_the_inbox_whole_and_leaves_no_temp_folders(tmp_path):
    src, dest = make_source(tmp_path), tmp_path / "inbox"
    run(src, dest)
    (src / "feedback_a.md").unlink()
    (src / "new.md").write_text("new\n")
    assert run(src, dest).returncode == 0
    assert sorted(p.name for p in dest.iterdir()) == ["MEMORY.md", "_copy_manifest.json", "new.md"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["inbox", "memory"]      # no .inbox.tmp.* or .prev left


def test_a_missing_source_fails_and_leaves_the_previous_inbox_untouched(tmp_path):
    src, dest = make_source(tmp_path), tmp_path / "inbox"
    run(src, dest)
    before = {p.name: p.read_bytes() for p in dest.iterdir()}
    result = run(tmp_path / "gone", dest)
    assert result.returncode == 4
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == before


def test_a_failed_copy_never_publishes_and_never_deletes(tmp_path):
    src, dest = make_source(tmp_path), tmp_path / "inbox"
    run(src, dest)
    before = {p.name: p.read_bytes() for p in dest.iterdir()}
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "rsync").write_text("#!/bin/sh\nexit 23\n")
    (fake / "rsync").chmod(0o755)
    result = run(src, dest, PATH=f"{fake}:{os.environ['PATH']}")
    assert result.returncode == 5
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == before
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".inbox")]


def test_low_disk_writes_nothing_and_deletes_nothing(tmp_path):
    src, dest = make_source(tmp_path), tmp_path / "inbox"
    run(src, dest)
    before = {p.name: p.read_bytes() for p in dest.iterdir()}
    result = run(src, dest, AGENT_MEMORY_MIN_FREE_KB=str(10 ** 12))
    assert result.returncode == 3 and "disk_low" in result.stdout
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == before


def test_not_configured_exits_2(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_MEMORY")}
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 2


def test_the_script_never_reads_file_contents_or_prints_names(tmp_path):
    src, dest = make_source(tmp_path), tmp_path / "inbox"
    (src / "private_diary.md").write_text("MARKER-ABC\n")
    result = run(src, dest)
    assert "private_diary" not in result.stdout + result.stderr and "MARKER-ABC" not in result.stdout + result.stderr
