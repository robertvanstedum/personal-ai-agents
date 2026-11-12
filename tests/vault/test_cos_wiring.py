"""The production CoS capture path's wiring (#509): the pinned Mac runtime, the jobs registry, the S3 access matrix and the nightly watch."""
from __future__ import annotations

import importlib.util
import json
import plistlib
from pathlib import Path

import pytest

from core.jobs import registry
from core.memory_sync import cos_batch as CB
from core.memory_sync import cos_pull as CP

REPO = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load("runtime_install_cos", "scripts/vault/runtime_install.py")
CHECK = _load("check_s3_access_cos", "scripts/vault/check_s3_access.py")


def config(tmp_path):
    doc = json.loads((REPO / "config/vault_runtime.example.json").read_text())
    doc["public_recipient"] = "age1" + "a" * 58
    for key in ["shelf_root", "inbox_root", "jobs_root", "logs_root", "ask_registry", "launch_agents"]:
        doc[key] = str(tmp_path / key)
    return doc


def _unlock(pin):
    for directory in [pin] + [p for p in pin.rglob("*") if p.is_dir()]:
        directory.chmod(0o700)


def test_a_cos_pull_identity_pins_the_cos_pull_agent_and_the_minimoi_cos_source(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    cfg["cos_pull_identity"] = "keychain:minimoi-vault-identity/age"
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(cfg))
    cfg = R.profile(p)
    monkeypatch.setattr(R, "dependency_receipt", lambda py: {"python": "test", "packages": {}})
    out = tmp_path / "prepared"
    result = R.prepare(REPO, cfg, out)
    pin = Path(result["runtime"])
    try:
        doc = plistlib.loads((out / "candidate-plists" / f"{R.COS_PULL_LABEL}.plist").read_bytes())
        assert doc["Label"] == "com.vanstedum.minimoi-vault-cos-pull"
        assert doc["StartInterval"] == 7200 and doc["RunAtLoad"] is True and "StartCalendarInterval" not in doc
        assert doc["ProgramArguments"][1:] == [str(pin / "scripts/vault/vault_cos_pull.py"), "run", "--execute",
                                               "--identity", "keychain:minimoi-vault-identity/age"]
        assert not any("IDENTITY" in k for k in doc["EnvironmentVariables"])
        assert (pin / "scripts/vault/vault_cos_pull.py").exists() and (pin / "core/memory_sync/cos_pull.py").exists()
        shelf = json.loads((pin / "config/memory_shelf.json").read_text())
        assert set(shelf["sources"]) == {"claude-code", "codex", "minimoi-cos"}
        assert shelf["sources"]["minimoi-cos"] == {"kind": "cos", "root": str(Path.home() / "minimoi-staging/cos-export"), "never_copy": []}
        assert str(Path.home() / R.COS_WATCH) == str(Path(CP.DEFAULT_WATCH).expanduser())
        assert any("`moi approve-source minimoi-cos`" in line for line in result["limitations"])
        assert not (out / "candidate-plists" / f"{R.LABELS['mc_pull']}.plist").exists()      # the mc pull stays its own choice
    finally:
        _unlock(pin)


def test_without_a_cos_pull_identity_there_is_no_agent_and_no_source(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(R, "dependency_receipt", lambda py: {"python": "test", "packages": {}})
    out = tmp_path / "prepared"
    result = R.prepare(REPO, cfg, out)
    pin = Path(result["runtime"])
    try:
        assert not (out / "candidate-plists" / f"{R.COS_PULL_LABEL}.plist").exists()
        assert "minimoi-cos" not in json.loads((pin / "config/memory_shelf.json").read_text())["sources"]
        assert any("no cos_pull_identity" in line for line in result["limitations"])
    finally:
        _unlock(pin)


@pytest.mark.parametrize("bad", ["AGE-SECRET-KEY-1ABC", "file:relative/path", "nope:x"])
def test_cos_pull_identity_must_be_a_spec_never_key_text(tmp_path, bad):
    p = tmp_path / "profile.json"
    doc = config(tmp_path)
    doc["cos_pull_identity"] = bad
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="invalid_cos_pull_identity"):
        R.profile(p)


def test_both_jobs_are_registered_and_not_judged_before_they_run():
    jobs = registry.load()
    pull, export = jobs.get("vault-cos-pull"), jobs.get("vault-cos-export")
    assert pull.host == "mac" and export.host == "ec2" and pull.active_from is None and export.active_from is None
    assert pull.runbook == "core/memory_sync/cos_pull.py" and export.runbook == "core/memory_sync/cos_export.py"


def test_the_s3_matrix_splits_the_cos_prefixes_like_mcs():
    mac, ec2 = CHECK.MATRIX["mac"], CHECK.MATRIX["ec2"]
    export, acks = CB.EXPORT_PREFIX + "/", CB.ACK_PREFIX + "/"
    assert acks in mac["put_allow"] and export in mac["put_deny"] and export in mac["get_allow"] and export in mac["list_allow"]
    assert export in ec2["put_allow"] and acks in ec2["put_deny"] and acks in ec2["get_allow"] and acks in ec2["list_allow"]
    assert {export, acks} <= set(mac["delete_deny"]) and {export, acks} <= set(ec2["delete_deny"])


def test_the_nightly_watch_runs_minimoi_cos_only_when_configured():
    dw = _load("daily_watch_cos", "scripts/memory/daily_watch.py")
    assert "minimoi-cos" in dw.OPTIONAL_SOURCES and "minimoi-cos" not in dw.SOURCES
