"""The Vault's production transport: one consistent (shelf, index) snapshot, published encrypted from the Mac and installed on the EC2 host.

Why it exists. Production serves keyword (FTS5) search from a copy of the Mac's index and reads records from a copy of the shelf. It cannot build an
index itself (no embedder there), so the pair must travel together and must agree: every record the index points at must be in the shelf it ships with.

Mac side, ``publish`` (``scripts/vault/publish_production.py``).
  * Index FIRST, under the indexer's own lock, copied with SQLite's online backup API into a private scratch file (never a byte copy of a live database).
    Then the shelf, as the same deterministic tar the backup makes, unpacked once into scratch and checked with the portable reader. Because the indexer only
    reads the shelf and records are only ever added or given new editions, a shelf captured AFTER the index contains every record the index knows; the pair
    check proves it (``index_records_missing_from_shelf`` must be 0) and records the honest lag (``shelf_records_not_in_index``, ``index_records_stale``).
  * Both plaintexts are age-encrypted to the existing PUBLIC recipient (the job cannot read them back) and wiped. Objects go to
    ``<dest>/production/<YYYY-MM-DD>/<attempt>/``: ``shelf.tar.gz.age``, ``index.sqlite.age``, ``manifest.json``, then ``COMPLETE.json`` LAST, and only then the
    pointer ``<dest>/production/latest.json`` is replaced. Every attempt object is a conditional create (``If-None-Match: *`` on S3, an exclusive link locally).
    A failure anywhere leaves an orphan attempt with no COMPLETE and the pointer where it was; re-running makes a NEW attempt. Nothing is ever overwritten
    except the pointer.
  * The manifest holds counts, sizes, hashes (ciphertext AND plaintext), the index build stamp (versions, embedder identity, built/updated times), the shelf
    record count by source provider, the source-key set digests (per provider and overall, from ``_ledger/source_index.json``), created time and
    source host. Never a record name, title, path or content.

EC2 side, ``refresh`` (``scripts/vault/production_refresh.py``), run as root on the host (not in a container).
  * Reads the pointer, follows pointer -> COMPLETE -> manifest -> ciphertext hashes, refuses anything that does not chain, and refuses a snapshot that is
    not newer than the installed one (or that was rolled back). Unchanged is a no-op only once the serving release proves itself again: its
    ``RELEASE.json`` must name the manifest the pointer chains to, the receipt must agree, and the same full ``verify_release`` an install runs must
    pass (with the configured probe). That costs one read of the shelf and the index plus the probe each time (linear in bytes: ~30 ms for the whole
    no-op on the synthetic test shelf, so seconds for a real one, plus a container start with ``--fts-probe docker:``), affordable for a daily or hourly no-op,
    and it is what catches a release a power loss left half on disk. A release that fails is rebuilt from the chain and replaced only after the
    rebuild verifies and is durable (the bad one serves until then); if the rebuild fails too, ``current_release_corrupt`` (an alerting failure).
  * The restored shelf's ``_ledger/source_index.json`` key-set digests must equal the manifest's (per provider and overall) or the install is refused.
  * Downloads into ``<vault>-work/.work-*`` (0700, OUTSIDE the tree containers mount, on the same filesystem as the releases, so the final move is a rename), decrypts with the root-only identity,
    extracts with its own strict extractor (regular files and folders only) straight into the new release with root ownership, the reader's group, files
    0640 and folders 0750, then verifies: record counts (total, all, by source) equal the manifest, the shelf passes the portable check, the index opens
    read-only, passes ``quick_check``, matches its build stamp and chunk count, an FTS query executes (on the host, or inside the consumer image), and the
    pair check holds again. Then it is made durable: every extracted file was fsynced as written, the decrypted index is fsynced, every folder of
    the build is fsynced bottom-up (the build folder last); only then is the release renamed to ``releases/<attempt>``, ``releases/`` fsynced, and
    ``current`` switched by an atomic symlink rename (relative target, so a container that mounts the whole vault folder follows it), the root fsynced.
    Every state file is written whole (a loop over short writes), fsynced, renamed, its folder fsynced. If ``installed.json`` cannot be written
    after the switch, ``current`` is moved back (``installed_write_failed``) or the exact state left is named (``installed_write_failed_after_switch``,
    ``installed_not_durable``); the next run resumes from the verified release. The previous ``keep`` releases stay for rollback; older ones are removed; the current
    release is never removed. ``installed.json`` and a job status file record the result. Scratch is wiped on success and failure; leftovers from a crash are
    wiped at the next start. Every step keeps the standing 5 GiB reserve (``ensure_space``).
  * ``rollback`` points ``current`` at the previous retained release (or a named one) and marks the rolled-back attempt so refresh will not reinstall it.

RUNBOOK (exact commands; nothing below has been run against real S3 or EC2).

  Mac, by hand (plan first, then execute; --allow-outside-window only when the owner starts it by hand):
    cd ~/Projects/personal-ai-agents
    venv/bin/python scripts/vault/publish_production.py plan
    VAULT_BACKUP_AGE_RECIPIENT="$(venv/bin/python -c 'import json,sys;print(json.load(open(sys.argv[1]))["public_recipient"])' <PIN>/config/runtime.json)" \\
        venv/bin/python scripts/vault/publish_production.py run --execute --allow-outside-window
    (<PIN> = the installed Vault runtime, e.g. the one ~/minimoi-runtime/operators/vault-external-backup-20261017/backup_external.py names; the same
     PUBLIC recipient the backup uses, so the EC2 identity that already opens the backups opens these)
    (defaults: --shelf ~/minimoi-staging/data/memory, --index ~/minimoi-staging/data/memory-index/memory.sqlite, --dest s3://minimoi-backups/vault,
     --age-bin ~/minimoi-staging/tools/age-v1.3.2/age --expect-age v1.3.2, --workdir <jobs-root>/.vault-publish-work; peak scratch is about
     max(2 x index, index + 2 x shelf tar + shelf) plus the 5 GiB reserve, so point --workdir at the external volume if the internal disk is tight)

  EC2, by the operator through SSM (as root; the bundle is the files listed under "EC2 bundle" below, root-owned, under /opt/minimoi/vault-tools/<sha>/):
    python3 -I -B /opt/minimoi/vault-tools/<sha>/scripts/vault/production_refresh.py plan --check-remote
    AWS_DEFAULT_REGION=us-east-1 python3 -I -B /opt/minimoi/vault-tools/<sha>/scripts/vault/production_refresh.py refresh --execute
    (defaults: --dest s3://minimoi-backups/vault, --vault-root /opt/minimoi/vault, --identity file:/opt/minimoi-vault-keys/identity.agekey,
     --age-bin /opt/minimoi-vault-tools/age-v1.3.2/age --expect-age v1.3.2, --reader-uid 0 --reader-gid 0, --keep 2, --jobs-root /opt/minimoi/jobs,
     --fts-probe host; use --fts-probe docker:<portal image id> to prove the query in the consumer's own SQLite as the reader uid, networkless)

  Rollback (EC2, root): point current at the previous retained release and hold the bad attempt:
    python3 -I -B /opt/minimoi/vault-tools/<sha>/scripts/vault/production_refresh.py rollback --execute [--to <attempt>]

  Consumer: mount /opt/minimoi/vault read-only (the whole folder, so the relative ``current`` link resolves inside the container) and read
  ``current/shelf`` and ``current/index/memory.sqlite`` (open ``mode=ro&immutable=1``). An open connection keeps reading its release until reopened; that
  release is retained for at least two further refreshes.

  Scheduled cadence (described, NOT installed):
    Mac: inside the 00:00-06:00 Chicago window after capture (00:15) and index (02:30) and alongside the 04:30 backup, e.g. 05:00 Chicago, via a launchd
      calendar job running ``runtime_install.py``-style ``run-job`` under the shared vault-batch lock (that runner holds the lock, so the job passes
      ``--no-batch-lock``) with MOI_BATCH_DEADLINE_EPOCH set to 06:00.
    EC2: shortly after, 06:15 America/Chicago, in root's crontab the same way scripts/setup_ec2_cron.sh installs entries (install_if_missing on a unique
      match string). The host clock is UTC, so the entry uses CRON_TZ (cronie) to stay on Chicago time; appended at the END of the crontab it applies
      only to the lines after it, so existing entries keep their timing:
        CRON_TZ=America/Chicago
        15 6 * * * AWS_DEFAULT_REGION=us-east-1 /usr/bin/python3 -I -B /opt/minimoi/vault-tools/current/scripts/vault/production_refresh.py refresh --execute >> /opt/minimoi/logs/vault_refresh.log 2>&1

  EC2 bundle (stdlib + PyYAML on the host python; no repo checkout exists there): core/__init__.py, core/jobs/{__init__,alert,batch_window,registry,
  schedule,status}.py, core/memory_backup/{__init__,backup,production}.py, core/vault_t1/{__init__,reader,portable,assets,asset_plan}.py,
  scripts/vault/production_refresh.py; for the incremental sync (``vault-apply``) also core/memory_sync/{__init__,versions,batch,apply}.py and
  scripts/vault/vault_apply.py (with ``--index-runner docker:<image>`` the index is written inside the image; ``--index-runner host`` also needs
  core/memory_index/ and core/memory_shelf/ and SQLite >= 3.43 on the host); for the production MC capture (``vault-mc-export``) also
  core/memory_sync/{mc_batch,mc_export}.py and scripts/vault/vault_mc_export.py.

  Once ``vault-apply`` has built a release (``RELEASE.json`` format ``vault-production-sync/1``), ``refresh`` refuses (``incremental_active``,
  a quiet warn) so an old daily refresh cannot replace the incrementally updated shelf with an older snapshot. A deliberate reseed (disaster
  recovery) is ``refresh --execute --reseed``: it skips the attempt-id comparison (attempt ids from the Mac and EC2 clocks are not comparable)
  and still requires a snapshot newer than the installed one.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError

FORMAT = "vault-production/1"
PREFIX = "production"
SHELF_OBJECT = "shelf.tar.gz.age"
INDEX_OBJECT = "index.sqlite.age"
INDEX_NAME = "memory.sqlite"
MANIFEST, COMPLETE, LATEST = B.MANIFEST, B.COMPLETE, B.LATEST
RELEASE_MARK = "RELEASE.json"
INSTALLED = "installed.json"
FULL_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
HOSTNAME = re.compile(r"[^A-Za-z0-9.-]")
META_KEYS = ("schema_version", "policy_version", "chunker_version", "embedder", "embedder_digest", "dims", "built_at", "updated_at")
FTS_PROBE_TERM = "minimoi"
SLACK = 16 << 20


# ── facts both sides compute the same way (counts and hashes only) ──
def provider_token(source) -> str:
    token = str(source or "").split(":", 1)[0].strip().lower()
    return token if TOKEN.match(token) else "other"


def source_key_facts(root) -> dict:
    """Per provider: how many source keys the shelf's own ``_ledger/source_index.json`` holds and a digest of exactly that set (sha256 of the sorted keys
    joined by newlines, keys verbatim ``<provider>:<id>``), plus one digest over every key. Proves two shelves hold the same record set without listing it."""
    path = Path(root) / "_ledger" / "source_index.json"
    if not path.exists():
        return {"present": False, "total": 0, "digest": hashlib.sha256(b"").hexdigest(), "by_provider": {}}
    if path.is_symlink() or not path.is_file():
        raise BackupError("source_index_unreadable")
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise BackupError("source_index_unreadable") from None
    if not isinstance(doc, dict) or not all(isinstance(k, str) for k in doc):
        raise BackupError("source_index_unreadable")
    keys = sorted(doc)
    groups = {}
    for key in keys:
        groups.setdefault(provider_token(key), []).append(key)
    digest = lambda ks: hashlib.sha256("\n".join(sorted(ks)).encode("utf-8")).hexdigest()
    return {"present": True, "total": len(keys), "digest": digest(keys),
            "by_provider": {p: {"count": len(ks), "digest": digest(ks)} for p, ks in sorted(groups.items())}}


def shelf_facts(root) -> dict:
    """Counts the manifest carries and the pair check needs. ``_ids`` (record id -> (relative record file, edition hash)) stays in memory and is never
    written anywhere."""
    from core.vault_t1 import portable
    from core.vault_t1.reader import Vault
    root = Path(root)
    vault = Vault(str(root), ())
    records = vault.coverage()["totals"]["records"]
    every = vault._all()
    by_source = Counter(provider_token(r.meta.get("source")) for r in every)
    ids = {r.id: (r.path.relative_to(root).as_posix(), str(r.meta.get("edition_hash") or "")) for r in every}
    verify = portable.verify_shelf(str(root))
    return {"records": int(records), "records_all": len(every), "records_by_source": dict(sorted(by_source.items())),
            "source_keys": source_key_facts(root), "verify_ok": bool(verify.get("ok")), "verify_problems": len(verify.get("problems") or []), "_ids": ids}


def _ro(path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).absolute().as_uri()}?mode=ro&immutable=1", uri=True, timeout=30)


def index_facts(path) -> dict:
    """The index's build stamp and counts, read from a private copy, without loading any extension. ``_records`` stays in memory."""
    con = _ro(path)
    try:
        check = con.execute("PRAGMA quick_check").fetchone()[0]
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        states = Counter()
        records = {}
        for rid, rel, edition_hash, state in con.execute("SELECT record_id, rel, edition_hash, state FROM records"):
            states[str(state)] += 1
            records[str(rid)] = (str(rel), str(edition_hash or ""), str(state))
        chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    except sqlite3.DatabaseError:
        raise BackupError("index_unreadable") from None
    finally:
        con.close()
    if check != "ok":
        raise BackupError("index_quick_check_failed")
    return {"build": {k: str(meta[k]) for k in META_KEYS if k in meta}, "quick_check": "ok", "records_total": sum(states.values()),
            "records_indexed": states.get("indexed", 0), "records_held": states.get("held", 0), "chunks": int(chunks), "_records": records}


FTS_PROBE_CODE = (
    "import json,sqlite3,sys\n"
    "from pathlib import Path\n"
    "c=sqlite3.connect(Path(sys.argv[1]).absolute().as_uri()+'?mode=ro&immutable=1',uri=True)\n"
    "n=c.execute('SELECT count(*) FROM fts WHERE fts MATCH ?',(sys.argv[2],)).fetchone()[0]\n"
    "print(json.dumps({'ok':True,'sqlite':sqlite3.sqlite_version,'rows':int(n)>=0}))\n")


def fts_probe(path) -> dict:
    """Execute one keyword query on the host's SQLite. A table made with options this SQLite does not know (``contentless_delete`` needs 3.43) fails here."""
    con = _ro(path)
    try:
        con.execute("SELECT count(*) FROM fts WHERE fts MATCH ?", (FTS_PROBE_TERM,)).fetchone()
    except sqlite3.DatabaseError:
        raise BackupError("index_fts_failed") from None
    finally:
        con.close()
    return {"where": "host", "sqlite": sqlite3.sqlite_version}


def docker_fts_probe(image: str, release: Path, uid: int, gid: int, *, run=subprocess.run, docker_bin: str = "docker") -> dict:
    """The same query inside the consumer's image, as the reader uid, networkless, read-only: proves both the SQLite and the permissions it will use."""
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$", image or ""):     # never "-...": docker would read an option
        raise BackupError("probe_image_malformed")
    argv = [docker_bin, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{uid}:{gid}", "-v", f"{release}:/probe:ro",
            "--entrypoint", "python3", image, "-I", "-B", "-c", FTS_PROBE_CODE, f"/probe/index/{INDEX_NAME}", FTS_PROBE_TERM]
    try:
        done = run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        raise BackupError("index_fts_failed") from None
    try:
        out = json.loads((done.stdout or "").strip().splitlines()[-1]) if done.returncode == 0 else {}
    except (ValueError, IndexError):
        out = {}
    if not out.get("ok"):
        raise BackupError("index_fts_failed")
    return {"where": "image", "sqlite": str(out.get("sqlite", ""))[:20]}


def pair_facts(shelf: dict, index: dict) -> dict:
    """Does this index belong to this shelf? Missing must be 0; lag and staleness are recorded, not refused."""
    ids, recs = shelf["_ids"], index["_records"]
    missing = stale = 0
    for rid, (rel, edition_hash, state) in recs.items():
        if state != "indexed":
            continue
        have = ids.get(rid)
        if have is None or have[0] != rel:
            missing += 1
        elif have[1] != edition_hash:
            stale += 1
    return {"order": "index_then_shelf", "index_records_missing_from_shelf": missing, "index_records_stale": stale,
            "shelf_records_not_in_index": sum(1 for rid in ids if rid not in recs)}


def public(facts: dict) -> dict:
    return {k: v for k, v in facts.items() if not k.startswith("_")}


def source_host() -> str:
    return (HOSTNAME.sub("", platform.node().split(".")[0]) or "unknown")[:40]


# ── Mac: publish ──
def copy_index(index_path: Path, out: Path, *, lock_wait: float = 120.0) -> None:
    """A transactionally consistent copy of a database that may be in use, under the indexer's own lock: SQLite's online backup API."""
    from core.memory_index.indexer import IndexBusy, index_lock
    if not index_path.is_file() or index_path.is_symlink():
        raise BackupError("index_missing")
    try:
        with index_lock(index_path, wait=lock_wait):
            src = _ro_live(index_path)
            dst = sqlite3.connect(str(out))
            try:
                src.backup(dst)
            except sqlite3.DatabaseError:
                raise BackupError("index_copy_failed") from None
            finally:
                dst.close()
                src.close()
    except IndexBusy:
        raise BackupError("index_busy") from None
    os.chmod(out, 0o600)


def _ro_live(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, timeout=60)


FINGERPRINT_SKIP = ("_ledger/ledger.jsonl", "_status/")         # append-only / per-run files: they change on every pass


def content_fingerprint(shelf_root, index_path) -> str:
    """What a publish would carry, cheaply: every shelf file's path, size and mtime (except the append-only ledger and
    run status, which change on every pass even when nothing was captured) plus the index file's size, mtime and
    build meta. Equal fingerprints mean the last published snapshot is still current, so a repeated run inside the
    window uploads nothing. Reads no record content."""
    root, h = Path(shelf_root), hashlib.sha256()
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel.startswith(FINGERPRINT_SKIP) or name.endswith(".lock"):
                continue
            st = os.lstat(path)
            h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    st = os.stat(index_path)
    con = _ro_live(Path(index_path))
    try:
        meta = sorted(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    h.update(f"index\0{st.st_size}\0{st.st_mtime_ns}\0{json.dumps(meta)}\n".encode())
    return h.hexdigest()


def publish_need(shelf_fp: dict, index_bytes: int) -> int:
    """Peak scratch: the index copy beside its ciphertext; then the index ciphertext plus the shelf backup's own sequential S3 peak."""
    index_cipher = B.ciphertext_bound(index_bytes)
    return max(B.ciphertext_bound(index_bytes) + index_cipher, index_cipher + B.backup_need(shelf_fp, s3=True)) + SLACK


def run_publish(*, shelf_root: str, index_path: str, dest: str, recipient: str, workdir: str, age_bin: str = "age", expect_age: str | None = None,
                aws_bin: str = "aws", now: datetime | None = None, enforce_window: bool = True, host: str | None = None, run=subprocess.run,
                usage=shutil.disk_usage, attempt: str | None = None, index_lock_wait: float = 120.0) -> dict:
    """Publish one consistent pair. Returns the manifest (plus ``state`` and ``key``). Raises BackupError with a fixed code."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if enforce_window and not B.in_window(now):
        raise BackupError("outside_window")
    B.batch_timeout()
    version = B.check_age(age_bin, expect_age)
    if not B.RECIPIENT.match(recipient or ""):
        raise BackupError("recipient_malformed")
    d = B.Dest(dest, aws_bin=aws_bin, run=run)
    attempt = attempt or B.new_attempt_id(now)
    if not B.ATTEMPT.match(attempt):
        raise BackupError("attempt_malformed")
    index_path = Path(index_path)
    if not index_path.is_file() or index_path.is_symlink():
        raise BackupError("index_missing")
    if not Path(shelf_root).is_dir():
        raise BackupError("shelf_missing")
    work = Path(workdir)
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    fp = B.footprint(shelf_root)
    index_bytes = index_path.stat().st_size
    B.ensure_space(work, publish_need(fp, index_bytes), usage=usage)
    if d.kind == "file":
        B.ensure_space(d.base, B.tar_estimate(fp["bytes"], fp["files"]) + B.ciphertext_bound(index_bytes) + SLACK, usage=usage)
    scratch = Path(tempfile.mkdtemp(prefix="vault-publish-", dir=work))
    try:
        os.chmod(scratch, 0o700)
        # 1. The index, first.
        index_plain, index_cipher = scratch / INDEX_NAME, scratch / INDEX_OBJECT
        B.ensure_space(work, B.ciphertext_bound(index_bytes) * 2 + SLACK, usage=usage)
        copy_index(index_path, index_plain, lock_wait=index_lock_wait)
        ifacts = index_facts(index_plain)
        index_plain_sha, index_plain_bytes = B.sha256_file(index_plain), index_plain.stat().st_size
        B.ensure_space(work, B.ciphertext_bound(index_plain_bytes) + SLACK, usage=usage)
        B.encrypt(age_bin, recipient, index_plain, index_cipher)
        if not B.wipe_file(index_plain):
            raise BackupError("cleanup_failed")
        # 2. The shelf, captured after the index, checked from the captured copy.
        shelf_plain, shelf_cipher = scratch / "shelf.tar.gz", scratch / SHELF_OBJECT
        counts = B.snapshot_tar(shelf_root, shelf_plain)
        shelf_plain_sha, shelf_plain_bytes = B.sha256_file(shelf_plain), shelf_plain.stat().st_size
        check = scratch / "captured"
        B.ensure_space(work, B.captured_extract_need(shelf_plain), usage=usage)
        check.mkdir(mode=0o700)
        B._safe_extract(shelf_plain, check)
        B.batch_timeout()
        sfacts = shelf_facts(check)
        pair = pair_facts(sfacts, ifacts)
        if not B.remove_tree(check):
            raise BackupError("cleanup_failed")
        if pair["index_records_missing_from_shelf"]:
            raise BackupError("index_shelf_mismatch")
        B.ensure_space(work, B.ciphertext_bound(shelf_plain_bytes) + SLACK, usage=usage)
        B.encrypt(age_bin, recipient, shelf_plain, shelf_cipher)
        if not B.wipe_file(shelf_plain):
            raise BackupError("cleanup_failed")
        # 3. Manifest, COMPLETE last, then the pointer.
        day = now.astimezone(B.CHICAGO).strftime("%Y-%m-%d")
        folder = f"{PREFIX}/{day}/{attempt}"
        manifest = {
            "format": FORMAT, "attempt": attempt, "day": day, "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "host": host or "mac", "source_host": source_host(),
            "shelf": {**public(sfacts), "files": counts["files"], "skipped": counts["skipped"], "bytes": counts["bytes"], "verified": "captured_copy"},
            "index": {"name": INDEX_NAME, "copied_with": "sqlite_online_backup", **public(ifacts)},
            "pair": pair,
            "objects": {
                "shelf": {"name": SHELF_OBJECT, "sha256": B.sha256_file(shelf_cipher), "bytes": shelf_cipher.stat().st_size,
                          "plaintext_sha256": shelf_plain_sha, "plaintext_bytes": shelf_plain_bytes},
                "index": {"name": INDEX_OBJECT, "sha256": B.sha256_file(index_cipher), "bytes": index_cipher.stat().st_size,
                          "plaintext_sha256": index_plain_sha, "plaintext_bytes": index_plain_bytes}},
            "age": {"version": version, "recipient_fingerprint": B.fingerprint(recipient)},
        }
        mpath = scratch / MANIFEST
        mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        complete = {"format": FORMAT, "attempt": attempt, "day": day, "created_utc": manifest["created_utc"], "manifest_sha256": B.sha256_file(mpath),
                    "objects": {k: {"sha256": v["sha256"], "bytes": v["bytes"]} for k, v in manifest["objects"].items()}}
        cpath = scratch / COMPLETE
        cpath.write_text(json.dumps(complete, indent=2, sort_keys=True) + "\n")
        pointer = {"format": FORMAT, "attempt": attempt, "day": day, "created_utc": manifest["created_utc"], "complete_sha256": B.sha256_file(cpath)}
        ppath = scratch / LATEST
        ppath.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
        d.put(shelf_cipher, f"{folder}/{SHELF_OBJECT}")
        d.put(index_cipher, f"{folder}/{INDEX_OBJECT}")
        d.put(mpath, f"{folder}/{MANIFEST}")
        d.put(cpath, f"{folder}/{COMPLETE}")                    # an attempt without COMPLETE is an orphan and is never installed
        d.put(ppath, f"{PREFIX}/{LATEST}", replace=True)        # only now does production point at it
        manifest["state"] = "ok" if sfacts["verify_ok"] else "warn"
        manifest["key"] = folder
        return manifest
    finally:
        if not B.remove_tree(scratch):
            raise BackupError("cleanup_failed")


# ── EC2: refresh ──
def _json(path: Path, code: str) -> dict:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        raise BackupError(code) from None
    if not isinstance(doc, dict):
        raise BackupError(code)
    return doc


_raw_write = os.write                   # the one low-level write; tests inject short and zero-progress writes here


def _fsync(fd: int, path) -> None:
    """The one fsync every durability step goes through. ``path`` only names what is being synced (tests record the order)."""
    os.fsync(fd)


def _write_all(fd: int, data: bytes) -> None:
    """``os.write`` may write less than asked: loop until every byte is written; a write that makes no progress is an error."""
    view = memoryview(data)
    while view:
        written = _raw_write(fd, view)
        if not written or written < 0:
            raise OSError("write made no progress")
        view = view[written:]


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        _fsync(fd, path)
    finally:
        os.close(fd)


def _sync_file(path: Path) -> None:
    """fsync a file some other program wrote (the decrypted index). Raises ``fsync_failed``."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            _fsync(fd, path)
        finally:
            os.close(fd)
    except OSError:
        raise BackupError("fsync_failed") from None


def _sync_tree(root: Path, *, files: bool) -> None:
    """Make a finished release durable before anything points at it: every folder bottom-up, ``root`` last (and every file first when ``files``).
    Links are never followed. Raises ``fsync_failed``."""
    try:
        for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
            if files:
                for name in sorted(filenames):
                    _sync_file(Path(dirpath) / name)
            _fsync_dir(Path(dirpath))
    except OSError:
        raise BackupError("fsync_failed") from None


def _write_private_json(path: Path, doc: dict, uid: int, gid: int, mode: int = 0o640) -> None:
    """Write-all, fsync, then rename into place and fsync the folder. Before the rename a failure leaves ``path`` exactly as it was
    (``state_write_failed``, the temp removed); after it, ``path`` holds the new document but may not be durable (``state_not_durable``)."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    created = False
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        created = True
        try:
            _write_all(fd, (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode())
            os.fchown(fd, uid, gid)
            os.fchmod(fd, mode)
            _fsync(fd, tmp)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError:
        if created:                                                 # only this call's temp; never one that already existed
            try:
                tmp.unlink()
            except OSError:
                pass
        raise BackupError("state_write_failed") from None
    try:
        _fsync_dir(path.parent)
    except OSError:
        raise BackupError("state_not_durable") from None


class Layout:
    """``<root>/current -> releases/<attempt>``, ``<root>/releases/<attempt>/{shelf,index/memory.sqlite,RELEASE.json}``, ``<root>/installed.json``."""

    def __init__(self, root, *, owner_uid: int = 0, reader_uid: int = 0, reader_gid: int = 0):
        self.root = Path(root)
        self.releases = self.root / "releases"
        self.current = self.root / "current"
        self.installed = self.root / INSTALLED
        self.lockfile = self.root / ".refresh.lock"
        # Decryption, the private identity file and the plaintext build live OUTSIDE the tree containers mount
        # (<root>-work, root-only, same filesystem so the finished build moves into releases/ by rename).
        self.work = self.root.parent / f"{self.root.name}-work"
        self.owner, self.reader_uid, self.gid = int(owner_uid), int(reader_uid), int(reader_gid)
        # Root-only parent when the reader is root; otherwise the reader's group may pass through (0710) but not list it.
        self.root_mode = 0o700 if (self.reader_uid == 0 and self.gid == 0) else 0o710

    def prepare(self) -> None:
        for path, mode in ((self.root, self.root_mode), (self.releases, 0o750)):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise BackupError("vault_root_unsafe")
            if not path.exists():
                path.mkdir(mode=0o700, parents=path is self.root)
            st = path.stat()
            if st.st_uid != self.owner:
                raise BackupError("vault_root_unsafe")
            os.chown(path, self.owner, self.gid)
            os.chmod(path, mode)
        if self.root.parent.stat().st_mode & 0o002:
            raise BackupError("vault_root_unsafe")
        if self.current.exists() and not self.current.is_symlink():
            raise BackupError("current_not_a_link")

    def current_attempt(self) -> str | None:
        if not self.current.is_symlink():
            return None
        target = os.readlink(self.current)
        parts = Path(target).parts
        if len(parts) != 2 or parts[0] != "releases" or not B.ATTEMPT.match(parts[1]):
            raise BackupError("current_link_unexpected")
        return parts[1]

    def release_ids(self) -> list[str]:
        if not self.releases.is_dir():
            return []
        return sorted(p.name for p in self.releases.iterdir() if B.ATTEMPT.match(p.name) and p.is_dir() and not p.is_symlink())

    def read_installed(self) -> dict:
        if not self.installed.exists():
            return {}
        return _json(self.installed, "installed_unreadable")

    def switch(self, attempt: str) -> None:
        """Atomic: a new relative symlink beside ``current``, renamed over it."""
        if not (self.releases / attempt).is_dir():
            raise BackupError("release_missing")
        tmp = self.root / f".current-{secrets.token_hex(6)}"
        os.symlink(f"releases/{attempt}", tmp)
        try:
            os.replace(tmp, self.current)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise BackupError("switch_failed") from None
        try:
            _fsync_dir(self.root)
        except OSError:
            raise BackupError("switch_not_durable") from None

    def unswitch(self, attempt: str | None) -> None:
        """Undo a switch whose receipt could not be written: back to ``attempt``, or no ``current`` at all when there was none."""
        if attempt is not None:
            self.switch(attempt)
            return
        try:
            self.current.unlink()
            _fsync_dir(self.root)
        except OSError:
            raise BackupError("switch_failed") from None

    def prune(self, keep: int) -> list[str]:
        """Keep the current release and the ``keep`` newest others; remove the rest. The current release is never removed."""
        current = self.current_attempt()
        others = [a for a in self.release_ids() if a != current]
        removed = []
        for attempt in others[: max(0, len(others) - keep)]:
            if attempt == current:                                  # belt and braces
                continue
            if not B.remove_tree(self.releases / attempt):
                raise BackupError("prune_failed")
            removed.append(attempt)
        if removed:
            _fsync_dir(self.releases)
        return removed

    def prepare_work(self) -> None:
        if self.work.is_symlink() or (self.work.exists() and not self.work.is_dir()):
            raise BackupError("work_unsafe")
        self.work.mkdir(mode=0o700, exist_ok=True)
        os.chown(self.work, self.owner, -1)                    # owner only; mode 0700 keeps everyone else out
        os.chmod(self.work, 0o700)
        if os.stat(self.work).st_dev != os.stat(self.root).st_dev:
            raise BackupError("work_not_same_filesystem")

    def wipe_leftovers(self) -> int:
        n = 0
        for p in [*self.root.iterdir(), *(self.work.iterdir() if self.work.is_dir() else [])]:
            if p.name.startswith(".work-") and p.is_dir() and not p.is_symlink():
                if not B.remove_tree(p):
                    raise BackupError("cleanup_failed")
                n += 1
            elif p.name.startswith(".current-") and p.is_symlink():
                p.unlink()
        return n


def _safe_member(name: str) -> Path:
    p = Path(name)
    if not name or p.is_absolute() or ".." in p.parts or "\0" in name:
        raise BackupError("archive_unsafe")
    return p


def extract_shelf(archive: Path, target: Path, uid: int, gid: int) -> int:
    """Our own strict extraction: regular files and folders only, nothing absolute or climbing, no duplicates, no links; files 0640 and folders 0750 owned
    ``uid:gid`` from the moment they exist; every file fsynced as written (the caller syncs the folders). Works the same on every Python 3.9+ (does not
    depend on tarfile's extraction filters)."""
    def mkdirs(path: Path) -> None:
        missing = []
        probe = path
        while not probe.exists():
            missing.append(probe)
            probe = probe.parent
        if probe.is_symlink() or not probe.is_dir():
            raise BackupError("archive_unsafe")
        for folder in reversed(missing):
            os.mkdir(folder, 0o700)
            os.chown(folder, uid, gid)
            os.chmod(folder, 0o750)

    n = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            B.batch_timeout()
            rel = _safe_member(member.name)
            dest = target / rel
            if member.isdir():
                mkdirs(dest)
                n += 1
                continue
            if not member.isfile():
                raise BackupError("archive_unsafe")
            mkdirs(dest.parent)
            try:
                fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                raise BackupError("archive_unsafe") from None
            src = tar.extractfile(member)
            try:
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                    out.flush()
                    os.fchown(out.fileno(), uid, gid)
                    os.fchmod(out.fileno(), 0o640)
                    try:
                        _fsync(out.fileno(), dest)                  # durable as written; folders are synced bottom-up once the tree is whole
                    except OSError:
                        raise BackupError("fsync_failed") from None
            finally:
                src.close()
            n += 1
    return n


def check_permissions(release: Path, *, owner_uid: int, reader_uid: int, gid: int) -> dict:
    """Everything owned by the owner, group the reader's group, no links, files no looser than 0640, folders no looser than 0750; and the reader can read."""
    files = dirs = 0
    for dirpath, dirnames, filenames in os.walk(release, followlinks=False):
        for name in [""] + dirnames + filenames:
            path = Path(dirpath) / name if name else Path(dirpath)
            st = os.lstat(path)
            if path.is_symlink() or st.st_uid != owner_uid or st.st_gid != gid:
                raise BackupError("release_permissions_wrong")
            mode = st.st_mode & 0o7777
            if path.is_dir():
                if name == "" and Path(dirpath) != release:
                    continue
                if mode & ~0o750 or (reader_uid != 0 and mode & 0o050 != 0o050):
                    raise BackupError("release_permissions_wrong")
                dirs += 1
            elif path.is_file():
                if mode & ~0o640 or (reader_uid != 0 and not mode & 0o040):
                    raise BackupError("release_permissions_wrong")
                files += 1
            else:
                raise BackupError("release_permissions_wrong")
    return {"files": files, "dirs": dirs}


def verify_release(release: Path, manifest: dict, *, owner_uid: int, reader_uid: int, gid: int, probe=None) -> dict:
    """Everything the manifest promises, checked on the installed bytes. Raises a fixed code on the first disagreement."""
    shelf, index = release / "shelf", release / "index" / INDEX_NAME
    if not shelf.is_dir() or not index.is_file():
        raise BackupError("release_incomplete")
    m_shelf, m_index = manifest["shelf"], manifest["index"]
    sfacts = shelf_facts(shelf)
    if (sfacts["records"], sfacts["records_all"], sfacts["records_by_source"]) != \
            (m_shelf["records"], m_shelf["records_all"], m_shelf["records_by_source"]):
        raise BackupError("record_count_mismatch")
    if sfacts["source_keys"] != m_shelf.get("source_keys"):
        raise BackupError("source_keys_mismatch")
    if m_shelf.get("verify_ok") and not sfacts["verify_ok"]:
        raise BackupError("shelf_verify_failed")
    ifacts = index_facts(index)
    if ifacts["build"] != m_index["build"]:
        raise BackupError("index_build_mismatch")
    if (ifacts["chunks"], ifacts["records_total"], ifacts["records_indexed"]) != (m_index["chunks"], m_index["records_total"], m_index["records_indexed"]):
        raise BackupError("index_count_mismatch")
    pair = pair_facts(sfacts, ifacts)
    if pair["index_records_missing_from_shelf"] or pair != manifest["pair"]:
        raise BackupError("index_shelf_mismatch")
    fts = probe(release) if probe else fts_probe(index)
    perms = check_permissions(release, owner_uid=owner_uid, reader_uid=reader_uid, gid=gid)
    return {"records": sfacts["records"], "records_all": sfacts["records_all"], "records_by_source": sfacts["records_by_source"],
            "source_keys": sfacts["source_keys"], "shelf_verify_ok": sfacts["verify_ok"], "index_chunks": ifacts["chunks"], "index_built_at": ifacts["build"].get("built_at"),
            "fts": fts, "permissions": perms}


def refresh_need(manifest: dict) -> int:
    o, s = manifest["objects"], manifest["shelf"]
    return (int(o["shelf"]["bytes"]) + int(o["index"]["bytes"]) + int(o["shelf"]["plaintext_bytes"]) + int(o["index"]["plaintext_bytes"])
            + int(s["bytes"]) + int(s["files"]) * B.BLOCK + SLACK)


def _fetch_chain(d: B.Dest, scratch: Path) -> tuple:
    """pointer -> COMPLETE -> manifest, each hash checked against the one before. Returns (pointer, complete, manifest, manifest_sha)."""
    ppath = scratch / LATEST
    d.get(f"{PREFIX}/{LATEST}", ppath)
    pointer = _json(ppath, "pointer_unreadable")
    attempt, day = pointer.get("attempt"), pointer.get("day")
    if pointer.get("format") != FORMAT or not isinstance(attempt, str) or not B.ATTEMPT.match(attempt) \
            or not isinstance(day, str) or not FULL_DAY.match(day):
        raise BackupError("pointer_unreadable")
    folder = f"{PREFIX}/{day}/{attempt}"
    cpath, mpath = scratch / COMPLETE, scratch / MANIFEST
    d.get(f"{folder}/{COMPLETE}", cpath)
    if B.sha256_file(cpath) != pointer.get("complete_sha256"):
        raise BackupError("complete_mismatch")
    complete = _json(cpath, "complete_unreadable")
    d.get(f"{folder}/{MANIFEST}", mpath)
    manifest_sha = B.sha256_file(mpath)
    if manifest_sha != complete.get("manifest_sha256"):
        raise BackupError("manifest_mismatch")
    manifest = _json(mpath, "manifest_unreadable")
    if manifest.get("format") != FORMAT or manifest.get("attempt") != attempt or complete.get("attempt") != attempt or manifest.get("day") != day:
        raise BackupError("manifest_format")
    try:
        for k in ("shelf", "index"):
            if complete["objects"][k] != {"sha256": manifest["objects"][k]["sha256"], "bytes": manifest["objects"][k]["bytes"]}:
                raise BackupError("manifest_mismatch")
        refresh_need(manifest)
    except (KeyError, TypeError, ValueError):
        raise BackupError("manifest_format") from None
    return pointer, complete, manifest, manifest_sha, folder


def _check_mark(release: Path, attempt: str, manifest_sha: str) -> dict:
    """The release's own marker, written only after it verified: it must name this attempt and exactly the manifest the pointer chains to."""
    mark = _json(release / RELEASE_MARK, "release_unreadable")
    if mark.get("attempt") not in (None, attempt) or mark.get("manifest_sha256") != manifest_sha:
        raise BackupError("release_conflict")
    return mark


def _receipt_failed(lay: Layout, exc: BackupError, *, back_to: str | None, serving_before: bool) -> None:
    """``current`` was switched but ``installed.json`` could not be written. Say exactly which state the host is left in.
      * the receipt was never replaced and this run moved ``current``: move it back, so both still describe the previous install
        (``installed_write_failed``; the verified release stays and the next run resumes from it);
      * otherwise ``current`` serves the new release and the receipt is old or not durable: ``installed_write_failed_after_switch`` /
        ``installed_not_durable`` (the next run sees ``current`` without a matching receipt and resumes, re-verifying first)."""
    if exc.code == "state_not_durable":
        raise BackupError("installed_not_durable") from None
    if serving_before:
        raise BackupError("installed_write_failed_after_switch") from None
    try:
        lay.unswitch(back_to)
    except BackupError:
        raise BackupError("installed_write_failed_after_switch") from None
    raise BackupError("installed_write_failed") from None


def _build_release(d: B.Dest, folder: str, manifest: dict, manifest_sha: str, attempt: str, scratch: Path, lay: Layout, release: Path, *,
                   identity_spec: str, age_bin: str, owner_uid: int, reader_gid: int, fetch, usage, verify, now: datetime) -> dict:
    """Download, check, decrypt, extract and verify into ``scratch/release``; make it durable (every file as written, the index, every folder
    bottom-up); only then rename it to ``release`` and fsync ``releases/``. A release already there (one that failed re-verification) is moved
    into scratch only after its replacement is verified and durable, so it is wiped with scratch. Returns the verification evidence."""
    B.ensure_space(lay.work, refresh_need(manifest), usage=usage)
    build = scratch / "release"
    os.mkdir(build, 0o700)
    shelf_cipher, index_cipher, shelf_plain = scratch / SHELF_OBJECT, scratch / INDEX_OBJECT, scratch / "shelf.tar.gz"
    for key, local in (("shelf", shelf_cipher), ("index", index_cipher)):
        d.get(f"{folder}/{manifest['objects'][key]['name']}", local)
        want = manifest["objects"][key]
        if B.sha256_file(local) != want["sha256"] or local.stat().st_size != want["bytes"]:
            raise BackupError("ciphertext_mismatch")
    identity = fetch(identity_spec)
    try:
        os.mkdir(build / "index", 0o700)
        index_plain = build / "index" / INDEX_NAME
        B.decrypt(age_bin, identity, index_cipher, index_plain, scratch)
        B.decrypt(age_bin, identity, shelf_cipher, shelf_plain, scratch)
    finally:
        identity = None
    if B.sha256_file(index_plain) != manifest["objects"]["index"]["plaintext_sha256"]:
        raise BackupError("plaintext_mismatch")
    if B.sha256_file(shelf_plain) != manifest["objects"]["shelf"]["plaintext_sha256"]:
        raise BackupError("plaintext_mismatch")
    for c in (shelf_cipher, index_cipher):
        c.unlink()
    os.mkdir(build / "shelf", 0o700)
    extract_shelf(shelf_plain, build / "shelf", owner_uid, reader_gid)
    if not B.wipe_file(shelf_plain):
        raise BackupError("cleanup_failed")
    os.chown(index_plain, owner_uid, reader_gid)
    os.chmod(index_plain, 0o640)
    _sync_file(index_plain)                                     # written by age, not by us: fsync it explicitly
    for folder_path in (build / "index", build / "shelf", build):
        os.chown(folder_path, owner_uid, reader_gid)
        os.chmod(folder_path, 0o750)
    evidence = verify(build)
    _write_private_json(build / RELEASE_MARK, {"format": FORMAT, "attempt": attempt, "manifest_sha256": manifest_sha,
                                               "created_utc": manifest["created_utc"], "verified_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                               "records": evidence["records"], "index_chunks": evidence["index_chunks"],
                                               "source_keys": evidence["source_keys"]},
                        owner_uid, reader_gid)
    _sync_tree(build, files=False)                              # every folder, bottom-up, the build folder last
    try:
        if os.path.lexists(release):
            os.rename(release, scratch / "replaced-release")
        os.rename(build, release)
    except OSError:
        raise BackupError("release_move_failed") from None
    try:
        _fsync_dir(lay.releases)
    except OSError:
        raise BackupError("fsync_failed") from None
    return evidence


class _Lock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise BackupError("refresh_busy") from None
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


class Admission:
    """One admission for a whole refresh or ``vault-apply`` run, on the vault's ``.refresh.lock``: taken BEFORE the job's status start and
    held to its status finish, so the status file always describes the run holding the lock and a competitor refused at the lock writes
    nothing at all, not even a status (Codex, 17 Oct). Raises ``refresh_busy``. The run functions take it as ``admission=`` and then do
    not lock again."""

    def __init__(self, vault_root, *, owner_uid: int = 0, reader_uid: int = 0, reader_gid: int = 0):
        self.lay = Layout(vault_root, owner_uid=owner_uid, reader_uid=reader_uid, reader_gid=reader_gid)
        self._lock = None

    def __enter__(self):
        old_umask = os.umask(0o077)
        try:
            self.lay.prepare()
            self.lay.prepare_work()
        finally:
            os.umask(old_umask)
        self._lock = _Lock(self.lay.lockfile).__enter__()
        return self

    def __exit__(self, *exc):
        if self._lock is not None:
            self._lock.__exit__(*exc)
            self._lock = None

    def covers(self, lay: Layout) -> bool:
        return self._lock is not None and Path(self.lay.lockfile).resolve() == Path(lay.lockfile).resolve()


def admitted(admission, lay: Layout):
    """The lock for one run: the caller's held admission (checked to be this vault's), or a fresh one."""
    if admission is None:
        return _Lock(lay.lockfile)
    if not admission.covers(lay):
        raise BackupError("admission_mismatch")
    return contextlib.nullcontext()


def run_refresh(*, dest: str, vault_root: str, identity_spec: str, age_bin: str = "age", expect_age: str | None = None, aws_bin: str = "aws",
                owner_uid: int = 0, reader_uid: int = 0, reader_gid: int = 0, keep: int = 2, require_root: bool = True, probe=None,
                run=subprocess.run, fetch=B.fetch_identity, usage=shutil.disk_usage, now: datetime | None = None, reseed: bool = False,
                admission: "Admission | None" = None) -> dict:
    """Install the latest COMPLETE production snapshot as a new release and switch to it. Returns a small evidence dict (counts and hashes only).
    When ``current`` is a ``vault-apply`` release, refuses ``incremental_active`` unless ``reseed``. ``admission``: the caller's held
    ``Admission`` (the CLI takes it before its status start); without one the run locks for itself."""
    if require_root and os.geteuid() != 0:
        raise BackupError("root_required")
    if keep < 1:
        raise BackupError("bad_arguments")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    old_umask = os.umask(0o077)
    try:
        version = B.check_age(age_bin, expect_age)
        d = B.Dest(dest, aws_bin=aws_bin, run=run)
        lay = Layout(vault_root, owner_uid=owner_uid, reader_uid=reader_uid, reader_gid=reader_gid)
        lay.prepare()
        lay.prepare_work()
        with admitted(admission, lay):
            lay.wipe_leftovers()
            B.ensure_space(lay.work, 4 << 20, usage=usage)
            scratch = Path(tempfile.mkdtemp(prefix=".work-", dir=lay.work))
            try:
                os.chmod(scratch, 0o700)
                pointer, complete, manifest, manifest_sha, folder = _fetch_chain(d, scratch)
                attempt = pointer["attempt"]
                installed = lay.read_installed()
                current = lay.current_attempt()
                try:                                                # a release vault-apply built (core/memory_sync/apply.py)?
                    current_format = json.loads((lay.releases / current / RELEASE_MARK).read_text()).get("format") if current else None
                except (OSError, ValueError, AttributeError):
                    current_format = None
                incremental = current_format == "vault-production-sync/1"
                if incremental and not reseed:
                    raise BackupError("incremental_active")            # vault-apply owns the serving shelf; a reseed is explicit
                if attempt in (installed.get("rolled_back") or []):
                    raise BackupError("attempt_rolled_back")
                release = lay.releases / attempt
                verify = lambda path: verify_release(path, manifest, owner_uid=owner_uid, reader_uid=reader_uid, gid=reader_gid, probe=probe)
                serving = current == attempt
                repair = None                                       # why the serving release failed re-verification, when it did
                if serving and installed.get("attempt") == attempt:
                    # Unchanged only if what serves still proves itself: a power loss soon after a switch can leave a release whose bytes never
                    # reached the disk. This is the install's own full verification (one read of the shelf and the index, plus the configured probe).
                    try:
                        _check_mark(release, attempt, manifest_sha)
                        if installed.get("manifest_sha256") != manifest_sha:
                            raise BackupError("installed_mismatch")
                        verify(release)
                        return {"state": "unchanged", "attempt": attempt, "verified": True}
                    except BackupError as exc:
                        repair = exc.code                           # rebuild from the chain; the bad release keeps serving until its replacement verifies
                if not serving:                                     # serving without a receipt: a crash after the switch; resume or repair below
                    if current is not None and not incremental and attempt <= current:     # a reseed compares created times only
                        raise BackupError("pointer_not_newer")
                    if installed.get("created_utc") and str(manifest.get("created_utc", "")) <= str(installed["created_utc"]):
                        raise BackupError("pointer_not_newer")
                resumed = False
                if repair is None and release.exists():
                    # Only ever created by the rename below, after full verification and fsync: resume from that confirmed progress.
                    try:
                        _check_mark(release, attempt, manifest_sha)
                        evidence = verify(release)
                        _sync_tree(release, files=True)             # durable before anything points at it (a release left by an older build)
                        resumed = True
                    except BackupError as exc:
                        if not serving:
                            raise
                        repair = exc.code
                if not resumed:
                    try:
                        evidence = _build_release(d, folder, manifest, manifest_sha, attempt, scratch, lay, release, identity_spec=identity_spec,
                                                  age_bin=age_bin, owner_uid=owner_uid, reader_gid=reader_gid, fetch=fetch, usage=usage,
                                                  verify=verify, now=now)
                    except BackupError as exc:
                        if repair is None:
                            raise
                        raise BackupError("current_release_corrupt", f"{repair}:{exc.code}") from None
                if not serving:
                    previous = current
                else:
                    previous = installed.get("previous") if installed.get("attempt") == attempt else installed.get("attempt")
                lay.switch(attempt)
                try:
                    _write_private_json(lay.installed, {
                        "format": FORMAT, "attempt": attempt, "created_utc": manifest["created_utc"], "installed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "previous": previous, "records": evidence["records"], "records_all": evidence["records_all"],
                        "records_by_source": evidence["records_by_source"], "source_keys": evidence["source_keys"], "index_chunks": evidence["index_chunks"],
                        "index_built_at": evidence["index_built_at"], "manifest_sha256": manifest_sha, "age_version": version,
                        "rolled_back": [a for a in (installed.get("rolled_back") or []) if B.ATTEMPT.match(str(a))]}, owner_uid, reader_gid)
                except BackupError as exc:
                    _receipt_failed(lay, exc, back_to=current, serving_before=serving)
                removed = lay.prune(keep)
                return {"state": "installed", "attempt": attempt, "previous": previous, "resumed": resumed, "pruned": len(removed),
                        "repaired": repair is not None, "repair_cause": repair,
                        "records": evidence["records"], "index_chunks": evidence["index_chunks"], "fts": evidence["fts"]["where"]}
            finally:
                if not B.remove_tree(scratch):
                    raise BackupError("cleanup_failed")
    finally:
        os.umask(old_umask)


def run_rollback(*, vault_root: str, to: str | None = None, owner_uid: int = 0, reader_uid: int = 0, reader_gid: int = 0, require_root: bool = True,
                 now: datetime | None = None) -> dict:
    """Point ``current`` at the previous retained release (or ``to``) and hold the attempt being left so refresh will not reinstall it."""
    if require_root and os.geteuid() != 0:
        raise BackupError("root_required")
    if to is not None and not B.ATTEMPT.match(to):
        raise BackupError("bad_arguments")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lay = Layout(vault_root, owner_uid=owner_uid, reader_uid=reader_uid, reader_gid=reader_gid)
    if not lay.root.is_dir():
        raise BackupError("nothing_installed")
    with _Lock(lay.lockfile):
        current = lay.current_attempt()
        if current is None:
            raise BackupError("nothing_installed")
        older = [a for a in lay.release_ids() if a < current]
        target = to or (older[-1] if older else None)
        if target is None or target == current or target not in lay.release_ids():
            raise BackupError("no_release_to_roll_back_to")
        mark = _json(lay.releases / target / RELEASE_MARK, "release_unreadable")
        if mark.get("attempt") != target:
            raise BackupError("release_unreadable")
        installed = lay.read_installed()
        lay.switch(target)
        held = [a for a in (installed.get("rolled_back") or []) if B.ATTEMPT.match(str(a))]
        if current not in held:
            held.append(current)
        try:
            _write_private_json(lay.installed, {
                "format": FORMAT, "attempt": target, "created_utc": mark.get("created_utc"), "installed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "previous": current, "records": mark.get("records"), "source_keys": mark.get("source_keys"), "index_chunks": mark.get("index_chunks"), "manifest_sha256": mark.get("manifest_sha256"),
                "rolled_back": held, "via": "rollback"}, owner_uid, reader_gid)
        except BackupError as exc:
            _receipt_failed(lay, exc, back_to=current, serving_before=False)
        return {"state": "rolled_back", "attempt": target, "previous": current}


def plan_refresh(*, vault_root: str, age_bin: str, identity_spec: str, usage=shutil.disk_usage) -> dict:
    """Read-only: what is installed, what is retained, free space, and whether this host can run the refresh. Never reads the key's content."""
    lay = Layout(vault_root)
    out = {"vault_root_exists": lay.root.is_dir(), "running_as_root": os.geteuid() == 0, "python": platform.python_version(),
           "sqlite": sqlite3.sqlite_version, "sqlite_supports_contentless_delete": tuple(int(x) for x in sqlite3.sqlite_version.split(".")[:2]) >= (3, 43)}
    try:
        import yaml  # noqa: F401
        out["pyyaml"] = True
    except ImportError:
        out["pyyaml"] = False
    try:
        out["age_version"] = B.age_version(age_bin)
    except BackupError as exc:
        out["age_version"] = f"unavailable ({exc.code})"
    kind, _, rest = identity_spec.partition(":")
    if kind == "file":
        p = Path(rest)
        try:
            st = p.lstat()
            out["identity_file"] = {"present": True, "owner_uid": st.st_uid, "mode": oct(st.st_mode & 0o777), "symlink": p.is_symlink()}
        except OSError:
            out["identity_file"] = {"present": False}
    if lay.root.is_dir():
        try:
            out["current"] = lay.current_attempt()
            out["releases"] = lay.release_ids()
            inst = lay.read_installed()
            out["installed"] = {k: inst.get(k) for k in ("attempt", "created_utc", "installed_utc", "records", "index_chunks", "rolled_back")}
        except (BackupError, OSError) as exc:
            out["state_error"] = getattr(exc, "code", "unreadable")
    probe = lay.root if lay.root.exists() else lay.root.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    out["free_bytes"] = usage(probe).free
    out["reserve_bytes"] = B.reserve_bytes()
    return out
