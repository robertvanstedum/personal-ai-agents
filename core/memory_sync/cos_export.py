"""``vault-cos-export``: the EC2 side of the production CoS capture path (11 Nov 2026, #509). Sends production's changed CoS conversations
to the Mac, encrypted, and checks that every one sent comes back in the serving Vault. Runs as root on the host (cron, every 2 h), like
``vault-mc-export``; the job script is ``scripts/vault/vault_cos_export.py``.

**The same run as MC's.** Steps 2-7 of ``core/memory_sync/mc_export.py`` (strict read of the export stream, acknowledgements, gap repair
against the serving Vault's ``shelf/_ledger/source_index.json``, changed-only selection with retries on the backoff, one age-encrypted
batch with COMPLETE last, wipe) are shared code (``mc_export.run_family_export``) with this module's ``family``: batch family
``cos_batch.COS``, file instance = the history's environment (``prod`` in production), source keys ``minimoi-cos:<environment>:<id>``,
state ``<vault>-sync/cos-export-state.json``, lock ``<vault>-sync/cos-export.lock`` (busy: ``cos_export_busy``), scratch
``<vault>-work/.cosx-*``. MC's state, lock and scratch are never touched.

**Step 1, the exporter, is this module's own** (``local_runner``): it reads the canonical CoS history DIRECTLY, read-only, from the host
path cos-scheduler writes (production ``/opt/minimoi/cos/conversations/history``; layout in ``domains/cos/history.py``), and writes the
same stream shape MC's portal exporter writes (``<environment>--<id>.json`` files, then ``EXPORT.json`` LAST) into the 0600 scratch file.

Why read the folder rather than ``docker exec`` into cos-scheduler: it is the narrowest access that works. Per conversation the job opens two
files ``O_RDONLY | O_NOFOLLOW`` (the metadata and the turn journal) and nothing else; it never opens the writer's ``.lock`` (that is
``O_RDWR``), never writes in the folder, and needs no container. An exec would run code inside the one container that holds the Docker
socket and the folder read-write, and would make the export depend on that container being up and on its Python. The job is already root
on the host for its state and the age encryption, so a direct read adds no privilege.

**What is sent (owner decision D-G1, 11 Nov):** every owner CoS conversation of this environment EXCEPT private ones, fail closed
(``select``). A conversation travels only when its metadata reads as the CoS conversation schema, names its own id, says ``"private":
false`` exactly, and names this environment and the ``personal`` data scope. Counted, never sent:

  ``private``            ``private`` is anything but ``false`` (true, null, absent, a string): privacy not proven is private
  ``other_environment``  another environment's conversation (a copied folder)
  ``other_scope``        a data scope other than ``personal``
  ``unreadable``         metadata or journal that cannot be read or parsed, or is not UTF-8 (privacy cannot be determined: not sent)
  ``unstable``           a file that changed during the read, or a journal whose last line is still being written
  ``too_large``          an export file over ``MAX_FILE_BYTES``
  ``bad_name``           a file in ``conversations/`` that is not ``<conversation id>.json``

The last four make the export ``partial`` (exit 1): the run goes on with what was read, and nothing pending is retired for a conversation
this run could not read. Private, other-environment and other-scope conversations are deliberate exclusions, not partial: a pending name
that turned private leaves the pending set on a complete export (``gone_from_export``), so it is never re-sent.

Each capture still waits for the owner's approval on the Mac (the ``minimoi-cos`` source, ``moi approve-source minimoi-cos``), exactly
like MC's. What leaves the host in plain text is conversation ids, hashes, sizes, counts and fixed codes: never a title or a turn.
Standard library plus the EC2 tools bundle (``memory_backup``, ``memory_sync``).
"""
from __future__ import annotations

import io
import json
import os
import stat
import tarfile
from datetime import timedelta
from pathlib import Path

from core.memory_backup.backup import BackupError
from core.memory_sync import cos_batch as CB
from core.memory_sync import mc_export as X

STATE_FORMAT = "vault-cos-export-state/1"
STATE_NAME = "cos-export-state.json"
LOCK_NAME = "cos-export.lock"
SCRATCH_PREFIX = ".cosx-"
PROVIDER = "minimoi-cos"                                    # core/memory_shelf/cos.py PROVIDER
EXPORT_DOC = X.EXPORT_DOC
DEFAULT_HISTORY = "/opt/minimoi/cos/conversations/history"
MAX_CONVERSATIONS = 20000
TURN_SCHEMA = "minimoi.cos.turn/1"
COUNTS = ("exported", "private", "other_environment", "other_scope", "unreadable", "unstable", "too_large", "bad_name")
PARTIAL_COUNTS = ("unreadable", "unstable", "too_large", "bad_name")
EXTRA_COUNTS = ("private", "other_environment", "other_scope", "unreadable", "unstable", "too_large")


def _extra(ec: dict) -> dict:
    return {k: int(ec.get(k, 0)) for k in EXTRA_COUNTS}


def family(environment: str) -> X.ExportFamily:
    if environment not in CB.ENVIRONMENTS:
        raise BackupError("bad_arguments")
    return X.ExportFamily(batch=CB.COS, instance=environment, provider=PROVIDER, state_format=STATE_FORMAT, state_name=STATE_NAME,
                          lock_name=LOCK_NAME, scratch_prefix=SCRATCH_PREFIX, busy="cos_export_busy", extra_counts=_extra)


# ── reading the history, read-only ──
class _Skip(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def snapshot(path: Path, limit: int) -> bytes:
    """Bounded, regular, non-symlink read with a replacement check (``core/memory_shelf/cos.py`` ``snapshot``, stdlib-only here).
    ``FileNotFoundError`` passes through; anything else is ``_Skip``."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError:
        raise _Skip("unreadable") from None
    try:
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise _Skip("unreadable")
            if before.st_size > limit:
                raise _Skip("too_large")
            raw = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        current = path.lstat()
    except OSError:
        raise _Skip("unreadable") from None
    sig = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) > limit:
        raise _Skip("too_large")
    if sig(before) != sig(after) or sig(after) != sig(current):
        raise _Skip("unstable")
    return raw


def check_root(history_root) -> Path:
    """The history folder and its two subfolders: real folders, never links. ``history_unsafe`` otherwise (the export fails)."""
    root = Path(history_root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise BackupError("history_unsafe")
    for name in ("conversations", "turns"):
        sub = root / name
        if sub.is_symlink() or (sub.exists() and not sub.is_dir()):
            raise BackupError("history_unsafe")
    if not (root / "conversations").is_dir():
        raise BackupError("history_unsafe")
    return root


def _one(root: Path, cid: str, environment: str) -> bytes:
    """One conversation's export file bytes, or ``_Skip`` with the reason it is not sent."""
    meta_path = root / "conversations" / f"{cid}.json"
    try:
        meta_raw = snapshot(meta_path, CB.META_MAX)
    except FileNotFoundError:
        raise _Skip("unstable") from None                   # listed, then gone
    try:
        meta = json.loads(meta_raw)
    except ValueError:
        raise _Skip("unreadable") from None
    if not isinstance(meta, dict) or meta.get("schema") != CB.CONVERSATION_SCHEMA or meta.get("conversation_id") != cid:
        raise _Skip("unreadable")                           # privacy cannot be determined: fail closed
    if meta.get("private") is not False:
        raise _Skip("private")
    if meta.get("environment") != environment:
        raise _Skip("other_environment")
    if meta.get("data_scope") != "personal":
        raise _Skip("other_scope")
    try:
        raw = snapshot(root / "turns" / f"{cid}.jsonl", CB.MAX_FILE_BYTES)
    except FileNotFoundError:
        raw = b""                                           # a new conversation has metadata before its first turn
    if raw and not raw.endswith(b"\n"):
        raise _Skip("unstable")                             # the writer is mid-append; next run
    try:
        if snapshot(meta_path, CB.META_MAX) != meta_raw:
            raise _Skip("unstable")
    except FileNotFoundError:
        raise _Skip("unstable") from None
    try:
        for line in raw.splitlines():
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("schema") != TURN_SCHEMA or row.get("conversation_id") != cid:
                raise _Skip("unreadable")
        doc = CB.export_doc(environment, cid, meta_raw, raw)
    except (ValueError, UnicodeDecodeError):
        raise _Skip("unreadable") from None
    if len(doc) > CB.MAX_FILE_BYTES:
        raise _Skip("too_large")
    return doc


def select(history_root, environment: str):
    """Yield ``(name, bytes)`` for every conversation that may travel; ``counts`` (a dict filled as it goes) says what was not sent and
    why. Raises ``history_unsafe`` / ``history_too_large`` before yielding anything."""
    root = check_root(history_root)
    counts = dict.fromkeys(COUNTS, 0)
    with os.scandir(root / "conversations") as it:
        entries = sorted((e.name, e.is_file(follow_symlinks=False)) for e in it)
    if len(entries) > MAX_CONVERSATIONS:
        raise BackupError("history_too_large")

    def gen():
        for fname, regular in entries:
            if fname.startswith("."):
                continue                                    # the writer's own temp files
            cid = fname[:-5] if fname.endswith(".json") else ""
            if not regular or not CB.CID.match(cid):
                counts["bad_name"] += 1
                continue
            try:
                doc = _one(root, cid, environment)
            except _Skip as skip:
                counts[skip.code] += 1
                continue
            counts["exported"] += 1
            yield f"{environment}--{cid}.json", doc
    return gen(), counts


def write_stream(out, history_root, environment: str) -> int:
    """The exporter's stream into ``out`` (an open binary file): each file, then ``EXPORT.json`` last. Returns 0 (ok) or 1 (partial).
    Holds one export file in memory at a time."""
    files, counts = select(history_root, environment)
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for name, doc in files:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(doc), 0o600, 0
            tar.addfile(info, io.BytesIO(doc))
        partial = any(counts[k] for k in PARTIAL_COUNTS)
        receipt = json.dumps({"status": "partial" if partial else "ok", "counts": counts}, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo(EXPORT_DOC)
        info.size, info.mode, info.mtime = len(receipt), 0o600, 0
        tar.addfile(info, io.BytesIO(receipt))
    return 1 if partial else 0


def local_runner(history_root, environment: str):
    """``runner(argv, stdout) -> exit code`` for ``run_family_export``: 0 ok, 1 partial, 2 a history that cannot be read safely."""
    def runner(_argv, stdout) -> int:
        try:
            return write_stream(stdout, history_root, environment)
        except BackupError:
            return 2
        except OSError:
            return 2
    return runner


def exporter_argv(history_root, environment: str) -> list:
    """What the run records as its exporter (no process is started: ``local_runner`` reads in-process)."""
    return ["vault-cos-export", "--history", str(history_root), "--environment", environment]


# ── one run ──
def run_export(*, dest: str, vault_root: str, recipient: str, history_root: str = DEFAULT_HISTORY, environment: str = "prod",
               runner=None, age_bin: str = "age", expect_age: str | None = None, aws_bin: str = "aws", owner_uid: int = 0,
               require_root: bool = True, gap_grace: timedelta = X.DEFAULT_GAP_GRACE, max_batch_bytes: int = X.DEFAULT_MAX_BATCH_BYTES,
               run=None, usage=None, now=None, put=None, hooks: dict | None = None, admission: X.Admission | None = None) -> dict:
    """One ``vault-cos-export`` run; the result is MC's (``mc_export.run_family_export``) with this exporter's counts (``EXTRA_COUNTS``)
    in place of MC's ``other_principal`` / ``off_record``. Raises BackupError with a fixed code (``cos_export_busy`` for a held lock)."""
    fam = family(environment)
    if not Path(str(history_root)).is_absolute():
        raise BackupError("bad_arguments")
    kw = {} if run is None else {"run": run}
    return X.run_family_export(fam=fam, argv=exporter_argv(history_root, environment), runner=runner or local_runner(history_root, environment),
                               dest=dest, vault_root=vault_root, recipient=recipient, age_bin=age_bin, expect_age=expect_age, aws_bin=aws_bin,
                               owner_uid=owner_uid, require_root=require_root, gap_grace=gap_grace, max_batch_bytes=max_batch_bytes,
                               usage=usage, now=now, put=put, hooks=hooks, admission=admission, **kw)


def admission(vault_root, *, environment: str = "prod", owner_uid: int = 0) -> X.Admission:
    return X.Admission(vault_root, owner_uid=owner_uid, fam=family(environment))


def plan_export(*, vault_root: str, history_root: str = DEFAULT_HISTORY, environment: str = "prod") -> dict:
    """Read-only: the state's counts and whether the history folder is there (how many metadata files; nothing is opened)."""
    out = X.plan_export(vault_root=vault_root, fam=family(environment))
    try:
        root = check_root(history_root)
        with os.scandir(root / "conversations") as it:
            out["history_conversations"] = sum(1 for e in it if e.name.endswith(".json") and not e.name.startswith("."))
        out["history"] = "ok"
    except (BackupError, OSError) as exc:
        out["history"] = getattr(exc, "code", "unreadable")
    return out
