"""``vault-mc-pull``: production's MC conversations into the Mac's ``minimoi-mc`` watch folder, then the approval-gated capture (17 Oct 2026).

The reverse leg of the production MC capture path. EC2 ``vault-mc-export`` writes encrypted batches of changed export files under
``<dest>/sync/mc-export/<batch_id>/`` (contract: ``core/memory_sync/mc_batch.py``). This job verifies and decrypts each batch, places the
files in the watch folder, acknowledges them, and runs the ``minimoi-mc`` watcher pass so the conversations become ordinary records,
which ``vault-sync``/``vault-apply`` then carry back to production as versions.

**A sibling of ``vault-sync``, never part of it.** ``vault-sync`` holds only the PUBLIC recipient. This job alone reads the private identity
(``B.fetch_identity(spec)``: ``file:`` | ``keychain:`` | ``ssm:`` | ``cmd:``), once per run and only when a batch needs decrypting; the key
text lives in memory and, for the one ``age -d`` call, in a 0600 file that ``B.decrypt`` wipes. It is never logged, never in status.

launchd runs it every 2 h while the Mac is awake (``StartInterval`` 7200 plus ``RunAtLoad``), from the pinned runtime. One run, under the
shared ``<jobs_root>/vault-batch.lock`` taken WITHOUT waiting and FIRST, held across the job-status start, the work and the finish (busy:
``pull=busy`` returned, state None, exit 0, and nothing written, not even a status, so the active run's receipt stays its own):

  1. **List.** ``mc_batch.complete_batches`` minus ``pulled.json``, oldest first. An orphan (no COMPLETE: an upload killed part way) is
     never listed, so it is never read.
  2. **Each batch.** ``fetch_chain`` (instance ``production``), the space check (``ensure_space``: ciphertext + plaintext + the extracted
     files + their copies in the watch folder, plus slack, above the reserve), ``fetch_ciphertext``, ``B.decrypt`` into ``work/``, the
     plaintext's sha256 and size against the manifest, ``mc_batch.extract`` and ``check_file`` per file.
       * An **integrity** failure (the chain, the ciphertext, decryption, the plaintext, the archive, ``BATCH.json``) acknowledges every
         manifest file ``rejected:<code>`` and marks the batch pulled: the same bytes would fail the same way again, so EC2 re-sends them
         in a NEW batch on its backoff.
       * A **transient** failure (download, list, space, identity, age, the ack upload, a local write, including
         ``extract_write_failed``: a full disk or a permission error while extracting) stops the run, acknowledges nothing for that batch
         and leaves it unpulled: the next run tries again.
  3. **Place** each verified file, atomically and idempotently. ``placed.json[name].batch_id`` newer than this batch: ``superseded`` (an
     older batch never overwrites newer bytes). The watch-folder file already holds these exact bytes: ``placed`` without a rewrite
     (counted ``unchanged``), so the watcher's size + mtime check skips it. Otherwise a temp file IN the watch folder (a dot name the
     watcher ignores), fsync, rename over the name, fsync the folder: ``placed``. A run killed mid-place leaves that temp file; the next
     run's start sweeps exactly that pattern (``.<name>.tmp-<pid>-<8 hex>``) from the watch folder, counted ``temp_swept``. A file that fails its own check is ``rejected:<code>``
     and the others still place.
  4. **Acknowledge, only after every placement is durable:** ``sync/mc-export-acks/<batch_id>.json`` (``mc_batch.ack_doc``, replaced
     whole), then ``placed.json``, then ``pulled.json`` (``V.write_json_atomic``). A crash before the ack means the next run pulls the
     batch again, finds identical bytes in place and acknowledges. ``placed.json`` is written before ``pulled.json`` so a crash between
     the two re-pulls (and re-finds the bytes) rather than forgetting which batch placed a name.
  5. **Capture.** When anything was placed this run, or the watch folder holds files the watcher has not captured at their current size
     and mtime: ``core.memory_shelf.config.load(MOI_CONFIG)``, ``Shelf(...)``, ``watchers.run_source(shelf, cfg.sources["minimoi-mc"])``.
     The approval gate is ``run_source``'s own (a missing or stale approval is ``capture=not_approved``; a source that is not configured is
     ``capture=not_configured``; a source rooted elsewhere than the watch folder is ``capture=source_root_mismatch``): all warn, and the
     files wait in the folder for the next run. ``MOI_BATCH_DEADLINE_EPOCH`` is never set: this is a light daytime job, not a window job.

State (``~/minimoi-staging/.vault-mc-pull/``, 0700, outside ``data/``, which staging mounts into containers): ``pulled.json``
``{batch_id: {"pulled_utc", "result"}}``, ``placed.json`` ``{name: {"sha256", "batch_id"}}`` and ``work/`` (plaintext scratch, wiped at the
start and end of every run and after each batch). Names are conversation ids, never titles: nothing in plain text outside the watch
folder (status, state, acknowledgement) carries a title or any conversation text.

Job status ``vault-mc-pull``: ``pull`` (ok | busy | a fixed code), ``batches``, ``placed``, ``unchanged``, ``superseded``, ``rejected``,
``capture``, ``captured``, ``editions_added``, ``temp_swept``, ``oldest_unpulled_age_s`` (from the batch id time of the oldest COMPLETE batch still not
pulled; 0 when none). ``warn`` on rejections, a capture that is not ok, or short space; ``failed`` with a Telegram alert on errors.
``last_success_at`` advances on every run whose pull finished ``ok`` (a capture waiting for approval is yellow, not missed).
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import shutil
import tarfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from core.jobs import alert, registry, schedule, status
from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_sync import mc_batch as MB
from core.memory_sync import versions as V

JOB_ID = "vault-mc-pull"
INSTANCE = "production"
SOURCE = "minimoi-mc"
LOCK_NAME = "vault-batch.lock"
PULLED_NAME, PLACED_NAME, WORK_NAME = "pulled.json", "placed.json", "work"
DEFAULT_WATCH = "~/minimoi-staging/mc-export"
DEFAULT_STATE = "~/minimoi-staging/.vault-mc-pull"
SLACK = 16 << 20
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
# Same bytes, same failure: acknowledge rejected and move on (EC2 re-sends in a new batch). Everything else stops the run unacknowledged.
INTEGRITY = frozenset({"complete_unreadable", "manifest_unreadable", "manifest_mismatch", "manifest_format", "ciphertext_mismatch",
                       "decrypt_failed", "plaintext_mismatch", "archive_unsafe", "batch_doc_unreadable", "batch_doc_mismatch"})
WARN_CODES = ("insufficient_space",)
CAPTURE_WARN = ("not_configured", "not_approved", "source_root_mismatch")
COUNTERS = ("batches", "placed", "unchanged", "superseded", "rejected", "captured", "editions_added", "oldest_unpulled_age_s",
            "temp_swept")
# ``place``'s own temp name, exactly: ``.<export name>.tmp-<pid>-<8 hex>``. Only these are ever swept from the watch folder.
TEMP = re.compile(r"^\.(" + MB.FILE.pattern[1:-1] + r")\.tmp-[0-9]+-[0-9a-f]{8}$")
IDENTITY_KINDS = ("file", "keychain", "ssm", "cmd")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return status.iso(moment)


def identity_spec_ok(spec) -> bool:
    """A spec names WHERE the identity is (``file:`` | ``keychain:`` | ``ssm:`` | ``cmd:``); it is never the key text itself."""
    if not isinstance(spec, str) or not 3 <= len(spec) <= 512 or "\n" in spec or "\r" in spec or "AGE-SECRET-KEY" in spec.upper():
        return False
    kind, sep, rest = spec.partition(":")
    if not sep or kind not in IDENTITY_KINDS or not rest.strip() or rest[0].isspace():
        return False
    return kind != "file" or Path(rest).is_absolute()      # fetch_identity reads the path as given (launchd expands no ~)


def batch_time(batch_id: str) -> datetime | None:
    try:
        return datetime.strptime(batch_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def job_definition(path=None):
    try:
        job = registry.load(path).get(JOB_ID)
        if job:
            return job
    except registry.RegistryError:
        pass
    return registry.parse({"schema_version": 1, "jobs": [{
        "id": JOB_ID, "name": "Vault MC pull from production (Mac)", "host": "mac",
        "schedule": {"text": "every 2 h while awake", "kind": "interval", "every_s": 7200},
        "expected_every_s": 7200, "missed_after_s": 21600, "stuck_after_s": 3600,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_sync/mc_pull.py"}]}).jobs[0]


# ── state ──
def load_pulled(path: Path) -> dict:
    """``{batch_id: {"pulled_utc", "result"}}``; none yet is empty. A file that exists and does not parse is ``pulled_unreadable`` (failed):
    starting over would only re-pull (placements are idempotent), but a broken state file is a fault worth seeing."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return {}
    doc = None if path.is_symlink() else V.read_json(path)
    if not isinstance(doc, dict) or not all(B.ATTEMPT.match(str(k)) and isinstance(v, dict) for k, v in doc.items()):
        raise BackupError("pulled_unreadable")
    return doc


def load_placed(path: Path) -> dict:
    """``{name: {"sha256", "batch_id"}}``; none yet is empty. Unreadable is ``placed_unreadable`` (failed): forgetting which batch placed a
    name would let an older batch overwrite newer bytes."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return {}
    doc = None if path.is_symlink() else V.read_json(path)
    try:
        ok = isinstance(doc, dict) and all(MB.name_ok(k) and isinstance(v, dict) and V.HEX64.match(str(v.get("sha256", "")))
                                           and B.ATTEMPT.match(str(v.get("batch_id", ""))) for k, v in doc.items())
    except TypeError:
        ok = False
    if not ok:
        raise BackupError("placed_unreadable")
    return doc


# ── placing one file ──
def _fsync_dir(folder: Path) -> None:
    fd = os.open(str(folder), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def same_bytes(target: Path, sha: str, size: int) -> bool:
    try:
        if target.is_symlink() or not target.is_file() or target.stat().st_size != size:
            return False
        return V.sha256_file(target) == sha
    except OSError:
        return False


def place(src: Path, watch: Path, name: str, sha: str, size: int) -> str:
    """``unchanged`` when ``watch/name`` already holds exactly these bytes (no rewrite: its size and mtime stay, so the watcher skips it),
    else ``placed`` after temp file, fsync, rename and folder fsync. The bytes written are hashed as they are copied and must equal ``sha``.
    Raises ``BackupError("place_failed")`` on any local write problem (transient: nothing is acknowledged)."""
    target = watch / name
    if same_bytes(target, sha, size):
        # A prior run may have renamed these bytes and died before directory fsync.
        # Equal bytes alone do not prove durable placement; replay the barrier before ACK.
        try:
            fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            _fsync_dir(watch)
        except OSError:
            raise BackupError("place_failed") from None
        return "unchanged"
    tmp = watch / f".{name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"         # == TEMP: a kill here leaves it for sweep_temps
    try:
        h, n = hashlib.sha256(), 0
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
                n += len(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if n != size or h.hexdigest() != sha:
            raise BackupError("place_failed")
        os.replace(tmp, target)                         # replaces a stray link itself, never what it points at
        _fsync_dir(watch)
    except OSError:
        raise BackupError("place_failed") from None
    finally:
        if tmp.exists() or tmp.is_symlink():
            try:
                tmp.unlink()
            except OSError:
                pass
    return "placed"


def sweep_temps(watch: Path) -> int:
    """Remove ``place``'s temp files left in the watch folder by a run killed mid-place (plain text that would otherwise stay forever).
    Only names matching ``TEMP`` exactly, only files or links (a link itself, never its target), never a folder or any other name. Runs at
    the start of every run under the batch lock, so no placement is in flight. Returns how many were removed."""
    with os.scandir(watch) as it:
        stale = [e.name for e in it if TEMP.fullmatch(e.name) and not e.is_dir(follow_symlinks=False)]
    n = 0
    for name in stale:
        try:
            os.unlink(os.path.join(watch, name))
            n += 1
        except FileNotFoundError:
            pass
    return n


# ── what the watcher has not captured yet ──
def waiting_files(shelf, cfg) -> int:
    """Export files in the watch folder whose current size and mtime the ``minimoi-mc`` watcher has not recorded as captured: a placement
    from an earlier run whose capture did not happen (not approved yet, a crash, a busy shelf). Read-only."""
    from core.memory_shelf import bundle as bundles
    from core.memory_shelf import codes, fsio, watchers
    doc = fsio.read_json(Path(shelf.status_dir) / "watch-state" / f"{cfg.name}.json")
    state = doc.get("files", {}) if isinstance(doc, dict) else {}
    n = 0
    for cand in watchers.discover(cfg):
        prev = state.get(bundles.ledger_key_for(cfg.name, watchers.logical_rel(cand.rel))) or {}
        if not (prev.get("outcome") in codes.OK_OUTCOMES and prev.get("size") == cand.size and prev.get("mtime_ns") == cand.mtime_ns):
            n += 1
    return n


def capture(watch: Path, config_path, placed_now: int, now: datetime) -> tuple[str, int, int]:
    """(capture code, captured, editions added). The approval gate is ``run_source``'s own; this never approves anything."""
    from core.memory_shelf import codes, config, watchers
    from core.memory_shelf.shelf import Shelf
    try:
        cfg = config.load(config_path or None)
    except config.ConfigError:
        raise BackupError("shelf_config_unreadable") from None
    src = cfg.sources.get(SOURCE)
    if src is None:
        return "not_configured", 0, 0
    if src.kind != "mc" or Path(src.root).expanduser().resolve() != watch.resolve():
        return "source_root_mismatch", 0, 0             # placed files would never be read by this source
    shelf = Shelf(cfg.shelf_root, min_free_bytes=cfg.min_free_bytes, min_free_fraction=cfg.min_free_fraction)
    if not placed_now and not waiting_files(shelf, src):
        return "skipped", 0, 0
    res = watchers.run_source(shelf, src, now=now)
    code = res.get("status") or "unknown"
    if code in ("dry_run_only", codes.NOT_APPROVED):    # first sight writes the owner's dry-run listing; either way nothing captured
        code = "not_approved"
    counts = res.get("counts") or {}
    return code, int(counts.get(codes.CAPTURED, 0)), int(counts.get(codes.EDITION_ADDED, 0))


# ── one run ──
class _Run:
    def __init__(self, *, dest, identity_spec, age_bin, expect_age, watch, state_dir, config_path, now, usage, fetch_identity):
        self.dest, self.identity_spec, self.age_bin, self.expect_age = dest, identity_spec, age_bin, expect_age
        self.watch, self.state_dir, self.config_path = Path(watch), Path(state_dir), config_path
        self.work = self.state_dir / WORK_NAME
        self.pulled_path, self.placed_path = self.state_dir / PULLED_NAME, self.state_dir / PLACED_NAME
        self.now, self.usage, self.fetch_identity = now, usage, fetch_identity
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.code, self.capture_code, self.ready = "ok", "skipped", False
        self.identity, self.age_version = None, None
        self.pulled, self.placed, self.remaining = {}, {}, []

    def prepare(self) -> None:
        for folder, code in ((self.state_dir, "state_dir_unsafe"), (self.watch, "watch_unsafe")):
            if folder.is_symlink():
                raise BackupError(code)
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            if folder.is_symlink() or not folder.is_dir():
                raise BackupError(code)
            os.chmod(folder, 0o700)
        if (self.work.exists() or self.work.is_symlink()) and not self.wipe_work():
            raise BackupError("cleanup_failed")
        self.work.mkdir(mode=0o700)
        try:
            self.counts["temp_swept"] = sweep_temps(self.watch)
        except OSError:
            raise BackupError("cleanup_failed") from None

    def wipe_work(self) -> bool:
        if self.work.is_symlink():
            self.work.unlink()
            return True
        if not self.work.exists():
            return True
        for p in self.work.rglob("*"):
            if p.is_file() and not p.is_symlink():
                B.wipe_file(p)
        return B.remove_tree(self.work)

    def go(self) -> None:
        self.prepare()
        self.pulled = load_pulled(self.pulled_path)
        self.placed = load_placed(self.placed_path)
        self.ready = True
        d = B.Dest(self.dest)
        listed = MB.complete_batches(d)
        self.remaining = [b for b in listed if b not in self.pulled]
        for bid in list(self.remaining):
            self._batch(d, bid)
            self.remaining.remove(bid)
        listed_set = set(listed)                         # forget batches that are no longer listed: they can never be pulled again
        if any(b not in listed_set for b in self.pulled):
            self.pulled = {b: e for b, e in self.pulled.items() if b in listed_set}
            V.write_json_atomic(self.pulled_path, self.pulled)

    def _identity(self) -> str:
        if self.age_version is None:
            self.age_version = B.check_age(self.age_bin, self.expect_age)
        if self.identity is None:
            self.identity = self.fetch_identity(self.identity_spec)
        return self.identity

    def _batch(self, d: B.Dest, bid: str) -> None:
        scratch = self.work / bid
        scratch.mkdir(mode=0o700)
        m = None
        cipher, plain, extracted = scratch / MB.BATCH_OBJECT, scratch / "batch.tar.gz", scratch / "x"
        try:
            try:
                m = MB.fetch_chain(d, bid, scratch, INSTANCE)
                obj = m["objects"]["batch"]
                files_bytes = sum(z for _n, _s, z in m["files"])             # each bounded by fetch_chain; extracted, then copied
                B.ensure_space(self.work, int(obj["bytes"]) + int(obj["plaintext_bytes"]) + 2 * files_bytes + SLACK, usage=self.usage)
                MB.fetch_ciphertext(d, bid, m, cipher)
                B.decrypt(self.age_bin, self._identity(), cipher, plain, scratch)
                B.wipe_file(cipher)
                if plain.stat().st_size != int(obj["plaintext_bytes"]) or B.sha256_file(plain) != obj["plaintext_sha256"]:
                    raise BackupError("plaintext_mismatch")
                try:
                    MB.extract(plain, extracted, m)
                except (tarfile.TarError, EOFError, zlib.error):
                    raise BackupError("archive_unsafe") from None   # a corrupt stream, not only a hostile one
                except OSError:                                     # a local fault (disk, permission) is not the batch's: retry
                    raise BackupError("extract_write_failed") from None
            except BackupError as exc:
                if exc.code not in INTEGRITY:
                    raise                                           # transient: stop, acknowledge nothing, retry next run
                files = [[n, s, f"{MB.REJECTED}:{exc.code}"] for n, s in self._manifest_names(m, scratch)]
                self._acknowledge(d, bid, files, {}, f"{MB.REJECTED}:{exc.code}")
                self.counts["rejected"] += len(files)
                return
            acks, newly = [], {}
            for name, sha, size in m["files"]:
                bad = MB.check_file(extracted, name, sha, size)
                if bad:
                    acks.append([name, sha, f"{MB.REJECTED}:{bad}"])
                    self.counts["rejected"] += 1
                    continue
                prev = self.placed.get(name)
                if prev and prev["batch_id"] > bid:
                    acks.append([name, sha, MB.SUPERSEDED])         # an older batch never overwrites newer bytes
                    self.counts["superseded"] += 1
                    continue
                outcome = place(extracted / name, self.watch, name, sha, size)
                self.counts[outcome] += 1
                acks.append([name, sha, MB.PLACED])
                newly[name] = {"sha256": sha, "batch_id": bid}
            self._acknowledge(d, bid, acks, newly, "ok")
        finally:
            for p in scratch.rglob("*"):
                if p.is_file() and not p.is_symlink():
                    B.wipe_file(p)
            if not B.remove_tree(scratch):
                raise BackupError("cleanup_failed")

    @staticmethod
    def _manifest_names(m, scratch: Path) -> list:
        """The files to reject: the verified manifest's, else whatever well-formed entries the downloaded (unverified) manifest lists, so EC2
        learns the verdict for its files rather than waiting out its backoff. Names and hashes only."""
        if m is None:
            doc = V.read_json(scratch / B.MANIFEST)
            entries = doc.get("files") if isinstance(doc, dict) else None
        else:
            entries = m["files"]
        out = []
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, list) and len(e) == 3 and MB.name_ok(str(e[0]), INSTANCE) and V.HEX64.match(str(e[1])):
                out.append((str(e[0]), str(e[1])))
        return sorted(set(out))

    def _acknowledge(self, d: B.Dest, bid: str, files: list, newly: dict, result: str) -> None:
        """Every placement above is already durable (fsync + rename + folder fsync). The ack first, then the local state."""
        at = self.now()
        local = self.work / f"ack-{bid}.json"
        V.write_json_atomic(local, MB.ack_doc(bid, files, at))
        d.put(local, f"{MB.ACK_PREFIX}/{bid}.json", replace=True)
        local.unlink(missing_ok=True)
        if newly:
            self.placed.update(newly)
            V.write_json_atomic(self.placed_path, self.placed)
        self.pulled[bid] = {"pulled_utc": iso(at), "result": result}
        V.write_json_atomic(self.pulled_path, self.pulled)
        self.counts["batches"] += 1

    def oldest_age(self, now: datetime) -> int:
        times = [t for t in (batch_time(b) for b in self.remaining) if t]
        return max(0, int((now - min(times)).total_seconds())) if times else 0


def run_pull(*, dest, identity_spec, age_bin, expect_age, jobs_root, watch=DEFAULT_WATCH, state_dir=DEFAULT_STATE, config_path=None,
             registry_path=None, take_lock: bool = True, now=utcnow, usage=shutil.disk_usage, fetch_identity=B.fetch_identity,
             alert_send=None) -> dict:
    """One ``vault-mc-pull`` run. Returns ``{"exit", "state", "results", "summary"}``; writes the job status throughout. ``config_path`` is
    the shelf config (``MOI_CONFIG``; ``None`` is ``config.load``'s own default). ``now``, ``usage``, ``fetch_identity`` and ``alert_send``
    are injected in tests.

    **One admission for the whole run** (the ``vault-sync`` pattern, Codex 17 Oct). The shared ``vault-batch`` lock is taken FIRST and held
    across the job-status start, the work and the job-status finish, so the status file always describes the run that holds the lock. A
    competitor refused at the lock writes nothing at all, not even a status: it returns ``state`` None and ``pull=busy`` (exit 0); the next
    2-hourly run catches up."""
    alert_send = alert_send or alert.send
    if not identity_spec_ok(identity_spec):
        return {"exit": EXIT_REFUSED, "state": None, "results": {}, "summary": "refused: identity_spec_invalid"}
    fd = None
    if take_lock:
        try:
            status.ensure_root(jobs_root)
            fd = os.open(str(Path(jobs_root) / LOCK_NAME), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except Exception:                                                   # noqa: BLE001
            return {"exit": EXIT_REFUSED, "state": None, "results": {}, "summary": "refused: lock_not_openable"}
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return {"exit": EXIT_OK, "state": None, "results": {"pull": "busy"}, "summary": "busy: another vault batch job holds the lock"}
    try:
        return _run_admitted(dest=dest, identity_spec=identity_spec, age_bin=age_bin, expect_age=expect_age, jobs_root=jobs_root,
                             watch=watch, state_dir=state_dir, config_path=config_path, registry_path=registry_path, now=now, usage=usage,
                             fetch_identity=fetch_identity, alert_send=alert_send)
    finally:
        if fd is not None:
            os.close(fd)


def _run_admitted(*, dest, identity_spec, age_bin, expect_age, jobs_root, watch, state_dir, config_path, registry_path, now, usage,
                  fetch_identity, alert_send) -> dict:
    """The run itself, for a caller that holds the admission: status start, work, status finish."""
    job = job_definition(registry_path)
    started = now()
    run_id = f"{started.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(jobs_root, job, run_id, started)
    except Exception:                                                       # noqa: BLE001
        return {"exit": EXIT_REFUSED, "state": None, "results": {}, "summary": "refused: status_file_not_writable"}
    r = _Run(dest=dest, identity_spec=identity_spec, age_bin=os.path.expanduser(age_bin), expect_age=expect_age,
             watch=Path(os.path.expanduser(str(watch))), state_dir=Path(os.path.expanduser(str(state_dir))), config_path=config_path,
             now=now, usage=usage, fetch_identity=fetch_identity)
    failed = capture_failed = False
    try:
        r.go()
    except BackupError as exc:
        r.code, failed = exc.code, True
    except Exception as exc:                                                # noqa: BLE001 - a fixed code, never the message
        r.code, failed = f"exception_{type(exc).__name__.lower()}"[:40], True
    finally:
        r.identity = None                                                   # the key text is not kept past the pull
    if not r.wipe_work() and not failed:
        r.code, failed = "cleanup_failed", True
    if r.ready:                         # files already in the folder are durable whatever happened to a later batch: capture them
        try:
            r.capture_code, r.counts["captured"], r.counts["editions_added"] = capture(r.watch, config_path, r.counts["placed"], now())
        except BackupError as exc:
            r.capture_code, capture_failed = exc.code, True
        except Exception as exc:                                            # noqa: BLE001
            r.capture_code, capture_failed = f"exception_{type(exc).__name__.lower()}"[:40], True
    finished = now()
    r.counts["oldest_unpulled_age_s"] = r.oldest_age(finished)
    results = {"pull": r.code, "capture": r.capture_code, **{k: str(r.counts[k]) for k in COUNTERS}}
    hard = failed and r.code not in WARN_CODES
    if hard or capture_failed:
        state = "failed"
    elif r.code in WARN_CODES or r.counts["rejected"] > 0 or r.capture_code not in ("ok", "skipped"):
        state = "warn"
    else:
        state = "ok"
    summary = (f"{r.code}: batches {r.counts['batches']}, placed {r.counts['placed']}, unchanged {r.counts['unchanged']}, "
               f"rejected {r.counts['rejected']}, temp_swept {r.counts['temp_swept']}, capture {r.capture_code}")
    return _finish(jobs_root, job, running, run_id, finished, state, results, summary, success=(r.code == "ok" and not capture_failed),
                   alert_send=alert_send)


def _finish(jobs_root, job, running, run_id, finished, state, results, summary, *, success, alert_send) -> dict:
    try:
        next_due = schedule.next_fire(job.schedule, finished)
    except Exception:                                                       # noqa: BLE001
        next_due = None
    alert_result = "not_needed"
    if state == "failed":
        code = results.get("pull") if results.get("pull") not in (None, "ok") else results.get("capture")
        try:
            alert_result = alert_send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {code}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    code = EXIT_FAIL if state == "failed" else EXIT_OK
    status.finish_run(jobs_root, job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result, success=success)
    return {"exit": code, "state": state, "results": results, "summary": summary}


# ── read-only plan ──
def plan_pull(*, dest, watch=DEFAULT_WATCH, state_dir=DEFAULT_STATE, config_path=None, now=utcnow) -> dict:
    """What a run would do, writing nothing and decrypting nothing: unpulled COMPLETE batches (ids only), the watch folder, the source's
    approval state."""
    state_dir, watch = Path(os.path.expanduser(str(state_dir))), Path(os.path.expanduser(str(watch)))
    out = {"job": JOB_ID, "watch_exists": watch.is_dir(), "state_dir_exists": state_dir.is_dir()}
    try:
        pulled = load_pulled(state_dir / PULLED_NAME)
        out["placed_names"] = len(load_placed(state_dir / PLACED_NAME))
    except BackupError as exc:
        out["state"] = exc.code
        return out
    try:
        waiting = [b for b in MB.complete_batches(B.Dest(dest)) if b not in pulled]
        out["unpulled_batches"] = len(waiting)
        times = [t for t in (batch_time(b) for b in waiting) if t]
        out["oldest_unpulled_age_s"] = max(0, int((now() - min(times)).total_seconds())) if times else 0
    except BackupError as exc:
        out["remote"] = exc.code
    try:
        from core.memory_shelf import approvals, config
        cfg = config.load(config_path or None)
        src = cfg.sources.get(SOURCE)
        if src is None:
            out["capture_source"] = "not_configured"
        else:
            from core.memory_shelf.shelf import Shelf
            shelf = Shelf(cfg.shelf_root, min_free_bytes=cfg.min_free_bytes, min_free_fraction=cfg.min_free_fraction)
            out["capture_source"] = approvals.source_status(shelf, SOURCE, src.fingerprint())
            out["capture_root_matches_watch"] = Path(src.root).expanduser().resolve() == watch.resolve()
    except Exception as exc:                                                # noqa: BLE001
        out["capture_source"] = f"unreadable_{type(exc).__name__.lower()}"[:40]
    return out
