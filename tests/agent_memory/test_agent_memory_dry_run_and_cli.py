"""Dry run (terminal only) and the approval gate on real copies (agent-memory v0.4 §3.5)."""
import json
import os
from datetime import timedelta

import pytest

from core.agent_memory import dry_run, run as run_mod
from core.agent_memory.config import ConfigError, load_config, parse_config
from core.agent_memory.status import STATUS_FILE

from agent_memory_helpers import NOW, make_cfg, tree, write_mac_manifest, write_tree

FILES = {"MEMORY.md": b"api key: sk-ant-FAKEFAKEFAKE0000canary\nhello\n", "feedback_a.md": b"clean\n",
         ".env": b"K=v", "auth.json": b"{}", "notes.txt": b"t", "private.md": b"mine"}


@pytest.fixture
def mac(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    write_tree(inbox, FILES)
    write_mac_manifest(inbox, len(FILES))
    config = tmp_path / "cfg.json"
    config.write_text(json.dumps({"schema_version": 1, "data_root": str(tmp_path / "data"), "sources": {
        "claude-code": {"source_kind": "mac_folder", "runtime": "claude-code", "inbox_env": "TEST_INBOX",
                        "patterns": ["*.md"], "never_copy": ["private.md"]}}}))
    monkeypatch.setenv("TEST_INBOX", str(inbox))
    return tmp_path, config


def test_dry_run_prints_rows_to_stdout_and_writes_nothing(mac, capsys, monkeypatch):
    tmp_path, config = mac
    monkeypatch.chdir(tmp_path)
    before = tree(tmp_path)
    assert dry_run.main(["--source", "claude-code", "--config", str(config)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert tree(tmp_path) == before and not (tmp_path / "data").exists()
    rows = {line.split("\t")[1]: line.split("\t")[0] + ":" + line.split("\t")[2] for line in out if not line.startswith("#")}
    assert rows == {"MEMORY.md": "would-copy:redactions=1", "feedback_a.md": "would-copy:redactions=0",
                    ".env": "skip:hidden", "auth.json": "skip:denied_name", "notes.txt": "skip:not_markdown",
                    "private.md": "skip:never_copy"}
    assert out[-1].startswith("# listing_sha256=")
    assert "sk-ant-FAKE" not in "\n".join(out)


def test_dry_run_logs_nothing(mac, caplog, capsys):
    import logging
    caplog.set_level(logging.DEBUG)
    dry_run.main(["--source", "claude-code", "--config", str(mac[1])])
    assert caplog.records == []


def test_dry_run_on_an_incomplete_mac_copy_lists_nothing(mac, capsys):
    tmp_path, config = mac
    (tmp_path / "inbox" / "_copy_manifest.json").unlink()
    assert dry_run.main(["--source", "claude-code", "--config", str(config)]) == 2
    out = capsys.readouterr().out
    assert "incomplete" in out and "MEMORY.md" not in out


def listing_hash(capsys, config):
    dry_run.main(["--source", "claude-code", "--config", str(config)])
    return capsys.readouterr().out.strip().splitlines()[-1].split("=")[1]


def test_real_copy_is_refused_without_a_matching_dry_run_hash(mac, capsys):
    tmp_path, config = mac
    data = tmp_path / "data"
    for argv in ([], ["--approved-by-dry-run", "0" * 64]):
        assert run_mod.main(["--source", "claude-code", "--config", str(config), *argv]) == 3
        assert not data.exists()
    assert "refused" in capsys.readouterr().out


def test_real_copy_runs_with_the_approved_hash_and_stops_when_the_listing_changes(mac, capsys):
    tmp_path, config = mac
    approved = listing_hash(capsys, config)
    assert run_mod.main(["--source", "claude-code", "--config", str(config), "--approved-by-dry-run", approved]) == 0
    stored = tree(tmp_path / "data" / "claude-code" / "current")
    assert sorted(k for k in stored if k != "_manifest.json") == ["MEMORY.md", "feedback_a.md"]
    assert b"sk-ant-FAKE" not in stored["MEMORY.md"]
    # A new file appears after approval: the listing changed, so the old approval no longer covers it.
    (tmp_path / "inbox" / "new_note.md").write_text("new")
    write_mac_manifest(tmp_path / "inbox", len(FILES) + 1)
    assert run_mod.main(["--source", "claude-code", "--config", str(config), "--approved-by-dry-run", approved]) == 3
    # Changed content alone (same listing) does not need a new approval.
    (tmp_path / "inbox" / "new_note.md").unlink()
    write_mac_manifest(tmp_path / "inbox", len(FILES))
    (tmp_path / "inbox" / "feedback_a.md").write_text("edited")
    assert run_mod.main(["--source", "claude-code", "--config", str(config), "--approved-by-dry-run", approved]) == 0


def test_config_errors_are_fixed_text(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope SECRET-VALUE")
    assert dry_run.main(["--source", "x", "--config", str(bad)]) == 2
    assert "SECRET" not in capsys.readouterr().out
    with pytest.raises(ConfigError):
        parse_config({"schema_version": 1, "sources": {"a": {"source_kind": "nope"}}})


def test_example_config_loads_with_no_absolute_paths():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "config" / "agent_memory_sources.example.json"
    text = path.read_text()
    assert "/Users/" not in text and "/Volumes/" not in text
    cfg = load_config(path)
    assert set(cfg.sources) == {"cos-agent-a", "master-craftsman", "claude-code"}
    assert cfg.sources["claude-code"].source_kind == "mac_folder" and cfg.sources["claude-code"].patterns == ("*.md",)
    assert cfg.sources["cos-agent-a"].workspace_path.endswith("workspace-cos-agent-a")
    assert cfg.sources["master-craftsman"].workspace_path.endswith("workspace-mc")
    assert all(not s.enabled for s in cfg.sources.values())    # nothing runs until Robert approves a dry run


def test_docker_source_is_a_marked_stub(tmp_path):
    from core.agent_memory.sources import DockerArchiveSource
    from core.agent_memory.run import run_source
    r = run_source(DockerArchiveSource("c", "/w"), make_cfg(), tmp_path / "data", NOW)
    assert not r.ok and r.code == "internal"      # NotImplementedError is recorded as a fixed code, not raised
    with pytest.raises(NotImplementedError):
        DockerArchiveSource("c", "/w").read()
