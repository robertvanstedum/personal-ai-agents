"""P0b gate: disk-full at every write of a cycle (and of the Telegram copy) fails closed and a
restart converges: one brief event, never a false 'delivered', never a second Telegram send
after an uncertain one, and no temporary files left behind."""
import errno

import pytest

from core.mc_standing.spool import Spool
from tests.mc_standing.test_spool_findings_runner import FakeIngress, _status, run
from tests.mc_standing.test_telegram_copy import Sender


def _count_writes(tmp_path, **kw):
    calls = {"n": 0}
    real = Spool._write

    def counting(self, path, data):
        calls["n"] += 1
        return real(self, path, data)
    Spool._write = counting
    try:
        _status(tmp_path / "src")
        run(tmp_path, FakeIngress(), **kw)
    finally:
        Spool._write = real
    return calls["n"]


def _disk_full_at(k):
    real = Spool._write
    calls = {"n": 0}

    def write(self, path, data):
        calls["n"] += 1
        if calls["n"] == k:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, path, data)
    return real, write


def _no_temp_files(root):
    return [p for p in root.rglob("*") if p.name.startswith(".") and p.name.endswith(".tmp")]


TOTAL = None


def test_every_write_point_of_a_cycle_fails_closed_and_recovers(tmp_path):
    total = _count_writes(tmp_path / "count")
    assert total > 5
    for k in range(1, total + 1):
        root = tmp_path / f"k{k}"
        _status(root / "src")
        ingress = FakeIngress()
        real, failing = _disk_full_at(k)
        Spool._write = failing
        try:
            try:
                spool, res = run(root, ingress)
                first_state = res["state"]
            except OSError as exc:
                assert exc.errno == errno.ENOSPC
                first_state = None
        finally:
            Spool._write = real
        spool = Spool(root / "state")
        cycle = [n for n in spool.names("cycles") if n.startswith("prod.")]
        if first_state != "delivered":
            # Fail closed: delivered is recorded only together with an acknowledgement.
            for name in cycle:
                rec = spool.read("cycles", name)
                assert rec["state"] != "delivered" or rec.get("ack"), (k, rec)
        _, res = run(root, ingress, spool=spool)
        assert res["state"] == "delivered" or res.get("reason") == "already_final", (k, res)
        assert len({e["event_id"] for e in ingress.posts}) == 1, k
        assert _no_temp_files(root / "state") == [], k


def test_disk_full_around_the_telegram_send_never_sends_twice(tmp_path):
    total = _count_writes(tmp_path / "count", telegram={"sender": Sender(), "destination_id": "c"})
    for k in range(1, total + 1):
        root = tmp_path / f"t{k}"
        _status(root / "src")
        sender, ingress = Sender(), FakeIngress()
        real, failing = _disk_full_at(k)
        Spool._write = failing
        try:
            try:
                run(root, ingress, telegram={"sender": sender, "destination_id": "c"})
            except OSError:
                pass
        finally:
            Spool._write = real
        spool = Spool(root / "state")
        _, res = run(root, ingress, spool=spool, telegram={"sender": sender, "destination_id": "c"})
        assert len(sender.sent) <= 1, k                       # never a second message
        assert res["channels"]["site"] == "delivered", (k, res)
        if len(sender.sent) == 1:
            assert res["channels"]["telegram"] in ("acknowledged", "uncertain"), (k, res)
        assert _no_temp_files(root / "state") == [], k


def test_disk_full_on_the_final_idle_status_does_not_mask_a_delivered_brief(tmp_path):
    _status(tmp_path / "src")
    real = Spool._write

    def write(self, path, data):
        if path.name == "worker-status.json" and b'"idle"' in data:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, path, data)
    Spool._write = write
    try:
        _, res = run(tmp_path, FakeIngress())
    finally:
        Spool._write = real
    assert res["state"] == "delivered"
