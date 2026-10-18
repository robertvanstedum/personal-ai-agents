"""vault-mc-pull, the Mac leg of the production MC capture path (17 Oct 2026). Synthetic export files only, a file:// destination, the
pinned real `age` when it is installed (else the transport tests' stand-in), an injected clock and free-space probe, and a stand-in for
EC2's vault-mc-export that writes batches with the contract alone (mc_batch.write_tar, B.encrypt, mc_batch.manifest/complete/upload).
Capture runs against a synthetic shelf whose config (MOI_CONFIG's role, passed as config_path) roots ``minimoi-mc`` at the watch folder."""
from __future__ import annotations

import errno
import fcntl
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.jobs import registry, status
from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_shelf import approvals, mc
from core.memory_shelf import events as ev
from core.memory_shelf.shelf import Shelf
from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_pull as P
from tests.memory_backup.test_production_transport import make_age

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("vault_mc_pull_cli", ROOT / "scripts/vault/vault_mc_pull.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)

T0 = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
PLENTY = SimpleNamespace(free=1 << 42)
OWNER = ev.OwnerAuthority("moi-approve")
A, Bc, C = "c-0123456789ab", "c-ba9876543210", "c-00000000000c"
SECRET = "KILN-SECRET-WORDS"                    # conversation text: must never appear outside the watch folder and the shelf


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def bid_at(minutes: int, tag: str = "0a1b2c3d") -> str:
    return f"{(T0 + timedelta(minutes=minutes)):%Y%m%dT%H%M%SZ}-{tag}"


def export_doc(cid: str, text: str = SECRET, instance: str = "production") -> bytes:
    """The exporter's shape (core/memory_shelf/mc.py load), minimal."""
    doc = {"schema": mc.SCHEMA, "instance": instance, "conversation_id": cid, "source_id": f"{instance}:{cid}", "archived": False,
           "legacy": False, "scope": None, "created_at": "2026-10-17T10:00:00+00:00", "updated_at": "2026-10-17T10:00:00+00:00",
           "turns": [{"note_id": 1, "created_at": "2026-10-17T10:00:00+00:00", "author_kind": "owner", "who": "owner", "label": "Owner",
                      "voice": None, "text": text},
                     {"note_id": 2, "created_at": "2026-10-17T10:01:00+00:00", "author_kind": "agent", "who": "master_craftsman",
                      "label": "Master Craftsman", "voice": None, "text": "reply " + text}]}
    return json.dumps(doc, sort_keys=True, indent=1).encode()


class W:
    """One Mac and one stand-in EC2 exporter sharing a file:// destination."""

    def __init__(self, tmp: Path, monkeypatch):
        monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
        self.tmp = tmp
        self.age_bin, self.recipient, self.identity = make_age(tmp)
        self.dest = tmp / "dest"
        self.dest.mkdir()
        self.d = B.Dest(f"file://{self.dest}")
        self.jobs, self.state, self.watch = tmp / "jobs", tmp / "state", tmp / "mc-export"
        self.shelf_root = tmp / "shelf"
        self.clock = Clock(T0 + timedelta(hours=1))
        self.config = tmp / "memory_shelf.json"
        self.write_config(with_source=True)
        self.usage = PLENTY
        self.alerts = []

    def write_config(self, *, with_source=True, root=None):
        sources = {"minimoi-mc": {"kind": "mc", "root": str(root or self.watch), "never_copy": []}} if with_source else {
            "codex": {"kind": "codex", "root": str(self.tmp / "no-codex"), "never_copy": []}}
        self.config.write_text(json.dumps({"schema_version": 1, "shelf_root": str(self.shelf_root), "inbox_root": str(self.tmp / "inbox"),
                                           "headroom": {"min_free_bytes": 0}, "never_copy": [], "sources": sources}))

    # ── the stand-in for EC2's vault-mc-export: the contract only ──
    def send(self, bid: str, files: dict, *, complete: bool = True) -> dict:
        src = self.tmp / "ec2" / bid
        src.mkdir(parents=True, mode=0o700)
        rows = []
        for name, data in sorted(files.items()):
            (src / name).write_bytes(data)
            rows.append((name, src / name, B.sha256_bytes(data), len(data)))
        plain, cipher = src / "batch.tar.gz", src / "batch.tar.gz.age"
        MB.write_tar(rows, bid, "production", plain)
        B.encrypt(self.age_bin, self.recipient, plain, cipher)
        m = MB.manifest(bid, "production", [(n, s, z) for n, _p, s, z in rows], created=T0, cipher=cipher, plain_sha=B.sha256_file(plain),
                        plain_bytes=plain.stat().st_size, age_version="v1.3.2", recipient=self.recipient)
        mpath, cpath = src / "manifest.json", src / "COMPLETE.json"
        mpath.write_text(json.dumps(m, sort_keys=True))
        cpath.write_text(json.dumps(MB.complete(m, B.sha256_file(mpath)), sort_keys=True))
        if complete:
            MB.upload(self.d, bid, cipher, mpath, cpath)
        else:                                               # an upload killed before COMPLETE: an orphan
            self.d.put(cipher, f"{MB.EXPORT_PREFIX}/{bid}/{MB.BATCH_OBJECT}")
            self.d.put(mpath, f"{MB.EXPORT_PREFIX}/{bid}/{B.MANIFEST}")
        self.last_src = src
        return m

    def complete_later(self, bid: str) -> None:
        self.d.put(self.last_src / "COMPLETE.json", f"{MB.EXPORT_PREFIX}/{bid}/{B.COMPLETE}")

    def ack(self, bid: str) -> dict | None:
        path = self.dest / MB.ACK_PREFIX / f"{bid}.json"
        return MB.parse_ack(json.loads(path.read_text()), bid) if path.exists() else None

    def run(self, **kw):
        kw.setdefault("identity_spec", self.identity)
        return P.run_pull(dest=f"file://{self.dest}", age_bin=self.age_bin, expect_age=None, jobs_root=self.jobs, watch=self.watch,
                          state_dir=self.state, config_path=str(self.config), now=self.clock, usage=lambda _p: self.usage,
                          alert_send=lambda text: self.alerts.append(text) or "sent", **kw)

    def status(self):
        return status.read_status(self.jobs, "vault-mc-pull.json", "vault-mc-pull")

    def shelf(self):
        return Shelf(self.shelf_root, min_free_bytes=0)

    def approve(self):
        shelf = self.shelf()
        cfg = SourceCfgFor(self)
        assert approvals.dry_run_path(shelf, "minimoi-mc").is_file()          # the job's first pass wrote the owner's listing
        approvals.approve_source(shelf, "minimoi-mc", cfg.fingerprint(), OWNER)


def SourceCfgFor(w):
    from core.memory_shelf import config
    return config.load(str(w.config)).sources["minimoi-mc"]


def name(cid):
    return f"production--{cid}.json"


@pytest.fixture
def w(tmp_path, monkeypatch):
    return W(tmp_path, monkeypatch)


# ── pull, verify, place, ack ──
def test_a_batch_is_verified_placed_then_acknowledged(w):
    files = {name(A): export_doc(A), name(Bc): export_doc(Bc, "other words")}
    bid = bid_at(0)
    w.send(bid, files)
    out = w.run()
    assert out["exit"] == 0 and out["results"]["pull"] == "ok", out
    for n, data in files.items():
        assert (w.watch / n).read_bytes() == data and (w.watch / n).stat().st_mode & 0o777 == 0o600
    assert w.watch.stat().st_mode & 0o777 == 0o700
    ack = w.ack(bid)
    assert sorted(ack["files"]) == sorted([n, B.sha256_bytes(d), "placed"] for n, d in files.items())
    pulled = json.loads((w.state / "pulled.json").read_text())
    placed = json.loads((w.state / "placed.json").read_text())
    assert pulled[bid]["result"] == "ok" and placed[name(A)] == {"sha256": B.sha256_bytes(files[name(A)]), "batch_id": bid}
    assert out["results"]["placed"] == "2" and out["results"]["batches"] == "1"
    again = w.run()                                         # pulled: never read again
    assert again["results"]["batches"] == "0" and again["results"]["placed"] == "0"


def test_a_crash_before_the_ack_re_pulls_finds_the_same_bytes_and_acknowledges(w, monkeypatch):
    files = {name(A): export_doc(A)}
    bid = bid_at(0)
    w.send(bid, files)
    real_put = B.Dest.put

    def no_ack(self, local, key, *, replace=False):
        if key.startswith(MB.ACK_PREFIX):
            raise BackupError("upload_failed")              # the run dies between placement and acknowledgement
        return real_put(self, local, key, replace=replace)
    monkeypatch.setattr(B.Dest, "put", no_ack)
    first = w.run()
    assert first["state"] == "failed" and first["results"]["pull"] == "upload_failed" and w.alerts
    assert w.ack(bid) is None and not (w.state / "pulled.json").exists()
    assert (w.watch / name(A)).read_bytes() == files[name(A)]          # the placement itself was durable
    stamp = (w.watch / name(A)).stat().st_mtime_ns
    monkeypatch.setattr(B.Dest, "put", real_put)
    second = w.run()
    assert second["results"]["pull"] == "ok" and second["results"]["unchanged"] == "1" and second["results"]["placed"] == "0"
    assert (w.watch / name(A)).stat().st_mtime_ns == stamp                 # not rewritten: the watcher's size+mtime check skips it
    assert w.ack(bid)["files"] == [[name(A), B.sha256_bytes(files[name(A)]), "placed"]]


def test_an_older_batch_never_overwrites_newer_bytes(w):
    old_bid, new_bid = bid_at(0), bid_at(120)
    w.send(old_bid, {name(A): export_doc(A, "old words")}, complete=False)   # its COMPLETE lands late
    old_src = w.last_src
    newer = export_doc(A, "new words")
    w.send(new_bid, {name(A): newer})
    w.run()
    assert (w.watch / name(A)).read_bytes() == newer
    w.last_src = old_src
    w.complete_later(old_bid)
    out = w.run()
    assert (w.watch / name(A)).read_bytes() == newer
    assert out["results"]["superseded"] == "1" and w.ack(old_bid)["files"][0][2] == "superseded"
    assert json.loads((w.state / "placed.json").read_text())[name(A)]["batch_id"] == new_bid


def test_a_tampered_ciphertext_is_rejected_and_the_batch_is_marked_pulled(w):
    bid = bid_at(0)
    w.send(bid, {name(A): export_doc(A)})
    obj = w.dest / MB.EXPORT_PREFIX / bid / MB.BATCH_OBJECT
    data = bytearray(obj.read_bytes())
    data[-1] ^= 1
    obj.write_bytes(bytes(data))
    out = w.run()
    assert w.ack(bid)["files"][0][2] == "rejected:ciphertext_mismatch"
    assert out["state"] == "warn" and out["results"]["rejected"] == "1" and not (w.watch / name(A)).exists()
    assert json.loads((w.state / "pulled.json").read_text())[bid]["result"] == "rejected:ciphertext_mismatch"


def test_a_file_that_is_not_the_export_for_its_own_name_is_rejected_and_the_others_place(w):
    bid = bid_at(0)
    w.send(bid, {name(A): export_doc(Bc), name(C): export_doc(C)})        # A's body names another conversation
    out = w.run()
    verdicts = {n: s for n, _sha, s in w.ack(bid)["files"]}
    assert verdicts == {name(A): "rejected:not_an_export", name(C): "placed"}
    assert not (w.watch / name(A)).exists() and (w.watch / name(C)).exists() and out["state"] == "warn"


def test_an_orphan_without_complete_is_never_pulled(w):
    bid = bid_at(0)
    w.send(bid, {name(A): export_doc(A)}, complete=False)
    out = w.run()
    assert out["state"] == "ok" and out["results"]["batches"] == "0" and out["results"]["oldest_unpulled_age_s"] == "0"
    assert w.ack(bid) is None and not w.watch.joinpath(name(A)).exists() and not (w.state / "pulled.json").exists()


def test_a_held_batch_lock_records_busy_and_does_nothing_else(w):
    w.send(bid_at(0), {name(A): export_doc(A)})
    w.jobs.mkdir(mode=0o700)
    fd = os.open(str(w.jobs / "vault-batch.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        out = w.run()
    finally:
        os.close(fd)
    assert out == {"exit": 0, "state": None, "results": {"pull": "busy"}, "summary": "busy: another vault batch job holds the lock"}
    assert not w.watch.exists() and not w.state.exists() and w.ack(bid_at(0)) is None
    assert not (w.jobs / "vault-mc-pull.json").exists() and w.status() is None     # refused at the lock: nothing written, not even a status
    assert sorted(p.name for p in w.jobs.iterdir()) == ["vault-batch.lock"]


# Codex review (17 Oct, same as vault-sync fix1): one admission across status start, work and status finish
def test_a_competitor_refused_at_the_lock_leaves_the_active_runs_receipt_byte_identical(w):
    bid = bid_at(0)
    w.send(bid, {name(A): export_doc(A)})
    seen = {}

    def fetch_then_compete(spec):
        seen["receipt"] = (w.jobs / "vault-mc-pull.json").read_bytes()       # inside the active run, which holds the lock
        seen["competitor"] = w.run()
        seen["after"] = (w.jobs / "vault-mc-pull.json").read_bytes()
        return B.fetch_identity(spec)
    active = w.run(fetch_identity=fetch_then_compete)
    assert seen["after"] == seen["receipt"] and json.loads(seen["receipt"])["state"] == "running"
    assert seen["competitor"] == {"exit": 0, "state": None, "results": {"pull": "busy"},
                                  "summary": "busy: another vault batch job holds the lock"}
    assert active["results"]["pull"] == "ok" and active["results"]["placed"] == "1"
    assert w.status()["run_id"] == json.loads(seen["receipt"])["run_id"] and w.ack(bid)["files"][0][2] == "placed"


def test_a_transient_failure_acknowledges_nothing_and_retries_next_run(w):
    bid = bid_at(0)
    w.send(bid, {name(A): export_doc(A)})
    out = w.run(fetch_identity=lambda spec: (_ for _ in ()).throw(BackupError("identity_unavailable")))
    assert out["state"] == "failed" and out["results"]["pull"] == "identity_unavailable" and w.ack(bid) is None
    assert out["results"]["oldest_unpulled_age_s"] == "3600"
    assert w.run()["results"]["placed"] == "1" and w.ack(bid)["files"][0][2] == "placed"


# Review LOW-1 (17 Oct, repro R5): a local write fault while extracting is the Mac's, never the batch's
@pytest.mark.parametrize("where,err", [("create", errno.ENOSPC), ("create", errno.EACCES), ("raw", errno.ENOSPC)])
def test_a_local_write_error_during_extract_is_transient_not_a_rejection(w, monkeypatch, where, err):
    bid = bid_at(0)
    w.send(bid, {name(A): export_doc(A), name(Bc): export_doc(Bc, "other words")})
    real_open, real_extract = MB.os.open, MB._extract

    def failing_open(path, flags, *a, **k):
        if "/x/production--" in str(path):                  # mc_batch._extract creating an output file in the batch's scratch
            raise OSError(err, os.strerror(err))
        return real_open(path, flags, *a, **k)

    def raw(*_a, **_k):                                     # the reviewer's R5: any local OSError escaping the extraction
        raise OSError(err, os.strerror(err))
    if where == "create":
        monkeypatch.setattr(MB.os, "open", failing_open)
    else:
        monkeypatch.setattr(MB, "_extract", raw)
    out = w.run()
    assert out["state"] == "failed" and out["results"]["pull"] == "extract_write_failed" and out["results"]["rejected"] == "0"
    assert w.ack(bid) is None and not (w.state / "pulled.json").exists() and not (w.watch / name(A)).exists()
    assert out["results"]["oldest_unpulled_age_s"] == "3600" and w.alerts
    monkeypatch.setattr(MB.os, "open", real_open)
    monkeypatch.setattr(MB, "_extract", real_extract)
    again = w.run()                                         # retried next run: the same batch places and is acknowledged
    assert again["results"]["pull"] == "ok" and again["results"]["placed"] == "2"
    assert {s for _n, _sha, s in w.ack(bid)["files"]} == {"placed"}
    assert json.loads((w.state / "pulled.json").read_text())[bid]["result"] == "ok"


# Review LOW-4 (17 Oct): a run killed mid-place leaves place()'s temp file; the next run sweeps exactly that pattern
def test_stale_place_temp_files_are_swept_and_nothing_else_is_touched(w, tmp_path):
    w.watch.mkdir(mode=0o700)
    stale = [w.watch / f".{name(A)}.tmp-4242-deadbeef", w.watch / f".{name(Bc)}.tmp-1-0a1b2c3d"]
    for p in stale:
        p.write_bytes(export_doc(A))                        # plain text left behind by a kill between write and rename
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    link = w.watch / f".{name(C)}.tmp-7-00ff00ff"
    link.symlink_to(outside)                                # a link of the pattern: the link goes, never its target
    keep = {f".{name(A)}.tmp-4242-deadbee": b"7 hex", f".{name(A)}.tmp-x-deadbeef": b"pid", f"{name(A)}.tmp-1-deadbeef": b"no dot",
            f".{name(A)}.tmp-1-deadbeef.bak": b"suffix", ".other.json.tmp-1-deadbeef": b"not an export name", "notes.txt": b"owner",
            f".{name(A)}.tmp-1-DEADBEEF": b"upper hex"}
    for n, data in keep.items():
        (w.watch / n).write_bytes(data)
    folder = w.watch / f".{name(Bc)}.tmp-9-abcdef01"
    folder.mkdir()                                          # a folder of the pattern is not place()'s: left alone
    out = w.run()
    assert out["results"]["temp_swept"] == "3" and out["results"]["pull"] == "ok"
    assert not any(p.exists() or p.is_symlink() for p in stale + [link]) and outside.read_text() == "keep me"
    assert all((w.watch / n).read_bytes() == data for n, data in keep.items()) and folder.is_dir()
    assert w.run()["results"]["temp_swept"] == "0"


# ── capture ──
def test_capture_runs_only_for_an_approved_source_and_makes_a_production_record(w, tmp_path):
    from tests.memory_shelf.test_mc_capture import NOW, PRINCIPAL, World, X
    (tmp_path / "portal").mkdir()
    world = World(tmp_path / "portal")
    conv = world.conversation(PRINCIPAL, ("owner", "about the kiln schedule"), ("mc", "fire on Tuesday"))
    out_dir = tmp_path / "portal-export"
    X.export(guild_dir=world.guild, out=out_dir, principal=PRINCIPAL, instance="production", floor_for=world.floor, now=NOW)
    files = {p.name: p.read_bytes() for p in out_dir.glob("production--*.json")}
    assert list(files) == [name(conv["id"])]
    w.send(bid_at(0), files)
    first = w.run()
    assert first["results"]["capture"] == "not_approved" and first["state"] == "warn" and w.shelf().list_records() == []
    assert (w.watch / name(conv["id"])).exists()                          # the file waits in the folder
    w.approve()
    second = w.run()                                                      # nothing new pulled: the waiting file is what triggers capture
    assert second["results"]["batches"] == "0" and second["results"]["capture"] == "ok" and second["results"]["captured"] == "1"
    assert set(w.shelf().index()) == {f"minimoi-mc:production:{conv['id']}"}
    assert second["state"] == "ok"
    third = w.run()
    assert third["results"]["capture"] == "skipped"                       # captured at this size and mtime: no pass


def test_capture_without_the_source_or_rooted_elsewhere_is_a_warning(w):
    w.send(bid_at(0), {name(A): export_doc(A)})
    w.write_config(with_source=False)
    out = w.run()
    assert out["results"]["capture"] == "not_configured" and out["state"] == "warn" and (w.watch / name(A)).exists()
    w.write_config(root=w.tmp / "elsewhere")
    assert w.run()["results"]["capture"] == "source_root_mismatch"


# ── status and plain text ──
def test_status_counts_and_the_oldest_unpulled_age(w):
    w.send(bid_at(0), {name(A): export_doc(A)})
    w.usage = SimpleNamespace(free=0)
    out = w.run()
    assert out["state"] == "warn" and out["results"]["pull"] == "insufficient_space" and not w.alerts
    assert out["results"]["oldest_unpulled_age_s"] == "3600"
    w.usage = PLENTY
    out = w.run()
    st = w.status()
    assert st["state"] == "warn" and st["last_success_at"]                     # capture waits for approval: yellow, pull succeeded
    assert set(st["results"]) == {"pull", "capture", *P.COUNTERS}
    assert st["results"]["pull"] == "ok" and st["results"]["placed"] == "1" and st["results"]["oldest_unpulled_age_s"] == "0"


def test_no_plain_text_outside_the_watch_folder_and_the_shelf_holds_content(w):
    w.send(bid_at(0), {name(A): export_doc(A)})
    tampered = bid_at(60)
    w.send(tampered, {name(Bc): export_doc(Bc)})
    obj = w.dest / MB.EXPORT_PREFIX / tampered / MB.BATCH_OBJECT
    obj.write_bytes(obj.read_bytes()[:-1] + bytes([obj.read_bytes()[-1] ^ 1]))
    w.run()
    assert not (w.state / "work").exists() or not any((w.state / "work").rglob("*"))
    for root in (w.dest, w.jobs, w.state):
        for p in root.rglob("*"):
            if p.is_file() and p.name != MB.BATCH_OBJECT:
                text = p.read_bytes()
                assert SECRET.encode() not in text and b"reply" not in text, p.name


def test_both_jobs_are_registered():
    reg = registry.load()
    pull, export = reg.get("vault-mc-pull"), reg.get("vault-mc-export")
    assert (pull.host, export.host) == ("mac", "ec2")
    for job in (pull, export):
        assert job.schedule["every_s"] == 7200 and (job.expected_every_s, job.missed_after_s, job.stuck_after_s) == (7200, 21600, 3600)


def test_the_cli_refuses_without_a_valid_identity_spec(w, capsys, monkeypatch):
    monkeypatch.delenv("VAULT_BACKUP_IDENTITY", raising=False)
    local = ["--dest", f"file://{w.dest}", "--jobs-root", str(w.jobs), "--state-dir", str(w.state), "--watch", str(w.watch),
             "--config", str(w.config), "--age-bin", w.age_bin]
    assert CLI.main(["run", "--execute", *local]) == 2
    assert CLI.main(["run", "--execute", "--identity", "AGE-SECRET-KEY-1" + "Q" * 58, *local]) == 2
    assert not w.jobs.exists() and not w.state.exists()                     # refused before any status or state is written
    err = capsys.readouterr().err
    assert "identity_required" in err and "identity_spec_invalid" in err and "SECRET-KEY-1Q" not in err
    assert CLI.main(["run", "--identity", w.identity]) == 2                  # plan-only without --execute
