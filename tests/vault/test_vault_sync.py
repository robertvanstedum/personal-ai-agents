"""vault-sync, the Mac side of the Vault's incremental sync (17 Oct 2026). Synthetic shelves, file:// destination, the pinned real `age`
when it is installed (else the transport tests' stand-in), an injected clock, and a hand-written stand-in for production: it reads
complete batches, decrypts and checks them with the shared batch module, keeps a fake production shelf, and writes acknowledgements and
``inventory.json`` with ``versions.inventory_doc``. The real EC2 apply (core/memory_sync/apply.py) is not used here."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.jobs import registry, status
from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_shelf.shelf import Shelf
from core.memory_sync import batch as BT
from core.memory_sync import sync as S
from core.memory_sync import versions as V
from tests.memory_backup.test_production_transport import make_age
from tests.memory_index.helpers import T0, add_record, designate, make_shelf

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("vault_sync_cli", ROOT / "scripts/vault/vault_sync.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)

DAY = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)                 # 10:00 CDT: outside the window
NIGHT = datetime(2026, 10, 18, 7, 0, tzinfo=timezone.utc)                # 02:00 CDT: inside it
PLENTY = SimpleNamespace(free=1 << 42)


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> datetime:
        self.t += timedelta(**kw)
        return self.t

    def set(self, t: datetime) -> datetime:
        self.t = t
        return t


class Production:
    """Stand-in for EC2's vault-apply: complete batches only, oldest first; a version it already holds is ``stored`` without reapplying."""

    def __init__(self, tmp: Path, dest: Path, age_bin: str, identity_spec: str):
        self.root, self.tmp, self.d = tmp / "prod-shelf", tmp / "prod-work", B.Dest(f"file://{dest}")
        self.age_bin, self.identity, self.identity_spec = age_bin, B.fetch_identity(identity_spec), identity_spec
        self.done, self.through, self.applied, self.received = set(), None, [], []

    def seed(self, shelf_root: Path) -> None:
        """The r3 snapshot seed: production starts as a copy of the shelf, then writes its first inventory."""
        shutil.copytree(shelf_root, self.root)
        self.write_inventory()

    def held(self) -> dict:
        return V.record_versions(self.root)[0]

    def write_inventory(self) -> None:
        vs, problems = V.record_versions(self.root)
        doc = V.inventory_doc(self.root, vs, generated_utc="2026-10-17T00:00:00Z", through_batch=self.through, release=None, problems=problems)
        self.tmp.mkdir(parents=True, exist_ok=True)
        V.write_json_atomic(self.tmp / "inventory.json", doc)
        self.d.put(self.tmp / "inventory.json", f"{V.ACK_PREFIX}/{V.INVENTORY_NAME}", replace=True)

    def apply(self, statuses: dict | None = None, *, inventory: bool = True) -> dict:
        statuses, out = statuses or {}, {}
        for bid in [b for b in BT.complete_batches(self.d) if b not in self.done]:
            scratch = self.tmp / bid
            scratch.mkdir(parents=True)
            _, m = BT.fetch_chain(self.d, bid, scratch)
            BT.fetch_ciphertext(self.d, bid, m, scratch / "batch.age")
            B.decrypt(self.age_bin, self.identity, scratch / "batch.age", scratch / "batch.tar.gz", scratch)
            doc = BT.extract(scratch / "batch.tar.gz", scratch / "x")
            assert BT.doc_matches_manifest(doc, m)
            acks = []
            for e in doc["versions"]:
                rid, held = e["record_id"], self.held()
                st, h = statuses.get(rid), held.get(e["record_id"])
                if st is None:
                    if h and h.digest == e["digest"]:
                        st = "stored"
                    elif h and h.edition > e["edition"]:
                        st = "superseded"
                    else:
                        assert BT.check_version(scratch / "x", e) is None
                        if h:
                            shutil.rmtree(self.root / h.dir)
                        shutil.copytree(scratch / "x" / "records" / e["kind"] / e["name"], self.root / e["kind"] / e["name"])
                        self.applied.append(rid)
                        st = "stored"
                acks.append({"record_id": rid, "edition": e["edition"], "edition_hash": e["edition_hash"], "digest": e["digest"], "status": st})
            ack = {"format": V.ACK_FORMAT, "batch_id": bid, "applied_utc": "2026-10-17T00:00:00Z", "release": None, "result": "applied",
                   "batch_code": None, "versions": acks}
            V.write_json_atomic(scratch / "ack.json", ack)
            self.d.put(scratch / "ack.json", f"{V.ACK_PREFIX}/{bid}.json", replace=True)
            self.done.add(bid)
            self.through = max(self.through or "", bid)
            self.received.append((bid, sorted(e["record_id"] for e in doc["versions"])))
            out[bid] = acks
        if inventory:
            self.write_inventory()
        return out


@pytest.fixture
def w(tmp_path):
    shelf = make_shelf(tmp_path)
    ids = {k: add_record(shelf, "grok", f"g-{k}", [("human", f"{k} zebraquartz question"), ("assistant", f"{k} zebraquartz answer")])
           for k in ("a", "b", "c")}
    age_bin, recipient, ident = make_age(tmp_path)
    dest = tmp_path / "dest"
    ns = SimpleNamespace(tmp=tmp_path, shelf=shelf, ids=ids, age_bin=age_bin, recipient=recipient, url=f"file://{dest}", dest=dest,
                         jobs=tmp_path / "jobs", state=tmp_path / "state", clock=Clock(DAY), alerts=[],
                         prod=Production(tmp_path, dest, age_bin, ident))
    return ns


def run(w, **kw):
    def alert_send(text):
        w.alerts.append(text)
        return "not_sent"
    args = dict(shelf_root=w.shelf.root, dest=w.url, recipient=w.recipient, age_bin=w.age_bin, expect_age="v1.3.2", jobs_root=w.jobs,
                state_dir=w.state, now=w.clock, usage=lambda p: PLENTY, alert_send=alert_send)
    args.update(kw)
    return S.run_sync(**args)


def pending(w) -> dict:
    return json.loads((w.state / "pending.json").read_text())


def batch_ids(w) -> list:
    base = w.dest / "sync" / "batches"
    return sorted(p.name for p in base.iterdir()) if base.exists() else []


def complete_ids(w) -> list:
    return BT.complete_batches(B.Dest(w.url))


def manifest(w, bid) -> dict:
    return json.loads((w.dest / "sync" / "batches" / bid / "manifest.json").read_text())


def job_status(w) -> dict:
    return status.read_status(w.jobs, "vault-sync.json", "vault-sync")


def r(out, key) -> int:
    return int(out["results"][key])


def new(w, sid, words=20) -> str:
    return add_record(w.shelf, "grok", sid, [("human", f"{sid} " + "zebraquartz " * words), ("assistant", f"{sid} reply")])


def local(w) -> dict:
    return V.record_versions(w.shelf.root)[0]


# 1
def test_without_a_production_inventory_nothing_is_sent_and_the_run_warns(w):
    out = run(w)
    assert out["results"]["sync"] == "no_production_inventory" and out["state"] == "warn" and out["exit"] == 0
    assert not w.dest.exists() and batch_ids(w) == []
    doc = job_status(w)
    assert doc["state"] == "warn" and doc["last_success_at"] is None
    assert not (w.state / "pending.json").exists()


# 2
def test_changed_records_are_sent_once_and_an_unchanged_run_sends_and_hashes_nothing(w):
    w.prod.seed(w.shelf.root)
    out = run(w)
    assert out["results"]["sync"] == "ok" and r(out, "sent") == 0 and r(out, "backlog_records") == 0
    fresh = new(w, "g-new")
    add_record(w.shelf, "grok", "g-a", [("human", "a zebraquartz question"), ("assistant", "a zebraquartz answer, revised")])
    designate(w.shelf, w.ids["b"])                                     # same edition, metadata-only change
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "sent") == 3 and r(out, "batches") == 1 and r(out, "backlog_records") == 3
    (bid,) = batch_ids(w)
    vs = local(w)
    assert {e["record_id"]: e["digest"] for e in manifest(w, bid)["versions"]} == {rid: vs[rid].digest for rid in (fresh, w.ids["a"], w.ids["b"])}
    assert vs[w.ids["a"]].edition == 2 and vs[w.ids["b"]].edition == 1
    w.clock.advance(minutes=10)
    out = run(w)                                                       # in flight, not yet acknowledged, not due: not sent again
    assert r(out, "sent") == 0 and batch_ids(w) == [bid]
    w.prod.apply()
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "acked") == 3 and r(out, "sent") == 0 and r(out, "backlog_records") == 0 and pending(w)["pending"] == {}
    w.clock.advance(hours=2)
    out = run(w)
    assert r(out, "sent") == 0 and out["hash_misses"] == 0 and batch_ids(w) == [bid]
    assert sorted(w.prod.applied) == sorted([fresh, w.ids["a"], w.ids["b"]])


# 3
def test_an_upload_interrupted_before_complete_stays_pending_and_is_resent_in_a_new_batch(w):
    w.prod.seed(w.shelf.root)
    fresh = new(w, "g-new")
    real = B.Dest(w.url)

    def flaky(local_path, key):
        if key.endswith(B.COMPLETE):
            raise BackupError("upload_failed")
        real.put(local_path, key)
    out = run(w, put=flaky)
    assert out["state"] == "failed" and out["results"]["sync"] == "upload_failed" and out["exit"] == 1 and len(w.alerts) == 1
    (orphan,) = batch_ids(w)
    assert complete_ids(w) == [] and not (w.dest / "sync" / "batches" / orphan / B.COMPLETE).exists()
    entry = pending(w)["pending"][fresh]
    assert entry["state"] == "sending" and entry["attempts"] == 0
    assert w.prod.apply() == {}                                       # production never sees an orphan
    w.clock.advance(minutes=5)
    out = run(w)
    assert r(out, "sent") == 1 and r(out, "retried") == 1 and out["state"] == "ok"
    (resent,) = complete_ids(w)
    assert resent != orphan and pending(w)["pending"][fresh]["state"] == "sent"
    acks = w.prod.apply()
    assert list(acks) == [resent] and w.prod.applied == [fresh]
    w.clock.advance(minutes=5)
    out = run(w)
    assert r(out, "acked") == 1 and pending(w)["pending"] == {} and r(out, "backlog_records") == 0
    assert job_status(w)["last_success_at"] == status.iso(w.clock())


# 4
def test_a_rejection_keeps_the_version_pending_with_backoff_and_a_resend_production_holds_clears_it(w):
    w.prod.seed(w.shelf.root)
    fresh = new(w, "g-new")
    t0 = w.clock()
    run(w)
    e = pending(w)["pending"][fresh]
    assert e["attempts"] == 1 and e["next_retry_utc"] == status.iso(t0 + timedelta(hours=1))
    w.prod.apply({fresh: "rejected:file_hash_mismatch"})
    w.clock.advance(minutes=10)
    out = run(w)
    e = pending(w)["pending"][fresh]
    assert r(out, "rejected") == 1 and out["state"] == "warn" and r(out, "sent") == 0
    assert e["state"] == "rejected" and e["reason"] == "file_hash_mismatch" and r(out, "backlog_records") == 1
    w.clock.advance(minutes=5)
    assert r(run(w), "rejected") == 0                                 # the same rejection read again changes nothing
    expected = [2, 4, 8, 12, 12]                                      # after attempts 2..6: min(2 ** (n - 1), 12) hours
    for hours_before, hours_after in zip([1] + expected[:-1], expected):
        w.clock.set(status.parse_time(pending(w)["pending"][fresh]["next_retry_utc"]) - timedelta(minutes=1))
        out = run(w)                                                  # a pending batch with no acknowledgement waits for its retry time
        assert r(out, "sent") == 0
        w.clock.advance(minutes=1)
        out = run(w)
        e = pending(w)["pending"][fresh]
        assert r(out, "sent") == 1 and r(out, "retried") == 1
        sent = status.parse_time(e["sent_utc"])
        assert sent == w.clock() and status.parse_time(e["next_retry_utc"]) - sent == timedelta(hours=hours_after)
    assert e["attempts"] == 6 and len(e["batches"]) == 6 and e["reason"] == "file_hash_mismatch"
    w.prod.apply()                                                    # the first unacknowledged re-send applies; the rest are held: stored
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "acked") == 1 and pending(w)["pending"] == {} and r(out, "backlog_records") == 0
    assert w.prod.applied == [fresh]                                  # applied once, however many times it was sent


# 5
def test_daytime_runs_send_one_light_batch_a_wake_run_catches_up_once_and_the_night_sends_the_rest(w):
    w.prod.seed(w.shelf.root)
    smalls = [new(w, f"g-s{i}") for i in range(4)]
    big = new(w, "g-big", words=4000)
    vs = local(w)
    s_max = max(vs[rid].bytes for rid in smalls)
    light, heavy = 2 * s_max + 1, 2 * s_max + 1
    assert vs[big].bytes > light
    kw = dict(light_bytes=light, light_records=200, heavy_bytes=heavy)
    out = run(w, **kw)
    (b1,) = batch_ids(w)
    m = manifest(w, b1)
    assert out["results"]["mode"] == "light" and r(out, "batches") == 1 and m["record_bytes"] <= light and 1 <= m["count"] <= 200
    assert big not in {e["record_id"] for e in m["versions"]} and r(out, "deferred_heavy") == 5 - m["count"] > 0
    first = m["versions"][0]["record_id"]
    w.prod.apply({first: "rejected:digest_mismatch"})
    w.clock.advance(hours=10)                                         # asleep: launchd runs the missed interval once on wake (20:00 CDT)
    out = run(w, **kw)
    (b2,) = [b for b in batch_ids(w) if b != b1]
    m2 = manifest(w, b2)
    assert out["results"]["mode"] == "light" and r(out, "batches") == 1 and m2["record_bytes"] <= light
    assert r(out, "acked") == m["count"] - 1 and r(out, "rejected") == 1 and r(out, "retried") == 1
    assert first in {e["record_id"] for e in m2["versions"]}                # the due retry goes first
    assert big not in {e["record_id"] for e in m2["versions"]} and r(out, "deferred_heavy") > 0
    out = run(w, light_bytes=1 << 30, light_records=1, heavy_bytes=heavy)
    assert r(out, "sent") <= 1                                        # the record bound holds too
    w.clock.set(NIGHT)
    out = run(w, **kw)
    assert out["results"]["mode"] == "heavy" and r(out, "deferred_heavy") == 0 and r(out, "batches") >= 2
    night = [b for b in batch_ids(w) if b not in (b1, b2)]
    for bid in night:
        mm = manifest(w, bid)
        assert mm["record_bytes"] <= heavy or mm["count"] == 1        # the big record goes alone
    sent_ids = {e["record_id"] for bid in batch_ids(w) for e in manifest(w, bid)["versions"]}
    assert set(smalls) | {big} <= sent_ids
    w.prod.apply()
    w.clock.advance(minutes=10)
    out = run(w, **kw)
    assert r(out, "backlog_records") == 0 and pending(w)["pending"] == {}


def test_a_night_run_stops_sending_when_the_window_ends(w):
    w.prod.seed(w.shelf.root)
    smalls = [new(w, f"g-s{i}") for i in range(4)]
    heavy = max(v.bytes for rid, v in local(w).items() if rid in smalls) + 1      # one record per batch
    real = B.Dest(w.url)

    def put(local_path, key):
        real.put(local_path, key)
        if key.endswith(B.COMPLETE):
            w.clock.set(datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc))      # 06:00 CDT: the window is over
    w.clock.set(datetime(2026, 10, 18, 10, 55, tzinfo=timezone.utc))              # 05:55 CDT
    out = run(w, heavy_bytes=heavy, put=put)
    assert out["results"]["mode"] == "heavy" and r(out, "batches") == 1 and r(out, "sent") == 1 and r(out, "deferred_heavy") == 3
    assert r(out, "backlog_records") == 4 and len(complete_ids(w)) == 1


# 6
def test_a_version_production_lost_after_acknowledging_it_is_requeued_and_repaired(w):
    w.prod.seed(w.shelf.root)
    fresh = new(w, "g-new")
    run(w)
    w.prod.apply()
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "acked") == 1 and r(out, "backlog_records") == 0
    w.clock.advance(hours=2)
    assert r(run(w), "gap_requeued") == 0                             # the inventory confirms it: nothing to do
    held = w.prod.held()[fresh]
    shutil.rmtree(w.prod.root / held.dir)                             # production loses it
    w.prod.write_inventory()
    w.clock.advance(hours=2)
    out = run(w)
    assert r(out, "gap_requeued") == 1 and r(out, "sent") == 1 and r(out, "backlog_records") == 1
    w.prod.apply()
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "backlog_records") == 0 and r(out, "gap_requeued") == 0
    assert V.version_set_digest(V.entries_of(w.prod.held())) == V.version_set_digest(V.entries_of(local(w)))     # identical_set


# 7
def test_status_shows_backlog_oldest_pending_and_last_success_only_at_backlog_zero(w):
    w.prod.seed(w.shelf.root)
    out = run(w)
    t_clean = w.clock()
    assert job_status(w)["last_success_at"] == status.iso(t_clean) and out["results"]["oldest_pending_age_s"] == "0"
    x = new(w, "g-x")
    y = new(w, "g-y", words=4000)
    light = local(w)[x].bytes + 1
    kw = dict(light_bytes=light, heavy_bytes=1 << 30)
    t1 = w.clock.advance(hours=1)
    out = run(w, **kw)
    st = job_status(w)
    assert st["results"]["backlog_records"] == "2" and st["results"]["oldest_pending_age_s"] == "0"
    assert st["results"]["sent"] == "1" and st["results"]["deferred_heavy"] == "1" and st["last_success_at"] == status.iso(t_clean)
    w.prod.apply()
    w.clock.advance(minutes=30)
    run(w, **kw)
    st = job_status(w)
    assert st["results"]["acked"] == "1" and st["results"]["backlog_records"] == "1" and st["results"]["deferred_heavy"] == "1"
    assert st["results"]["oldest_pending_age_s"] == "1800" and st["last_success_at"] == status.iso(t_clean)
    w.clock.set(NIGHT)
    run(w, **kw)
    st = job_status(w)
    assert st["results"]["sent"] == "1" and st["results"]["backlog_records"] == "1"
    assert st["results"]["oldest_pending_age_s"] == str(int((NIGHT - t1).total_seconds()))
    w.prod.apply({y: "rejected:file_size_mismatch"})
    t4 = w.clock.advance(minutes=10)
    run(w, **kw)
    st = job_status(w)
    assert st["state"] == "warn" and st["results"]["rejected"] == "1" and st["results"]["backlog_records"] == "1"
    assert st["results"]["oldest_pending_age_s"] == str(int((t4 - t1).total_seconds())) and st["last_success_at"] == status.iso(t_clean)
    w.clock.advance(hours=1)
    run(w, **kw)
    assert job_status(w)["results"]["retried"] == "1" and job_status(w)["last_success_at"] == status.iso(t_clean)
    w.prod.apply()
    t6 = w.clock.advance(minutes=10)
    run(w, **kw)
    st = job_status(w)
    assert st["results"]["backlog_records"] == "0" and st["results"]["oldest_pending_age_s"] == "0" and st["last_success_at"] == status.iso(t6)
    assert pending(w)["backlog_since"] == {}


# 8
def test_a_held_batch_lock_records_busy_and_does_nothing_else(w):
    w.prod.seed(w.shelf.root)
    new(w, "g-new")
    w.jobs.mkdir(mode=0o700)
    fd = os.open(str(w.jobs / "vault-batch.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        out = run(w)
    finally:
        os.close(fd)
    assert out["results"] == {"sync": "busy"} and out["state"] is None and out["exit"] == 0
    assert not w.state.exists() and batch_ids(w) == []
    assert job_status(w) is None                                       # a run refused at the lock writes nothing, not even a status


# Codex review (17 Oct): one admission across status start, work and status finish
def test_a_competitor_refused_at_the_lock_leaves_the_active_runs_receipt_byte_identical(w):
    w.prod.seed(w.shelf.root)
    new(w, "g-competed")
    seen = {}

    def put_then_compete(local, key, replace=False):
        if "receipt" not in seen:                                      # inside the active run, which holds the lock
            seen["receipt"] = (w.jobs / "vault-sync.json").read_bytes()
            seen["competitor"] = run(w)
            seen["after"] = (w.jobs / "vault-sync.json").read_bytes()
        return B.Dest(w.url).put(local, key, replace=replace)
    w.clock.advance(minutes=10)
    active = run(w, put=put_then_compete)
    assert seen["competitor"]["results"] == {"sync": "busy"} and seen["competitor"]["state"] is None
    assert seen["after"] == seen["receipt"] and json.loads(seen["receipt"])["state"] == "running"
    assert active["state"] == "ok" and job_status(w)["run_id"] == json.loads(seen["receipt"])["run_id"]


# 9
def test_short_of_the_reserve_nothing_is_uploaded_and_nothing_is_lost(w):
    w.prod.seed(w.shelf.root)
    fresh = new(w, "g-new")
    out = run(w, usage=lambda p: SimpleNamespace(free=B.reserve_bytes() + (1 << 20)))
    assert out["results"]["sync"] == "insufficient_space" and out["state"] == "warn" and out["exit"] == 0
    assert batch_ids(w) == [] and pending(w)["pending"] == {} and r(out, "backlog_records") == 1
    assert not (w.state / "work").exists()                           # scratch wiped
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "sent") == 1 and fresh in pending(w)["pending"]


# 10
def test_production_newer_and_production_only_versions_are_flagged_never_sent_or_deleted(w):
    w.prod.seed(w.shelf.root)
    prod_shelf = Shelf(w.prod.root, min_free_bytes=0, clock=lambda: T0)
    add_record(prod_shelf, "grok", "g-a", [("human", "a zebraquartz question"), ("assistant", "a newer answer held only by production")])
    only = add_record(prod_shelf, "grok", "g-only", [("human", "production only"), ("assistant", "reply")])
    w.prod.write_inventory()
    designate(w.shelf, w.ids["a"])                                     # the Mac changes its older edition: still never sent
    designate(w.shelf, w.ids["b"])                                     # an ordinary change still goes
    out = run(w)
    assert r(out, "production_newer") == 1 and r(out, "production_only") == 1 and r(out, "sent") == 1
    (bid,) = batch_ids(w)
    assert [e["record_id"] for e in manifest(w, bid)["versions"]] == [w.ids["b"]]
    assert w.ids["a"] not in pending(w)["pending"] and only not in local(w)
    w.prod.apply()
    w.clock.advance(minutes=10)
    out = run(w)
    held = w.prod.held()
    assert only in held and held[w.ids["a"]].edition == 2 and r(out, "backlog_records") == 0 and r(out, "production_newer") == 1


# 11
def test_the_launchd_agent_comes_only_from_the_pinned_runtime_and_the_registry_entries_parse(capsys):
    """The plist itself is generated and checked by runtime_install (tests/memory_backup/test_runtime_install.py): every 2 h and at load,
    from the hashed pin. The sync CLI no longer prints an agent that would run a working checkout's unpinned code."""
    with pytest.raises(SystemExit):
        CLI.main(["launchd-plist"])
    capsys.readouterr()
    reg = registry.load()
    sync_job, apply_job = reg.get("vault-sync"), reg.get("vault-apply")
    assert (sync_job.host, sync_job.schedule["every_s"], sync_job.expected_every_s, sync_job.missed_after_s, sync_job.stuck_after_s,
            sync_job.status_file, sync_job.active_from) == ("mac", 7200, 7200, 21600, 3600, "vault-sync.json", None)
    assert (apply_job.host, apply_job.schedule["every_s"], apply_job.expected_every_s, apply_job.missed_after_s, apply_job.stuck_after_s,
            apply_job.status_file, apply_job.active_from) == ("ec2", 900, 900, 7200, 3600, "vault-apply.json", None)
    assert sync_job.runbook == "core/memory_sync/sync.py" and apply_job.runbook == "core/memory_sync/apply.py"
    assert S.job_definition().id == "vault-sync"


# 12
def test_nothing_plain_text_names_a_record_folder_title_or_text(w):
    w.prod.seed(w.shelf.root)
    new(w, "g-new")
    designate(w.shelf, w.ids["a"])
    real = B.Dest(w.url)

    def flaky(local_path, key):
        if key.endswith(B.COMPLETE):
            raise BackupError("upload_failed")
        real.put(local_path, key)
    run(w, put=flaky)
    w.clock.advance(minutes=5)
    run(w)
    w.prod.apply({w.ids["a"]: "rejected:file_hash_mismatch"})
    w.clock.advance(minutes=5)
    run(w)
    names = [v.name for v in local(w).values()]
    forbidden = names + ["zebraquartz", "grok g-", "g-new", "question", "answer", "reply", str(w.shelf.root)]
    texts = {"pending.json": (w.state / "pending.json").read_text(), "status": (w.jobs / "vault-sync.json").read_text()}
    for p in (w.dest / "sync" / "batches").rglob("*.json"):
        texts[str(p.relative_to(w.dest))] = p.read_text()
    assert any("manifest.json" in k for k in texts) and any(B.COMPLETE in k for k in texts)
    for where, text in texts.items():
        for word in forbidden:
            assert word not in text, (where, word)


# CLI
def test_the_cli_refuses_without_execute_and_plan_writes_nothing(w, capsys, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")                         # the real disk probe: keep the test independent of free space
    w.prod.seed(w.shelf.root)
    new(w, "g-new")
    base = ["--shelf", str(w.shelf.root), "--dest", w.url, "--recipient", w.recipient, "--age-bin", w.age_bin, "--jobs-root", str(w.jobs),
            "--state-dir", str(w.state)]
    assert CLI.main(base + ["run"]) == 2 and "plan_only_use_execute" in capsys.readouterr().err
    assert CLI.main(base + ["--light-mib", "0", "run", "--execute"]) == 2
    assert CLI.main(base + ["plan", "--check-remote"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["production_inventory"] == "present" and plan["changed"] == 1 and plan["would_send"] == 1
    assert not w.state.exists() and not w.jobs.exists() and batch_ids(w) == []
    monkeypatch.setattr(S.alert, "send", lambda text: "not_sent")
    assert CLI.main(base + ["run", "--execute"]) == 0
    assert len(complete_ids(w)) == 1 and job_status(w)["state"] == "ok"


# review R1 (Mac half): a version never travels without its source-index entry
def test_a_record_whose_index_write_crashed_is_sent_with_its_entry_rebuilt_from_the_shelf(w):
    w.prod.seed(w.shelf.root)
    run(w)
    fresh = new(w, "g-crash")
    idx = w.shelf.root / "_ledger" / "source_index.json"
    doc = json.loads(idx.read_text())
    del doc["grok:g-crash"]                                            # the ingest wrote the record, then crashed before the index ...
    idx.write_text(json.dumps(doc))
    (w.shelf.root / "_ledger" / "index.dirty").write_text("")          # ... leaving the shelf's own dirty marker
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "sent") == 1 and r(out, "source_entry_missing") == 0
    (bid,) = batch_ids(w)
    scratch = w.tmp / "inspect"
    scratch.mkdir()
    B.decrypt(w.prod.age_bin, w.prod.identity, w.dest / "sync" / "batches" / bid / V.BATCH_OBJECT, scratch / "b.tar.gz", scratch)
    sent = BT.extract(scratch / "b.tar.gz", scratch / "x")
    entry = sent["versions"][0]["source_index_entry"]
    assert sent["versions"][0]["record_id"] == fresh and entry["id"] == fresh and entry["dir"] == sent["versions"][0]["name"]


def test_a_sessions_record_without_an_entry_is_held_back_and_stays_in_the_backlog(w):
    w.prod.seed(w.shelf.root)
    run(w)
    new(w, "g-orphan")
    idx = w.shelf.root / "_ledger" / "source_index.json"
    doc = json.loads(idx.read_text())
    del doc["grok:g-orphan"]                                           # no dirty marker: the shelf's index simply lacks it
    idx.write_text(json.dumps(doc))
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "sent") == 0 and r(out, "source_entry_missing") == 1 and r(out, "backlog_records") == 1
    assert batch_ids(w) == [] and job_status(w)["last_success_at"] != job_status(w)["finished_at"]


# review M1 + second review N2: an unacknowledged version whose local folder became unsyncable is never dropped or hidden; it is
# reported as stranded (warn), and does not hold the backlog above 0 forever
def test_a_pending_version_whose_record_became_unsyncable_is_kept_and_reported_stranded(w):
    w.prod.seed(w.shelf.root)
    run(w)
    fresh = new(w, "g-later-damaged")
    w.clock.advance(minutes=10)
    run(w)
    assert fresh in pending(w)["pending"]
    folder = w.shelf.root / local(w)[fresh].dir
    (folder / f"{folder.name}.md").unlink()                            # the folder is no longer a readable record
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "unsyncable") == 1 and r(out, "stranded") == 1 and fresh in pending(w)["pending"]
    assert out["state"] == "warn" and job_status(w)["state"] == "warn"
    assert r(out, "backlog_records") == 0                              # nothing the sync can send; visible as stranded, not an endless backlog


# ── Codex review (17 Oct): stale acknowledgements must never hide a gap ──
def forget_scratch(w) -> None:
    """The stand-in production replays batches it forgot: clear its per-batch scratch (a real reset loses that too)."""
    for p in w.prod.tmp.iterdir():
        if p.is_dir():
            shutil.rmtree(p)


def identical(w) -> bool:
    return V.version_set_digest(V.entries_of(w.prod.held())) == V.version_set_digest(V.entries_of(local(w)))


def two_acked(w) -> list:
    """Two versions sent in two batches, both acknowledged stored, pending cleared; production's inventory then names the newest batch."""
    w.prod.seed(w.shelf.root)
    ids = []
    for sid in ("g-first", "g-second"):
        ids.append(new(w, sid))
        w.clock.advance(minutes=10)
        run(w)
        w.prod.apply()
    w.clock.advance(minutes=10)
    out = run(w)
    assert r(out, "backlog_records") == 0 and pending(w)["pending"] == {} and identical(w)
    return ids


def test_codex_repro_an_old_acknowledgement_never_hides_a_version_a_reset_production_lacks(w):
    """Unit level, Codex's exact shape: local digest A; synced batches (A, B); production's inventory empty with through_batch None."""
    shelf_versions = local(w)
    rid = w.ids["a"]
    state = S.empty_state("file:///dest")
    state["synced"] = {rid: {"digest": shelf_versions[rid].digest, "batch_id": "20261017T150000Z-0000000b"}}
    counts = dict.fromkeys(S.COUNTERS, 0)
    work = S.reconcile(state, {rid: shelf_versions[rid]}, {}, None, DAY, counts)
    assert work["changed"] == [rid] and counts["backlog_records"] == 1 and counts["gap_requeued"] == 1


def test_a_production_reset_to_an_empty_inventory_requeues_every_missing_version_and_recovers(w):
    ids = two_acked(w)
    held = w.prod.held()
    for rid in ids:
        shutil.rmtree(w.prod.root / held[rid].dir)                      # production reset: it lost both, and forgot every batch
    w.prod.through, w.prod.done = None, set()
    forget_scratch(w)
    w.prod.write_inventory()
    w.clock.advance(hours=2)
    out = run(w)
    assert r(out, "backlog_records") == 2 and r(out, "gap_requeued") == 2 and r(out, "sent") == 2
    assert out["results"]["production_reset"] == "yes"
    w.prod.apply()
    w.clock.advance(minutes=10)
    assert r(run(w), "backlog_records") == 0 and identical(w)


def test_a_reseed_to_a_new_generation_that_lacks_versions_requeues_them(w):
    ids = two_acked(w)
    held = w.prod.held()
    shutil.rmtree(w.prod.root / held[ids[1]].dir)                       # the reseeded snapshot predates the second version ...
    w.prod.through, w.prod.done = None, set()                           # ... and the new generation has no apply log yet
    forget_scratch(w)
    w.prod.write_inventory()
    w.clock.advance(hours=2)
    out = run(w)
    assert r(out, "backlog_records") == 1 and r(out, "gap_requeued") == 1 and r(out, "sent") == 1
    assert out["results"]["production_reset"] == "yes"
    w.prod.apply()
    w.clock.advance(minutes=10)
    assert r(run(w), "backlog_records") == 0 and identical(w)


def test_production_inventory_older_than_the_local_watermark_requeues_what_it_lacks(w):
    ids = two_acked(w)
    held = w.prod.held()
    shutil.rmtree(w.prod.root / held[ids[1]].dir)                       # rolled back to before the second batch
    w.prod.through = sorted(w.prod.done)[0]                             # its inventory names only the first batch
    w.prod.write_inventory()
    w.clock.advance(hours=2)
    out = run(w)
    assert r(out, "backlog_records") == 1 and r(out, "gap_requeued") == 1 and out["results"]["production_reset"] == "yes"
    w.prod.done = {sorted(w.prod.done)[0]}                              # production replays what it forgot, re-sends included
    forget_scratch(w)
    w.prod.apply()
    w.clock.advance(minutes=10)
    assert r(run(w), "backlog_records") == 0 and identical(w)


def test_a_changed_destination_starts_fresh_and_sends_what_that_production_lacks(w, tmp_path):
    two_acked(w)
    dest2 = tmp_path / "dest2"
    prod2 = Production(tmp_path / "second", dest2, w.age_bin, w.prod.identity_spec)
    seed_root = tmp_path / "older-shelf"
    shutil.copytree(w.prod.root, seed_root)
    for v in V.record_versions(seed_root)[0].values():
        if v.record_id not in w.ids.values():
            shutil.rmtree(seed_root / v.dir)                            # the second production was seeded before both new records
    prod2.seed(seed_root)
    w.clock.advance(hours=2)
    out = run(w, dest=f"file://{dest2}")
    assert out["results"]["state_reset"] == "destination_changed"
    assert r(out, "backlog_records") == 2 and r(out, "sent") == 2 and pending(w)["dest"] == f"file://{dest2}"
    prod2.apply()
    w.clock.advance(minutes=10)
    out = run(w, dest=f"file://{dest2}")
    assert r(out, "backlog_records") == 0
    assert V.version_set_digest(V.entries_of(prod2.held())) == V.version_set_digest(V.entries_of(local(w)))


def test_a_reseed_that_kept_its_apply_log_is_also_requeued_from_the_inventory(w):
    """The other reseed shape (EC2's apply log survived, through_batch unchanged): the inventory still decides. A guard, not a fix test."""
    ids = two_acked(w)
    shutil.rmtree(w.prod.root / w.prod.held()[ids[1]].dir)
    w.prod.write_inventory()
    w.clock.advance(hours=2)
    out = run(w)
    assert r(out, "backlog_records") == 1 and r(out, "gap_requeued") == 1
    w.prod.apply()
    w.clock.advance(minutes=10)
    assert r(run(w), "backlog_records") == 0 and identical(w)
