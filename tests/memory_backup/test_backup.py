"""The Vault's independent backup (V5) and restore drill. A stand-in `age` (same flags, a toy cipher) and a stand-in `aws`; no network, no real key, no provider.

Proves: only the PUBLIC recipient is used to back up; the private identity appears only in the drill and only as a 0600 file name; the manifest is last and holds no
names; wrong key, damaged ciphertext and a changed manifest are refused; failures upload nothing; the window rule; monthly copies; the job status file."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO / "tests" / "vault_t1" / "snapshot"
IDENT_BODY = "A" * 58
IDENTITY = f"AGE-SECRET-KEY-1{IDENT_BODY}\n"
RECIPIENT = "age1" + IDENT_BODY.lower()
OTHER_IDENTITY = f"AGE-SECRET-KEY-1{'B' * 58}\n"
IN_WINDOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)           # 04:00 CDT
OUT_WINDOW = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)         # 13:00 CDT
FIRST_OF_MONTH = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)      # 04:00 CST on the 1st

FAKE_AGE = r'''#!/usr/bin/env python3
import hashlib, os, sys
a = sys.argv[1:]
log = os.environ.get("FAKE_AGE_LOG")
if log:
    open(log, "a").write(" ".join(a) + "\n")
if a == ["--version"]:
    print("v1.2.1"); sys.exit(0)
def stream(r): return hashlib.sha256(r.encode()).digest()
if "-r" in a:
    r, out, src = a[a.index("-r") + 1], a[a.index("-o") + 1], a[-1]
    if os.environ.get("FAKE_AGE_FAIL_ENCRYPT"): sys.exit(1)
    k = stream(r); d = open(src, "rb").read()
    open(out, "wb").write(b"FAKEAGE\n" + r.encode() + b"\n" + bytes(b ^ k[i % 32] for i, b in enumerate(d)))
elif "-d" in a:
    ident, out, src = a[a.index("-i") + 1], a[a.index("-o") + 1], a[-1]
    txt = open(ident).read().strip()
    mode = oct(os.stat(ident).st_mode & 0o777)
    open(os.environ["FAKE_AGE_MODES"], "a").write(mode + "\n") if os.environ.get("FAKE_AGE_MODES") else None
    r = "age1" + txt.split("AGE-SECRET-KEY-1")[1].lower()
    raw = open(src, "rb").read(); head, rec, body = raw.split(b"\n", 2)
    if head != b"FAKEAGE" or rec.decode() != r: sys.exit(1)
    k = stream(r); open(out, "wb").write(bytes(b ^ k[i % 32] for i, b in enumerate(body)))
else:
    sys.exit(2)
'''
FAKE_AWS = r'''#!/usr/bin/env python3
import os, shutil, sys
a = sys.argv[1:]
open(os.environ["FAKE_AWS_LOG"], "a").write(" ".join(a) + "\n")
if os.environ.get("FAKE_AWS_FAIL_KEY") and any(os.environ["FAKE_AWS_FAIL_KEY"] in x for x in a): sys.exit(1)
root = os.environ["FAKE_S3_ROOT"]
def m(p): return os.path.join(root, p[len("s3://"):]) if p.startswith("s3://") else p
if a[:2] == ["s3", "cp"]:
    src, dst = m(a[2]), m(a[3])
    os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(src, dst); sys.exit(0)
if a[:2] == ["s3api", "put-object"]:
    opt = {a[i]: a[i + 1] for i in range(2, len(a) - 1, 2)}
    dst = os.path.join(root, opt["--bucket"], opt["--key"])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if opt.get("--if-none-match") == "*":
        try:
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)       # exclusive, like S3's conditional create
        except FileExistsError:
            sys.stderr.write("An error occurred (PreconditionFailed) when calling the PutObject operation: At least one of the pre-conditions you specified did not hold\n"); sys.exit(254)
        with os.fdopen(fd, "wb") as out, open(opt["--body"], "rb") as src: shutil.copyfileobj(src, out)
    else:
        shutil.copyfile(opt["--body"], dst)
    sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def world(tmp_path, monkeypatch):
    shelf = tmp_path / "shelf"
    shutil.copytree(SNAPSHOT, shelf)
    age, aws = tmp_path / "age", tmp_path / "aws"
    age.write_text(FAKE_AGE); aws.write_text(FAKE_AWS)
    for f in (age, aws):
        f.chmod(0o755)
    monkeypatch.setenv("FAKE_AGE_LOG", str(tmp_path / "age.log"))
    monkeypatch.setenv("FAKE_AGE_MODES", str(tmp_path / "age.modes"))
    monkeypatch.setenv("FAKE_AWS_LOG", str(tmp_path / "aws.log"))
    monkeypatch.setenv("FAKE_S3_ROOT", str(tmp_path / "s3"))
    ident = tmp_path / "identity.txt"
    ident.write_text(IDENTITY); ident.chmod(0o600)
    return {"shelf": shelf, "age": str(age), "aws": str(aws), "dest": f"file://{tmp_path / 'dest'}", "destdir": tmp_path / "dest", "work": tmp_path / "work",
            "identity": f"file:{ident}", "tmp": tmp_path}


def backup(w, now=IN_WINDOW, **kw):
    args = dict(shelf_root=str(w["shelf"]), dest=w["dest"], recipient=RECIPIENT, workdir=str(w["work"]), age_bin=w["age"], now=now)
    args.update(kw)
    return B.run_backup(**args)


def latest(w, day="2026-10-14", prefix="daily"):
    """The attempt folder the day's pointer names."""
    base = w["destdir"] / prefix / day
    return base / json.loads((base / "latest.json").read_text())["attempt"]


def drill(w, day="2026-10-14", **kw):
    args = dict(dest=w["dest"], day=day, identity_spec=w["identity"], workdir=str(w["work"]), age_bin=w["age"])
    args.update(kw)
    return B.restore_drill(**args)


def test_a_backup_is_encrypted_hashed_and_complete_comes_last_then_the_pointer(world):
    m = backup(world)
    base = world["destdir"] / "daily" / "2026-10-14"
    day = latest(world)
    assert sorted(p.name for p in base.iterdir()) == sorted(["latest.json", day.name]) and B.ATTEMPT.match(day.name)
    assert sorted(p.name for p in day.iterdir()) == ["COMPLETE.json", "manifest.json", "vault.tar.gz.age"]
    cipher = (day / "vault.tar.gz.age").read_bytes()
    assert not cipher.startswith(b"\x1f\x8b") and b"FAKEAGE" in cipher[:10]                       # not a gzip: the archive is not stored readable
    on_disk = json.loads((day / "manifest.json").read_text())
    assert on_disk["ciphertext"]["sha256"] == hashlib.sha256(cipher).hexdigest() and on_disk["ciphertext"]["bytes"] == len(cipher)
    assert on_disk["shelf"]["records"] == 4 and on_disk["shelf"]["verify_ok"] is True and on_disk["shelf"]["verified"] == "captured_copy" and m["state"] == "ok"
    assert on_disk["age"] == {"version": "v1.2.1", "recipient_fingerprint": B.fingerprint(RECIPIENT)} and on_disk["attempt"] == day.name
    complete = json.loads((day / "COMPLETE.json").read_text())
    assert complete["manifest_sha256"] == hashlib.sha256((day / "manifest.json").read_bytes()).hexdigest() and complete["ciphertext_sha256"] == on_disk["ciphertext"]["sha256"]
    pointer = json.loads((base / "latest.json").read_text())
    assert pointer["attempt"] == day.name and pointer["complete_sha256"] == hashlib.sha256((day / "COMPLETE.json").read_bytes()).hexdigest()
    assert not list(world["work"].iterdir())                                                        # no plaintext archive or scratch left behind


def test_the_manifest_holds_numbers_and_hashes_never_names_or_content(world):
    backup(world)
    text = (latest(world) / "manifest.json").read_text()
    for needle in ("naming-the-compost-heap", "claude-ai", "synthetic", "sessions-raw", str(world["shelf"]), RECIPIENT, IDENT_BODY):
        assert needle not in text


def test_the_backup_job_only_ever_sees_the_public_recipient(world):
    backup(world)
    log = Path(os.environ["FAKE_AGE_LOG"]).read_text().splitlines()
    encrypt_calls = [l for l in log if "-r" in l.split()]
    assert encrypt_calls and all(RECIPIENT in l for l in encrypt_calls)
    assert not any("-d" in l.split() or "-i" in l.split() or "AGE-SECRET-KEY" in l for l in log)
    assert not (world["tmp"] / "age.modes").exists()                                                # decrypt was never called


def test_restore_drill_opens_the_backup_with_only_the_object_the_key_and_age(world):
    backup(world)
    evidence = drill(world)
    assert evidence["ok"] is True and evidence["records"] == evidence["records_expected"] == 4 and evidence["verify"] == {"ok": True, "problems": 0}
    assert evidence["sample_edition"]["edition_bytes"] > 0 and len(evidence["sample_edition"]["edition_sha256"]) == 64
    assert "MiniMoi" in evidence["not_needed"] and "a provider login" in evidence["not_needed"]
    text = json.dumps(evidence)
    assert "naming-the-compost-heap" not in text and IDENT_BODY not in text                         # evidence carries counts and hashes only
    assert Path(os.environ["FAKE_AGE_MODES"]).read_text().split() == ["0o600"]                       # the identity existed only as a 0600 file
    assert not list(world["work"].iterdir())                                                        # and nothing is left behind


@pytest.mark.parametrize("what", ["wrong_key", "damaged_ciphertext", "changed_manifest", "changed_complete", "missing_complete", "missing_pointer", "missing_archive"])
def test_the_drill_refuses_a_wrong_key_damage_or_an_incomplete_backup(world, what):
    backup(world)
    day = latest(world)
    if what == "wrong_key":
        other = world["tmp"] / "other.txt"
        other.write_text(OTHER_IDENTITY)
        other.chmod(0o600)
        kw = {"identity_spec": f"file:{other}"}
    else:
        kw = {}
        if what == "damaged_ciphertext":
            raw = bytearray((day / "vault.tar.gz.age").read_bytes()); raw[-5] ^= 0xFF
            (day / "vault.tar.gz.age").write_bytes(bytes(raw))
        elif what == "changed_manifest":
            doc = json.loads((day / "manifest.json").read_text()); doc["archive"]["plaintext_sha256"] = "0" * 64
            (day / "manifest.json").write_text(json.dumps(doc))
        elif what == "changed_complete":
            doc = json.loads((day / "COMPLETE.json").read_text()); doc["ciphertext_bytes"] += 1
            (day / "COMPLETE.json").write_text(json.dumps(doc))
        elif what == "missing_complete":
            (day / "COMPLETE.json").unlink()
        elif what == "missing_pointer":
            (day.parent / "latest.json").unlink()
        else:
            (day / "vault.tar.gz.age").unlink()
    with pytest.raises(B.BackupError) as e:
        drill(world, **kw)
    assert e.value.code in {"decrypt_failed", "ciphertext_mismatch", "plaintext_mismatch", "download_failed", "manifest_mismatch", "complete_mismatch"}
    assert not list(world["work"].iterdir())


def test_a_missing_or_wrong_version_age_fails_before_anything_is_uploaded(world):
    with pytest.raises(B.BackupError) as e:
        backup(world, age_bin=str(world["tmp"] / "no-such-age"))
    assert e.value.code == "age_unavailable" and not world["destdir"].exists()
    with pytest.raises(B.BackupError) as e:
        backup(world, expect_age="v9.9.9")
    assert e.value.code == "age_version_mismatch" and not world["destdir"].exists()


def test_a_malformed_recipient_is_refused_and_a_private_key_is_never_accepted_as_one(world):
    for bad in ("", "age1short", IDENTITY.strip(), "ssh-ed25519 AAAA"):
        with pytest.raises(B.BackupError) as e:
            backup(world, recipient=bad)
        assert e.value.code == "recipient_malformed" and not world["destdir"].exists()


def test_the_job_runs_only_inside_the_standing_window(world):
    with pytest.raises(B.BackupError) as e:
        backup(world, now=OUT_WINDOW)
    assert e.value.code == "outside_window" and not world["destdir"].exists()
    backup(world, now=OUT_WINDOW, enforce_window=False)                                              # the owner starting it by hand
    assert (latest(world) / "COMPLETE.json").exists()
    assert B.in_window(IN_WINDOW) and not B.in_window(OUT_WINDOW)
    for utc, inside in (("2026-10-14T05:00:00", True), ("2026-10-14T10:59:00", True), ("2026-10-14T11:00:00", False), ("2026-10-14T04:59:00", False),     # CDT: 00:00-06:00
                        ("2026-12-14T06:00:00", True), ("2026-12-14T11:59:00", True), ("2026-12-14T12:00:00", False)):                                   # CST
        assert B.in_window(datetime.fromisoformat(utc).replace(tzinfo=timezone.utc)) is inside, utc


def test_the_first_of_the_month_also_keeps_a_monthly_copy_with_its_own_pointer_and_the_drill_can_open_it(world):
    m = backup(world, now=FIRST_OF_MONTH)
    attempt = m["keys"][0].split("/")[-1]
    assert m["keys"] == [f"daily/2026-12-01/{attempt}", f"monthly/2026-12/{attempt}"]
    assert (world["destdir"] / "monthly" / "2026-12" / attempt / "vault.tar.gz.age").exists() and (world["destdir"] / "monthly" / "2026-12" / "latest.json").exists()
    assert drill(world, day="2026-12", monthly=True)["ok"] is True


def test_a_failed_attempt_never_moves_the_pointer_or_touches_the_earlier_complete_backup(world):
    aws, s3d = world["aws"], "s3://bucket/vault"
    first = backup(world, dest=s3d, aws_bin=aws, now=IN_WINDOW)
    base = world["tmp"] / "s3" / "bucket" / "vault" / "daily" / "2026-10-14"
    before = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
    os.environ["FAKE_AWS_FAIL_KEY"] = "COMPLETE.json"
    try:
        with pytest.raises(B.BackupError) as e:
            backup(world, dest=s3d, aws_bin=aws, now=IN_WINDOW.replace(minute=30))
    finally:
        del os.environ["FAKE_AWS_FAIL_KEY"]
    assert e.value.code == "upload_failed"
    after = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()}
    assert {k: v for k, v in after.items() if k in before} == before                                  # every earlier object is byte-for-byte unchanged
    orphans = sorted(set(after) - set(before))
    assert orphans and all("COMPLETE.json" not in o and "latest.json" not in o for o in orphans)       # the failed attempt left objects but no COMPLETE and no pointer
    assert json.loads(after["latest.json"])["attempt"] == first["attempt"]
    assert drill(world, dest=s3d, aws_bin=aws)["ok"] is True                                           # the day still restores from the earlier complete attempt


def test_two_backups_the_same_day_both_survive_and_each_attempt_restores_exactly(world):
    one = backup(world, attempt="20261014T090000Z-aaaaaaaa")
    two = backup(world, attempt="20261014T093000Z-bbbbbbbb")
    base = world["destdir"] / "daily" / "2026-10-14"
    assert sorted(p.name for p in base.iterdir()) == ["20261014T090000Z-aaaaaaaa", "20261014T093000Z-bbbbbbbb", "latest.json"]
    assert json.loads((base / "latest.json").read_text())["attempt"] == two["attempt"]
    assert drill(world)["backup"].endswith("bbbbbbbb")
    assert drill(world, attempt="20261014T090000Z-aaaaaaaa")["backup"].endswith("aaaaaaaa")           # the earlier one is still there, still complete


def test_concurrent_writers_never_share_a_key_and_a_repeated_attempt_id_is_refused(world):
    import threading
    results, errors = [], []

    def go():
        try:
            results.append(backup(world)["attempt"])
        except Exception as exc:                                                                         # pragma: no cover
            errors.append(exc)
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    base = world["destdir"] / "daily" / "2026-10-14"
    assert not errors and len(set(results)) == 4 and sorted(p.name for p in base.iterdir() if p.is_dir()) == sorted(results)
    assert all((base / a / "COMPLETE.json").exists() for a in results) and json.loads((base / "latest.json").read_text())["attempt"] in results
    snapshot = {p: p.read_bytes() for p in base.rglob("*") if p.is_file() and p.name != "latest.json"}
    with pytest.raises(B.BackupError) as e:
        backup(world, attempt=results[0])                                                                # same id again: refused, nothing overwritten
    assert e.value.code == "object_exists" and {p: p.read_bytes() for p in snapshot} == snapshot


def test_s3_attempt_objects_are_conditional_creates_with_sse_and_the_pointer_comes_last(world):
    m = backup(world, dest="s3://minimoi-backups/vault", aws_bin=world["aws"])
    calls = Path(os.environ["FAKE_AWS_LOG"]).read_text().splitlines()
    assert len(calls) == 4
    for c, name in zip(calls[:3], ("vault.tar.gz.age", "manifest.json", "COMPLETE.json")):
        assert c.startswith("s3api put-object --bucket minimoi-backups --key vault/daily/2026-10-14/" + m["attempt"] + "/" + name)
        assert '--if-none-match *' in c and "--server-side-encryption AES256" in c                       # a real conditional create, never an unconditional copy
    assert calls[3].startswith("s3 cp ") and calls[3].endswith("s3://minimoi-backups/vault/daily/2026-10-14/latest.json --sse AES256 --only-show-errors")      # the pointer, last
    assert drill(world, dest="s3://minimoi-backups/vault", aws_bin=world["aws"])["ok"] is True


def test_a_colliding_attempt_id_on_s3_is_refused_by_the_store_and_the_first_objects_are_untouched(world):
    s3d, aws = "s3://bucket/vault", world["aws"]
    first = backup(world, dest=s3d, aws_bin=aws, attempt="20261014T090000Z-aaaaaaaa")
    folder = world["tmp"] / "s3" / "bucket" / "vault" / "daily" / "2026-10-14" / first["attempt"]
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    with pytest.raises(B.BackupError) as e:
        backup(world, dest=s3d, aws_bin=aws, attempt="20261014T090000Z-aaaaaaaa", now=IN_WINDOW.replace(minute=45))
    assert e.value.code == "object_exists" and {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_local_publication_is_exclusive_even_if_a_competing_writer_wins_between_any_check_and_the_write(world, monkeypatch):
    d = B.Dest(world["dest"])
    src = world["tmp"] / "payload.bin"
    src.write_bytes(b"MINE")
    real_link = os.link

    def rival_wins_first(a, b, *args, **kw):
        Path(b).write_bytes(b"THEIRS")                                                                   # the competing writer publishes at the last instant
        return real_link(a, b, *args, **kw)
    monkeypatch.setattr(B.os, "link", rival_wins_first)
    with pytest.raises(B.BackupError) as e:
        d.put(src, "daily/2026-10-14/x/vault.tar.gz.age")
    assert e.value.code == "object_exists" and (world["destdir"] / "daily/2026-10-14/x/vault.tar.gz.age").read_bytes() == b"THEIRS"
    assert not [p for p in (world["destdir"] / "daily/2026-10-14/x").iterdir() if ".tmp-" in p.name]       # no temp file left behind


def test_racing_writers_to_one_key_exactly_one_wins_and_its_bytes_are_intact(world):
    import threading
    d = B.Dest(world["dest"])
    outcomes, start = [], threading.Barrier(8)
    sources = []
    for i in range(8):
        f = world["tmp"] / f"w{i}.bin"
        f.write_bytes(f"writer-{i}".encode() * 1000)
        sources.append(f)

    def go(i):
        start.wait()
        try:
            d.put(sources[i], "daily/2026-10-14/same/vault.tar.gz.age")
            outcomes.append(("won", i))
        except B.BackupError as exc:
            outcomes.append((exc.code, i))
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    winners = [i for o, i in outcomes if o == "won"]
    assert len(winners) == 1 and sorted(o for o, _ in outcomes).count("object_exists") == 7
    assert (world["destdir"] / "daily/2026-10-14/same/vault.tar.gz.age").read_bytes() == sources[winners[0]].read_bytes()


def test_a_pointer_is_the_only_object_that_may_be_replaced(world):
    d = B.Dest(world["dest"])
    a, b = world["tmp"] / "a", world["tmp"] / "b"
    a.write_bytes(b"one"); b.write_bytes(b"two")
    d.put(a, "daily/2026-10-14/latest.json", replace=True)
    d.put(b, "daily/2026-10-14/latest.json", replace=True)
    assert (world["destdir"] / "daily/2026-10-14/latest.json").read_bytes() == b"two"
    with pytest.raises(B.BackupError):
        d.put(a, "daily/2026-10-14/latest.json")


def test_an_attempt_object_too_large_for_a_single_conditional_put_is_refused_not_downgraded(world, monkeypatch):
    d = B.Dest("s3://bucket/vault", aws_bin=world["aws"])
    big = world["tmp"] / "big.bin"
    big.write_bytes(b"x")
    monkeypatch.setattr(B, "MAX_CONDITIONAL_PUT", 0)
    with pytest.raises(B.BackupError) as e:
        d.put(big, "daily/2026-10-14/a/vault.tar.gz.age")
    assert e.value.code == "object_too_large_for_conditional_put" and not Path(os.environ["FAKE_AWS_LOG"]).exists()


def test_wiping_a_large_file_is_done_in_bounded_chunks(tmp_path, monkeypatch):
    f = tmp_path / "big.bin"
    f.write_bytes(b"S" * (3 * 1024 * 1024 + 123))
    monkeypatch.setattr(B, "WIPE_CHUNK", 64 * 1024)
    writes, real_open = [], open

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def write(self, data):
            writes.append(len(data))
            return self.fh.write(data)

        def __getattr__(self, name):
            return getattr(self.fh, name)

        def __enter__(self):
            self.fh.__enter__()
            return self

        def __exit__(self, *a):
            return self.fh.__exit__(*a)
    monkeypatch.setattr(B, "open", lambda p, mode="r", *a, **k: Spy(real_open(p, mode, *a, **k)) if "+" in mode else real_open(p, mode, *a, **k), raising=False)
    seen = {}
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (seen.setdefault("bytes", real_open(self, "rb").read()), real_unlink(self, *a, **k))[1])
    assert B.wipe_file(f) is True and not f.exists()
    assert max(writes) <= 64 * 1024 and sum(writes) == 3 * 1024 * 1024 + 123 and len(writes) > 40        # many small writes, never one big allocation
    assert set(seen["bytes"]) == {0} and len(seen["bytes"]) == 3 * 1024 * 1024 + 123                      # every byte zeroed before the file was removed


# ── free space: the standing reserve guards every scratch write ──
def free(n):
    return lambda path: type("U", (), {"free": n, "total": 10 * n, "used": 0})()


def test_backup_refuses_before_writing_anything_when_the_reserve_would_not_remain(world):
    need = B.backup_need(B.footprint(str(world["shelf"])))
    for ok_free in (need + B.reserve_bytes() - 1,):
        with pytest.raises(B.BackupError) as e:
            backup(world, usage=free(ok_free))
        assert e.value.code == "insufficient_space" and not world["destdir"].exists() and not list(world["work"].iterdir())
    backup(world, usage=free(need + B.reserve_bytes()))                                                  # exactly enough: allowed
    assert (world["destdir"] / "daily").exists()
    with pytest.raises(B.BackupError) as e:                                                              # a destination disk that would drop under the reserve
        backup(world, usage=lambda p: type("U", (), {"free": (need + B.reserve_bytes()) if str(p).startswith(str(world["work"])) else 1})())
    assert e.value.code == "insufficient_space"


def test_restore_refuses_before_downloading_or_decrypting_when_the_reserve_would_not_remain(world):
    backup(world, dest="s3://bucket/vault", aws_bin=world["aws"])
    calls_before = Path(os.environ["FAKE_AWS_LOG"]).read_text().splitlines()
    m = json.loads((world["tmp"] / "s3" / "bucket" / "vault" / "daily" / "2026-10-14" / json.loads((world["tmp"] / "s3" / "bucket" / "vault" / "daily" / "2026-10-14" / "latest.json").read_text())["attempt"] / "manifest.json").read_text())
    need = B.restore_need(m)
    with pytest.raises(B.BackupError) as e:
        drill(world, dest="s3://bucket/vault", aws_bin=world["aws"], usage=free(need + B.reserve_bytes() - 1))
    assert e.value.code == "insufficient_space"
    pulled = [c for c in Path(os.environ["FAKE_AWS_LOG"]).read_text().splitlines()[len(calls_before):] if "vault.tar.gz.age" in c]
    assert not pulled and not list(world["work"].iterdir()) and not (world["tmp"] / "age.modes").exists()    # the ciphertext was never downloaded, nothing decrypted
    assert drill(world, dest="s3://bucket/vault", aws_bin=world["aws"], usage=free(need + B.reserve_bytes()))["ok"] is True


def test_the_reserve_is_five_gib_and_a_bad_setting_never_lowers_it(monkeypatch):
    monkeypatch.delenv("MOI_RESERVE_GIB", raising=False)
    assert B.reserve_bytes() == 5 * (1 << 30)
    for bad in ("", "abc", "-3", "nan-ish"):
        monkeypatch.setenv("MOI_RESERVE_GIB", bad)
        assert B.reserve_bytes() == 5 * (1 << 30)
    monkeypatch.setenv("MOI_RESERVE_GIB", "8")
    assert B.reserve_bytes() == 8 * (1 << 30)


# ── the captured copy is what is verified and counted ──
def test_the_manifest_comes_from_the_captured_archive_not_the_live_shelf(world, monkeypatch):
    real = B.snapshot_tar

    def capture_then_the_shelf_changes(root, out):
        counts = real(root, out)
        for victim in (world["shelf"] / "sessions-raw").rglob("*.jsonl"):                                # the live shelf is damaged AFTER the copy was taken
            victim.write_bytes(victim.read_bytes() + b"\ncorrupt-after-capture")
        return counts
    monkeypatch.setattr(B, "snapshot_tar", capture_then_the_shelf_changes)
    m = backup(world)
    assert m["state"] == "ok" and m["shelf"]["verify_ok"] is True and m["shelf"]["records"] == 4        # the captured copy was fine, and it is what was checked and counted
    assert drill(world)["verify"]["ok"] is True


def test_a_damaged_capture_says_warn_even_if_the_live_shelf_is_repaired_afterwards(world, monkeypatch):
    victims = list((world["shelf"] / "sessions-raw").rglob("*.jsonl"))
    originals = {v: v.read_bytes() for v in victims}
    for v in victims:
        v.write_bytes(originals[v] + b"\ncorrupt")
    real = B.snapshot_tar

    def capture_then_repair(root, out):
        counts = real(root, out)
        for v in victims:
            v.write_bytes(originals[v])
        return counts
    monkeypatch.setattr(B, "snapshot_tar", capture_then_repair)
    m = backup(world)
    assert m["state"] == "warn" and m["shelf"]["verify_ok"] is False


# ── cleanup of sensitive scratch is checked, not ignored ──
def test_wipe_file_overwrites_removes_and_reports_failure(tmp_path, monkeypatch):
    f = tmp_path / "id.txt"
    f.write_text(IDENTITY)
    seen = {}
    real_unlink = Path.unlink

    def spy(self, *a, **k):
        seen["content_before_unlink"] = self.read_bytes()
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", spy)
    assert B.wipe_file(f) is True and not f.exists() and set(seen["content_before_unlink"]) == {0}      # zeros, not the key
    monkeypatch.undo()
    g = tmp_path / "stuck.txt"
    g.write_text("secret")
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("nope")))
    assert B.wipe_file(g) is False and g.exists()


def test_a_leftover_plaintext_archive_or_scratch_fails_the_run_loudly(world, monkeypatch):
    monkeypatch.setattr(B, "remove_tree", lambda path: False)
    with pytest.raises(B.BackupError) as e:
        backup(world)
    assert e.value.code == "cleanup_failed"
    monkeypatch.undo()
    monkeypatch.setattr(B, "wipe_file", lambda path: False)
    with pytest.raises(B.BackupError) as e:
        backup(world)
    assert e.value.code == "cleanup_failed"


def test_a_key_file_that_cannot_be_removed_fails_the_drill_even_if_decryption_worked(world, monkeypatch):
    backup(world)
    real = B.wipe_file
    monkeypatch.setattr(B, "wipe_file", lambda path: (real(path), False)[1] if Path(path).name.startswith(".id-") else real(path))
    with pytest.raises(B.BackupError) as e:
        drill(world)
    assert e.value.code == "key_cleanup_failed"


def test_an_encrypt_failure_uploads_nothing_and_leaves_no_plaintext(world):
    os.environ["FAKE_AGE_FAIL_ENCRYPT"] = "1"
    try:
        with pytest.raises(B.BackupError) as e:
            backup(world)
    finally:
        del os.environ["FAKE_AGE_FAIL_ENCRYPT"]
    assert e.value.code == "encrypt_failed" and not world["destdir"].exists() and not list(world["work"].iterdir())


def test_the_archive_skips_temp_and_lock_files_and_never_follows_symlinks(world):
    (world["shelf"] / "_ledger" / ".ledger.tmp-123").write_text("x")
    (world["shelf"] / "_ledger" / "shelf.lock").write_text("x")
    outside = world["tmp"] / "outside-secret.txt"
    outside.write_text("do not archive me")
    os.symlink(outside, world["shelf"] / "_ledger" / "link.txt")
    out = world["tmp"] / "a.tar.gz"
    counts = B.snapshot_tar(str(world["shelf"]), out)
    names = tarfile.open(out).getnames()
    assert not any("tmp-" in n or n.endswith(".lock") or n.endswith("link.txt") for n in names) and counts["skipped"] >= 3
    assert b"do not archive me" not in b"".join(tarfile.open(out).extractfile(m).read() for m in tarfile.open(out) if m.isfile())


def test_a_damaged_shelf_is_still_backed_up_but_the_run_says_warn(world):
    victim = next((world["shelf"] / "sessions-raw").rglob("*.jsonl"))
    victim.write_bytes(victim.read_bytes() + b"\ncorrupt")
    m = backup(world)
    assert m["state"] == "warn" and m["shelf"]["verify_ok"] is False


def test_identity_adapters_are_configurable_and_never_leak_the_key(world):
    assert B.fetch_identity(world["identity"]).strip() == IDENTITY.strip()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"stdout": IDENTITY})()
    assert B.fetch_identity("keychain:minimoi-vault/age_identity", run=fake_run).strip() == IDENTITY.strip()
    assert calls[-1][:2] == ["security", "find-generic-password"] and "-w" in calls[-1]
    assert B.fetch_identity("ssm:/minimoi/production/vault_backup_age_identity", run=fake_run).strip() == IDENTITY.strip()
    assert calls[-1][:3] == ["aws", "ssm", "get-parameter"] and "--with-decryption" in calls[-1]
    for bad in ("nope:x", "file:/definitely/not/here"):
        with pytest.raises(B.BackupError):
            B.fetch_identity(bad)
    junk = world["tmp"] / "junk.txt"
    junk.write_text("not a key")
    with pytest.raises(B.BackupError) as e:
        B.fetch_identity(f"file:{junk}")
    assert e.value.code == "identity_malformed" and "not a key" not in str(e.value)


# ── the job wrapper ──
@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("vault_backup_cli", REPO / "scripts" / "vault" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(cli, world, *extra, monkeypatch=None):
    return cli.main(["--shelf", str(world["shelf"]), "--dest", world["dest"], "--recipient", RECIPIENT, "--age-bin", world["age"],
                     "--jobs-root", str(world["tmp"] / "jobs"), "--registry", str(world["tmp"] / "no-registry.json"), *extra])


def test_the_job_is_plan_only_until_told_to_run_and_the_plan_touches_nothing(cli, world, capsys):
    assert run_cli(cli, world, "plan") == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["recipient_valid"] is True and plan["recipient_fingerprint"] == B.fingerprint(RECIPIENT) and RECIPIENT not in json.dumps(plan)
    assert plan["age_version"] == "v1.2.1" and plan["window"] == "00:00-06:00 America/Chicago"
    assert run_cli(cli, world, "run") == 2                                                          # no --execute
    assert not world["destdir"].exists() and not (world["tmp"] / "jobs").exists()


def test_a_run_writes_the_job_status_file_and_a_failure_alerts_without_leaking(cli, world, monkeypatch):
    alerts = []
    monkeypatch.setattr(cli.alert, "send", lambda msg: (alerts.append(msg), "sent")[1])
    assert run_cli(cli, world, "run", "--execute", "--allow-outside-window") == 0
    doc = json.loads((world["tmp"] / "jobs" / "vault-backup.json").read_text())
    assert doc["state"] == "ok" and doc["job_id"] == "vault-backup" and "4 records" in doc["summary"] and not alerts
    monkeypatch.setenv("FAKE_AGE_FAIL_ENCRYPT", "1")
    assert run_cli(cli, world, "run", "--execute", "--allow-outside-window") == 1
    doc = json.loads((world["tmp"] / "jobs" / "vault-backup.json").read_text())
    assert doc["state"] == "failed" and doc["results"]["backup"] == "encrypt_failed" and len(alerts) == 1
    assert RECIPIENT not in alerts[0] and str(world["shelf"]) not in alerts[0]


def test_outside_the_window_the_job_fails_closed_and_says_why(cli, world, monkeypatch):
    monkeypatch.setattr(cli.alert, "send", lambda msg: "sent")
    monkeypatch.setattr(B, "in_window", lambda now: False)
    assert run_cli(cli, world, "run", "--execute") == 1
    assert json.loads((world["tmp"] / "jobs" / "vault-backup.json").read_text())["results"]["backup"] == "outside_window" and not world["destdir"].exists()


def test_the_cli_drill_prints_evidence(cli, world, capsys, monkeypatch):
    monkeypatch.setattr(cli.alert, "send", lambda msg: "sent")
    day = datetime.now(timezone.utc).astimezone(B.CHICAGO).strftime("%Y-%m-%d")
    assert run_cli(cli, world, "run", "--execute", "--allow-outside-window") == 0
    capsys.readouterr()
    assert cli.main(["--dest", world["dest"], "--age-bin", world["age"], "drill", "--day", day, "--identity", world["identity"], "--workdir", str(world["work"])]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


# ── the estimates include per-file overhead, not just logical bytes ──
def test_many_tiny_files_cost_whole_blocks_and_tar_headers_not_just_their_bytes(tmp_path):
    many = tmp_path / "many"
    many.mkdir()
    for i in range(3000):
        (many / f"f{i}.txt").write_text("x")                                                             # 3000 files of 1 byte: 3000 logical bytes
    fp = B.footprint(str(many))
    assert fp["bytes"] == 3000 and fp["files"] == 3000 and fp["alloc"] >= 3000 * B.BLOCK                  # but at least a block each on disk
    assert B.tar_estimate(fp["bytes"], fp["files"]) >= 3000 * B.TAR_PER_FILE                              # and a header-and-padding allowance each in the archive
    logical_only = 3 * fp["bytes"] + (16 << 20)                                                          # what the old estimate would have asked for
    assert B.backup_need(fp) > logical_only + 3000 * B.BLOCK                                              # the new one is larger by at least the per-file overhead
    assert B.backup_need(fp) >= 2 * 3000 * B.TAR_PER_FILE + fp["alloc"]


def test_a_free_space_that_only_covers_the_logical_bytes_is_now_refused_for_backup_and_restore(world):
    many = world["tmp"] / "manyshelf"
    shutil.copytree(SNAPSHOT, many)
    for i in range(2000):
        (many / "_ledger" / f"tiny{i}.jsonl").write_text("x\n")
    fp = B.footprint(str(many))
    old_style = 3 * fp["bytes"] + (16 << 20)
    assert B.backup_need(fp) - old_style >= 2000 * B.BLOCK
    with pytest.raises(B.BackupError) as e:
        backup(world, shelf_root=str(many), usage=free(old_style + B.reserve_bytes()))
    assert e.value.code == "insufficient_space" and not world["destdir"].exists()
    m = backup(world, shelf_root=str(many), usage=free(B.backup_need(fp) + B.reserve_bytes()))
    manifest = json.loads((latest(world) / "manifest.json").read_text())
    assert manifest["shelf"]["files"] >= 2000 and B.restore_need(manifest) >= manifest["shelf"]["bytes"] + manifest["shelf"]["files"] * B.BLOCK
    legacy = manifest["ciphertext"]["bytes"] + manifest["archive"]["plaintext_bytes"] + manifest["shelf"]["bytes"] + (16 << 20)
    with pytest.raises(B.BackupError) as e:
        drill(world, usage=free(legacy + B.reserve_bytes()))
    assert e.value.code == "insufficient_space"
    assert drill(world, usage=free(B.restore_need(manifest) + B.reserve_bytes()))["ok"] is True


def test_s3_peak_counts_sequential_phases_and_age_overhead():
    # Incompressible data is bounded by the tar allowance; age adds chunk tags and a header.
    fp = {"bytes": 16 * B.GIB, "files": 1, "alloc": 16 * B.GIB + B.BLOCK}
    archive = B.tar_estimate(fp["bytes"], fp["files"])
    cipher = B.ciphertext_bound(archive)
    assert cipher >= archive + -(-archive // 65536) * 16 + 65536
    assert cipher % B.BLOCK == 0
    assert B.backup_need(fp, s3=True) == max(archive + fp["alloc"], archive + cipher) + (16 << 20)
    assert B.backup_need(fp, s3=True) < B.backup_need(fp)
    # Many tiny files can make the extracted allocation dominate both compressed files.
    tiny = {"bytes": 3000, "files": 3000, "alloc": 3000 * B.BLOCK + 1000 * B.BLOCK}
    assert B.backup_need(tiny, s3=True) >= B.tar_estimate(tiny["bytes"], tiny["files"]) + tiny["alloc"]


def test_failed_check_cleanup_never_encrypts_or_uploads(world, monkeypatch):
    real_remove = B.remove_tree
    calls = []
    monkeypatch.setattr(B, "remove_tree", lambda p: False if Path(p).name == "captured" else real_remove(p))
    monkeypatch.setattr(B, "encrypt", lambda *a, **kw: calls.append("encrypt"))
    monkeypatch.setattr(B.Dest, "put", lambda *a, **kw: calls.append("upload"))
    with pytest.raises(B.BackupError) as exc:
        backup(world, dest="s3://bucket/vault", aws_bin=world["aws"])
    assert exc.value.code == "cleanup_failed"
    assert not calls and not list(world["work"].iterdir())


def test_s3_archive_growth_is_refused_before_extract(world, monkeypatch):
    import io
    import tarfile
    original = B.snapshot_tar
    before = B.footprint(str(world["shelf"]))
    def capture_growth(root, output):
        counts = original(root, output)
        # A file added after discovery: archive header is the authority for extraction headroom.
        with tarfile.open(output, "w:gz") as archive:
            member = tarfile.TarInfo("new/large.bin")
            member.size = 32 << 20
            archive.addfile(member, io.BytesIO(b"x" * member.size))
        return counts
    monkeypatch.setattr(B, "snapshot_tar", capture_growth)
    monkeypatch.setattr(B, "_safe_extract", lambda *a: pytest.fail("extraction must not begin"))
    with pytest.raises(B.BackupError) as exc:
        backup(world, dest="s3://bucket/vault", aws_bin=world["aws"],
               usage=free(B.backup_need(before, s3=True) + B.reserve_bytes()))
    assert exc.value.code == "insufficient_space" and not list(world["work"].iterdir())


def test_captured_extract_estimate_includes_parent_directories(tmp_path):
    import io
    import tarfile
    archive = tmp_path / "small.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("a/b/one", "a/c/two"):
            member = tarfile.TarInfo(name)
            member.size = 1
            tar.addfile(member, io.BytesIO(b"x"))
    # Two file blocks plus root/a/a-b/a-c directory blocks and metadata slack.
    assert B.captured_extract_need(archive) == 6 * B.BLOCK + (16 << 20)


@pytest.mark.parametrize("orphan", [False, True])
def test_captured_native_assets_have_verified_counts_and_orphan_warning(world, orphan):
    from core.vault_t1 import assets
    source = world["tmp"] / "native.txt"
    source.write_bytes(b"synthetic native original")
    world["shelf"].chmod(0o700)
    assets.capture(world["shelf"], source, source_instance="test", source_id="native-one")
    if orphan:
        blob = b"verified unreferenced native bytes"
        digest = hashlib.sha256(blob).hexdigest()
        (world["shelf"] / assets.object_path(digest)).write_bytes(blob)
    result = backup(world)
    stored = json.loads((latest(world) / "manifest.json").read_text())
    assert stored["shelf"]["records"] == 4
    assert stored["shelf"]["native_assets"] == {
        "accepted": 1, "editions": 1, "referenced_objects": 1,
        "unreferenced_objects": int(orphan),
    }
    assert result["state"] == ("warn" if orphan else "ok")
    assert "native-one" not in json.dumps(stored)
    assert not list(world["work"].iterdir())
