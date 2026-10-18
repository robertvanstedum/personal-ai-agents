"""``vault-apply``, the EC2 side of the incremental sync (17 Oct 2026).

Fully synthetic and local: a "Mac" shelf built with the shelf's own writer, seeded into production through r3's real publish and refresh
(file:// destination, real pinned `age` when installed, else the transport tests' stand-in), then batches made with the shared contract only
(``versions`` + ``batch``: the Mac job is built separately and is not imported here). Runs as the current user (``require_root=False``).
No network, no AWS, no docker (the docker index runner is checked through an injected ``run``)."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.memory_index import indexer, policy, search, store
from core.memory_sync import apply as A
from core.memory_sync import batch as BT
from core.memory_sync import versions as V
from tests.memory_backup.test_production_transport import make_age
from tests.memory_index.helpers import FakeEmbedder, add_record, designate, make_shelf

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
RAW = policy.Caller(tier="all")
TEXT_WORDS = ("kumquat", "xylophone", "zeppelin", "marmalade", "gazebo")


class Clock:
    """The injected ``now``: every call moves one minute on, so batch and release ids are ordered."""

    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        self.t += timedelta(minutes=1)
        return self.t

    def jump(self, delta):
        self.t += delta


class NeverCalled(FakeEmbedder):
    def identity(self):
        raise AssertionError("keyword-only search must never ask an embedder")


class W:
    pass


def seed(w, *, vector=False, now=T0):
    """r3's own path: index the Mac shelf, publish the pair, install it as the serving release."""
    index = w.tmp / f"mac-index-{now:%H%M}" / "memory.sqlite"
    if vector:
        assert indexer.update(w.mac.root, FakeEmbedder(), index_path=index, min_free_bytes=None)["code"] == "ok"
    else:
        assert indexer.update(w.mac.root, None, index_path=index, min_free_bytes=None, keyword_only=True)["code"] == "ok"
    m = P.run_publish(shelf_root=str(w.mac.root), index_path=str(index), dest=w.dest, recipient=w.recipient, workdir=str(w.tmp / "pubwork"),
                      age_bin=w.age, now=now, enforce_window=False)
    return m, P.run_refresh(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, age_bin=w.age, owner_uid=os.geteuid(),
                            reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, reseed=w.reseed_next)


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    w = W()
    w.tmp, w.clock, w.reseed_next = tmp_path, Clock(), False
    w.mac = make_shelf(tmp_path / "mac")
    w.ids = {"a": add_record(w.mac, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha original kumquat")]),
             "b": add_record(w.mac, "codex", "c-b", [("human", "beta question"), ("assistant", "beta answer marmalade")]),
             "c": add_record(w.mac, "grok", "g-c", [("human", "gamma question"), ("assistant", "gamma answer gazebo")])}
    w.age, w.recipient, w.identity = make_age(tmp_path)
    w.dest, w.destdir = f"file://{tmp_path / 'dest'}", tmp_path / "dest"
    w.vault = tmp_path / "ec2" / "vault"
    w.vault.parent.mkdir(mode=0o755)
    w.sync = w.vault.parent / "vault-sync"
    w.seed_manifest, _ = seed(w)
    return w


def apply(w, **kw):
    args = dict(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, index_runner="host", age_bin=w.age, owner_uid=os.geteuid(),
                reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, now=w.clock)
    args.update(kw)
    return A.run_apply(**args)


def send(w, rids=None, *, root=None, put=None, sources=None, edit_manifest=None):
    """What the Mac job does for one batch, with the shared contract only: tar, encrypt, manifest, COMPLETE, upload (COMPLETE last).
    ``sources``: the source index the batch carries entries from (default: the root's own)."""
    root = Path(root or w.mac.root)
    got, _ = V.record_versions(root)
    chosen = [got[r] for r in (rids if rids is not None else sorted(got))]
    created = w.clock()
    bid = B.new_attempt_id(created)
    work = w.tmp / "macwork" / bid
    work.mkdir(parents=True)
    plain, cipher = work / "batch.tar.gz", work / V.BATCH_OBJECT
    res = BT.write_tar(root, chosen, V.source_index(root) if sources is None else sources, bid, plain)
    B.encrypt(w.age, w.recipient, plain, cipher)
    m = BT.manifest(bid, res["versions"], created=created, cipher=cipher, plain_sha=B.sha256_file(plain), plain_bytes=plain.stat().st_size,
                    age_version="v1.3.2", recipient=w.recipient, mode="light")
    if edit_manifest:
        edit_manifest(m)
    mp, cp = work / B.MANIFEST, work / B.COMPLETE
    mp.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    cp.write_text(json.dumps(BT.complete(m, B.sha256_file(mp)), indent=2, sort_keys=True) + "\n")
    BT.upload(B.Dest(w.dest), bid, cipher, mp, cp, put=put)
    return bid


def ack(w, bid):
    return json.loads((w.destdir / V.ACK_PREFIX / f"{bid}.json").read_text())


def inventory(w):
    return json.loads((w.destdir / V.ACK_PREFIX / V.INVENTORY_NAME).read_text())


def current(w) -> str:
    target = os.readlink(w.vault / "current")
    assert target.startswith("releases/")                                       # relative: resolves inside a container mount
    return target.split("/", 1)[1]


def releases(w) -> list:
    return sorted(p.name for p in (w.vault / "releases").iterdir())


def mac_digest(w, root=None):
    return V.version_set_digest(V.entries_of(V.record_versions(root or w.mac.root)[0]))


def statuses(doc) -> dict:
    return {v["record_id"]: v["status"] for v in doc["versions"]}


def keyword(w, query, monkeypatch) -> list:
    """vault-ask's own read: current/shelf and current/index/memory.sqlite, keyword-only (no sqlite-vec on the host)."""
    with monkeypatch.context() as m:
        for mod in (store, indexer, search):
            m.setattr(mod, "sqlite_vec", None)
        r = search.search(query, shelf_root=w.vault / "current" / "shelf", embedder=NeverCalled(),
                          index_path=w.vault / "current" / "index" / "memory.sqlite", caller=RAW, k=5)
    assert r["status"]["meaning"] == search.VECTOR_UNAVAILABLE
    return [h["record_id"] for h in r["keyword"]]


def tree_state(root: Path) -> dict:
    """Every file under ``root``: its bytes, mode and owner (a write or a chmod through a hard link would show here)."""
    out = {}
    for p in sorted(root.rglob("*")):
        st = os.lstat(p)
        out[p.relative_to(root).as_posix()] = (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir",
                                               st.st_mode & 0o7777, st.st_uid, st.st_gid)
    return out


def changes_on_mac(w):
    """A new edition of a, a metadata-only change of b, a new record d."""
    add_record(w.mac, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha revised xylophone")])
    designate(w.mac, w.ids["b"])
    w.ids["d"] = add_record(w.mac, "grok", "g-d", [("human", "delta question"), ("assistant", "delta answer zeppelin")])


def no_scratch_left(w):
    work = w.vault.parent / "vault-work"
    assert not list(work.iterdir())
    assert not [p for p in w.vault.iterdir() if p.name.startswith((".work-", ".current-")) or ".tmp-" in p.name]


# 1. ── apply: new record, new edition, metadata-only change ──
def test_a_batch_installs_a_new_release_that_vault_ask_reads_and_acks_every_version_stored(w, monkeypatch):
    first = apply(w)                                                            # no batch yet: the first inventory only
    assert first["code"] == "noop" and first["inventory"] == "written" and not first["built"]
    seed_attempt = current(w)
    assert inventory(w)["version_set_digest"] == mac_digest(w) and inventory(w)["release"] == seed_attempt
    assert w.ids["a"] in keyword(w, "kumquat", monkeypatch)
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["b"], w.ids["d"]])
    out = apply(w)
    assert out["code"] == "ok" and out["built"] and out["applied"] == 3 and out["stored"] == 3 and out["rejected"] == 0
    new = current(w)
    assert new != seed_attempt and new == out["release"]
    doc = ack(w, bid)
    assert doc["format"] == V.ACK_FORMAT and doc["result"] == "applied" and doc["batch_code"] is None and doc["release"] == out["release"]
    assert statuses(doc) == {w.ids["a"]: "stored", w.ids["b"]: "stored", w.ids["d"]: "stored"}
    mac = V.record_versions(w.mac.root)[0]
    assert {v["record_id"]: v["digest"] for v in doc["versions"]} == {r: mac[r].digest for r in (w.ids["a"], w.ids["b"], w.ids["d"])}
    rel = json.loads((w.vault / "current" / P.RELEASE_MARK).read_text())
    assert rel["format"] == "vault-production-sync/1" and rel["attempt"] == out["release"] and rel["base"] == seed_attempt
    assert rel["snapshot"] == w.seed_manifest["attempt"] and rel["batches"] == [bid] and rel["index"] == {"keyword_only": True}
    assert rel["records"] == 4 and rel["version_set_digest"] == mac_digest(w)
    # vault-ask's paths
    sources = json.loads((w.vault / "current" / "shelf" / "_ledger" / "source_index.json").read_text())
    assert {e["id"] for e in sources.values()} == set(w.ids.values()) and "grok:g-d" in sources
    con = sqlite3.connect(f"file:{w.vault / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        assert store.is_keyword_only(con)
    finally:
        con.close()
    assert w.ids["a"] in keyword(w, "xylophone", monkeypatch)
    assert w.ids["a"] not in keyword(w, "kumquat", monkeypatch)                # the replaced edition's text is gone
    assert w.ids["d"] in keyword(w, "zeppelin", monkeypatch)
    # the inventory, here and at the destination
    inv = inventory(w)
    local = json.loads((w.sync / "inventory.json").read_text())
    assert inv == local and inv["version_set_digest"] == mac_digest(w) and inv["through_batch"] == bid and inv["release"] == out["release"]
    assert V.parse_inventory(inv) == V.entries_of(mac)
    applied = json.loads((w.sync / "applied.json").read_text())
    assert applied[bid]["result"] == "applied" and applied[bid]["release"] == out["release"]
    no_scratch_left(w)


# 2. ── the previous release is never written through a hard link ──
def test_the_previous_release_is_byte_for_byte_unchanged_after_an_apply(w):
    apply(w)
    old = w.vault / "releases" / current(w)
    before = tree_state(old)
    changes_on_mac(w)
    send(w, [w.ids["a"], w.ids["b"], w.ids["d"]])
    out = apply(w)
    assert out["built"] and tree_state(old) == before
    new = w.vault / "releases" / out["release"]
    same_inode = [p for p in (new / "shelf").rglob("*") if p.is_file() and (old / "shelf" / p.relative_to(new / "shelf")).exists()
                  and p.stat().st_ino == (old / "shelf" / p.relative_to(new / "shelf")).stat().st_ino]
    assert same_inode                                                            # untouched files are links, not copies
    assert (new / "index" / "memory.sqlite").stat().st_ino != (old / "index" / "memory.sqlite").stat().st_ino


# 3. ── interrupted applies ──
@pytest.mark.parametrize("where", ["placed", "indexed", "before_switch"])
def test_a_crash_before_the_switch_leaves_the_old_release_serving_with_no_ack_and_the_next_run_completes(w, where):
    apply(w)
    before_current, before_releases = current(w), releases(w)
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["b"], w.ids["d"]])

    def crash(*_):
        raise RuntimeError("injected crash")
    with pytest.raises(RuntimeError):
        apply(w, hooks={where: crash})
    assert current(w) == before_current and releases(w) == before_releases
    serving = w.vault / "current" / "shelf"
    assert w.ids["d"] not in V.record_versions(serving)[0]                     # no partial record visible
    assert V.record_versions(serving)[0][w.ids["a"]].edition == 1
    assert not (w.destdir / V.ACK_PREFIX / f"{bid}.json").exists()
    assert bid not in json.loads((w.sync / "applied.json").read_text()) if (w.sync / "applied.json").exists() else True
    no_scratch_left(w)
    out = apply(w)
    assert out["built"] and statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["b"]: "stored", w.ids["d"]: "stored"}
    assert inventory(w)["version_set_digest"] == mac_digest(w)


def test_a_crash_after_the_switch_before_the_ack_is_acked_stored_next_run_without_another_release(w):
    apply(w)
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])

    def crash(*_):
        raise RuntimeError("injected crash")
    with pytest.raises(RuntimeError):
        apply(w, hooks={"after_switch": crash})
    switched, after_crash = current(w), releases(w)
    assert not (w.destdir / V.ACK_PREFIX / f"{bid}.json").exists()
    out = apply(w)
    assert not out["built"] and current(w) == switched and releases(w) == after_crash
    assert statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
    assert ack(w, bid)["release"] == switched


# 4. ── idempotent re-send ──
def test_a_re_sent_version_production_already_holds_is_acked_stored_without_reapplying(w):
    changes_on_mac(w)
    first = send(w, [w.ids["a"], w.ids["d"]])
    out1 = apply(w)
    assert out1["built"] and statuses(ack(w, first)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
    serving, kept = current(w), releases(w)
    again = send(w, [w.ids["a"], w.ids["d"]])                                  # a retry: the Mac never saw the first ack
    out2 = apply(w)
    assert not out2["built"] and current(w) == serving and releases(w) == kept
    assert statuses(ack(w, again)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
    shelf = w.vault / "current" / "shelf" / "sessions-raw"
    holders = [d for d in shelf.iterdir() if V.read_front_matter(d / f"{d.name}.md").get("id") in (w.ids["a"], w.ids["d"])]
    assert len(holders) == 2                                                     # no duplicate record folder


# 5. ── rejections ──
def test_a_version_whose_bytes_do_not_match_is_rejected_and_the_rest_of_the_batch_applies(w):
    apply(w)
    changes_on_mac(w)
    live_a = V.record_versions(w.vault / "current" / "shelf")[0][w.ids["a"]].digest
    bid = send(w, [w.ids["a"], w.ids["d"]])

    def tamper(batch_id, extracted):
        main = next(extracted.glob(f"{V.RECORDS_ROOT}/sessions-raw/grok-g-a*/grok-g-a*.md"))
        data = bytearray(main.read_bytes())
        data[-3] ^= 0x01                                                         # same size, other bytes
        main.write_bytes(bytes(data))
    out = apply(w, hooks={"extracted": tamper})
    assert statuses(ack(w, bid)) == {w.ids["a"]: "rejected:file_hash_mismatch", w.ids["d"]: "stored"}
    assert out["rejected"] == 1 and out["applied"] == 1
    held = V.record_versions(w.vault / "current" / "shelf")[0]
    assert held[w.ids["a"]].digest == live_a and w.ids["d"] in held           # the live record is untouched


def test_a_batch_whose_ciphertext_does_not_match_its_manifest_is_rejected_whole(w):
    apply(w)
    changes_on_mac(w)
    serving = current(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])
    cipher = w.destdir / V.BATCH_PREFIX / bid / V.BATCH_OBJECT
    data = bytearray(cipher.read_bytes()); data[len(data) // 2] ^= 0x01; cipher.write_bytes(bytes(data))
    out = apply(w)
    doc = ack(w, bid)
    assert doc["result"] == "rejected_batch" and doc["batch_code"] == "ciphertext_mismatch" and doc["release"] is None
    assert statuses(doc) == {w.ids["a"]: "rejected:ciphertext_mismatch", w.ids["d"]: "rejected:ciphertext_mismatch"}
    assert out["rejected"] == 2 and not out["built"] and current(w) == serving
    assert json.loads((w.sync / "applied.json").read_text())[bid]["result"] == "rejected_batch"
    assert apply(w)["batches"] == 0                                              # never processed again


def test_an_older_edition_after_a_newer_one_is_superseded(w):
    add_record(w.mac, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha second edition")])
    older = w.tmp / "mac-older"
    shutil.copytree(w.mac.root, older)                                           # the Mac as it was at edition 2
    add_record(w.mac, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha third edition")])
    newer = send(w, [w.ids["a"]])
    stale = send(w, [w.ids["a"]], root=older)
    out = apply(w)
    assert statuses(ack(w, newer)) == {w.ids["a"]: "stored"} and statuses(ack(w, stale)) == {w.ids["a"]: "superseded"}
    assert out["superseded"] == 1 and V.record_versions(w.vault / "current" / "shelf")[0][w.ids["a"]].edition == 3


def test_the_same_edition_with_another_edition_hash_is_an_edition_conflict(w):
    forged = w.tmp / "mac-forged"
    shutil.copytree(w.mac.root, forged)
    v = V.record_versions(forged)[0][w.ids["a"]]
    main = forged / v.dir / f"{v.name}.md"
    main.write_text(main.read_text().replace(v.edition_hash, "e" * 64))
    bid = send(w, [w.ids["a"]], root=forged)
    out = apply(w)
    assert statuses(ack(w, bid)) == {w.ids["a"]: "rejected:edition_conflict"} and not out["built"]
    assert V.record_versions(w.vault / "current" / "shelf")[0][w.ids["a"]].edition_hash == v.edition_hash


# 6. ── orphans ──
def test_a_batch_without_complete_is_never_listed_or_acked(w):
    d = B.Dest(w.dest)

    def no_complete(local, key):
        if key.endswith(B.COMPLETE):
            raise B.BackupError("upload_failed")
        d.put(local, key)
    changes_on_mac(w)
    with pytest.raises(B.BackupError):
        send(w, [w.ids["d"]], put=no_complete)
    orphan = next((w.destdir / V.BATCH_PREFIX).iterdir()).name
    out = apply(w)
    assert out["code"] == "noop" and out["batches"] == 0 and not out["built"]
    assert not (w.destdir / V.ACK_PREFIX / f"{orphan}.json").exists()
    assert orphan not in json.loads((w.sync / "applied.json").read_text()) if (w.sync / "applied.json").exists() else True
    assert inventory(w)["through_batch"] is None


# 7. ── the index seed ──
def test_the_first_run_after_a_vector_snapshot_rebuilds_a_keyword_only_index_and_writes_the_first_inventory(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    w = W()
    w.tmp, w.clock, w.reseed_next = tmp_path, Clock(), False
    w.mac = make_shelf(tmp_path / "mac")
    w.ids = {"a": add_record(w.mac, "grok", "g-a", [("human", "alpha question"), ("assistant", "alpha original kumquat")])}
    w.age, w.recipient, w.identity = make_age(tmp_path)
    w.dest, w.destdir = f"file://{tmp_path / 'dest'}", tmp_path / "dest"
    w.vault = tmp_path / "ec2" / "vault"
    w.vault.parent.mkdir(mode=0o755)
    w.sync = w.vault.parent / "vault-sync"
    seed(w, vector=True)
    seeded = w.vault / "current" / "index" / "memory.sqlite"
    assert not A.index_is_keyword_only(seeded)
    out = apply(w)
    assert out["index_seed"] and out["built"] and out["batches"] == 0 and out["inventory"] == "written"
    con = sqlite3.connect(f"file:{w.vault / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        assert store.is_keyword_only(con)
        assert con.execute("SELECT count(*) FROM fts_docsize").fetchone()[0] == con.execute("SELECT count(*) FROM chunks").fetchone()[0] > 0
    finally:
        con.close()
    assert inventory(w)["version_set_digest"] == mac_digest(w)
    assert w.ids["a"] in keyword(w, "kumquat", monkeypatch)
    assert not apply(w)["built"]                                                 # seeded once


# 8. ── the daily inventory ──
def test_with_no_batch_the_inventory_is_rewritten_only_when_older_than_20_hours(w):
    assert apply(w)["inventory"] == "written"
    stamp = inventory(w)["generated_utc"]
    w.clock.jump(timedelta(hours=1))
    assert apply(w)["inventory"] == "skipped" and inventory(w)["generated_utc"] == stamp
    w.clock.jump(timedelta(hours=20))
    assert apply(w)["inventory"] == "written" and inventory(w)["generated_utc"] != stamp


# 9. ── gap repair support ──
def test_a_lost_record_drops_out_of_the_inventory_and_a_re_sent_version_restores_the_identical_set(w):
    apply(w)
    held = V.record_versions(w.vault / "current" / "shelf")[0]
    lost, damaged = held[w.ids["c"]], held[w.ids["b"]]
    shutil.rmtree(w.vault / "current" / "shelf" / lost.dir)                     # simulated loss on production
    (w.vault / "current" / "shelf" / damaged.dir / f"{damaged.name}.md").unlink()   # and a damaged record folder
    out = apply(w, force_inventory=True)
    assert out["inventory"] == "written" and not out["built"]
    inv = inventory(w)
    assert w.ids["c"] not in inv["records"] and w.ids["b"] not in inv["records"] and inv["count"] == 1
    assert inv["version_set_digest"] != mac_digest(w) and inv["problems"] == {"folder_without_record_file": 1}
    bid = send(w, [w.ids["b"], w.ids["c"]])                                     # the Mac's reconciliation re-queues both
    apply(w)
    assert statuses(ack(w, bid)) == {w.ids["b"]: "stored", w.ids["c"]: "stored"}
    inv = inventory(w)
    assert inv["version_set_digest"] == mac_digest(w) and inv["problems"] == {}


def test_a_transient_failure_stops_the_run_after_the_batches_already_processed_and_retries_the_rest(w, monkeypatch):
    apply(w)
    changes_on_mac(w)
    first = send(w, [w.ids["d"]])
    second = send(w, [w.ids["a"], w.ids["b"]])
    real_get = B.Dest.get

    def flaky(self, key, local):
        if key.startswith(f"{V.BATCH_PREFIX}/{second}/") and key.endswith(V.BATCH_OBJECT):
            raise B.BackupError("download_failed")
        return real_get(self, key, local)
    monkeypatch.setattr(B.Dest, "get", flaky)
    out = apply(w)
    assert out["code"] == "download_failed" and out["batches"] == 1 and out["built"]
    assert statuses(ack(w, first)) == {w.ids["d"]: "stored"} and not (w.destdir / V.ACK_PREFIX / f"{second}.json").exists()
    assert second not in json.loads((w.sync / "applied.json").read_text())
    monkeypatch.setattr(B.Dest, "get", real_get)
    again = apply(w)
    assert again["code"] == "ok" and statuses(ack(w, second)) == {w.ids["a"]: "stored", w.ids["b"]: "stored"}
    assert inventory(w)["version_set_digest"] == mac_digest(w)


def test_one_run_applies_at_most_max_run_bytes_of_batches_and_the_rest_waits(w):
    apply(w)
    changes_on_mac(w)
    first, second = send(w, [w.ids["d"]]), send(w, [w.ids["a"]])
    out = apply(w, max_run_bytes=1)                                              # always at least one batch per run
    assert out["batches"] == 1 and (w.destdir / V.ACK_PREFIX / f"{first}.json").exists()
    assert not (w.destdir / V.ACK_PREFIX / f"{second}.json").exists()
    assert apply(w, max_run_bytes=1)["batches"] == 1 and statuses(ack(w, second)) == {w.ids["a"]: "stored"}


# 10. ── busy ──
def test_an_apply_while_the_refresh_lock_is_held_is_a_quiet_busy_warning_and_does_nothing(w, monkeypatch):
    changes_on_mac(w)
    bid = send(w, [w.ids["d"]])
    serving = current(w)
    va = load("vault_apply")
    jobs = w.tmp / "jobs"
    monkeypatch.setattr(va.alert, "send", lambda text: (_ for _ in ()).throw(AssertionError("no alert for busy")))
    with P._Lock(w.vault / ".refresh.lock"):
        with pytest.raises(B.BackupError) as e:
            apply(w)
        assert e.value.code == "apply_busy"
        assert va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"]) == 2
    assert not (jobs / "vault-apply.json").exists()                            # refused at the lock: writes nothing, not even a status
    assert current(w) == serving and not (w.destdir / V.ACK_PREFIX / f"{bid}.json").exists() and not (w.sync / "applied.json").exists()


# Codex review (17 Oct): one admission across status start, work and status finish, for vault-apply AND the r3 refresh (same lock)
@pytest.mark.parametrize("competitor", ["vault_apply", "production_refresh"])
def test_a_competitor_refused_at_the_lock_leaves_the_active_apply_receipt_byte_identical(w, monkeypatch, competitor):
    changes_on_mac(w)
    send(w, [w.ids["d"]])
    va, ref = load("vault_apply"), load("production_refresh")
    jobs = w.tmp / "jobs"
    seen = {}
    real = va.apply.run_apply

    def active(**kw):                                                           # runs inside the CLI's admission, after its status start
        seen["receipt"] = (jobs / "vault-apply.json").read_bytes()
        if competitor == "vault_apply":
            seen["exit"] = va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"])
        else:
            seen["exit"] = ref.main(cli(w, jobs) + ["--fts-probe", "host", "refresh", "--execute"])
        seen["after"] = (jobs / "vault-apply.json").read_bytes()
        seen["refresh_status"] = (jobs / "vault-production-refresh.json").exists()
        return real(**kw)
    monkeypatch.setattr(va.apply, "run_apply", active)
    assert va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"]) == 0
    assert seen["exit"] == 2 and seen["after"] == seen["receipt"] and json.loads(seen["receipt"])["state"] == "running"
    assert not seen["refresh_status"]                                           # the refused refresh wrote no status of its own either
    final = json.loads((jobs / "vault-apply.json").read_text())
    assert final["state"] == "ok" and final["run_id"] == json.loads(seen["receipt"])["run_id"]


def test_a_refused_apply_never_overwrites_an_active_refresh_receipt(w, monkeypatch):
    va, ref = load("vault_apply"), load("production_refresh")
    jobs = w.tmp / "jobs"
    seen = {}
    real = ref.production.run_refresh

    def active(**kw):
        seen["receipt"] = (jobs / "vault-production-refresh.json").read_bytes()
        seen["exit"] = va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"])
        seen["after"] = (jobs / "vault-production-refresh.json").read_bytes()
        return real(**kw)
    monkeypatch.setattr(ref.production, "run_refresh", active)
    ref.main(cli(w, jobs) + ["--fts-probe", "host", "refresh", "--execute"])
    assert seen["exit"] == 2 and seen["after"] == seen["receipt"] and not (jobs / "vault-apply.json").exists()


# 11. ── the r3 refresh guard ──
def test_refresh_refuses_over_a_sync_release_unless_reseeding_a_newer_snapshot(w, monkeypatch):
    changes_on_mac(w)
    send(w, [w.ids["d"]])
    out = apply(w)
    sync_release = current(w)
    with pytest.raises(B.BackupError) as e:
        P.run_refresh(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, age_bin=w.age, owner_uid=os.geteuid(),
                      reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False)
    assert e.value.code == "incremental_active" and current(w) == sync_release == out["release"]
    ref = load("production_refresh")
    jobs = w.tmp / "jobs"
    monkeypatch.setattr(ref.alert, "send", lambda text: (_ for _ in ()).throw(AssertionError("no alert for a quiet refusal")))
    rcommon = ["--dest", w.dest, "--vault-root", str(w.vault), "--identity", w.identity, "--age-bin", w.age, "--jobs-root", str(jobs),
               "--reader-uid", str(os.geteuid()), "--reader-gid", str(os.getegid()), "--_allow-non-root", "--fts-probe", "host"]
    assert ref.main(rcommon + ["refresh", "--execute"]) == 2
    st = json.loads((jobs / "vault-production-refresh.json").read_text())
    assert st["state"] == "warn" and st["results"] == {"refresh": "incremental_active"}
    add_record(w.mac, "grok", "g-e", [("human", "epsilon question"), ("assistant", "epsilon answer")])
    w.reseed_next = True
    m, installed = seed(w, now=T0 + timedelta(hours=2))                         # a newer snapshot, installed with reseed
    assert installed["state"] == "installed" and current(w) == m["attempt"]
    assert json.loads((w.vault / "installed.json").read_text())["previous"] == out["release"]
    nxt = apply(w)                                                              # incremental sync continues from the reseeded shelf
    assert nxt["inventory"] == "written" and inventory(w)["version_set_digest"] == mac_digest(w)


def test_reseed_still_requires_a_snapshot_newer_than_the_installed_one(w):
    changes_on_mac(w)
    send(w, [w.ids["d"]])
    apply(w)
    with pytest.raises(B.BackupError) as e:                                     # the pointer still names the installed seed
        P.run_refresh(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, age_bin=w.age, owner_uid=os.geteuid(),
                      reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, reseed=True)
    assert e.value.code == "pointer_not_newer"


# 12. ── the index runner ──
def test_the_index_runner_is_required_and_host_is_refused_on_an_old_sqlite(w, monkeypatch):
    va = load("vault_apply")
    jobs = w.tmp / "jobs"
    assert va.main(cli(w, jobs) + ["run", "--execute"]) == 2 and not (jobs / "vault-apply.json").exists()
    with pytest.raises(B.BackupError) as e:
        A.make_index_runner(None)
    assert e.value.code == "index_runner_required"
    monkeypatch.setattr(A.sqlite3, "sqlite_version", "3.40.1")
    with pytest.raises(B.BackupError) as e:
        A.make_index_runner("host")
    assert e.value.code == "host_sqlite_too_old"
    assert va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"]) == 2
    with pytest.raises(B.BackupError) as e:
        apply(w)
    assert e.value.code == "host_sqlite_too_old"


def test_the_docker_index_runner_argv_and_protocol(tmp_path):
    build = tmp_path / "scratch" / "build"
    build.mkdir(parents=True)
    image = "sha256:" + "a" * 64
    seen = []

    def fake(stdout):
        def run(argv, **kw):
            only = Path(next(argv[i + 1] for i, a in enumerate(argv) if a == "-v" and argv[i + 1].endswith(":/only.json:ro")).split(":")[0])
            seen.append((argv, json.loads(only.read_text()), kw))
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        return run
    rid = "01M55RJ7RTGFC980AN2Y4XMZBC"
    runner = A.make_index_runner(f"docker:{image}", reader_uid=0, reader_gid=0, run=fake('noise\n{"protocol": 1, "code": "ok", "reindexed": 1}\n'))
    assert runner.index(build, [rid]) == {"protocol": 1, "code": "ok", "reindexed": 1}
    only_file = build.parent / "only.json"
    # hardened: no network, read-only root filesystem, no capabilities, no privilege gain; the build's shelf (hard links into the live
    # release) read-only, only the build's index folder writable; never the whole build
    assert seen[0][0] == ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
                          "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "0:0",
                          "-v", f"{build / 'shelf'}:/shelf:ro", "-v", f"{build / 'index'}:/index", "-v", f"{only_file}:/only.json:ro",
                          "--entrypoint", "python3", image, "-I", "-B", "/app/scripts/vault/vault_apply.py", "index-step",
                          "--shelf", "/shelf", "--index", "/index/memory.sqlite", "--only-file", "/only.json"]
    assert f"{build}:/build" not in seen[0][0]
    assert seen[0][1] == [rid] and seen[0][2]["timeout"] == 3600 and not only_file.exists()
    runner.index(build, None)
    assert seen[1][0][-1] == "--rebuild" and "--only-file" not in seen[1][0]
    for stdout, code in (('{"protocol": 2, "code": "ok"}', "index_runner_incompatible"), ('{"protocol": 1, "code": "disk_low"}', "index_failed:disk_low"),
                         ("Traceback ...", "index_runner_failed")):
        with pytest.raises(B.BackupError) as e:
            A.make_index_runner(f"docker:{image}", run=fake(stdout)).index(build, [rid])
        assert e.value.code == code
    for bad in ("bad image; rm -rf /", "--privileged", "-v/:/host", ""):
        with pytest.raises(B.BackupError) as e:
            A.make_index_runner(f"docker:{bad}")
        assert e.value.code == "bad_arguments"


def test_an_image_name_starting_with_a_dash_is_refused_by_both_docker_steps(tmp_path):
    """Finding 5: a name docker would read as an option (``--privileged``, ``-v/:/host``) is never placed in an argv."""
    ran = []
    for bad in ("--privileged", "-v/:/host"):
        with pytest.raises(B.BackupError) as e:
            A.make_index_runner(f"docker:{bad}", run=lambda argv, **kw: ran.append(argv))
        assert e.value.code == "bad_arguments"
        with pytest.raises(B.BackupError) as e:
            P.docker_fts_probe(bad, tmp_path, 0, 0, run=lambda argv, **kw: ran.append(argv))
        assert e.value.code == "probe_image_malformed"
    assert not ran
    assert A.make_index_runner("docker:registry.example/minimoi-portal:sha-1").image == "registry.example/minimoi-portal:sha-1"


def test_a_whole_apply_through_the_docker_runner_uses_the_index_step_protocol(w, monkeypatch):
    """The container is simulated by running ``vault_apply.py index-step`` in-process on the mounted paths: the same JSON line comes back."""
    va = load("vault_apply")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        mounts = {}
        for spec in (argv[i + 1] for i, a in enumerate(argv) if a == "-v"):
            src, dst = spec.removesuffix(":ro").rsplit(":", 1)
            mounts[dst] = src
        if "index-step" in argv:
            assert "/build" not in mounts and set(mounts) == {"/shelf", "/index", "/only.json"}
            assert f"{mounts['/shelf']}:/shelf:ro" in argv and f"{mounts['/index']}:/index" in argv    # the shelf is mounted read-only
            args = ["index-step", "--shelf", mounts["/shelf"], "--index", str(Path(mounts["/index"]) / "memory.sqlite")] + \
                (["--rebuild"] if "--rebuild" in argv else ["--only-file", mounts["/only.json"]])
            before = tree_state(Path(mounts["/shelf"]))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = va.main(args)
            assert tree_state(Path(mounts["/shelf"])) == before                 # the step only reads the shelf
            return subprocess.CompletedProcess(argv, rc, stdout=buf.getvalue(), stderr="")
        probe = Path(mounts["/probe"]) / "index" / "memory.sqlite"
        P.fts_probe(probe)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true, "sqlite": "3.46.1", "rows": true}\n', stderr="")
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])
    out = apply(w, index_runner=A.make_index_runner("docker:sha256:" + "b" * 64, run=fake_run))
    assert out["built"] and statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
    assert [c for c in calls if "index-step" in c] and [c for c in calls if "--read-only" in c]
    assert w.ids["d"] in keyword(w, "zeppelin", monkeypatch)


# 13. ── permissions ──
def test_the_new_release_has_r3_permissions(w):
    changes_on_mac(w)
    send(w, [w.ids["a"], w.ids["b"], w.ids["d"]])
    out = apply(w)
    release = w.vault / "releases" / out["release"]
    assert P.check_permissions(release, owner_uid=os.geteuid(), reader_uid=os.geteuid(), gid=os.getegid())["files"] > 0
    for p in [release, *release.rglob("*")]:
        st = os.lstat(p)
        assert not p.is_symlink() and st.st_uid == os.geteuid() and st.st_gid == os.getegid()
        assert (st.st_mode & 0o777) == (0o750 if p.is_dir() else 0o640), p.relative_to(release)


# 14. ── nothing private in plain text ──
def test_acks_inventory_state_and_status_hold_no_folder_name_title_or_text(w, monkeypatch, capsys):
    changes_on_mac(w)
    tampered = send(w, [w.ids["a"], w.ids["b"], w.ids["d"]])
    va = load("vault_apply")
    jobs = w.tmp / "jobs"
    monkeypatch.setattr(va.alert, "send", lambda text: "not_sent")
    assert va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"]) == 0
    st = json.loads((jobs / "vault-apply.json").read_text())
    assert st["state"] == "ok" and st["results"]["apply"] == "ok" and st["results"]["applied"] == "3"
    private = []
    for v in V.record_versions(w.mac.root)[0].values():
        private.append(v.name)
    sources = json.loads((w.mac.root / "_ledger" / "source_index.json").read_text())
    private += list(sources) + [e["title"] for e in sources.values() if e.get("title")] + list(TEXT_WORDS) + ["alpha question"]
    printed = capsys.readouterr()
    everything = (printed.out + printed.err + "".join(p.read_text() for p in (w.destdir / V.ACK_PREFIX).iterdir())
                  + "".join(p.read_text() for p in jobs.glob("*.json")) + (w.sync / "applied.json").read_text()
                  + (w.sync / "inventory.json").read_text() + json.dumps(ack(w, tampered)))
    assert not [s for s in private if s in everything]


def test_plan_is_read_only_and_reports_the_seed_due(w, capsys):
    va = load("vault_apply")
    before = tree_state(w.vault.parent)
    assert va.main(cli(w, w.tmp / "jobs") + ["plan", "--check-remote"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["current"] == current(w) and plan["current_format"] == P.FORMAT and plan["index_seed_due"] is False
    assert plan["remote"] == {"complete_batches": 0, "not_applied": 0, "oldest_waiting": None}
    assert tree_state(w.vault.parent) == before


# 15. ── review findings (17 Oct adversarial review) ──
def stray_edition(root: Path, rid: str) -> None:
    """A non-contiguous edition file next to the real ones: valid front matter, so ``check_version`` passes, but the record does not verify."""
    v = V.record_versions(root)[0][rid]
    eds = sorted((root / v.kind / v.name / "editions").iterdir())
    (root / v.kind / v.name / "editions" / ("9--" + "a" * 12 + eds[0].suffix)).write_bytes(eds[0].read_bytes())


def test_r2_a_version_that_does_not_verify_on_its_own_is_rejected_and_never_blocks_later_batches(w, monkeypatch):
    apply(w)
    good = add_record(w.mac, "grok", "g-d", [("human", "delta question"), ("assistant", "delta answer zeppelin")])
    bad = w.ids["a"]
    live_bad = V.record_versions(w.vault / "current" / "shelf")[0][bad].digest
    stray_edition(w.mac.root, bad)
    b1 = send(w, [good, bad])
    placed = []
    out = apply(w, hooks={"placed": placed.append})
    assert out["code"] == "ok" and out["built"] and out["applied"] == 1 and out["rejected"] == 1
    assert statuses(ack(w, b1)) == {good: "stored", bad: "rejected:record_does_not_verify"}
    assert placed == [good]                                                     # refused at evaluation: never placed in a build
    held = V.record_versions(w.vault / "current" / "shelf")[0]
    assert held[bad].digest == live_bad and held[good].digest == V.record_versions(w.mac.root)[0][good].digest
    assert w.ids["a"] in keyword(w, "kumquat", monkeypatch) and good in keyword(w, "zeppelin", monkeypatch)
    later = add_record(w.mac, "grok", "g-e", [("human", "epsilon"), ("assistant", "epsilon answer gazebo")])
    b2 = send(w, [later])
    again = apply(w)                                                            # no repeated failure: the later batch applies
    assert again["code"] == "ok" and again["batches"] == 1 and statuses(ack(w, b2)) == {later: "stored"}
    assert json.loads((w.sync / "applied.json").read_text())[b1]["result"] == "applied"
    no_scratch_left(w)


def test_r2_a_whole_release_check_the_build_pins_on_one_version_rejects_it_and_completes_the_rest(w, monkeypatch):
    """The one-record check is bypassed at evaluation (as if a check only the whole release makes failed): the build's own verification
    names the version, the build is redone without it, the others apply and are acked, nothing is retried forever."""
    apply(w)
    good = add_record(w.mac, "grok", "g-d", [("human", "delta question"), ("assistant", "delta answer zeppelin")])
    bad = w.ids["a"]
    live_bad = V.record_versions(w.vault / "current" / "shelf")[0][bad].digest
    stray_edition(w.mac.root, bad)
    real = A.record_verifies
    monkeypatch.setattr(A, "record_verifies", lambda folder, kind, name, work: True if work.parent.name.startswith("b-")
                        else real(folder, kind, name, work))
    b1 = send(w, [good, bad])
    b2 = send(w, [bad])                                                         # a retry of the same bad version in the same run
    out = apply(w)
    assert out["code"] == "ok" and out["built"] and out["applied"] == 1 and out["rejected"] == 2
    assert statuses(ack(w, b1)) == {good: "stored", bad: "rejected:record_does_not_verify"}
    assert statuses(ack(w, b2)) == {bad: "rejected:record_does_not_verify"}     # never "stored" against the refused version
    held = V.record_versions(w.vault / "current" / "shelf")[0]
    assert held[bad].digest == live_bad and good in held
    assert P.check_permissions(w.vault / "releases" / out["release"], owner_uid=os.geteuid(), reader_uid=os.geteuid(), gid=os.getegid())
    no_scratch_left(w)


def test_r2_an_infrastructure_failure_in_the_build_still_fails_the_run_with_no_ack(w):
    apply(w)
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])
    serving = current(w)

    class Down(A.HostRunner):
        def index(self, build, only):
            raise B.BackupError("index_runner_failed")
    with pytest.raises(B.BackupError) as e:
        apply(w, index_runner=Down())
    assert e.value.code == "index_runner_failed" and current(w) == serving
    assert not (w.destdir / V.ACK_PREFIX / f"{bid}.json").exists()
    no_scratch_left(w)


def test_r4_an_index_from_an_older_chunker_is_rebuilt_in_the_same_build_and_rebuild_index_forces_one(w, monkeypatch):
    from core.memory_index import chunker
    assert apply(w)["code"] in ("ok", "noop")
    rid = add_record(w.mac, "grok", "g-d", [("human", "delta question"), ("assistant", "delta answer zeppelin")])
    bid = send(w, [rid])
    monkeypatch.setattr(chunker, "CHUNKER_VERSION", chunker.CHUNKER_VERSION + 1)    # a new portal image with a newer chunker
    out = apply(w)
    assert out["code"] == "ok" and out["built"] and out["index_seed"] and statuses(ack(w, bid)) == {rid: "stored"}
    con = sqlite3.connect(f"file:{w.vault / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    try:
        assert store.get_meta(con)["chunker_version"] == str(chunker.CHUNKER_VERSION) and store.is_keyword_only(con)
    finally:
        con.close()
    assert rid in keyword(w, "zeppelin", monkeypatch) and w.ids["a"] in keyword(w, "kumquat", monkeypatch)
    # an explicit rebuild, no batch: a new release with a rebuilt keyword index
    serving = current(w)
    forced = apply(w, rebuild_index=True)
    assert forced["built"] and forced["index_seed"] and forced["batches"] == 0 and current(w) != serving
    va = load("vault_apply")
    jobs = w.tmp / "jobs"
    monkeypatch.setattr(va.alert, "send", lambda text: "not_sent")
    assert va.main(cli(w, jobs) + ["--index-runner", "host", "--rebuild-index", "run", "--execute"]) == 0
    st = json.loads((jobs / "vault-apply.json").read_text())
    assert st["results"]["index_seed"] == "yes" and st["results"]["apply"] == "ok"


def test_r1_a_sessions_raw_version_without_its_source_entry_is_rejected_not_acked_stored(w, monkeypatch):
    apply(w)
    rid = add_record(w.mac, "grok", "g-d", [("human", "delta question"), ("assistant", "delta answer zeppelin")])
    full = V.source_index(w.mac.root)
    bid = send(w, [rid], sources={k: v for k, v in full.items() if v.get("id") != rid})   # the state a racing ingest leaves
    out = apply(w)
    assert statuses(ack(w, bid)) == {rid: "rejected:source_entry_missing"} and not out["built"]
    assert rid not in inventory(w)["records"]                                   # so the Mac's diff sends it again, with its entry
    again = send(w, [rid])
    apply(w)
    assert statuses(ack(w, again)) == {rid: "stored"} and rid in keyword(w, "zeppelin", monkeypatch)


def test_r1_every_applied_sessions_raw_record_must_have_an_index_row(w, monkeypatch):
    """The source index the build writes loses one applied record's entry (so the indexer never reads it): that record is rejected
    ``not_indexed``, never acked ``stored`` while vault-ask cannot find it; the other applies."""
    apply(w)
    changes_on_mac(w)
    real = A._rewrite_source_index

    def lose_d(shelf, to_apply, uid, gid):
        doc = real(shelf, to_apply, uid, gid)
        doc = {k: e for k, e in doc.items() if e.get("id") != w.ids["d"]}
        P._write_private_json(shelf / "_ledger" / "source_index.json", doc, uid, gid)
        return doc
    monkeypatch.setattr(A, "_rewrite_source_index", lose_d)
    bid = send(w, [w.ids["a"], w.ids["d"]])
    out = apply(w)
    assert statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["d"]: "rejected:not_indexed"} and out["applied"] == 1
    assert w.ids["d"] not in V.record_versions(w.vault / "current" / "shelf")[0]


def test_r3_a_failed_inventory_upload_is_retried_on_the_next_run(w, monkeypatch):
    real_put = B.Dest.put
    calls = {"n": 0}

    def flaky(self, local, key, *, replace=False):
        if key.endswith(V.INVENTORY_NAME) and calls["n"] == 0:
            calls["n"] += 1
            raise B.BackupError("upload_failed")
        return real_put(self, local, key, replace=replace)
    monkeypatch.setattr(B.Dest, "put", flaky)
    with pytest.raises(B.BackupError):
        apply(w)                                  # the first inventory's upload fails
    assert not (w.destdir / V.ACK_PREFIX / V.INVENTORY_NAME).exists() and not (w.sync / "inventory.json").exists()
    out = apply(w)                                # 15 min later, nothing new: the upload is retried, not skipped for 20 h
    assert out["inventory"] == "written" and inventory(w)["version_set_digest"] == mac_digest(w)
    assert json.loads((w.sync / "inventory.json").read_text()) == inventory(w)


def test_6_a_source_entry_whose_folder_differs_from_the_record_is_rejected_for_every_kind(w):
    apply(w)
    forged = w.tmp / "mac-notes"
    shutil.copytree(w.mac.root, forged)
    c = V.record_versions(forged)[0][w.ids["c"]]
    (forged / "notes").mkdir(exist_ok=True)
    os.rename(forged / c.dir, forged / "notes" / c.name)                       # the record as a notes record (not sessions-raw)
    sources = V.source_index(forged)
    key = next(k for k, e in sources.items() if e["id"] == w.ids["c"])
    for bad_dir in ("../sessions-raw/elsewhere", "other-folder"):
        sources[key] = {**sources[key], "dir": bad_dir}
        bid = send(w, [w.ids["c"]], root=forged, sources=sources)
        out = apply(w)
        assert statuses(ack(w, bid)) == {w.ids["c"]: "rejected:source_entry_mismatch"} and not out["built"]
    assert json.loads((w.vault / "current" / "shelf" / "_ledger" / "source_index.json").read_text())[key]["dir"] == c.name


def test_6_a_source_key_production_maps_to_another_record_is_never_repointed(w):
    apply(w)
    w.ids["d"] = add_record(w.mac, "grok", "g-d", [("human", "delta question"), ("assistant", "delta answer zeppelin")])
    forged = w.tmp / "mac-keys"
    shutil.copytree(w.mac.root, forged)
    a, d = V.record_versions(forged)[0][w.ids["a"]], V.record_versions(forged)[0][w.ids["d"]]
    main = forged / d.dir / f"{d.name}.md"
    main.write_text(main.read_text().replace(f"source: {d.source_key}", f"source: {a.source_key}", 1))
    assert V.record_versions(forged)[0][w.ids["d"]].source_key == a.source_key
    sources = V.source_index(forged)
    sources[a.source_key] = {**sources[d.source_key]}                          # d's entry under a's key
    bid = send(w, [w.ids["d"]], root=forged, sources=sources)
    out = apply(w)
    assert statuses(ack(w, bid)) == {w.ids["d"]: "rejected:source_key_conflict"} and not out["built"]
    assert json.loads((w.vault / "current" / "shelf" / "_ledger" / "source_index.json").read_text())[a.source_key]["id"] == w.ids["a"]


def test_7_current_and_releases_are_fsynced_before_anything_is_acked_stored(w, monkeypatch):
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])

    def crash(*_):
        raise RuntimeError("injected crash")
    with pytest.raises(RuntimeError):
        apply(w, hooks={"after_switch": crash})
    events = []
    real_fsync, real_put = P._fsync_dir, B.Dest.put
    monkeypatch.setattr(P, "_fsync_dir", lambda path: (events.append(("fsync", Path(path))), real_fsync(path))[1])
    monkeypatch.setattr(B.Dest, "put", lambda self, local, key, *, replace=False: (events.append(("put", key)),
                                                                                    real_put(self, local, key, replace=replace))[1])
    out = apply(w)                                                              # acks stored from the serving release, no new build
    assert not out["built"] and statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
    first_ack = events.index(("put", f"{V.ACK_PREFIX}/{bid}.json"))
    assert ("fsync", w.vault) in events[:first_ack] and ("fsync", w.vault / "releases") in events[:first_ack]


def test_8_status_reports_the_last_batch_processed(w, monkeypatch):
    changes_on_mac(w)
    send(w, [w.ids["d"]])
    bid = send(w, [w.ids["a"]])
    va = load("vault_apply")
    jobs = w.tmp / "jobs"
    monkeypatch.setattr(va.alert, "send", lambda text: "not_sent")
    assert va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"]) == 0
    assert json.loads((jobs / "vault-apply.json").read_text())["results"]["last_batch"] == bid.lower()
    assert va.main(cli(w, jobs) + ["--index-runner", "host", "run", "--execute"]) == 0
    assert json.loads((jobs / "vault-apply.json").read_text())["results"]["last_batch"] == "none"


# ── helpers for the command lines ──
def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "vault" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def cli(w, jobs):
    return ["--dest", w.dest, "--vault-root", str(w.vault), "--identity", w.identity, "--age-bin", w.age, "--jobs-root", str(jobs),
            "--reader-uid", str(os.geteuid()), "--reader-gid", str(os.getegid()), "--_allow-non-root"]



def test_a_batch_declaring_more_bytes_than_its_manifest_reserved_is_rejected_whole_and_never_extracted_past_it(w):
    """Space is reserved from the manifest's record_bytes; BATCH.json may not declare (and extraction may not write) more than that."""
    changes_on_mac(w)
    bid = send(w, edit_manifest=lambda m: m.update(record_bytes=10))
    out = apply(w)
    assert out["rejected_batches"] == 1
    assert {s for s in statuses(ack(w, bid)).values()} == {f"{V.REJECTED}:batch_too_large"}


def test_when_every_applied_version_fails_to_recompute_the_build_is_at_fault_and_nothing_is_rejected_for_good(w):
    """Second review: a fault in the build itself (every placed version wrong) must fail the run and retry, never permanently reject
    versions that were verified on their own."""
    apply(w)
    before = current(w)
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])

    def damage(build):                                  # every applied record's freshly written main file (never a hard link) is wrong
        for rid in (w.ids["a"], w.ids["d"]):
            v = V.record_versions(w.mac.root)[0][rid]
            main = Path(build) / "shelf" / v.kind / v.name / f"{v.name}.md"
            main.write_bytes(main.read_bytes() + b"x")
    with pytest.raises(B.BackupError) as exc:
        apply(w, hooks={"indexed": damage})
    assert exc.value.code == "applied_version_mismatch" and current(w) == before
    assert not (w.destdir / V.ACK_PREFIX / f"{bid}.json").exists()
    out = apply(w)
    assert out["built"] and statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
