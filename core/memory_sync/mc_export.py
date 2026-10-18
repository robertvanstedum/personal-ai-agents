"""``vault-mc-export``: the EC2 side of the production MC capture path (17 Oct 2026). Sends production's changed Master Craftsman
conversations to the Mac, encrypted, and checks that every one sent comes back in the serving Vault. Runs as root on the host (cron, every
2 h), like ``vault-apply``; the job script is ``scripts/vault/vault_mc_export.py``.

Why this direction exists. The Vault's sync carries records Mac -> production, and only the Mac captures (the shelf, its approvals and the
watcher live there). Conversations kept in PRODUCTION's portal therefore travel the other way first: this job sends the exporter's files,
the Mac's ``vault-mc-pull`` places them in the ``minimoi-mc`` watch folder and its approval-gated watcher captures them as ordinary records,
and the existing ``vault-sync`` / ``vault-apply`` bring the records back to production as versions. The batch format between the two light
jobs is ``core/memory_sync/mc_batch.py``; this module only decides WHAT to send and WHEN, and proves it arrived.

One run (all under ``<vault>-sync/mc-export.lock``, non-blocking: a second run is ``mc_export_busy``). The job script takes that lock
FIRST, as an ``Admission`` held across its status start, the work and its status finish (Codex, 17 Oct: the same pattern as vault-sync,
vault-apply and the r3 refresh), so the status file always describes the run holding the lock; a competitor refused at the lock writes
nothing, not even a status, and exits 2 (``refused: mc_export_busy``).
  1. **Export.** ``runner(argv, stdout)`` runs the portal's own exporter in ``--stdout-tar`` mode
     (``docker exec -i minimoi-portal python3 -I -B /app/scripts/vault/export_mc_conversations.py --instance production --principal <owner>
     --stdout-tar``) with stdout going straight into ``<vault>-work/.mcx-*/export.tar`` (folder 0700, file 0600), so the plaintext never
     lands in the container's filesystem. Exit 2, any other exit, or a stream that is not a well-formed export is ``export_failed`` (no
     batch, alert). Exit 1 (``partial``: a conversation was too large or its floor unreadable) continues with what was exported and warns.
  2. **Strict read** of that tar: regular files only, no duplicates, no folders or links; every name ``mc_batch.FILE`` with instance
     ``production`` (so nothing can name a path); each at most ``MAX_FILE_BYTES``; ``EXPORT.json`` LAST (a stream cut short has none); each
     file the export shape for its own name (``mc_batch.check_file``). Extracted into the same scratch with exclusive creates.
  3. **Acknowledgements** for every pending batch (``mc_batch.read_ack``): ``placed`` -> ``acked[name]`` (its sha256 and the EC2 time it
     was learned: the grace below runs on one clock); ``superseded`` -> dropped from that batch (a newer batch carries newer bytes);
     ``rejected:<code>`` -> stays pending and is retried on the backoff. An empty batch is removed. A DOWNLOAD that fails is an error (the
     run stops, the state is unchanged). An ack that does not parse (``ack_malformed``) is "no ack" for THAT batch only, counted and warned
     (adversarial review HIGH-1: raising wedged every later run forever): the batch stays pending, its retry re-sends the files in a new
     batch, and the old batch (and its unreadable ack) leaves the state.
  4. **Gap repair** (reconciliation against the serving Vault, before deciding what to send): the serving release's
     ``shelf/_ledger/source_index.json`` keys ``minimoi-mc:production:<conversation id>`` and each record main file's ``source_hash`` (the
     sha256 of the export file the Mac captured) make every exported name ``current``, ``stale`` (older bytes) or ``missing``. A name acked
     with the bytes production still exports but not ``current`` for longer than the grace (default 12 h after it was acked) is re-queued:
     removed from ``acked`` and sent again (``gap_requeued``). That re-send repairs a lost watch-folder file or a batch the Mac lost. It
     happens at most ONCE per (name, sha256) (``requeued``; review MEDIUM-2): the real Mac reports identical bytes ``unchanged`` and its
     watcher skips them, so a second re-send of the same bytes cannot repair anything and would only refresh ``acked_utc`` and loop. After
     that one re-send the name counts ``behind_after_repair`` (warn) until its bytes change. A capture that waits on approval stays
     visible as ``missing_in_vault`` and ``lag_oldest_s``. An unreadable serving source index (review MEDIUM-3) makes the reconciliation
     unknown for this run (``vault=unreadable``, warn): no gap repair, but changed files are still sent.
  5. **What to send.** Changed = exported names whose sha256 is not the acked one and not already pending with the same sha256. Retry =
     batches still ``sending`` (an upload that did not finish) or past ``next_retry_utc``: their names are re-sent with the CURRENT bytes
     when those still differ from the acked ones. Backoff 1 h, 2 h, 4 h, 8 h, then 12 h. Nothing unacknowledged is ever dropped: a name
     production did not export this run (a partial run) keeps its pending entry; a name over this run's size budget waits for the next.
  6. **One batch** of at most ``max_batch_bytes`` of files: ``mc_batch.write_tar`` -> ``B.encrypt`` to the PUBLIC recipient (this host
     cannot read it back) -> the plaintext tar wiped -> manifest -> COMPLETE. The batch is recorded ``sending`` (state written) BEFORE the
     upload and ``sent`` after it, with its attempts and next retry; a name in it leaves every older pending batch. ``mc_batch.upload`` puts
     COMPLETE last, so an interrupted upload is an orphan the Mac never lists, and the ``sending`` batch is re-sent next run in a NEW batch.
  7. **Wipe**: every plaintext file in scratch is zeroed and removed (``B.wipe_file``, then ``remove_tree``) on success and on failure;
     leftovers of a killed run (``.mcx-*``) are wiped at the next start.

State (``<vault>-sync/mc-export-state.json``, root 0600, names, hashes and times only; a conversation id is the name, never a title)::

    {"format": "vault-mc-export-state/1",
     "acked":      {name: {"sha256", "acked_utc"}},
     "pending":    {batch_id: {"files": {name: sha256}, "sent_utc", "attempts", "next_retry_utc", "state": "sending"|"sent"}},
     "first_seen": {name: {"sha256", "utc"}},
     "behind_since": {name: utc}, "behind_held": {name: sha256 | ""}, "unacked_since": {name: utc}, "requeued": {name: sha256}}

``first_seen`` is when production first exported the current bytes of a name (informational). ``behind_since`` is when a name FIRST went
not ``current`` in the serving Vault and the Vault made no progress on it: kept while production's bytes keep changing, restarted when the
Vault's held version of it changes (``behind_held``: the Mac is delivering, only one cycle behind a busy conversation), cleared when it is
current (review MEDIUM-1: a first-seen clock reset on every change hid a dead Mac; second review N2: a clock never reset by progress
flagged a healthy Mac as dead). ``lag_oldest_s`` is now minus the oldest ``behind_since`` (0 when all
are current). ``unacked_since`` is when a name entered the pending set with no ``placed`` ack since, kept across re-sends:
``oldest_unacked_s`` is its oldest age, so a Mac that stopped acknowledging shows even while every send is fresh. Each warns past the grace.
The three newer maps are optional on read (a state written before them reads as empty maps).

What leaves the host in plain text (manifest, COMPLETE, the job status, the alert) is conversation ids, hashes, sizes, counts and fixed codes:
never a title or a note. This module is stdlib plus the EC2 tools bundle (``memory_backup``, ``memory_sync``); the job status lives in the
script. It never changes the frozen sync's state or files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.memory_backup.backup import BackupError
from core.memory_sync import batch as BT
from core.memory_sync import mc_batch as MB
from core.memory_sync import versions as V

STATE_FORMAT = "vault-mc-export-state/1"
STATE_NAME = "mc-export-state.json"
LOCK_NAME = "mc-export.lock"
SCRATCH_PREFIX = ".mcx-"
INSTANCE = "production"
PROVIDER = "minimoi-mc"                                     # core/memory_shelf/mc.py PROVIDER: the source key is provider:instance:id
EXPORT_DOC = "EXPORT.json"                                  # scripts/vault/export_mc_conversations.py EXPORT_DOC (kept equal by a test)
EXPORTER = "/app/scripts/vault/export_mc_conversations.py"
SESSION_DIR = "sessions-raw"
BACKOFF = (timedelta(hours=1), timedelta(hours=2), timedelta(hours=4), timedelta(hours=8), timedelta(hours=12))
DEFAULT_GAP_GRACE = timedelta(hours=12)
DEFAULT_MAX_BATCH_BYTES = 256 << 20
EXPORT_RESERVE = 64 << 20                                   # free space wanted before the export streams in (plus the standing reserve)
EXPORT_TIMEOUT = 1800
CURRENT, STALE, MISSING = "current", "stale", "missing"
PRINCIPAL = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@+-]{0,63}$")         # an argv word for docker exec: never an option, never a path
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SENDING, SENT = "sending", "sent"


def _utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(text) -> datetime | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def backoff(attempts: int) -> timedelta:
    """After ``attempts`` sends: 1 h, 2 h, 4 h, 8 h, then 12 h."""
    return BACKOFF[min(max(int(attempts), 1), len(BACKOFF)) - 1]


def source_key(name: str) -> str:
    """``production--c-0123456789ab.json`` -> ``minimoi-mc:production:c-0123456789ab`` (the record's source, as the watcher writes it)."""
    m = MB.FILE.match(name)
    return f"{PROVIDER}:{m.group(1)}:{m.group(2)}"


# ── the exporter ──
def exporter_argv(principal: str, *, container: str = "minimoi-portal", docker_bin: str = "docker") -> list:
    if not PRINCIPAL.match(principal or "") or not CONTAINER.match(container or ""):
        raise BackupError("bad_arguments")
    return [docker_bin, "exec", "-i", container, "python3", "-I", "-B", EXPORTER, "--instance", INSTANCE, "--principal", principal,
            "--stdout-tar"]


def subprocess_runner(argv: list, stdout) -> int:
    """Production: the exporter's stdout goes straight into the scratch file; its stderr (one counts line) is not kept."""
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.DEVNULL, timeout=EXPORT_TIMEOUT).returncode
    except (OSError, subprocess.SubprocessError):
        raise BackupError("export_failed") from None


def read_export(archive: Path, target: Path) -> tuple[dict, dict]:
    """``({name: (path, sha256, size)}, EXPORT.json)`` from the exporter's stream, read strictly (see the module doc); else ``export_failed``.
    Each member is written exactly at its declared size into a new file (exclusive create, no link followed), hashed as written."""
    target.mkdir(mode=0o700)
    files, doc, done = {}, None, False
    try:
        with tarfile.open(archive, "r:") as tar:
            for member in tar:
                name = member.name
                if done or not member.isfile() or "/" in name or name in files:
                    raise BackupError("export_failed")
                if name == EXPORT_DOC:
                    if member.size > MB.MAX_DOC_BYTES:
                        raise BackupError("export_failed")
                    doc = json.loads(tar.extractfile(member).read().decode("utf-8"))
                    done = True
                    continue
                if not MB.name_ok(name, INSTANCE) or member.size > MB.MAX_FILE_BYTES:
                    raise BackupError("export_failed")
                src, h, n = tar.extractfile(member), hashlib.sha256(), 0
                path = target / name
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "wb") as out:
                    while True:
                        block = src.read(1 << 20)
                        if not block:
                            break
                        h.update(block)
                        n += len(block)
                        out.write(block)
                if n != member.size:
                    raise BackupError("export_failed")
                files[name] = (path, h.hexdigest(), n)
    except BackupError:
        raise
    except (tarfile.TarError, OSError, ValueError, UnicodeDecodeError, EOFError):
        raise BackupError("export_failed") from None
    if not done or not isinstance(doc, dict) or doc.get("status") not in ("ok", "partial") or not isinstance(doc.get("counts"), dict) \
            or not all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in doc["counts"].items()):
        raise BackupError("export_failed")
    for name, (_path, sha, size) in files.items():
        if MB.check_file(target, name, sha, size) is not None:
            raise BackupError("export_failed")
    return files, doc


# ── the serving Vault ──
def serving_hashes(vault_root, names) -> dict:
    """``{name: source_hash | None}`` for the given export names, from the serving release (``current`` resolved once, so a switch during the
    read cannot mix two releases). No release installed: every name is ``None``. An unreadable source index is ``vault_unreadable``; a
    record whose main file cannot be read, or names another source, counts as missing."""
    out = {name: None for name in names}
    lay = P.Layout(vault_root)
    try:
        attempt = lay.current_attempt() if lay.current.is_symlink() else None
    except BackupError:                                     # current_link_unexpected: a Vault we cannot read, not an empty one
        raise BackupError("vault_unreadable") from None
    if attempt is None:
        return out
    shelf = lay.releases / attempt / "shelf"
    if not (shelf / "_ledger" / "source_index.json").is_file():
        raise BackupError("vault_unreadable")               # a release without its index (or pruned mid-read) is unknown, never "all missing"
    try:
        index = V.source_index(shelf)
    except V.Damaged:
        raise BackupError("vault_unreadable") from None
    for name in names:
        key = source_key(name)
        entry = index.get(key)
        folder = entry.get("dir") if isinstance(entry, dict) else None
        if not isinstance(folder, str) or not BT.NAME.match(folder):
            continue
        main = shelf / SESSION_DIR / folder / f"{folder}.md"
        try:
            if main.is_symlink() or not main.is_file():
                continue
            meta = V.read_front_matter(main)
        except (V.Damaged, OSError, ValueError):
            continue
        if isinstance(meta, dict) and str(meta.get("source")) == key and V.HEX64.match(str(meta.get("source_hash") or "")):
            out[name] = str(meta["source_hash"])
    return out


def reconcile(exported: dict, held: dict) -> dict:
    """``{name: current | stale | missing}`` for every exported name (``exported``: name -> sha256; ``held``: name -> source_hash | None)."""
    return {n: (MISSING if held.get(n) is None else CURRENT if held[n] == sha else STALE) for n, sha in exported.items()}


# ── state ──
OPTIONAL_MAPS = ("behind_since", "behind_held", "unacked_since", "requeued")


def empty_state() -> dict:
    return {"format": STATE_FORMAT, "acked": {}, "pending": {}, "first_seen": {}, **{k: {} for k in OPTIONAL_MAPS}}


def read_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    if path.is_symlink() or not path.is_file():
        raise BackupError("state_unreadable")
    doc = V.read_json(path)
    try:
        ok = isinstance(doc, dict) and doc.get("format") == STATE_FORMAT
        ok = ok and all(MB.name_ok(n) and V.HEX64.match(str(e["sha256"])) and _parse_utc(e["acked_utc"]) for n, e in doc["acked"].items())
        ok = ok and all(MB.name_ok(n) and V.HEX64.match(str(e["sha256"])) and _parse_utc(e["utc"]) for n, e in doc["first_seen"].items())
        for bid, p in doc["pending"].items():
            ok = ok and bool(B.ATTEMPT.match(bid)) and p["state"] in (SENDING, SENT) and isinstance(p["attempts"], int) \
                and _parse_utc(p["sent_utc"]) is not None and _parse_utc(p["next_retry_utc"]) is not None \
                and all(MB.name_ok(n) and V.HEX64.match(str(s)) for n, s in p["files"].items())
        for key in OPTIONAL_MAPS:                           # absent in a state written before them: empty
            doc.setdefault(key, {})
        ok = ok and all(MB.name_ok(n) and _parse_utc(u) for n, u in doc["behind_since"].items())
        ok = ok and all(MB.name_ok(n) and _parse_utc(u) for n, u in doc["unacked_since"].items())
        ok = ok and all(MB.name_ok(n) and V.HEX64.match(str(s)) for n, s in doc["requeued"].items())
        ok = ok and all(MB.name_ok(n) and (s == "" or V.HEX64.match(str(s))) for n, s in doc["behind_held"].items())
    except (KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise BackupError("state_unreadable")
    return doc


def _private_dir(path: Path, owner: int, code: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise BackupError(code)
    path.mkdir(mode=0o700, exist_ok=True)
    if path.stat().st_uid != owner:
        raise BackupError(code)
    os.chmod(path, 0o700)


def wipe_tree(path: Path) -> bool:
    """Zero and remove every regular file under ``path`` (links are only unlinked), then remove the tree; True when nothing is left."""
    ok = True
    for dirpath, _dirs, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                p.unlink(missing_ok=True)
            elif not B.wipe_file(p):
                ok = False
    return B.remove_tree(path) and ok


class Paths:
    """``<vault>-sync`` (state, lock: root 0700, the frozen sync's own folder) and ``<vault>-work`` (r3's root-only scratch)."""

    def __init__(self, vault_root):
        root = Path(vault_root)
        self.vault = root
        self.sync = root.parent / f"{root.name}-sync"
        self.work = root.parent / f"{root.name}-work"
        self.state = self.sync / STATE_NAME
        self.lock = self.sync / LOCK_NAME


class Admission:
    """One admission for a whole ``vault-mc-export`` run on ``<vault>-sync/mc-export.lock``: the job script takes it BEFORE its status start
    and holds it to its status finish, so the status file always describes the run holding the lock, and a competitor refused here writes
    nothing at all (Codex, 17 Oct; the pattern of ``production.Admission``). Raises ``mc_export_busy`` (or ``sync_state_unsafe``).
    ``run_export(admission=...)`` then does not lock again."""

    def __init__(self, vault_root, *, owner_uid: int = 0):
        self.paths = Paths(vault_root)
        self.owner_uid = owner_uid
        self._lock = None

    def __enter__(self):
        old_umask = os.umask(0o077)
        try:
            _private_dir(self.paths.sync, self.owner_uid, "sync_state_unsafe")
        except OSError:
            raise BackupError("sync_state_unsafe") from None
        finally:
            os.umask(old_umask)
        try:
            self._lock = P._Lock(self.paths.lock).__enter__()
        except BackupError as exc:
            raise BackupError("mc_export_busy" if exc.code == "refresh_busy" else exc.code) from None
        except OSError:                                     # the lock file itself cannot be opened (a link, permissions): a fixed code
            raise BackupError("lock_not_openable") from None
        return self

    def __exit__(self, *exc):
        if self._lock is not None:
            self._lock.__exit__(*exc)
            self._lock = None

    def covers(self, paths: "Paths") -> bool:
        return self._lock is not None and self.paths.lock.resolve() == paths.lock.resolve()


def plan_export(*, vault_root: str) -> dict:
    """Read-only: what the state says (counts only). Never reads the identity, never runs the exporter."""
    paths = Paths(vault_root)
    out = {"running_as_root": os.geteuid() == 0, "state_exists": paths.state.is_file(), "vault_root_exists": paths.vault.is_dir()}
    if paths.state.is_file():
        try:
            st = read_state(paths.state)
            out.update(acked=len(st["acked"]), pending_batches=len(st["pending"]),
                       pending_files=sum(len(p["files"]) for p in st["pending"].values()))
        except BackupError as exc:
            out["state_error"] = exc.code
    return out


# ── one run ──
def run_export(*, dest: str, vault_root: str, recipient: str, principal: str, runner=None, container: str = "minimoi-portal",
               docker_bin: str = "docker", age_bin: str = "age", expect_age: str | None = None, aws_bin: str = "aws", owner_uid: int = 0,
               require_root: bool = True, gap_grace: timedelta = DEFAULT_GAP_GRACE, max_batch_bytes: int = DEFAULT_MAX_BATCH_BYTES,
               run=subprocess.run, usage=None, now=None, put=None, hooks: dict | None = None, admission: Admission | None = None) -> dict:
    """One run; returns counts and ``export`` (``ok`` | ``partial``). Raises BackupError with a fixed code on failure (``mc_export_busy``
    for a held lock). ``runner(argv, stdout) -> exit code`` runs the exporter (default: ``docker exec``). ``now``: a callable returning the
    time. ``put``: the upload callable (tests interrupt it). ``hooks``: fault injection. ``admission``: the caller's held ``Admission``
    for this vault (the job script takes it before its status start); without one the run locks for itself."""
    if require_root and os.geteuid() != 0:
        raise BackupError("root_required")
    if not B.RECIPIENT.match(recipient or ""):
        raise BackupError("recipient_malformed")
    if max_batch_bytes < 1 or gap_grace < timedelta(0):
        raise BackupError("bad_arguments")
    argv = exporter_argv(principal, container=container, docker_bin=docker_bin)
    clock = now or (lambda: datetime.now(timezone.utc))
    hook = lambda name, *args: (hooks or {}).get(name, lambda *a: None)(*args)
    usage = usage or shutil.disk_usage
    old_umask = os.umask(0o077)
    try:
        age_ver = B.check_age(age_bin, expect_age)
        d = B.Dest(dest, aws_bin=aws_bin, run=run)
        paths = Paths(vault_root)
        _private_dir(paths.sync, owner_uid, "sync_state_unsafe")
        _private_dir(paths.work, owner_uid, "work_unsafe")
        if admission is None:
            lock = Admission(vault_root, owner_uid=owner_uid).__enter__()     # raises mc_export_busy
        elif not admission.covers(paths):
            raise BackupError("admission_mismatch")
        else:
            lock = None                                                         # the caller holds it for the whole run
        try:
            for p in paths.work.iterdir():                  # a killed run's plaintext: wiped before anything else
                if p.name.startswith(SCRATCH_PREFIX) and p.is_dir() and not p.is_symlink() and not wipe_tree(p):
                    raise BackupError("cleanup_failed")
            state = read_state(paths.state)
            B.ensure_space(paths.work, EXPORT_RESERVE, usage=usage)
            scratch = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=paths.work))
            try:
                os.chmod(scratch, 0o700)
                return _run_locked(d, paths, state, scratch, argv=argv, runner=runner or subprocess_runner, recipient=recipient,
                                   age_bin=age_bin, age_ver=age_ver, gap_grace=gap_grace, max_batch_bytes=max_batch_bytes, usage=usage, clock=clock,
                                   put=put, hook=hook)
            finally:
                if not wipe_tree(scratch):
                    raise BackupError("cleanup_failed")
        finally:
            if lock is not None:
                lock.__exit__(None, None, None)
    finally:
        os.umask(old_umask)


def _new_batch_id(state: dict, now: datetime) -> str:
    """Newer than every pending batch: the Mac orders placements by batch id (an older batch never overwrites a newer one's bytes)."""
    t = now.astimezone(timezone.utc).replace(microsecond=0)
    if state["pending"]:
        newest = datetime.strptime(max(state["pending"])[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if t <= newest:
            t = newest + timedelta(seconds=1)
    return B.new_attempt_id(t)


def _track_unacked(state: dict) -> None:
    """``unacked_since`` follows the pending set: a name entering it keeps the time of the oldest batch that carries it (a re-send in a new
    batch does not reset it), and a name that left it (placed, or its bytes acked by another batch) is dropped."""
    unacked, carried = state["unacked_since"], {}
    for bid in sorted(state["pending"]):
        for name in state["pending"][bid]["files"]:
            carried.setdefault(name, state["pending"][bid]["sent_utc"])
    for name in [n for n in unacked if n not in carried]:
        del unacked[name]
    for name, utc in carried.items():
        unacked.setdefault(name, utc)


def _run_locked(d, paths, state, scratch, *, argv, runner, recipient, age_bin, age_ver, gap_grace, max_batch_bytes, usage, clock, put, hook) -> dict:
    now = clock()
    # 1. Export: stdout straight into a 0600 file in the 0700 scratch.
    tar_path = scratch / "export.tar"
    fd = os.open(str(tar_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            code = runner(list(argv), out)
    except BackupError:
        raise
    except Exception:                                                       # noqa: BLE001 - the runner's own failure, whatever it is
        raise BackupError("export_failed") from None
    hook("exported", tar_path)
    if code not in (0, 1):
        raise BackupError("export_failed")

    # 2. Strict read; the stream itself is wiped as soon as its files are out.
    B.ensure_space(paths.work, tar_path.stat().st_size + P.SLACK, usage=usage)
    files, doc = read_export(tar_path, scratch / "x")
    if not B.wipe_file(tar_path):
        raise BackupError("cleanup_failed")
    exported = {name: sha for name, (_p, sha, _z) in files.items()}
    partial = code == 1 or doc["status"] == "partial"
    hook("extracted", scratch / "x")

    # 3. Acknowledgements for every pending batch. A download that fails is an error (nothing below runs, the state is unchanged); an
    #    ack that does not parse is "no ack" for that batch only: counted, and its files go out again in a new batch on the backoff.
    acked, pending, first_seen = state["acked"], state["pending"], state["first_seen"]
    behind_since, unacked_since, requeued = state["behind_since"], state["unacked_since"], state["requeued"]
    counts = {"acked": 0, "rejected": 0, "superseded": 0, "ack_malformed": 0}
    for bid in sorted(pending):
        try:
            ack = MB.read_ack(d, bid, scratch)
        except BackupError as exc:
            if exc.code != "ack_malformed":
                raise
            counts["ack_malformed"] += 1
            continue
        if ack is None:
            continue
        batch = pending[bid]["files"]
        for name, sha, status in ack["files"]:
            if batch.get(name) != sha:
                continue                                    # not what this batch carried: never trusted for anything
            if status == MB.PLACED:
                acked[name] = {"sha256": sha, "acked_utc": _utc(now)}       # EC2's clock: the grace below compares with EC2's clock
                del batch[name]
                unacked_since.pop(name, None)               # the Mac answered: a re-send of newer bytes starts a fresh clock (review N2)
                counts["acked"] += 1
            elif status == MB.SUPERSEDED:
                del batch[name]
                counts["superseded"] += 1
            else:
                counts["rejected"] += 1                     # stays pending; re-sent when the batch's retry falls due
    gone = 0
    for bid in list(pending):                               # bytes already placed need no other batch; an empty batch is done
        files_of = pending[bid]["files"]
        for name in [n for n, s in files_of.items() if acked.get(n, {}).get("sha256") == s]:
            del files_of[name]
        if not partial:
            # A complete export that no longer carries a pending name (the conversation was removed, or is no longer the owner's kept
            # on-record conversation) retires it: there is nothing left to deliver (review N1). A partial export keeps it.
            for name in [n for n in files_of if n not in exported]:
                del files_of[name]
                gone += 1
        if not files_of:
            del pending[bid]

    # 4. Reconciliation and gap repair, against the serving Vault.
    for name, sha in exported.items():
        if first_seen.get(name, {}).get("sha256") != sha:
            first_seen[name] = {"sha256": sha, "utc": _utc(now)}
    for name, sha in exported.items():
        if name in requeued and requeued[name] != sha:
            del requeued[name]                              # new bytes: they get their own one re-queue
    behind_held = state["behind_held"]
    if not partial:                                         # a conversation production no longer exports cannot be behind
        for name in [n for n in behind_since if n not in exported]:
            behind_since.pop(name, None)
            behind_held.pop(name, None)
    try:
        held = serving_hashes(paths.vault, sorted(exported))
        where = reconcile(exported, held)
    except BackupError as exc:
        if exc.code != "vault_unreadable":
            raise
        where = None                                        # unknown this run: no gap repair, no behind_since change; sending goes on
    gap = after_repair = 0
    if where is not None:
        for name in exported:
            if where[name] == CURRENT:
                behind_since.pop(name, None)
                behind_held.pop(name, None)
            elif name not in behind_since or behind_held.get(name) != (held[name] or ""):
                # First behind, or the Vault moved since (a healthy Mac one cycle behind a busy conversation): the clock (re)starts.
                # Production's bytes changing does NOT restart it, so an active conversation cannot hide a dead Mac.
                behind_since[name] = _utc(now)
                behind_held[name] = held[name] or ""
        for name, sha in sorted(exported.items()):
            a = acked.get(name)
            if not (a and a["sha256"] == sha and where[name] != CURRENT and now - _parse_utc(a["acked_utc"]) > gap_grace):
                continue
            if requeued.get(name) == sha:
                after_repair += 1                           # re-sent once already: identical bytes cannot repair it, so never again
                continue
            del acked[name]
            requeued[name] = sha
            gap += 1

    # 5. What to send: retry batches' names first (oldest first), then the changed names.
    due = [bid for bid in sorted(pending) if pending[bid]["state"] == SENDING or now >= _parse_utc(pending[bid]["next_retry_utc"])]
    prior: dict = {}                                        # name -> attempts already spent on it
    order: list = []
    for bid in due:
        for name in sorted(pending[bid]["files"]):
            if name in exported and acked.get(name, {}).get("sha256") != exported[name]:
                prior[name] = max(prior.get(name, 0), int(pending[bid]["attempts"]))
                if name not in order:
                    order.append(name)
    in_flight = {(n, s) for p in pending.values() for n, s in p["files"].items()}
    changed = [n for n in sorted(exported) if acked.get(n, {}).get("sha256") != exported[n] and (n, exported[n]) not in in_flight]
    order += [n for n in changed if n not in order]
    chosen, size = [], 0
    for name in order:
        z = files[name][2]
        if chosen and size + z > max_batch_bytes:
            break                                           # the rest waits for the next run; nothing is dropped
        chosen.append(name)
        size += z

    # 6. One batch: encrypted, recorded "sending" before the upload, "sent" after it.
    sent_batch = None
    if chosen:
        bid = _new_batch_id(state, now)
        bdir = scratch / "batch"
        os.mkdir(bdir, 0o700)
        plain, cipher = bdir / "batch.tar.gz", bdir / MB.BATCH_OBJECT
        B.ensure_space(paths.work, 2 * size + (len(chosen) + 2) * 4 * B.BLOCK + P.SLACK, usage=usage)
        res = MB.write_tar([(n, files[n][0], files[n][1], files[n][2]) for n in chosen], bid, INSTANCE, plain)
        B.encrypt(age_bin, recipient, plain, cipher)
        plain_sha, plain_bytes = B.sha256_file(plain), plain.stat().st_size
        if not B.wipe_file(plain):
            raise BackupError("cleanup_failed")
        m = MB.manifest(bid, INSTANCE, res["files"], created=now, cipher=cipher, plain_sha=plain_sha, plain_bytes=plain_bytes,
                        age_version=age_ver, recipient=recipient)
        mp, cp = bdir / B.MANIFEST, bdir / B.COMPLETE
        mp.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
        cp.write_text(json.dumps(MB.complete(m, B.sha256_file(mp)), indent=2, sort_keys=True) + "\n")
        attempts = max([prior.get(n, 0) for n in chosen] or [0]) + 1
        for other in list(pending):                         # the new batch carries these names now
            for name in chosen:
                pending[other]["files"].pop(name, None)
            if not pending[other]["files"]:
                del pending[other]
        pending[bid] = {"files": {n: exported[n] for n in chosen}, "sent_utc": _utc(now), "attempts": attempts,
                        "next_retry_utc": _utc(now + backoff(attempts)), "state": SENDING}
        _track_unacked(state)
        V.write_json_atomic(paths.state, state)
        hook("before_upload", bid)
        MB.upload(d, bid, cipher, mp, cp, put=put)          # COMPLETE last; a failure leaves "sending": the next run re-sends
        pending[bid]["state"] = SENT
        sent_batch = bid
    _track_unacked(state)
    V.write_json_atomic(paths.state, state)

    # 7. Counts (the status): conversation ids, hashes and times never leave as anything but counts.
    grace_s = gap_grace.total_seconds()
    ages = [(now - _parse_utc(behind_since[n])).total_seconds() for n in exported if n in behind_since]
    lag = int(max(ages)) if ages else 0
    waits = [(now - _parse_utc(u)).total_seconds() for u in unacked_since.values()]
    tally = (lambda v: sum(1 for x in where.values() if x == v)) if where is not None else (lambda v: None)
    ec = doc["counts"]
    return {"export": "partial" if partial else "ok", "conversations": len(exported), "changed": len(changed), "sent": len(chosen),
            "acked": counts["acked"], "rejected": counts["rejected"], "superseded": counts["superseded"],
            "pending_files": sum(len(p["files"]) for p in pending.values()), "pending_batches": len(pending), "gap_requeued": gap,
            "behind_after_repair": after_repair, "ack_malformed": counts["ack_malformed"], "gone_from_export": gone,
            "vault": "ok" if where is not None else "unreadable",
            "in_vault_current": tally(CURRENT), "in_vault_stale": tally(STALE), "missing_in_vault": tally(MISSING), "lag_oldest_s": lag,
            "behind_past_grace": sum(1 for a in ages if a > grace_s),
            "oldest_unacked_s": int(max(waits)) if waits else 0, "unacked_past_grace": sum(1 for x in waits if x > grace_s),
            "other_principal": int(ec.get("other_principal", 0)), "off_record": int(ec.get("off_record", 0)) + int(ec.get("off_record_note", 0)),
            "batch": sent_batch}
