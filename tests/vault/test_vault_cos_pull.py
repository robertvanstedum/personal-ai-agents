"""``vault-cos-pull``, the Mac leg of the production CoS capture path (#509). A stand-in EC2 writes batches with the contract alone
(``mc_batch`` with the ``COS`` family, ``B.encrypt``); the destination is file://; capture runs the real ``minimoi-cos`` watcher on a
synthetic shelf whose config roots the source at the watch folder. Invented words only."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.memory_backup import backup as B
from core.memory_shelf import approvals
from core.memory_shelf import config as shelf_config
from core.memory_shelf.shelf import Shelf
from core.memory_sync import cos_batch as CB
from core.memory_sync import cos_pull as CP
from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_pull as MP
from tests.memory_backup.test_production_transport import make_age
from tests.memory_shelf.test_mc_capture import OWNER
from tests.vault.cos_history import History, name

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("vault_cos_pull_cli", ROOT / "scripts/vault/vault_cos_pull.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)
T0 = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)


def bid_at(minutes: int) -> str:
    return f"{(T0 + timedelta(minutes=minutes)):%Y%m%dT%H%M%SZ}-0a1b2c3d"


class W:
    def __init__(self, tmp: Path, monkeypatch):
        monkeypatch.setenv("MOI_RESERVE_GIB", "0")
        monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
        self.tmp = tmp
        self.age, self.recipient, self.identity = make_age(tmp)
        self.dest = f"file://{tmp / 'dest'}"
        self.d = B.Dest(self.dest)
        self.history = History(tmp / "history")
        self.jobs, self.state, self.watch = tmp / "jobs", tmp / "state", tmp / "cos-export"
        self.shelf_root = tmp / "shelf"
        self.config = tmp / "memory_shelf.json"
        self.alerts = []
        self.write_config({"minimoi-cos": {"kind": "cos", "root": str(self.watch), "never_copy": []}})

    def write_config(self, sources):
        self.config.write_text(json.dumps({"schema_version": 1, "shelf_root": str(self.shelf_root), "inbox_root": str(self.tmp / "inbox"),
                                           "headroom": {"min_free_bytes": 0}, "never_copy": [], "sources": sources}))

    def send(self, bid, docs: dict, environment="prod"):
        """The stand-in EC2: {name: bytes} -> one COS batch, COMPLETE last."""
        work = self.tmp / "ec2" / bid
        work.mkdir(parents=True)
        files = []
        for n, data in docs.items():
            (work / n).write_bytes(data)
            files.append((n, work / n, B.sha256_file(work / n), len(data)))
        res = MB.write_tar(files, bid, environment, work / "p.tar.gz", CB.COS)
        B.encrypt(self.age, self.recipient, work / "p.tar.gz", work / MB.BATCH_OBJECT)
        m = MB.manifest(bid, environment, res["files"], created=T0, cipher=work / MB.BATCH_OBJECT, plain_sha=B.sha256_file(work / "p.tar.gz"),
                        plain_bytes=(work / "p.tar.gz").stat().st_size, age_version="test", recipient=self.recipient, fam=CB.COS)
        (work / B.MANIFEST).write_text(json.dumps(m))
        (work / B.COMPLETE).write_text(json.dumps(MB.complete(m, B.sha256_file(work / B.MANIFEST), CB.COS)))
        MB.upload(self.d, bid, work / MB.BATCH_OBJECT, work / B.MANIFEST, work / B.COMPLETE, fam=CB.COS)

    def doc(self, cid, environment="prod", meta_change=None) -> bytes:
        meta = json.loads(self.history.meta_path(cid).read_text())
        meta.update(meta_change or {})
        return CB.export_doc(environment, cid, json.dumps(meta).encode(), self.history.turns_path(cid).read_bytes())

    def run(self, **kw):
        args = dict(dest=self.dest, identity_spec=self.identity, age_bin=self.age, expect_age=None, jobs_root=self.jobs, watch=self.watch,
                    state_dir=self.state, config_path=str(self.config), now=lambda: T0 + timedelta(hours=1),
                    usage=lambda p: SimpleNamespace(free=1 << 42), alert_send=lambda t: self.alerts.append(t) or "not_sent")
        args.update(kw)
        return CP.run_pull(**args)

    def ack(self, bid):
        p = self.tmp / "dest" / CB.ACK_PREFIX / f"{bid}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def approve(self):
        shelf = Shelf(self.shelf_root, min_free_bytes=0)
        shelf.init_layout()
        from core.memory_shelf import watchers
        cfg = shelf_config.load(str(self.config)).sources["minimoi-cos"]
        watchers.run_source(shelf, cfg, now=T0)
        approvals.approve_source(shelf, "minimoi-cos", cfg.fingerprint(), OWNER)
        return shelf


@pytest.fixture
def w(tmp_path, monkeypatch):
    return W(tmp_path, monkeypatch)


def test_files_are_placed_acknowledged_and_wait_for_approval(w):
    a = w.history.conversation(("KILN-SECRET when?", "KILN-SECRET Tuesday"))
    b = w.history.conversation(("GLAZE-SECRET which?", None))
    bid = bid_at(0)
    w.send(bid, {name("prod", a): w.doc(a), name("prod", b): w.doc(b)})
    out = w.run()
    assert out["results"]["pull"] == "ok" and out["results"]["placed"] == "2" and out["results"]["capture"] == "not_approved"
    assert out["state"] == "warn" and {s for _n, _h, s in w.ack(bid)["files"]} == {"placed"}
    assert w.ack(bid)["format"] == CB.ACK_FORMAT
    assert not (w.tmp / "dest" / MB.ACK_PREFIX).exists()            # nothing under MC's acknowledgement prefix
    shelf = w.approve()
    again = w.run()
    assert again["results"]["capture"] == "ok" and again["results"]["captured"] == "2" and again["state"] == "ok"
    assert f"minimoi-cos:prod:{a}" in shelf.index()
    status = (w.jobs / "vault-cos-pull.json").read_text()
    assert "KILN-SECRET" not in status and "TITLE-SECRET" not in status


@pytest.mark.parametrize("change", [{"private": True}, {"private": None}, {"environment": "dev"}])
def test_a_file_whose_metadata_may_not_travel_is_rejected_and_never_placed(w, change):
    good = w.history.conversation(("KILN-SECRET when?", "KILN-SECRET Tuesday"))
    bad = w.history.conversation(("PRIVATE-SECRET words", "PRIVATE-SECRET reply"))
    bid = bid_at(0)
    w.send(bid, {name("prod", good): w.doc(good), name("prod", bad): w.doc(bad, meta_change=change)})
    out = w.run()
    acks = {n: s for n, _h, s in w.ack(bid)["files"]}
    assert acks == {name("prod", good): "placed", name("prod", bad): "rejected:not_an_export"}
    assert out["results"]["rejected"] == "1" and not (w.watch / name("prod", bad)).exists()
    assert not any("PRIVATE-SECRET" in p.read_text() for p in w.watch.iterdir())


def test_a_batch_for_another_environment_is_rejected_whole(w):
    cid = w.history.conversation(("DEV-SECRET words", "DEV-SECRET reply"), environment="dev")
    bid = bid_at(0)
    w.send(bid, {name("dev", cid): w.doc(cid, "dev")}, environment="dev")
    out = w.run()                                                   # this pull accepts prod only: the manifest itself is refused
    assert out["results"]["pull"] == "ok" and out["results"]["placed"] == "0" and w.ack(bid)["files"] == []
    assert bid in json.loads((w.state / "pulled.json").read_text()) and list(w.watch.iterdir()) == []


def test_capture_needs_a_cos_kind_source_at_the_watch_folder(w):
    cid = w.history.conversation(("KILN-SECRET when?", None))
    w.send(bid_at(0), {name("prod", cid): w.doc(cid)})
    w.write_config({})
    assert w.run()["results"]["capture"] == "not_configured"
    w.write_config({"minimoi-cos": {"kind": "mc", "root": str(w.watch), "never_copy": []}})
    assert w.run()["results"]["capture"] == "source_root_mismatch"


def test_only_this_familys_temp_files_are_swept(w):
    w.watch.mkdir(mode=0o700)
    cos_tmp = w.watch / f".{name('prod', 'cosc-0123456789abcdef01234567')}.tmp-123-0a1b2c3d"
    mc_tmp = w.watch / ".production--c-0123456789ab.json.tmp-123-0a1b2c3d"
    cos_tmp.write_text("x")
    mc_tmp.write_text("x")
    assert w.run()["results"]["temp_swept"] == "1" and not cos_tmp.exists() and mc_tmp.exists()


def test_it_shares_the_vault_batch_lock(w):
    w.jobs.mkdir(parents=True)
    fd = os.open(str(w.jobs / MP.LOCK_NAME), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        out = w.run()
        assert out["state"] is None and out["results"] == {"pull": "busy"} and not (w.jobs / "vault-cos-pull.json").exists()
    finally:
        os.close(fd)


def test_the_script_plans_and_refuses_without_an_identity(w, capsys):
    base = ["--dest", w.dest, "--watch", str(w.watch), "--state-dir", str(w.state), "--config", str(w.config), "--jobs-root", str(w.jobs)]
    assert CLI.main(["plan", *base]) == CLI.EXIT_OK
    plan = json.loads(capsys.readouterr().out)
    assert plan["job"] == "vault-cos-pull" and plan["capture_source"] == "no_dry_run" and plan["environment"] == "prod"
    assert CLI.main(["run", "--execute", *base]) == CLI.EXIT_REFUSED
    assert CLI.main(["run", "--execute", "--identity", "AGE-SECRET-KEY-1XYZ", *base]) == CLI.EXIT_REFUSED
