"""Spool backup and restore test (standing §11 activation prerequisite)."""
import fcntl
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "mc_standing"))
import backup_spool  # noqa: E402

from tests.mc_standing.test_spool_findings_runner import FakeIngress, _status, run


def delivered_spool(tmp_path):
    _status(tmp_path / "src")
    spool, res = run(tmp_path, FakeIngress())
    assert res["state"] == "delivered"
    return spool


def test_backup_then_verify_restores_every_record(tmp_path):
    spool = delivered_spool(tmp_path)
    (spool.root / "cycles" / ".partial.json.123.tmp").write_text("{")    # never captured
    out = backup_spool.backup(spool.root, tmp_path / "backups")
    assert out["state"] == "ok" and out["counts"]["outbox"] >= 1
    archive = tmp_path / "backups" / out["archive"]
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(archive) as tar:
        assert not any(".tmp" in m.name for m in tar.getmembers())
    res = backup_spool.verify(archive)
    assert res == {"state": "ok", "counts": out["counts"]}


def test_verify_fails_on_tampering(tmp_path):
    spool = delivered_spool(tmp_path)
    out = backup_spool.backup(spool.root, tmp_path / "b")
    archive = tmp_path / "b" / out["archive"]
    with open(archive, "ab") as fh:
        fh.write(b"x")
    assert backup_spool.verify(archive) == {"state": "failed", "reason": "digest_mismatch"}


def test_verify_refuses_unexpected_members(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    payload = tmp_path / "x.json"
    payload.write_text("{}")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="../../escape.json")
    (tmp_path / "evil.tar.gz.sha256").write_text(f"{backup_spool._sha256(archive)}  evil.tar.gz\n")
    assert backup_spool.verify(archive)["reason"] == "unexpected_member"


def test_corrupt_record_fails_the_restore_test(tmp_path):
    spool = delivered_spool(tmp_path)
    name = spool.names("cycles")[0]
    (spool.root / "cycles" / f"{name}.json").write_text("{not json")
    out = backup_spool.backup(spool.root, tmp_path / "b")
    assert backup_spool.verify(tmp_path / "b" / out["archive"])["state"] == "failed"


def test_backup_waits_for_and_never_overlaps_a_cycle(tmp_path):
    spool = delivered_spool(tmp_path)
    fd = os.open(spool.root / ".cycle.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(SystemExit) as exc:
            backup_spool.backup(spool.root, tmp_path / "b", wait_s=0)
        assert json.loads(str(exc.value)) == {"state": "busy", "reason": "cycle_in_flight"}
    finally:
        os.close(fd)
