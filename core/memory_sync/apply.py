"""``vault-apply``: the EC2 side of the Vault's incremental sync (17 Oct 2026). Installs the record versions the Mac's ``vault-sync`` sends,
then acknowledges them and publishes production's inventory. Runs as root on the host (cron, every 15 min; a no-op when nothing is new).

Why copy-on-write releases. r3 serves ``<vault>/current -> releases/<attempt>`` and promises readers (``vault-ask`` opens the index with
``immutable=1``) that a release never changes under them. So apply never edits a live release: each run that installs anything builds a NEW
release from the serving one and switches ``current`` atomically, exactly like a snapshot refresh.
  * In ``<vault>-work/.work-*`` (r3's scratch, root-only, same filesystem): every shelf file of ``current/shelf`` is HARD-LINKED into
    ``build/shelf`` (folders are new, 0750 owner:gid), so an unchanged record costs one link. The serving keyword-only index is COPIED (never
    linked: SQLite writes it). A hard-linked file is never opened for writing, chowned, chmodded or wiped (that would change the live release's
    bytes or metadata through the shared inode): a changed file is a NEW file renamed over the link, and trees are removed with ``remove_tree``
    (unlink only).
  * Each applied version is written from the verified extraction into ``build/shelf/<kind>/.sync-<name>`` (files 0640, folders 0750, every
    file fsynced as written), the record's previous folder (whatever its name) is moved out to scratch, and the new one renamed in.
    ``_ledger/source_index.json`` is rewritten as a new file (temp + rename) with each applied version's entry; other keys naming the same
    record id are dropped.
  * Production's keyword index: ``indexer.update(..., keyword_only=True, only=<applied ids>)`` on the copy, through the configured index
    runner (``host``: in-process, needs SQLite >= 3.43 for ``contentless_delete``; ``docker:<image>``: the consumer image, networkless, no
    capabilities, read-only root, the build's shelf mounted read-only and only its index folder writable, one JSON line back, protocol 1).
    The first run after a snapshot seed (whose index is the Mac's vector index) rebuilds a keyword-only index from the shelf instead (the
    index seed), even with no batch; so does an update the image's indexer answers ``rebuild_needed`` (a newer chunker/policy/schema), in
    the same build, and ``--rebuild-index`` on demand.
  * Verified before anything points at it: each applied version recomputes to its digest, every untouched record keeps its digest and none is
    lost, the portable shelf check holds if it held before, the index passes ``quick_check``, is keyword-only, has one FTS row per chunk and a
    ``records`` row per applied (indexable) record at its edition, the FTS query runs through the runner, and the permissions are r3's. Then
    ``RELEASE.json`` (format ``vault-production-sync/1``), every folder fsynced bottom-up, renamed to ``releases/<attempt>``, ``releases/``
    fsynced, ``current`` switched. ``installed.json`` stays the snapshot receipt. A verification failure the build pins on applied versions
    (one that does not recompute, a shelf-check problem naming it or a record that does not verify alone, a missing index row while others
    indexed) rejects those versions and the build is redone without them; a failure no version explains fails the run with no ack.

Ordering is the guarantee. Each run first fsyncs ``releases/`` and the vault root (so a switch a dead run left unsynced is durable before
anything is acked ``stored`` from it). Acknowledgements are written only after the switch is durable, then ``applied.json``, then the
inventory (uploaded first, the local copy written only after the upload succeeds, so a failed upload is retried next run). A
failure (or a kill) anywhere before the switch leaves ``current`` serving the previous release, no partial record visible and no ack: the next
run redoes the same batch. A kill after the switch but before the acks is also safe: the next run finds every version already held and acks it
``stored`` without building another release. Re-sent versions are ``stored`` without reapplying, so a retry never duplicates.

Per version: same digest as held -> ``stored``; held edition newer -> ``superseded``; same edition, other edition hash ->
``rejected:edition_conflict``; ``batch.check_version`` failure -> ``rejected:<code>``; a sessions-raw version without its source-index entry
-> ``rejected:source_entry_missing``; an entry whose id, folder (``batch.NAME``, equal to the record's name) or key does not match ->
``rejected:source_entry_mismatch``; a source key production maps to another record -> ``rejected:source_key_conflict``; a record that does not
verify on its own (portable reader: editions contiguous, current present and hashing to ``edition_hash``) -> ``rejected:record_does_not_verify``;
else applied (batches apply oldest first, so the later of two versions of a record wins). Integrity failures of a whole batch (chain, ciphertext, decrypt, plaintext, archive, ``BATCH.json``) reject it
(``rejected_batch``: every manifest version ``rejected:<code>`` when the manifest is readable) and mark it applied. Transient failures
(download/list, space, identity) stop the run's batch loop; the batches already processed still complete, and the failed one is retried.

What leaves the host in plain text (acks, ``sync/acks/inventory.json``, the job status) holds record ids, editions, hashes, counts and fixed
codes only: never a folder name, title, path or text.

State (``<vault>-sync``, root 0700, outside the tree containers mount): ``applied.json`` {batch id: {applied_utc, release, result}},
``inventory.json`` (the last inventory written), ``inventory-cache.json`` (``versions.HashCache``; inode-keyed, so hard links hit and an
unchanged run hashes nothing). The lock is r3's ``.refresh.lock``: a snapshot refresh and an apply never run at once (``apply_busy``).

RUNBOOK (described, NOT installed; nothing here has run against real S3 or EC2):
  python3 -I -B /opt/minimoi/vault-tools/<sha>/scripts/vault/vault_apply.py plan --check-remote
  AWS_DEFAULT_REGION=us-east-1 python3 -I -B /opt/minimoi/vault-tools/<sha>/scripts/vault/vault_apply.py --index-runner docker:<portal image id> run --execute
  cron (root, after the seed's first inventory exists):
    */15 * * * * AWS_DEFAULT_REGION=us-east-1 /usr/bin/python3 -I -B /opt/minimoi/vault-tools/current/scripts/vault/vault_apply.py --index-runner docker:<portal image id> run --execute >> /opt/minimoi/logs/vault_apply.log 2>&1
  Reseed (disaster recovery): publish a snapshot on the Mac, then ``production_refresh.py refresh --execute --reseed``; the next apply rebuilds
  the keyword index (index seed) and writes a fresh inventory.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.memory_backup.backup import BackupError
from core.memory_sync import batch as BT
from core.memory_sync import versions as V

SYNC_FORMAT = "vault-production-sync/1"
INDEX_PROTOCOL = 1
MIN_HOST_SQLITE = (3, 43)
SESSION_DIR = "sessions-raw"                    # the only record folder the indexer reads (memory_shelf.shelf.SESSION_DIR)
INVENTORY_MAX_AGE = timedelta(hours=20)         # "at least daily", with slack for a cron that drifts
DEFAULT_MAX_RUN_BYTES = 2048 << 20
APPLY_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}$")    # never starts with "-": docker would read it as an option
REBUILD_NEEDED = "index_failed:rebuild_needed"     # indexer.INCOMPATIBLE through the runner: the image's chunker/policy/schema moved on
# A whole batch fails its chain or its encryption: reject it (ack every manifest version) and never retry that batch id.
INTEGRITY = frozenset({"complete_unreadable", "manifest_mismatch", "manifest_unreadable", "manifest_format", "ciphertext_mismatch",
                       "plaintext_mismatch", "decrypt_failed", "archive_unsafe", "batch_doc_unreadable", "batch_doc_mismatch",
                       "batch_too_large"})
# The network, the disk or the key is not available right now: stop, ack nothing for this batch, retry it next run.
TRANSIENT = frozenset({"download_failed", "list_failed", "insufficient_space", "identity_unavailable", "identity_malformed",
                       "identity_source_unknown", "key_write_failed", "key_cleanup_failed", "batch_deadline", "outside_window"})


def _utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(text) -> datetime | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def sqlite_version_ok() -> bool:
    try:
        return tuple(int(x) for x in sqlite3.sqlite_version.split(".")[:2]) >= MIN_HOST_SQLITE
    except ValueError:
        return False


def release_format(release: Path) -> str | None:
    """The ``format`` a release's ``RELEASE.json`` names (``vault-production/1`` for a snapshot, ``vault-production-sync/1`` for an apply);
    ``None`` when it cannot be read."""
    try:
        doc = json.loads((Path(release) / P.RELEASE_MARK).read_text())
    except (OSError, ValueError):
        return None
    return doc.get("format") if isinstance(doc, dict) else None


def index_is_keyword_only(path: Path) -> bool:
    """Read-only, no extension loaded (the host may have no sqlite-vec and an old SQLite: reading ``meta`` needs neither)."""
    try:
        con = P._ro(path)
        try:
            row = con.execute("SELECT value FROM meta WHERE key='keyword_only'").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row and row[0] == "1")


# ── the index runner: where the keyword index is written and probed ──
def index_step(shelf: Path, index: Path, only: list | None) -> dict:
    """Update (``only`` = record ids) or rebuild (``only`` = None) the keyword-only index file ``index`` from ``shelf`` (the shelf is only
    read: the docker runner mounts it read-only). Runs in-process for ``host`` and as ``vault_apply.py index-step`` inside the image for
    ``docker:``. Counts and a fixed code only."""
    from core.memory_index import indexer
    shelf, index = Path(shelf), Path(index)
    if only is None:
        out = indexer.rebuild(shelf, None, index_path=index, keyword_only=True, min_free_bytes=None)
    else:
        out = indexer.update(shelf, None, index_path=index, keyword_only=True, only=list(only), min_free_bytes=None)
    code = str(out.get("code"))
    counts = {k: v for k, v in out.items() if isinstance(v, int) and not isinstance(v, bool) and V.CODE.match(k)}
    return {"protocol": INDEX_PROTOCOL, "code": code if V.CODE.match(code) else "unknown", **counts}


class HostRunner:
    where = "host"

    def index(self, build: Path, only: list | None) -> dict:
        return _checked(index_step(build / "shelf", build / "index" / P.INDEX_NAME, only))

    def probe(self, build: Path) -> dict:
        return P.fts_probe(build / "index" / P.INDEX_NAME)


class DockerRunner:
    """The consumer image's own SQLite writes and probes the index: networkless, no capabilities, no privilege gain, a read-only root
    filesystem; the build's shelf (hard links into the live release) mounted READ-ONLY at ``/shelf``, only the build's own index folder
    writable at ``/index``, the applied ids read-only at ``/only.json``."""
    where = "image"

    def __init__(self, image: str, reader_uid: int, reader_gid: int, *, run=subprocess.run, docker_bin: str = "docker"):
        self.image, self.reader_uid, self.reader_gid, self.run, self.docker = image, reader_uid, reader_gid, run, docker_bin

    def argv(self, build: Path, only_file: Path, rebuild: bool) -> list:
        return [self.docker, "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--user", "0:0", "-v", f"{build / 'shelf'}:/shelf:ro", "-v", f"{build / 'index'}:/index",
                "-v", f"{only_file}:/only.json:ro", "--entrypoint", "python3", self.image, "-I", "-B", "/app/scripts/vault/vault_apply.py",
                "index-step", "--shelf", "/shelf", "--index", f"/index/{P.INDEX_NAME}", *(["--rebuild"] if rebuild else ["--only-file", "/only.json"])]

    def index(self, build: Path, only: list | None) -> dict:
        only_file = build.parent / "only.json"
        only_file.write_text(json.dumps(sorted(only or [])))
        try:
            done = self.run(self.argv(build, only_file, only is None), capture_output=True, text=True, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            raise BackupError("index_runner_failed") from None
        finally:
            only_file.unlink(missing_ok=True)
        try:
            out = json.loads([line for line in (done.stdout or "").splitlines() if line.strip()][-1])
        except (ValueError, IndexError):
            raise BackupError("index_runner_failed") from None
        if not isinstance(out, dict):
            raise BackupError("index_runner_failed")
        return _checked(out)

    def probe(self, build: Path) -> dict:
        return P.docker_fts_probe(self.image, build, self.reader_uid, self.reader_gid, run=self.run, docker_bin=self.docker)


def _checked(out: dict) -> dict:
    if out.get("protocol") != INDEX_PROTOCOL:
        raise BackupError("index_runner_incompatible")
    code = str(out.get("code"))
    if code != "ok":
        raise BackupError(f"index_failed:{code if V.CODE.match(code) else 'unknown'}")
    return out


def make_index_runner(spec: str | None, *, reader_uid: int = 0, reader_gid: int = 0, run=subprocess.run, docker_bin: str = "docker"):
    """``host`` | ``docker:<image>``. Required: the EC2 host's SQLite may be too old to write the index, so the operator says where."""
    if not spec:
        raise BackupError("index_runner_required")
    if spec == "host":
        if not sqlite_version_ok():
            raise BackupError("host_sqlite_too_old")
        return HostRunner()
    if spec.startswith("docker:"):
        image = spec[len("docker:"):]
        if not APPLY_IMAGE.match(image):
            raise BackupError("bad_arguments")
        return DockerRunner(image, reader_uid, reader_gid, run=run, docker_bin=docker_bin)
    raise BackupError("bad_arguments")


# ── local state ──
class SyncState:
    """``<vault>-sync``: root-only, outside the tree containers mount."""

    def __init__(self, lay: P.Layout):
        self.root = lay.root.parent / f"{lay.root.name}-sync"
        self.applied = self.root / "applied.json"
        self.inventory = self.root / V.INVENTORY_NAME
        self.cache = self.root / "inventory-cache.json"
        self.owner = lay.owner

    def prepare(self) -> None:
        if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
            raise BackupError("sync_state_unsafe")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.stat().st_uid != self.owner:
            raise BackupError("sync_state_unsafe")
        os.chmod(self.root, 0o700)

    def read_applied(self) -> dict:
        if not self.applied.exists():
            return {}
        doc = V.read_json(self.applied)
        if not isinstance(doc, dict) or not all(B.ATTEMPT.match(str(k)) for k in doc):
            raise BackupError("applied_unreadable")
        return doc

    def read_inventory(self) -> dict | None:
        doc = V.read_json(self.inventory) if self.inventory.exists() else None
        return doc if isinstance(doc, dict) else None


# ── building a release (never writing through a hard link) ──
def _own_dir(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)
    os.chmod(path, 0o750)


def _mkdirs(path: Path, stop: Path, uid: int, gid: int) -> None:
    missing = []
    probe = path
    while not probe.exists() and probe != stop:
        missing.append(probe)
        probe = probe.parent
    if probe.is_symlink() or not probe.is_dir():
        raise BackupError("build_unsafe")
    for folder in reversed(missing):
        os.mkdir(folder, 0o700)
        _own_dir(folder, uid, gid)


def _link_tree(src: Path, dst: Path, uid: int, gid: int) -> int:
    """Hard-link every file of ``src`` into a new tree ``dst`` (new folders, 0750 owner:gid). Anything but a folder or a regular file refuses."""
    os.mkdir(dst, 0o700)
    _own_dir(dst, uid, gid)
    n = 0
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        here = Path(dirpath)
        out = dst / here.relative_to(src)
        for name in sorted(dirnames):
            if (here / name).is_symlink():
                raise BackupError("release_unexpected_entry")
            os.mkdir(out / name, 0o700)
            _own_dir(out / name, uid, gid)
        for name in sorted(filenames):
            st = os.lstat(here / name)
            if st.st_mode & 0o170000 != 0o100000:
                raise BackupError("release_unexpected_entry")
            os.link(here / name, out / name, follow_symlinks=False)
            n += 1
    return n


def _write_new_file(src: Path, dst: Path, uid: int, gid: int) -> None:
    """A NEW file (exclusive create, never through a link), 0640 owner:gid, fsynced as written."""
    fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
        shutil.copyfileobj(f, out, 1 << 20)
        out.flush()
        os.fchown(out.fileno(), uid, gid)
        os.fchmod(out.fileno(), 0o640)
        try:
            P._fsync(out.fileno(), dst)
        except OSError:
            raise BackupError("fsync_failed") from None


class _Rejected(Exception):
    """The build's verification failed because of these applied versions (record id -> fixed code): reject them, rebuild without them."""

    def __init__(self, codes: dict):
        super().__init__("applied_versions_rejected")
        self.codes = codes


def record_verifies(folder: Path, kind: str, name: str, work: Path) -> bool:
    """The portable reader's checks on ONE record folder on its own (``portable.verify_shelf`` over a one-record shelf of hard links in
    scratch): front matter readable with a valid id, editions numbered 1..n without gaps, every edition named by its own hash, the current
    edition present and hashing to ``edition_hash``, every turn's hash. A version that fails is ``rejected:record_does_not_verify``, so a
    record that cannot verify never reaches a release (where it would fail the whole-release check and block every later batch)."""
    from core.vault_t1 import portable
    from core.vault_t1.reader import VaultError
    if os.path.lexists(work):
        raise BackupError("build_unsafe")
    try:
        os.mkdir(work, 0o700)
        os.mkdir(work / kind, 0o700)
        os.mkdir(work / kind / name, 0o700)
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            here = Path(dirpath)
            dst = work / kind / name / here.relative_to(folder)
            for d in dirnames:
                if (here / d).is_symlink():
                    return False
                os.mkdir(dst / d, 0o700)
            for f in filenames:
                if os.lstat(here / f).st_mode & 0o170000 != 0o100000:
                    return False
                os.link(here / f, dst / f, follow_symlinks=False)
        try:
            check = portable.verify_shelf(str(work))
        except (VaultError, OSError, ValueError, UnicodeDecodeError):
            return False
        return bool(check.get("ok")) and check.get("checked", {}).get("records") == 1
    finally:
        if os.path.lexists(work) and not B.remove_tree(work):                  # links to our own scratch files: unlink only
            raise BackupError("cleanup_failed")


def _place(entry: dict, src: Path, shelf: Path, replaced: Path, old: V.Version | None, uid: int, gid: int) -> None:
    kind, name = entry["kind"], entry["name"]
    _mkdirs(shelf / kind, shelf, uid, gid)
    tmp = shelf / kind / f".sync-{name}"
    if os.path.lexists(tmp):
        raise BackupError("build_unsafe")
    os.mkdir(tmp, 0o700)
    _own_dir(tmp, uid, gid)
    for rel, _sha, _size in entry["files"]:
        dst = tmp / rel
        _mkdirs(dst.parent, tmp, uid, gid)
        _write_new_file(src / rel, dst, uid, gid)
    # Out to scratch (links only; wiped with scratch, unlink only): the record's previous folder, whatever its name, and a damaged folder at
    # the new name (not a readable record of any id, else _evaluate refused name_conflict), which this version repairs.
    for stale in ([shelf / old.kind / old.name] if old is not None else []) + [shelf / kind / name]:
        if os.path.lexists(stale):
            os.rename(stale, replaced / f"{len(os.listdir(replaced)):06d}")
    os.rename(tmp, shelf / kind / name)


def _rewrite_source_index(shelf: Path, to_apply: dict, uid: int, gid: int) -> dict:
    try:
        doc = V.source_index(shelf)
    except V.Damaged:
        raise BackupError("source_index_unreadable") from None
    changed = False
    for rid in sorted(to_apply):
        entry = to_apply[rid][0]
        key, value = entry.get("source_key"), entry.get("source_index_entry")
        if not key or not isinstance(value, dict):
            continue
        for other in [k for k, e in doc.items() if k != key and isinstance(e, dict) and e.get("id") == rid]:
            del doc[other]
            changed = True
        if doc.get(key) != value:
            doc[key] = value
            changed = True
    if changed:
        _mkdirs(shelf / "_ledger", shelf, uid, gid)
        P._write_private_json(shelf / "_ledger" / "source_index.json", doc, uid, gid)    # temp + rename: replaces the link, never writes it
    return doc


def _indexable(doc: dict, to_apply: dict) -> dict:
    """Applied record id -> edition hash, for EVERY applied ``sessions-raw`` record (the folder the indexer reads). Each one must have an
    index ``records`` row: ``_evaluate`` refused a sessions-raw version without its source-index entry, so a missing row is never an
    accepted, acked-``stored`` record that vault-ask can never find."""
    return {rid: entry["edition_hash"] for rid, (entry, _src, _bid) in to_apply.items() if entry["kind"] == SESSION_DIR}


def _verify_build(build: Path, *, to_apply: dict, base_versions: dict, base_problems, base_ok, source_doc: dict, runner, cache,
                  owner_uid: int, reader_uid: int, gid: int) -> dict:
    from core.vault_t1 import portable
    shelf, index = build / "shelf", build / "index" / P.INDEX_NAME
    versions, problems = V.record_versions(shelf, cache)
    mismatched = {rid: "applied_version_mismatch" for rid, (entry, _src, _bid) in to_apply.items()
                  if versions.get(rid) is None or versions[rid].digest != entry["digest"]}
    if mismatched and len(mismatched) < len(to_apply):
        raise _Rejected(mismatched)
    if mismatched:                                  # every applied version is wrong: the build is at fault, not the versions; retry next run
        raise BackupError("applied_version_mismatch")
    for rid, v in base_versions.items():
        if rid not in to_apply and (rid not in versions or versions[rid].digest != v.digest):
            raise BackupError("base_record_changed")
    if sum(problems.values()) > sum(base_problems.values()):
        raise BackupError("shelf_problems_grew")
    check = portable.verify_shelf(str(shelf))
    verify_ok = bool(check.get("ok"))
    if not verify_ok and base_ok():
        # Whose fault? A problem naming an applied record, or an applied record that does not verify on its own: reject those and rebuild
        # without them. Nothing attributable (a base record, the shelf as a whole): the run fails, no ack, as before.
        named = {str(p.get("where", "")).split("#", 1)[0] for p in check.get("problems", []) if isinstance(p, dict)}
        bad = {rid for rid in to_apply if rid in named}
        for rid, (entry, _src, _bid) in sorted(to_apply.items()):
            if rid not in bad and not record_verifies(shelf / entry["kind"] / entry["name"], entry["kind"], entry["name"],
                                                      build.parent / "verify-record"):
                bad.add(rid)
        if bad:
            raise _Rejected({rid: "record_does_not_verify" for rid in bad})
        raise BackupError("shelf_verify_failed")
    try:
        con = P._ro(index)
        try:
            quick = con.execute("PRAGMA quick_check").fetchone()[0]
            keyword_only = (con.execute("SELECT value FROM meta WHERE key='keyword_only'").fetchone() or [None])[0] == "1"
            chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
            fts_rows = con.execute("SELECT count(*) FROM fts_docsize").fetchone()[0]
            rows = {r[0]: (r[1], r[2]) for r in con.execute("SELECT record_id, edition_hash, state FROM records")}
        finally:
            con.close()
    except sqlite3.DatabaseError:
        raise BackupError("index_unreadable") from None
    if quick != "ok":
        raise BackupError("index_quick_check_failed")
    if not keyword_only:
        raise BackupError("index_not_keyword_only")
    if fts_rows != chunks:
        raise BackupError("index_fts_rows_mismatch")
    indexable = _indexable(source_doc, to_apply)
    missing = sorted(rid for rid, edition_hash in indexable.items()
                     if rows.get(rid) is None or (rows[rid][0] != edition_hash and rows[rid][1] != "held"))
    if missing:
        # Some applied records indexed and others not: the runner works, so the others are the versions' fault. None indexed: the runner
        # or the index is at fault (an infrastructure failure): fail the run, no ack.
        if len(missing) < len(indexable):
            raise _Rejected({rid: "not_indexed" for rid in missing})
        raise BackupError("index_record_missing")
    fts = runner.probe(build)
    perms = P.check_permissions(build, owner_uid=owner_uid, reader_uid=reader_uid, gid=gid)
    return {"versions": versions, "problems": problems, "verify_ok": verify_ok, "chunks": int(chunks), "fts": fts, "permissions": perms}


def _new_attempt(lay: P.Layout, now: datetime) -> str:
    """An attempt id newer than every retained release (the Mac's clock named the snapshot ones)."""
    t = now.astimezone(timezone.utc).replace(microsecond=0)
    ids = lay.release_ids()
    if ids:
        newest = datetime.strptime(ids[-1][:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if t <= newest:
            t = newest + timedelta(seconds=1)
    return B.new_attempt_id(t)


# ── one batch ──
def _manifest_versions(path: Path) -> list:
    """The manifest's versions when its file parses and each is well formed (a rejected batch still names what it carried)."""
    try:
        doc = json.loads(path.read_text("utf-8"))
        out = []
        for e in doc["versions"]:
            if not V.ULID.match(str(e["record_id"])) or not V.HEX64.match(str(e["digest"])) or not V.HEX64.match(str(e["edition_hash"])) \
                    or isinstance(e["edition"], bool) or not isinstance(e["edition"], int):
                return []
            out.append({k: e[k] for k in ("record_id", "edition", "edition_hash", "digest")})
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _key(entry: dict) -> dict:
    return {k: entry.get(k) for k in ("record_id", "edition", "edition_hash", "digest")}


def _evaluate(entry: dict, held: dict, dirs: dict, extracted: Path, *, keys: dict | None = None, work: Path | None = None) -> str:
    """``stored`` | ``superseded`` | ``rejected:<code>`` | ``apply``. ``keys``: source key -> record id as production's source index
    (plus this run's applied versions) has it. ``work``: an absent scratch path for the one-record verification."""
    try:
        rid, edition, edition_hash, digest = str(entry["record_id"]), entry["edition"], str(entry["edition_hash"]), str(entry["digest"])
        if isinstance(edition, bool) or not isinstance(edition, int):
            raise TypeError
    except (KeyError, TypeError):
        return f"{V.REJECTED}:entry_malformed"
    have = held.get(rid)
    if have is not None:
        if have[2] == digest:
            return V.STORED
        if have[0] > edition:
            return V.SUPERSEDED
        if have[0] == edition and have[1] != edition_hash:
            return f"{V.REJECTED}:edition_conflict"
    code = BT.check_version(extracted, entry)
    if code:
        return f"{V.REJECTED}:{code}"
    value = entry.get("source_index_entry")
    if value is None:
        if entry["kind"] == SESSION_DIR:                    # the indexer lists sessions-raw records from the source index only
            return f"{V.REJECTED}:source_entry_missing"
    else:
        folder = value.get("dir") if isinstance(value, dict) else None
        if not isinstance(value, dict) or not entry.get("source_key") or value.get("id") != rid or not isinstance(folder, str) \
                or not BT.NAME.match(folder) or folder != entry["name"]:
            return f"{V.REJECTED}:source_entry_mismatch"
        if (keys or {}).get(entry["source_key"]) not in (None, rid):              # never repoint another record's source key
            return f"{V.REJECTED}:source_key_conflict"
    if dirs.get(f"{entry['kind']}/{entry['name']}") not in (None, rid):         # another record's folder: never replaced
        return f"{V.REJECTED}:name_conflict"
    if work is not None and not record_verifies(extracted / V.RECORDS_ROOT / entry["kind"] / entry["name"], entry["kind"], entry["name"],
                                                work):
        return f"{V.REJECTED}:record_does_not_verify"
    return "apply"


def run_apply(*, dest: str, vault_root: str, identity_spec: str, index_runner, age_bin: str = "age", expect_age: str | None = None,
              aws_bin: str = "aws", owner_uid: int = 0, reader_uid: int = 0, reader_gid: int = 0, keep: int = 3,
              max_run_bytes: int = DEFAULT_MAX_RUN_BYTES, require_root: bool = True, force_inventory: bool = False,
              rebuild_index: bool = False, run=subprocess.run, fetch=B.fetch_identity, usage=shutil.disk_usage, now=None,
              hooks: dict | None = None, admission: "P.Admission | None" = None) -> dict:
    """One apply run. Returns counts, the serving release and ``code`` (``ok`` | ``noop`` | a transient code). Raises BackupError with a
    fixed code when nothing could be done (``apply_busy``, ``nothing_installed``, a failed build: ``current`` untouched, no ack).
    ``index_runner``: ``host`` | ``docker:<image>`` or a runner object. ``rebuild_index``: build a new release whose keyword index is
    rebuilt from the shelf this run, batch or not (after a new image's chunker/policy version, or to repair). ``now``: a callable
    returning the time. ``hooks``: fault injection. ``admission``: the caller's held ``production.Admission`` (the CLI takes it before its
    status start, so a refused competitor writes nothing); without one the run locks for itself."""
    if require_root and os.geteuid() != 0:
        raise BackupError("root_required")
    if keep < 1 or max_run_bytes < 1:
        raise BackupError("bad_arguments")
    clock = now or (lambda: datetime.now(timezone.utc))
    hook = lambda name, *args: (hooks or {}).get(name, lambda *a: None)(*args)
    runner = make_index_runner(index_runner, reader_uid=reader_uid, reader_gid=reader_gid, run=run) \
        if index_runner is None or isinstance(index_runner, str) else index_runner
    old_umask = os.umask(0o077)
    try:
        B.check_age(age_bin, expect_age)
        d = B.Dest(dest, aws_bin=aws_bin, run=run)
        lay = P.Layout(vault_root, owner_uid=owner_uid, reader_uid=reader_uid, reader_gid=reader_gid)
        lay.prepare()
        lay.prepare_work()
        state = SyncState(lay)
        state.prepare()
        lock = P.admitted(admission, lay)
        try:
            lock.__enter__()
        except BackupError as exc:
            raise BackupError("apply_busy" if exc.code == "refresh_busy" else exc.code) from None
        try:
            lay.wipe_leftovers()
            current = lay.current_attempt()
            if current is None:
                raise BackupError("nothing_installed")
            B.ensure_space(lay.work, 4 << 20, usage=usage)
            scratch = Path(tempfile.mkdtemp(prefix=".work-", dir=lay.work))
            try:
                os.chmod(scratch, 0o700)
                return _run_locked(d, lay, state, scratch, current, runner=runner, identity_spec=identity_spec, age_bin=age_bin,
                                   owner_uid=owner_uid, reader_uid=reader_uid, reader_gid=reader_gid, keep=keep, max_run_bytes=max_run_bytes,
                                   force_inventory=force_inventory, rebuild_index=rebuild_index, fetch=fetch, usage=usage, clock=clock,
                                   hook=hook)
            finally:
                if not B.remove_tree(scratch):
                    raise BackupError("cleanup_failed")
        finally:
            lock.__exit__(None, None, None)
    finally:
        os.umask(old_umask)


def _run_locked(d, lay, state, scratch, current, *, runner, identity_spec, age_bin, owner_uid, reader_uid, reader_gid, keep, max_run_bytes,
                force_inventory, rebuild_index, fetch, usage, clock, hook) -> dict:
    # The serving release may have been switched by a run that died before its own fsyncs reached the disk (or by a snapshot refresh):
    # make ``current`` and ``releases/`` durable before anything is acknowledged ``stored`` from what ``current`` names.
    try:
        P._fsync_dir(lay.releases)
        P._fsync_dir(lay.root)
    except OSError:
        raise BackupError("fsync_failed") from None
    base = lay.releases / current
    mark = P._json(base / P.RELEASE_MARK, "release_unreadable")
    base_shelf, base_index = base / "shelf", base / "index" / P.INDEX_NAME
    applied = state.read_applied()
    cache = V.HashCache(state.cache)
    base_versions, base_problems = V.record_versions(base_shelf, cache)
    seed_due = rebuild_index or not index_is_keyword_only(base_index)
    held = {rid: (v.edition, v.edition_hash, v.digest) for rid, v in base_versions.items()}
    dirs = {v.dir: rid for rid, v in base_versions.items()}
    try:
        source_keys = {k: e.get("id") for k, e in V.source_index(base_shelf).items() if isinstance(e, dict)}
    except V.Damaged:
        source_keys = {}                                       # a build would refuse source_index_unreadable; a no-op run does not need it
    to_apply: dict = {}                         # record id -> (BATCH.json entry, extracted folder, batch id); the latest wins
    processed: list = []                        # [{"batch_id", "result", "code", "versions": [(key, status)]}]
    transient, identity, budget = None, None, 0

    # 1. Batches: COMPLETE, not yet applied, oldest first, bounded by plaintext bytes per run.
    try:
        listed = [b for b in BT.complete_batches(d) if b not in applied]
    except BackupError as exc:
        if exc.code not in TRANSIENT:
            raise
        listed, transient = [], exc.code
    for bid in listed:
        bdir = scratch / f"b-{bid}"
        os.mkdir(bdir, 0o700)
        m = None
        try:
            _comp, m = BT.fetch_chain(d, bid, bdir)
            try:
                plain_bytes = int(m["objects"]["batch"]["plaintext_bytes"])
                cipher_bytes = int(m["objects"]["batch"]["bytes"])
                record_bytes = int(m.get("record_bytes") or 0)
            except (KeyError, TypeError, ValueError):
                raise BackupError("manifest_format") from None
            if budget and budget + plain_bytes > max_run_bytes:
                B.remove_tree(bdir)
                break                                               # the rest waits for the next run
            budget += plain_bytes
            B.ensure_space(lay.work, cipher_bytes + plain_bytes + 2 * record_bytes + (len(m["versions"]) + 1) * 64 * B.BLOCK + P.SLACK,
                           usage=usage)
            cipher, plain = bdir / V.BATCH_OBJECT, bdir / "batch.tar.gz"
            BT.fetch_ciphertext(d, bid, m, cipher)
            if identity is None:
                identity = fetch(identity_spec)                     # once per run, only when a batch needs it
            B.decrypt(age_bin, identity, cipher, plain, bdir)
            cipher.unlink()
            if plain.stat().st_size != plain_bytes or B.sha256_file(plain) != m["objects"]["batch"]["plaintext_sha256"]:
                raise BackupError("plaintext_mismatch")
            doc = BT.extract(plain, bdir / "x", max_bytes=record_bytes)     # never more than the space reserved above
            if not B.wipe_file(plain):
                raise BackupError("cleanup_failed")
            if not BT.doc_matches_manifest(doc, m):
                raise BackupError("batch_doc_mismatch")
        except BackupError as exc:
            if exc.code in TRANSIENT:
                transient = exc.code
                break
            if exc.code not in INTEGRITY:
                raise
            carried = m["versions"] if m is not None else _manifest_versions(bdir / B.MANIFEST)     # never the source-key map
            processed.append({"batch_id": bid, "result": "rejected_batch", "code": exc.code,
                              "versions": [(_key(e), f"{V.REJECTED}:{exc.code}") for e in carried]})
            if not B.remove_tree(bdir):
                raise BackupError("cleanup_failed")
            continue
        hook("extracted", bid, bdir / "x")
        statuses = []
        for entry in sorted(doc["versions"], key=lambda e: str(e.get("record_id") if isinstance(e, dict) else "")):
            if not isinstance(entry, dict):
                continue                                            # doc_matches_manifest already proved every entry is a dict
            status = _evaluate(entry, held, dirs, bdir / "x", keys=source_keys, work=bdir / "verify-record")
            rid = entry.get("record_id")
            if status == "apply":
                held[rid] = (entry["edition"], entry["edition_hash"], entry["digest"])
                for folder in [f for f, r in dirs.items() if r == rid]:
                    del dirs[folder]
                dirs[f"{entry['kind']}/{entry['name']}"] = rid
                if isinstance(entry.get("source_index_entry"), dict):          # as _rewrite_source_index will write it
                    for k in [k for k, r in source_keys.items() if r == rid]:
                        del source_keys[k]
                    source_keys[entry["source_key"]] = rid
                to_apply[rid] = (entry, bdir / "x" / V.RECORDS_ROOT / entry["kind"] / entry["name"], bid)
            elif status in (V.STORED, V.SUPERSEDED) and rid in to_apply:
                status = "apply"            # judged against a version applied earlier THIS run: settled only once the build keeps that one
            statuses.append((_key(entry), status))
        processed.append({"batch_id": bid, "result": "applied", "code": None, "versions": statuses})

    # 2. Build, verify and switch, when anything is to be applied or the keyword index must be seeded. A version the build's own
    #    verification pins a failure on is rejected and the build redone without it (each round drops at least one), so one bad version
    #    never blocks the rest or any later batch. A failure no version explains (the index runner, the disk) fails the run: no ack.
    serving, built = current, None
    build_rejects: dict = {}                    # record id -> (code, digest of the version the build refused)
    if to_apply or seed_due:
        while True:
            try:
                built = _build_and_switch(lay, scratch, base, mark, to_apply=to_apply, base_versions=base_versions,
                                          base_problems=base_problems, seed=seed_due,
                                          batches=[p["batch_id"] for p in processed if p["result"] == "applied"], runner=runner,
                                          cache=cache, owner_uid=owner_uid, reader_uid=reader_uid, reader_gid=reader_gid, usage=usage,
                                          clock=clock, hook=hook)
                break
            except _Rejected as r:
                for rid, code in r.codes.items():
                    build_rejects[rid] = (code, to_apply.pop(rid)[0]["digest"])
                for leftover in (scratch / "build", scratch / "replaced", scratch / "verify-record"):
                    if os.path.lexists(leftover) and not B.remove_tree(leftover):     # links only: unlink, never a write
                        raise BackupError("cleanup_failed")
                if not to_apply and not seed_due:
                    break
        if built:
            serving = built["attempt"]
            hook("after_switch", serving)

    # 3. Only now (the switch is durable): acks, then applied.json, then the inventory.
    now = clock()
    counts = {"stored": 0, "superseded": 0, "rejected": 0}
    for p in processed:
        versions = []
        for key, status in p["versions"]:
            if status == "apply" and key["record_id"] in build_rejects:
                code, digest = build_rejects[key["record_id"]]
                status = f"{V.REJECTED}:{code}" if digest == key["digest"] else f"{V.REJECTED}:later_version_rejected"
            elif status == "apply":
                status = V.STORED if held[key["record_id"]][2] == key["digest"] else V.SUPERSEDED
            counts["rejected" if status.startswith(V.REJECTED) else status] += 1
            versions.append({**key, "status": status})
        ack = {"format": V.ACK_FORMAT, "batch_id": p["batch_id"], "applied_utc": _utc(now),
               "release": serving if p["result"] == "applied" else None, "result": p["result"], "batch_code": p["code"],
               "versions": versions}
        path = scratch / f"ack-{p['batch_id']}.json"
        path.write_text(json.dumps(ack, indent=1, sort_keys=True) + "\n")
        d.put(path, f"{V.ACK_PREFIX}/{p['batch_id']}.json", replace=True)
        applied[p["batch_id"]] = {"applied_utc": _utc(now), "release": ack["release"], "result": p["result"]}
    if processed:
        V.write_json_atomic(state.applied, applied)
    hook("before_inventory")
    last = state.read_inventory()
    last_at = _parse_utc((last or {}).get("generated_utc"))
    inventory = "skipped"
    # Every run that changed anything; when none exists or it describes another release (a reseed switched under it); at least daily.
    if force_inventory or built or processed or last is None or last_at is None or last.get("release") != serving \
            or now - last_at >= INVENTORY_MAX_AGE:
        serving_shelf = lay.releases / serving / "shelf"
        if built:
            versions, problems = built["versions"], built["problems"]
        else:
            versions, problems = V.record_versions(serving_shelf, cache)
        doc = V.inventory_doc(serving_shelf, versions, generated_utc=_utc(now), through_batch=max(applied) if applied else None,
                              release=serving, problems=problems)
        out = scratch / V.INVENTORY_NAME
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        d.put(out, f"{V.ACK_PREFIX}/{V.INVENTORY_NAME}", replace=True)
        V.write_json_atomic(state.inventory, doc)     # only after the upload: a failed put leaves the old copy, so the next run retries
        inventory = "written"
    cache.save()
    pruned = lay.prune(keep) if built else []
    return {"code": transient or ("ok" if (processed or built) else "noop"), "batches": len(processed),
            "rejected_batches": sum(1 for p in processed if p["result"] == "rejected_batch"), **counts, "applied": len(to_apply),
            "release": serving, "built": bool(built), "index_seed": bool(built and built["index_rebuilt"]), "inventory": inventory,
            "pruned": len(pruned), "records": len(built["versions"]) if built else len(base_versions),
            "last_batch": max(p["batch_id"] for p in processed) if processed else None}


def _build_and_switch(lay, scratch, base, mark, *, to_apply, base_versions, base_problems, seed, batches, runner, cache, owner_uid,
                      reader_uid, reader_gid, usage, clock, hook) -> dict:
    uid, gid = owner_uid, reader_gid
    base_index = base / "index" / P.INDEX_NAME
    index_bytes = base_index.stat().st_size if base_index.is_file() else 0
    B.ensure_space(lay.work, 2 * index_bytes + sum(int(sz) for e, _s, _b in to_apply.values() for _r, _h, sz in e["files"])
                   + (len(to_apply) + 1) * 64 * B.BLOCK + P.SLACK, usage=usage)
    build = scratch / "build"
    os.mkdir(build, 0o700)
    _own_dir(build, uid, gid)
    shelf = build / "shelf"
    _link_tree(base / "shelf", shelf, uid, gid)
    os.mkdir(build / "index", 0o700)
    _own_dir(build / "index", uid, gid)
    index = build / "index" / P.INDEX_NAME
    if not seed:
        shutil.copyfile(base_index, index)                              # a copy: SQLite writes this file
    replaced = scratch / "replaced"
    os.mkdir(replaced, 0o700)
    for rid in sorted(to_apply):
        entry, src, _bid = to_apply[rid]
        _place(entry, src, shelf, replaced, base_versions.get(rid), uid, gid)
        hook("placed", rid)
    source_doc = _rewrite_source_index(shelf, to_apply, uid, gid)
    rebuilt = seed
    try:
        runner.index(build, None if seed else sorted(to_apply))
    except BackupError as exc:
        if seed or exc.code != REBUILD_NEEDED:
            raise
        # The image's indexer no longer accepts this index (a newer chunker, policy or schema): rebuild it keyword-only from the shelf, in
        # this same build, exactly like the index seed.
        runner.index(build, None)
        rebuilt = True
    hook("indexed", build)
    if not index.is_file() or index.is_symlink():
        raise BackupError("index_missing")
    for extra in (build / "index").iterdir():                          # the indexer's lock, a journal: new files of ours, never links
        if extra.name != P.INDEX_NAME:
            extra.unlink()
    os.chown(index, uid, gid)
    os.chmod(index, 0o640)
    P._sync_file(index)
    _own_dir(build / "index", uid, gid)
    base_ok_cache: list = []

    def base_ok() -> bool:                                              # computed only when the build's own check fails
        if not base_ok_cache:
            if mark.get("format") == SYNC_FORMAT:
                base_ok_cache.append(bool(mark.get("shelf_verify_ok", True)))
            else:
                from core.vault_t1 import portable
                base_ok_cache.append(bool(portable.verify_shelf(str(base / "shelf")).get("ok")))
        return base_ok_cache[0]

    evidence = _verify_build(build, to_apply=to_apply, base_versions=base_versions, base_problems=base_problems, base_ok=base_ok,
                             source_doc=source_doc, runner=runner, cache=cache, owner_uid=uid, reader_uid=reader_uid, gid=gid)
    now = clock()
    attempt = _new_attempt(lay, now)
    snapshot = mark.get("attempt") if mark.get("format") == P.FORMAT else mark.get("snapshot")
    entries = V.entries_of(evidence["versions"])
    P._write_private_json(build / P.RELEASE_MARK, {
        "format": SYNC_FORMAT, "attempt": attempt, "base": base.name, "snapshot": snapshot, "batches": batches,
        "created_utc": _utc(now), "verified_utc": _utc(clock()), "records": len(entries), "index_chunks": evidence["chunks"],
        "source_keys": P.source_key_facts(shelf), "version_set_digest": V.version_set_digest(entries), "index": {"keyword_only": True},
        "shelf_verify_ok": evidence["verify_ok"]}, uid, gid)
    P._sync_tree(build, files=False)            # files we wrote were fsynced as written; linked files were already durable
    release = lay.releases / attempt
    try:
        os.rename(build, release)
    except OSError:
        raise BackupError("release_move_failed") from None
    try:
        P._fsync_dir(lay.releases)
        hook("before_switch", release)
        lay.switch(attempt)
    except BaseException as exc:
        if not (isinstance(exc, BackupError) and exc.code == "switch_not_durable"):     # current still names the old release
            try:
                os.rename(release, scratch / "unswitched")                                 # never left behind as a release nobody served
            except OSError:
                pass
        if isinstance(exc, OSError):
            raise BackupError("fsync_failed") from None
        raise
    return {"attempt": attempt, "versions": evidence["versions"], "problems": evidence["problems"], "chunks": evidence["chunks"],
            "fts": evidence["fts"]["where"], "index_rebuilt": rebuilt}


def plan_apply(*, vault_root: str) -> dict:
    """Read-only: what serves, what kind of release it is, whether its index is keyword-only, what the sync state says. Never reads the key."""
    lay = P.Layout(vault_root)
    state = SyncState(lay)
    out = {"vault_root_exists": lay.root.is_dir(), "running_as_root": os.geteuid() == 0, "sqlite": sqlite3.sqlite_version,
           "host_index_runner_possible": sqlite_version_ok(), "sync_state_exists": state.root.is_dir()}
    if lay.root.is_dir():
        try:
            current = lay.current_attempt()
            out["current"] = current
            out["releases"] = lay.release_ids()
            if current:
                release = lay.releases / current
                out["current_format"] = release_format(release)
                out["index_keyword_only"] = index_is_keyword_only(release / "index" / P.INDEX_NAME)
                out["index_seed_due"] = not out["index_keyword_only"]
        except (BackupError, OSError) as exc:
            out["state_error"] = getattr(exc, "code", "unreadable")
    if state.root.is_dir():
        try:
            applied = state.read_applied()
            out["applied_batches"] = len(applied)
            out["last_applied_batch"] = max(applied) if applied else None
        except BackupError as exc:
            out["applied_error"] = exc.code
        inv = state.read_inventory()
        out["inventory"] = {k: (inv or {}).get(k) for k in ("generated_utc", "through_batch", "release", "count", "version_set_digest")}
    return out
