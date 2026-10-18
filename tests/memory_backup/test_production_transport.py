"""The Vault's production transport: Mac publish of one consistent (shelf, index) pair, and the EC2 refresh that installs it.

Fully synthetic: the shelf is the synthetic vault_t1 snapshot, the index is a tiny SQLite file with the real table shapes (FTS5 contentless, and a
sqlite-vec table when that extension is installed), the destination is file://, and encryption uses the pinned real `age` with an identity generated
in the test when it is present, otherwise a stand-in with the same flags. No network, no AWS, no real key, no owner data."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.vault_t1.reader import Vault

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "tests" / "vault_t1" / "snapshot"
REAL_AGE = Path.home() / "minimoi-staging" / "tools" / "age-v1.3.2" / "age"
REAL_KEYGEN = REAL_AGE.with_name("age-keygen")
T0 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)                 # 05:00 CDT

FAKE_AGE = r'''#!/usr/bin/env python3
import hashlib, sys
a = sys.argv[1:]
if a == ["--version"]:
    print("v1.3.2"); sys.exit(0)
def k(r): return hashlib.sha256(r.encode()).digest()
if "-r" in a:
    r, out, src = a[a.index("-r") + 1], a[a.index("-o") + 1], a[-1]
    d = open(src, "rb").read(); key = k(r)
    open(out, "wb").write(b"FAKEAGE\n" + r.encode() + b"\n" + bytes(b ^ key[i % 32] for i, b in enumerate(d)))
elif "-d" in a:
    ident, out, src = a[a.index("-i") + 1], a[a.index("-o") + 1], a[-1]
    r = "age1" + open(ident).read().strip().split("AGE-SECRET-KEY-1")[1].lower()
    head, rec, body = open(src, "rb").read().split(b"\n", 2)
    if head != b"FAKEAGE" or rec.decode() != r: sys.exit(1)
    key = k(r); open(out, "wb").write(bytes(b ^ key[i % 32] for i, b in enumerate(body)))
else:
    sys.exit(2)
'''


def make_age(tmp: Path) -> tuple[str, str, str]:
    """(age binary, public recipient, file:identity spec)."""
    ident = tmp / "keys" / "identity.agekey"
    ident.parent.mkdir(mode=0o700)
    if REAL_AGE.is_file() and REAL_KEYGEN.is_file():
        subprocess.run([str(REAL_KEYGEN), "-o", str(ident)], check=True, capture_output=True)
        recipient = subprocess.run([str(REAL_KEYGEN), "-y", str(ident)], check=True, capture_output=True, text=True).stdout.strip()
        ident.chmod(0o600)
        return str(REAL_AGE), recipient, f"file:{ident}"
    body = "Q" * 58
    ident.write_text(f"AGE-SECRET-KEY-1{body}\n"); ident.chmod(0o600)
    fake = tmp / "age"
    fake.write_text(FAKE_AGE); fake.chmod(0o755)
    return str(fake), "age1" + body.lower(), f"file:{ident}"


def build_index(shelf: Path, path: Path, *, drop: str | None = None, ghost: bool = False) -> None:
    """A tiny index with the real table shapes, pointing at the shelf's records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript("""
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE records(record_id TEXT PRIMARY KEY, rel TEXT NOT NULL, edition INTEGER, edition_hash TEXT, file_size INTEGER, file_mtime_ns INTEGER,
  file_sha256 TEXT, provider TEXT, chair TEXT, kind TEXT, tier TEXT, scope TEXT, canary INTEGER, created TEXT, normalizer INTEGER,
  state TEXT NOT NULL, reason TEXT, chunks INTEGER NOT NULL DEFAULT 0, indexed_at TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY, chunk_id TEXT UNIQUE NOT NULL, record_id TEXT NOT NULL, edition_hash TEXT NOT NULL, ordinal INTEGER NOT NULL,
  turn INTEGER NOT NULL, speaker TEXT NOT NULL, rel TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, text_hash TEXT NOT NULL,
  embedder TEXT NOT NULL, embedder_version TEXT NOT NULL, tier TEXT NOT NULL, scope TEXT NOT NULL, canary INTEGER NOT NULL, created TEXT,
  truncated INTEGER NOT NULL);
CREATE VIRTUAL TABLE fts USING fts5(text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
""")
    try:
        import sqlite_vec
        con.enable_load_extension(True); sqlite_vec.load(con); con.enable_load_extension(False)
        con.execute("CREATE VIRTUAL TABLE vec USING vec0(id INTEGER PRIMARY KEY, embedding float[4] distance_metric=cosine, tier TEXT, canary INTEGER)")
    except Exception:                                                           # noqa: BLE001 - optional realism
        pass
    con.executemany("INSERT INTO meta VALUES(?, ?)", [("schema_version", "1"), ("policy_version", "3"), ("chunker_version", "2"),
                                                      ("embedder", "synthetic"), ("embedder_digest", "d" * 64), ("dims", "4"),
                                                      ("built_at", "2026-10-17T07:30:00Z"), ("updated_at", "2026-10-17T07:31:00Z")])
    n = 0
    for rec in Vault(str(shelf), ())._all():
        if rec.id == drop:
            continue
        rel = rec.path.relative_to(shelf).as_posix()
        con.execute("INSERT INTO records(record_id, rel, edition_hash, state, chunks) VALUES(?,?,?,?,1)", (rec.id, rel, rec.meta.get("edition_hash"), "indexed"))
        n += 1
        con.execute("INSERT INTO chunks VALUES(?,?,?,?,0,0,'human',?,1,2,'h','synthetic','1','raw','robert',0,NULL,0)",
                    (n, f"{rec.id}#{n}", rec.id, rec.meta.get("edition_hash") or "", rel))
        con.execute("INSERT INTO fts(rowid, text) VALUES(?, ?)", (n, "synthetic garden words minimoi"))
    if ghost:
        con.execute("INSERT INTO records(record_id, rel, edition_hash, state) VALUES('01ZZZZZZZZZZZZZZZZZZZZZZZZ','sessions-raw/x/x.md','e','indexed')")
    con.commit(); con.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    shelf = tmp_path / "data" / "memory"
    shutil.copytree(SNAPSHOT, shelf)
    index = tmp_path / "data" / "memory-index" / "memory.sqlite"
    build_index(shelf, index)
    age, recipient, identity = make_age(tmp_path)
    vault = tmp_path / "ec2" / "vault"
    vault.parent.mkdir(mode=0o755)
    return {"tmp": tmp_path, "shelf": shelf, "index": index, "age": age, "recipient": recipient, "identity": identity,
            "dest": f"file://{tmp_path / 'dest'}", "destdir": tmp_path / "dest", "work": tmp_path / "work", "vault": vault}


def publish(w, now=T0, **kw):
    args = dict(shelf_root=str(w["shelf"]), index_path=str(w["index"]), dest=w["dest"], recipient=w["recipient"], workdir=str(w["work"]),
                age_bin=w["age"], now=now, enforce_window=False)
    args.update(kw)
    return P.run_publish(**args)


def refresh(w, **kw):
    args = dict(dest=w["dest"], vault_root=str(w["vault"]), identity_spec=w["identity"], age_bin=w["age"], owner_uid=os.geteuid(),
                reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False)
    args.update(kw)
    return P.run_refresh(**args)


def pointer(w) -> dict:
    return json.loads((w["destdir"] / "production" / "latest.json").read_text())


def attempt_dir(w, m) -> Path:
    return w["destdir"] / m["key"]


def private_strings(shelf: Path) -> list[str]:
    """Every record id, folder name and source key on the synthetic shelf: none may appear in anything the transport writes in the clear."""
    out = []
    for rec in Vault(str(shelf), ())._all():
        out += [rec.id, rec.path.parent.name]
    out += list(json.loads((shelf / "_ledger" / "source_index.json").read_text()))
    return out


def reseal(w, m, mutate) -> None:
    """A publisher that is internally consistent but wrong: rewrite the manifest, then COMPLETE and the pointer so every hash still chains."""
    folder = attempt_dir(w, m)
    man = json.loads((folder / "manifest.json").read_text()); mutate(man)
    (folder / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    comp = json.loads((folder / "COMPLETE.json").read_text())
    comp["manifest_sha256"] = B.sha256_file(folder / "manifest.json")
    (folder / "COMPLETE.json").write_text(json.dumps(comp, indent=2, sort_keys=True) + "\n")
    ptr = pointer(w); ptr["complete_sha256"] = B.sha256_file(folder / "COMPLETE.json")
    (w["destdir"] / "production" / "latest.json").write_text(json.dumps(ptr) + "\n")


# ── publish ──
def test_publish_writes_one_consistent_encrypted_pair_with_complete_last_then_the_pointer(world, monkeypatch):
    order = []
    real_put = B.Dest.put
    monkeypatch.setattr(B.Dest, "put", lambda self, local, key, replace=False: (order.append(key.rsplit("/", 1)[-1]), real_put(self, local, key, replace=replace))[1])
    m = publish(world)
    assert order == ["shelf.tar.gz.age", "index.sqlite.age", "manifest.json", "COMPLETE.json", "latest.json"]
    folder = attempt_dir(world, m)
    assert folder.parent.name == "2026-10-17" and folder.parent.parent.name == "production"
    assert sorted(p.name for p in folder.iterdir()) == ["COMPLETE.json", "index.sqlite.age", "manifest.json", "shelf.tar.gz.age"]
    for name in ("shelf.tar.gz.age", "index.sqlite.age"):
        head = (folder / name).read_bytes()[:16]
        assert not head.startswith(b"\x1f\x8b") and not head.startswith(b"SQLite format 3")
    man = json.loads((folder / "manifest.json").read_text())
    for k in ("shelf", "index"):
        assert man["objects"][k]["sha256"] == hashlib.sha256((folder / man["objects"][k]["name"]).read_bytes()).hexdigest()
        assert len(man["objects"][k]["plaintext_sha256"]) == 64 and man["objects"][k]["plaintext_bytes"] > 0
    assert man["shelf"]["records"] == 4 and man["shelf"]["records_all"] == 5 and sum(man["shelf"]["records_by_source"].values()) == 5
    assert man["index"]["build"]["built_at"] == "2026-10-17T07:30:00Z" and man["index"]["chunks"] == 5 and man["index"]["copied_with"] == "sqlite_online_backup"
    assert man["pair"] == {"order": "index_then_shelf", "index_records_missing_from_shelf": 0, "index_records_stale": 0, "shelf_records_not_in_index": 0}
    assert man["created_utc"] == "2026-10-17T10:00:00Z" and man["source_host"] and man["age"]["recipient_fingerprint"] == B.fingerprint(world["recipient"])
    comp = json.loads((folder / "COMPLETE.json").read_text())
    assert comp["manifest_sha256"] == B.sha256_file(folder / "manifest.json")
    assert pointer(world) == {"format": P.FORMAT, "attempt": m["attempt"], "day": "2026-10-17", "created_utc": m["created_utc"],
                              "complete_sha256": B.sha256_file(folder / "COMPLETE.json")}
    assert not list(world["work"].iterdir())                                    # no plaintext or scratch left
    text = (folder / "manifest.json").read_text() + (folder / "COMPLETE.json").read_text() + json.dumps(pointer(world))
    assert not [s for s in private_strings(world["shelf"]) if s in text]


def test_the_source_key_digests_are_exactly_the_ledger_keys_per_provider_and_overall(world):
    m = publish(world)
    keys = sorted(json.loads((world["shelf"] / "_ledger" / "source_index.json").read_text()))
    digest = lambda ks: hashlib.sha256("\n".join(sorted(ks)).encode()).hexdigest()
    sk = m["shelf"]["source_keys"]
    assert sk["present"] and sk["total"] == len(keys) and sk["digest"] == digest(keys)
    for provider in {k.split(":", 1)[0] for k in keys}:
        mine = [k for k in keys if k.split(":", 1)[0] == provider]
        assert sk["by_provider"][provider] == {"count": len(mine), "digest": digest(mine)}


def test_a_failed_upload_leaves_the_pointer_where_it_was_and_a_rerun_makes_a_new_attempt(world, monkeypatch):
    first = publish(world)
    before = pointer(world)
    real_put = B.Dest.put

    def failing(self, local, key, replace=False):
        if key.endswith("COMPLETE.json"):
            raise B.BackupError("upload_failed")
        return real_put(self, local, key, replace=replace)
    monkeypatch.setattr(B.Dest, "put", failing)
    with pytest.raises(B.BackupError) as e:
        publish(world, now=T0 + timedelta(minutes=5))
    assert e.value.code == "upload_failed" and pointer(world) == before
    orphans = [p for p in (world["destdir"] / "production" / "2026-10-17").iterdir() if p.name != first["attempt"]]
    assert len(orphans) == 1 and not (orphans[0] / "COMPLETE.json").exists()
    assert not list(world["work"].iterdir())
    monkeypatch.setattr(B.Dest, "put", real_put)
    again = publish(world, now=T0 + timedelta(minutes=10))
    assert pointer(world)["attempt"] == again["attempt"] not in (first["attempt"], orphans[0].name)
    refresh(world)                                                              # the orphan is never installed
    assert os.readlink(world["vault"] / "current") == f"releases/{again['attempt']}"


def test_an_index_that_points_at_records_the_shelf_lacks_is_refused_before_upload(world):
    build_index(world["shelf"], world["index"].with_name("ghost.sqlite"), ghost=True)
    with pytest.raises(B.BackupError) as e:
        publish(world, index_path=str(world["index"].with_name("ghost.sqlite")))
    assert e.value.code == "index_shelf_mismatch" and not world["destdir"].exists()
    assert not list(world["work"].iterdir())


def test_index_lag_is_recorded_not_refused_and_an_open_writer_never_leaks_uncommitted_rows(world):
    first = Vault(str(world["shelf"]), ())._all()[0].id
    lag = world["index"].with_name("lag.sqlite")
    build_index(world["shelf"], lag, drop=first)
    writer = sqlite3.connect(str(lag))
    writer.execute("BEGIN")
    writer.execute("INSERT INTO meta VALUES('uncommitted', 'x')")
    try:
        m = publish(world, index_path=str(lag))
    finally:
        writer.rollback(); writer.close()
    assert m["pair"]["shelf_records_not_in_index"] == 1 and m["pair"]["index_records_missing_from_shelf"] == 0
    assert "uncommitted" not in json.dumps(m["index"])


def test_publish_refuses_when_the_reserve_would_not_remain(world):
    with pytest.raises(B.BackupError) as e:
        publish(world, usage=lambda p: shutil._ntuple_diskusage(10, 9, 1))
    assert e.value.code == "insufficient_space" and not world["destdir"].exists()


# ── refresh ──
def test_refresh_installs_verifies_and_switches_atomically_with_tight_permissions(world):
    m = publish(world)
    out = refresh(world)
    v = world["vault"]
    assert out["state"] == "installed" and out["attempt"] == m["attempt"] and out["records"] == 4 and out["index_chunks"] == 5
    assert (v / "current").is_symlink() and os.readlink(v / "current") == f"releases/{m['attempt']}"     # relative: follows inside a container mount
    release = v / "releases" / m["attempt"]
    assert (v / "current" / "index" / "memory.sqlite").is_file() and (v / "current" / "shelf" / "_ledger" / "source_index.json").is_file()
    assert v.stat().st_mode & 0o777 in (0o700, 0o710)
    for p in [release, *release.rglob("*")]:
        mode = p.stat().st_mode & 0o777
        assert p.stat().st_uid == os.geteuid() and p.stat().st_gid == os.getegid() and not p.is_symlink()
        assert (mode & ~0o750 == 0) if p.is_dir() else (mode & ~0o640 == 0)
    inst = json.loads((v / "installed.json").read_text())
    assert inst["attempt"] == m["attempt"] and inst["records"] == 4 and inst["source_keys"] == m["shelf"]["source_keys"]
    assert not [p for p in v.iterdir() if p.name.startswith(".work-")]          # scratch wiped
    con = sqlite3.connect(f"file:{v / 'current' / 'index' / 'memory.sqlite'}?mode=ro&immutable=1", uri=True)
    assert con.execute("SELECT count(*) FROM fts WHERE fts MATCH 'garden'").fetchone()[0] == 5
    con.close()
    text = (v / "installed.json").read_text() + (release / "RELEASE.json").read_text() + json.dumps(out)
    assert not [s for s in private_strings(world["shelf"]) if s in text]


def test_refresh_is_a_no_op_when_nothing_new_was_published(world):
    publish(world)
    refresh(world)
    before = os.readlink(world["vault"] / "current"), (world["vault"] / "installed.json").read_bytes()
    out = refresh(world)
    assert out["state"] == "unchanged"
    assert (os.readlink(world["vault"] / "current"), (world["vault"] / "installed.json").read_bytes()) == before


@pytest.mark.parametrize("what,code", [("ciphertext", "ciphertext_mismatch"), ("manifest", "manifest_mismatch"), ("complete", "complete_mismatch")])
def test_refresh_refuses_a_tampered_object_and_leaves_the_current_release_serving(world, what, code):
    publish(world)
    refresh(world)
    current = os.readlink(world["vault"] / "current")
    m = publish(world, now=T0 + timedelta(hours=1))
    folder = attempt_dir(world, m)
    target = {"ciphertext": folder / "index.sqlite.age", "manifest": folder / "manifest.json", "complete": folder / "COMPLETE.json"}[what]
    data = bytearray(target.read_bytes()); data[len(data) // 2] ^= 0x01; target.write_bytes(bytes(data))
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == code and os.readlink(world["vault"] / "current") == current
    assert not (world["vault"] / "releases" / m["attempt"]).exists()
    assert not [p for p in world["vault"].iterdir() if p.name.startswith(".work-")]


@pytest.mark.parametrize("mutate,code", [
    (lambda man: man["shelf"].__setitem__("records", man["shelf"]["records"] + 1), "record_count_mismatch"),
    (lambda man: man["shelf"]["source_keys"].__setitem__("digest", "0" * 64), "source_keys_mismatch"),
    (lambda man: man["index"].__setitem__("chunks", man["index"]["chunks"] + 1), "index_count_mismatch"),
    (lambda man: man["index"]["build"].__setitem__("built_at", "2026-10-16T00:00:00Z"), "index_build_mismatch"),
])
def test_refresh_refuses_counts_or_digests_that_disagree_with_the_restored_bytes(world, mutate, code):
    m = publish(world)
    reseal(world, m, mutate)
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == code and not (world["vault"] / "current").exists()
    assert not list((world["vault"] / "releases").iterdir()) and not [p for p in world["vault"].iterdir() if p.name.startswith(".work-")]


def test_refresh_refuses_when_the_reserve_would_not_remain(world):
    publish(world)
    with pytest.raises(B.BackupError) as e:
        refresh(world, usage=lambda p: shutil._ntuple_diskusage(10, 9, 1))
    assert e.value.code == "insufficient_space" and not (world["vault"] / "current").exists()


def test_refresh_refuses_a_pointer_older_than_what_is_installed(world):
    old = publish(world)
    old_ptr = pointer(world)
    publish(world, now=T0 + timedelta(hours=1))
    refresh(world)
    (world["destdir"] / "production" / "latest.json").write_text(json.dumps(old_ptr) + "\n")
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == "pointer_not_newer" and old["attempt"] not in os.readlink(world["vault"] / "current")


def test_retention_keeps_current_plus_two_and_rollback_holds_the_bad_attempt(world):
    attempts = []
    for hour in range(4):
        attempts.append(publish(world, now=T0 + timedelta(hours=hour))["attempt"])
        refresh(world)
    releases = sorted(p.name for p in (world["vault"] / "releases").iterdir())
    assert releases == attempts[1:] and os.readlink(world["vault"] / "current") == f"releases/{attempts[3]}"
    out = P.run_rollback(vault_root=str(world["vault"]), owner_uid=os.geteuid(), reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False)
    assert out == {"state": "rolled_back", "attempt": attempts[2], "previous": attempts[3]}
    assert os.readlink(world["vault"] / "current") == f"releases/{attempts[2]}"
    inst = json.loads((world["vault"] / "installed.json").read_text())
    assert inst["attempt"] == attempts[2] and inst["rolled_back"] == [attempts[3]]
    with pytest.raises(B.BackupError) as e:
        refresh(world)                                                          # the pointer still names the rolled-back attempt
    assert e.value.code == "attempt_rolled_back"
    newer = publish(world, now=T0 + timedelta(hours=5))["attempt"]
    refresh(world)
    assert os.readlink(world["vault"] / "current") == f"releases/{newer}"
    assert attempts[2] in os.listdir(world["vault"] / "releases")               # the release rolled back to is retained as a previous


def test_a_crash_between_verification_and_switch_resumes_from_the_verified_release(world, monkeypatch):
    m = publish(world)
    real = P.Layout.switch
    monkeypatch.setattr(P.Layout, "switch", lambda self, a: (_ for _ in ()).throw(B.BackupError("switch_failed")))
    with pytest.raises(B.BackupError):
        refresh(world)
    assert (world["vault"] / "releases" / m["attempt"] / "RELEASE.json").is_file() and not (world["vault"] / "current").exists()
    monkeypatch.setattr(P.Layout, "switch", real)
    calls = []
    monkeypatch.setattr(B.Dest, "get", (lambda orig: lambda self, key, local: (calls.append(key), orig(self, key, local))[1])(B.Dest.get))
    out = refresh(world)
    assert out["state"] == "installed" and out["resumed"] is True
    assert not [k for k in calls if k.endswith(".age")]                         # no ciphertext downloaded again


def test_the_extractor_refuses_links_and_climbing_paths(tmp_path):
    for kind in ("symlink", "climb"):
        archive = tmp_path / f"{kind}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo("../evil" if kind == "climb" else "link")
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = 1
                import io
                tar.addfile(info, io.BytesIO(b"x"))
        target = tmp_path / f"out-{kind}"
        target.mkdir()
        with pytest.raises(B.BackupError) as e:
            P.extract_shelf(archive, target, os.geteuid(), os.getegid())
        assert e.value.code == "archive_unsafe" and not list(target.iterdir())


def test_a_docker_probe_runs_networkless_read_only_as_the_reader(tmp_path):
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true, "sqlite": "3.46.1", "rows": true}\n', stderr="")
    out = P.docker_fts_probe("sha256:" + "a" * 64, tmp_path, 0, 0, run=fake_run)
    argv = seen[0]
    assert out == {"where": "image", "sqlite": "3.46.1"}
    assert argv[argv.index("--network") + 1] == "none" and "--read-only" in argv and argv[argv.index("--user") + 1] == "0:0"
    assert f"{tmp_path}:/probe:ro" in argv


# ── the two command lines ──
def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "vault" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_both_jobs_are_plan_only_until_told_and_write_content_free_status_files(world, monkeypatch, capsys):
    pub, ref = load("publish_production"), load("production_refresh")
    jobs = world["tmp"] / "jobs"
    common = ["--shelf", str(world["shelf"]), "--index", str(world["index"]), "--dest", world["dest"], "--recipient", world["recipient"],
              "--age-bin", world["age"], "--jobs-root", str(jobs), "--workdir", str(world["work"])]
    assert pub.main(common + ["plan"]) == 0
    assert pub.main(common + ["run"]) == 2 and not world["destdir"].exists()
    monkeypatch.setattr(pub.alert, "send", lambda text: "not_sent")
    assert pub.main(common + ["run", "--execute", "--allow-outside-window"]) == 0
    st = json.loads((jobs / "vault-publish-production.json").read_text())
    assert st["state"] == "ok" and st["results"]["records"] == "4" and st["host"] == "mac"
    rcommon = ["--dest", world["dest"], "--vault-root", str(world["vault"]), "--identity", world["identity"], "--age-bin", world["age"],
               "--jobs-root", str(jobs), "--reader-uid", str(os.geteuid()), "--reader-gid", str(os.getegid()), "--_allow-non-root"]
    assert ref.main(rcommon + ["plan", "--check-remote"]) == 0
    assert ref.main(rcommon + ["refresh"]) == 2 and not world["vault"].exists()
    assert ref.main(rcommon + ["refresh", "--execute"]) == 2 and not world["vault"].exists()     # where to probe is required
    assert ref.main(rcommon + ["--fts-probe", "host", "refresh", "--execute"]) == 0
    st = json.loads((jobs / "vault-production-refresh.json").read_text())
    assert st["state"] == "ok" and st["results"]["refresh"] == "installed" and st["host"] == "ec2"
    work = world["vault"].parent / (world["vault"].name + "-work")
    assert work.is_dir() and (work.stat().st_mode & 0o777) == 0o700 and not list(work.iterdir())   # decrypt work outside the mount
    assert not [p for p in world["vault"].iterdir() if p.name.startswith(".work-")]
    assert ref.main(rcommon + ["--fts-probe", "host", "refresh", "--execute"]) == 0
    assert json.loads((jobs / "vault-production-refresh.json").read_text())["results"] == {"refresh": "unchanged", "verified": "ok"}
    printed = capsys.readouterr()
    everything = printed.out + printed.err + "".join(p.read_text() for p in jobs.glob("*.json"))
    assert not [s for s in private_strings(world["shelf"]) if s in everything]


# ── durability and fault injection (17 Oct review) ──
def _snapshot_state(w):
    v = w["vault"]
    return os.readlink(v / "current"), (v / "installed.json").read_bytes()


def _two_published(w):
    """A installed and serving; B published and not yet installed."""
    a = publish(w)
    refresh(w)
    b = publish(w, now=T0 + timedelta(hours=1))
    return a, b


def _no_temp_left(w):
    v = w["vault"]
    assert not [p for p in v.iterdir() if ".tmp-" in p.name or p.name.startswith((".work-", ".current-"))]
    work = v.parent / (v.name + "-work")
    assert not list(work.iterdir())


def test_a_short_write_still_lands_the_whole_document(tmp_path, monkeypatch):
    calls = []

    def half_first(fd, data):
        calls.append(len(data))
        return os.write(fd, bytes(data[: max(1, len(data) // 2)]) if len(calls) == 1 else data)
    monkeypatch.setattr(P, "_raw_write", half_first)
    doc = {"format": P.FORMAT, "attempt": "20261017T100000Z-aaaaaaaa", "padding": "x" * 4000}
    target = tmp_path / "installed.json"
    P._write_private_json(target, doc, os.geteuid(), os.getegid())
    assert len(calls) >= 2 and json.loads(target.read_text()) == doc
    assert [p.name for p in tmp_path.iterdir()] == ["installed.json"]


def test_a_write_that_makes_no_progress_refuses_before_the_switch_and_changes_nothing(world, monkeypatch):
    a, b = _two_published(world)
    before = _snapshot_state(world)
    monkeypatch.setattr(P, "_raw_write", lambda fd, data: 0)
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == "state_write_failed"
    assert _snapshot_state(world) == before and json.loads(before[1])["attempt"] == a["attempt"]
    assert not (world["vault"] / "releases" / b["attempt"]).exists()
    _no_temp_left(world)


def test_a_receipt_that_cannot_be_written_after_the_switch_moves_current_back_and_the_next_run_resumes(world, monkeypatch):
    a, b = _two_published(world)
    before = _snapshot_state(world)
    calls = []

    def fail_second(fd, data):                                                  # 1st: RELEASE.json in the build; 2nd: installed.json
        calls.append(1)
        return 0 if len(calls) >= 2 else os.write(fd, data)
    monkeypatch.setattr(P, "_raw_write", fail_second)
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == "installed_write_failed"
    assert _snapshot_state(world) == before                                      # current and the receipt still agree on A
    assert (world["vault"] / "releases" / b["attempt"] / "RELEASE.json").is_file()
    _no_temp_left(world)
    monkeypatch.undo()
    out = refresh(world)
    assert out["state"] == "installed" and out["resumed"] is True and out["attempt"] == b["attempt"]
    assert json.loads((world["vault"] / "installed.json").read_text())["previous"] == a["attempt"]


def _build_relative(path: str):
    """A path inside the refresh's build folder (<work>/.work-*/release/...), relative to it; None when elsewhere."""
    parts = Path(path).parts
    for i, part in enumerate(parts[:-1]):
        if part.startswith(".work-") and parts[i + 1] == "release":
            rel = Path(*parts[i + 2:]) if len(parts) > i + 2 else Path(".")
            m = __import__("re").match(r"^\.(.+)\.tmp-", rel.name)               # a state file is fsynced under its temp name, then renamed
            return (rel.with_name(m.group(1)) if m else rel).as_posix()
    return None


def test_every_payload_file_and_folder_is_fsynced_before_the_switch(world, monkeypatch):
    m = publish(world)
    events = []
    real_fsync, real_switch = P._fsync, P.Layout.switch
    monkeypatch.setattr(P, "_fsync", lambda fd, path: (events.append(("fsync", str(path))), real_fsync(fd, path))[1])
    monkeypatch.setattr(P.Layout, "switch", lambda self, attempt: (events.append(("switch", attempt)), real_switch(self, attempt))[1])
    refresh(world)
    switch_at = events.index(("switch", m["attempt"]))
    synced = [_build_relative(p) for kind, p in events[:switch_at] if kind == "fsync"]
    release = world["vault"] / "releases" / m["attempt"]
    expected = {"."} | {p.relative_to(release).as_posix() for p in release.rglob("*")}
    assert expected - set(synced) == set()                                       # every extracted file, the index, RELEASE.json, every folder
    order = [p for p in synced if p is not None]
    for folder in [p for p in expected if (release / p).is_dir() and p != "."]:
        children = [c for c in expected if c.startswith(folder + "/")]
        assert all(order.index(c) < len(order) - 1 - order[::-1].index(folder) for c in children)   # bottom-up: a folder after its contents
    assert order[-1] == "."                                                       # the build folder last
    after_build = [p for kind, p in events[:switch_at] if kind == "fsync" and _build_relative(p) is None]
    assert str(world["vault"] / "releases") in after_build                        # the rename into releases/ is durable before the switch
    assert ("fsync", str(world["vault"])) in events[switch_at:]                   # and the switch itself afterwards


@pytest.mark.parametrize("target", ["extracted_file", "index_file", "folder"])
def test_an_fsync_failure_before_the_switch_refuses_and_current_still_serves(world, monkeypatch, target):
    a, b = _two_published(world)
    before = _snapshot_state(world)
    real_fsync = P._fsync

    def failing(fd, path):
        rel = _build_relative(str(path))
        hit = rel is not None and {"extracted_file": rel.startswith("shelf/") and (rel.endswith(".jsonl") or rel.endswith(".md")),
                                   "index_file": rel == "index/memory.sqlite", "folder": rel == "shelf"}[target]
        if hit:
            raise OSError("injected fsync failure")
        return real_fsync(fd, path)
    monkeypatch.setattr(P, "_fsync", failing)
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == "fsync_failed"
    assert _snapshot_state(world) == before and not (world["vault"] / "releases" / b["attempt"]).exists()
    _no_temp_left(world)


def _corrupt(release: Path, how: str) -> None:
    if how == "index_truncated":
        idx = release / "index" / "memory.sqlite"
        idx.write_bytes(idx.read_bytes()[: idx.stat().st_size // 2])
    elif how == "edition_changed":
        ed = sorted(release.glob("shelf/sessions-raw/*/editions/*.jsonl"))[0]
        data = bytearray(ed.read_bytes()); data[len(data) // 2] ^= 0x01; ed.write_bytes(bytes(data))
    elif how == "marker_wrong":
        mark = release / "RELEASE.json"
        doc = json.loads(mark.read_text()); doc["manifest_sha256"] = "0" * 64; mark.write_text(json.dumps(doc))
    elif how == "release_gone":
        shutil.rmtree(release)


@pytest.mark.parametrize("how,cause", [("index_truncated", "index_unreadable"), ("edition_changed", "shelf_verify_failed"),
                                       ("marker_wrong", "release_conflict"), ("release_gone", "release_unreadable")])
def test_an_unchanged_refresh_reverifies_the_serving_release_and_rebuilds_a_corrupt_one(world, how, cause):
    m = publish(world)
    refresh(world)
    release = world["vault"] / "releases" / m["attempt"]
    _corrupt(release, how)
    out = refresh(world)
    assert out["state"] == "installed" and out["repaired"] is True and out["attempt"] == m["attempt"] and out["repair_cause"] == cause
    assert os.readlink(world["vault"] / "current") == f"releases/{m['attempt']}"
    again = refresh(world)
    assert again == {"state": "unchanged", "attempt": m["attempt"], "verified": True}
    _no_temp_left(world)


def test_a_corrupt_serving_release_that_cannot_be_rebuilt_is_refused_loudly_and_left_in_place(world):
    m = publish(world)
    refresh(world)
    release = world["vault"] / "releases" / m["attempt"]
    _corrupt(release, "edition_changed")
    before = _snapshot_state(world)
    cipher = attempt_dir(world, m) / "shelf.tar.gz.age"
    data = bytearray(cipher.read_bytes()); data[len(data) // 2] ^= 0x01; cipher.write_bytes(bytes(data))
    with pytest.raises(B.BackupError) as e:
        refresh(world)
    assert e.value.code == "current_release_corrupt" and e.value.detail == "shelf_verify_failed:ciphertext_mismatch"
    assert _snapshot_state(world) == before and (release / "RELEASE.json").is_file()   # nothing moved; the operator rolls back
    _no_temp_left(world)


def test_the_unchanged_reverification_costs_one_verify(world, monkeypatch):
    import time
    publish(world)
    refresh(world)
    calls = []
    real = P.verify_release
    monkeypatch.setattr(P, "verify_release", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    t = time.perf_counter()
    assert refresh(world)["state"] == "unchanged"
    elapsed = time.perf_counter() - t
    assert calls == [1] and elapsed < 5


def test_the_decrypt_identity_file_is_written_whole_even_under_short_writes(tmp_path, monkeypatch):
    from core.memory_backup import backup as B
    real, seen = B._raw_write, {}

    def short(fd, data):
        return real(fd, bytes(data)[:max(1, len(data) // 3)])
    monkeypatch.setattr(B, "_raw_write", short)

    def fake_run(argv, **kw):
        seen["identity"] = Path(argv[argv.index("-i") + 1]).read_text()
        Path(argv[argv.index("-o") + 1]).write_text("plain")
        return None
    monkeypatch.setattr(B.subprocess, "run", fake_run)
    identity = "AGE-SECRET-KEY-1" + "Q" * 58
    B.decrypt("age", identity, tmp_path / "c.age", tmp_path / "p", tmp_path)
    assert seen["identity"] == identity and not list(tmp_path.glob(".id-*"))
    monkeypatch.setattr(B, "_raw_write", lambda fd, data: 0)
    with pytest.raises(B.BackupError, match="key_write_failed"):
        B.decrypt("age", identity, tmp_path / "c.age", tmp_path / "p2", tmp_path)
    assert not list(tmp_path.glob(".id-*"))
