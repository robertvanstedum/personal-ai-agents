"""Standing pause/resume and emergency disable, worker side."""
import json
import os

from core.mc_standing.control import Control
from core.mc_standing.spool import Spool


def write(path, doc):
    path.write_text(json.dumps(doc) if isinstance(doc, dict) else doc)


def ctl(tmp_path):
    spool = Spool(tmp_path / "state")
    return Control(tmp_path / "control.json", tmp_path / "DISABLED", spool), spool


def test_missing_or_bad_control_is_paused_fail_safe(tmp_path):
    c, _ = ctl(tmp_path)
    assert c.paused() is True
    assert c.acknowledged()["reason"] == "control_unreadable_fail_safe"
    for bad in ("{oops", {"schema": "other", "version": 1, "state": "enabled"},
                {"schema": "mc-standing-control/1", "version": 0, "state": "enabled"},
                {"schema": "mc-standing-control/1", "version": True, "state": "enabled"},
                {"schema": "mc-standing-control/1", "version": 2, "state": "running"}):
        write(tmp_path / "control.json", bad)
        assert c.paused() is True


def test_enabled_and_paused_versions_are_acknowledged(tmp_path):
    c, _ = ctl(tmp_path)
    write(tmp_path / "control.json", {"schema": "mc-standing-control/1", "version": 3, "state": "enabled"})
    assert c.paused() is False
    assert c.acknowledged()["applied_version"] == 3 and c.acknowledged()["applied_state"] == "enabled"
    write(tmp_path / "control.json", {"schema": "mc-standing-control/1", "version": 4, "state": "paused"})
    assert c.paused() is True and c.acknowledged()["applied_version"] == 4


def test_symlinked_or_oversize_control_is_paused(tmp_path):
    c, _ = ctl(tmp_path)
    real = tmp_path / "real.json"
    write(real, {"schema": "mc-standing-control/1", "version": 1, "state": "enabled"})
    os.symlink(real, tmp_path / "control.json")
    assert c.paused() is True
    os.unlink(tmp_path / "control.json")
    write(tmp_path / "control.json", "x" * 5000)
    assert c.paused() is True


def test_disable_file_kills_even_if_unreadable(tmp_path):
    c, _ = ctl(tmp_path)
    assert c.killed() is False
    (tmp_path / "DISABLED").write_text("")
    os.chmod(tmp_path / "DISABLED", 0)
    assert c.killed() is True
    os.chmod(tmp_path / "DISABLED", 0o600)
    os.unlink(tmp_path / "DISABLED")
    os.symlink(tmp_path / "nowhere", tmp_path / "DISABLED")       # dangling link still counts
    assert c.killed() is True


def test_runner_uses_control_for_pause(tmp_path):
    from test_spool_findings_runner import FakeIngress, _status, run
    _status(tmp_path / "src")
    c, spool = ctl(tmp_path)
    _, res = run(tmp_path, FakeIngress(), spool=spool, paused=c.paused, kill=c.killed)
    assert res["state"] == "paused"                              # no control doc → paused
    write(tmp_path / "control.json", {"schema": "mc-standing-control/1", "version": 1, "state": "enabled"})
    _, res = run(tmp_path, FakeIngress(), spool=spool, paused=c.paused, kill=c.killed)
    assert res["state"] == "delivered"
