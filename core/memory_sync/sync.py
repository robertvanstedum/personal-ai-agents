"""``vault-sync``: the Mac side of the Vault's incremental sync to production (17 Oct 2026). Design: _working/VAULT_INCREMENTAL_SYNC_DESIGN_2026-10-17.md.

launchd runs it every 2 h while the Mac is awake (``StartInterval`` 7200 plus ``RunAtLoad``: a missed interval runs once on wake, which is
the catch-up after sleep). One run, under the shared ``<jobs_root>/vault-batch.lock`` taken WITHOUT waiting (a heavy job holding it means
this run records ``busy`` and does nothing else):

  1. **Local inventory.** ``versions.record_versions`` over the shelf with the per-file hash cache (``inventory-cache.json``), so a run with
     nothing new is one directory walk and no hashing.
  2. **Production inventory.** ``sync/acks/inventory.json``, written by ``vault-apply`` on EC2. None yet means production was never seeded:
     the first population is r3's whole-snapshot transport, so this job refuses (``no_production_inventory``, warn) and sends nothing.
  3. **Acknowledgements.** ``sync/acks/<batch_id>.json`` for every batch a pending version travelled in. ``stored``/``superseded`` for the
     pending digest clears it; ``rejected:<code>`` keeps it pending with that reason until its backoff retry. A read that fails is an error
     (``download_failed``), never "no acknowledgement yet".
  4. **Diff.** Production's view of a record is the newest confirmation: an acknowledgement newer than the inventory's ``through_batch``,
     else the inventory. A record is in sync (same digest), ``production_newer`` (production holds a later edition: flagged, never sent,
     never deleted), in flight (pending with this digest; due when its retry time passed or its upload never finished), or changed (new,
     a new edition, or a metadata-only change). A changed record whose exact digest production once confirmed is a gap (``gap_requeued``).
     Production records the shelf lacks are ``production_only`` (flagged; nothing here ever deletes anything anywhere).
  5. **Bound.** Outside 00:00-06:00 Chicago a run sends ONE light batch (``light_mib`` MiB of record bytes, ``light_records`` records);
     what does not fit waits for the window (``deferred_heavy``), and a record larger than the light bound always waits. Inside the window,
     batches of ``heavy_mib`` until the backlog is done or the window ends (checked before each batch). Every batch first keeps the 5 GiB
     reserve (``ensure_space``; short of it the run stops with ``insufficient_space``, warn, and nothing is lost).
  6. **Batch** (``core.memory_sync.batch``): the plaintext tar in ``work/`` (0700, wiped), age-encrypted to the PUBLIC recipient, the
     plaintext wiped, then the content-free manifest and COMPLETE. The versions are written to ``pending.json`` as ``sending`` BEFORE the
     upload and as ``sent`` (with ``attempts`` and the next retry) after it, so an upload killed part way leaves an orphan without COMPLETE
     (production never lists it) and versions that the next run re-sends at once in a NEW batch. Backoff after each send: 1 h, 2 h, 4 h,
     8 h, then 12 h (``min(2 ** (attempts - 1), 12)`` hours). A retry sends the CURRENT local version; production acknowledges a version it
     already holds as ``stored`` without reapplying it, so a retry never duplicates.

**Never drop an unacknowledged version.** An entry leaves ``pending`` only when production confirms that digest (an acknowledgement or the
inventory), when a newer local version of the record replaces it in a new batch, or when production already holds the CURRENT local
version or a later edition (then there is nothing left to send).

``pending.json`` (``vault-sync-pending/1``) keeps ``pending`` (per record: digest, edition, edition hash, the latest batch id, the last few
batch ids that carried it, state, reason, attempts, first/last send time, next retry), ``synced`` (the last version production confirmed
for each record, by acknowledgement or inventory: the memory that tells a gap from a record never sent), ``backlog_since`` (when each
out-of-sync record was first seen out of sync, for ``oldest_pending_age_s``) and the last inventory's time, ``through_batch`` and count.
Record ids, hashes and batch ids only: no folder name, title or text is ever in plain text (the manifest, COMPLETE, pending, status).

Job status ``vault-sync``: ``last_success_at`` advances ONLY on a run that ends with backlog 0, so the Operate tile and the watchdog
(``missed_after_s`` 6 h) show how long the backlog has been outstanding. Errors are ``failed`` with a Telegram alert (fixed codes).
"""
from __future__ import annotations

import copy
import fcntl
import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.jobs import alert, registry, schedule, status
from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_sync import batch as BT
from core.memory_sync import versions as V

JOB_ID = "vault-sync"
PENDING_FORMAT = "vault-sync-pending/1"
LOCK_NAME = "vault-batch.lock"
CACHE_NAME, PENDING_NAME, WORK_NAME = "inventory-cache.json", "pending.json", "work"
MIB = 1 << 20
LIGHT_BYTES, LIGHT_RECORDS, HEAVY_BYTES = 64 * MIB, 200, 512 * MIB
SLACK = 16 << 20
DOC_PER_VERSION, DOC_PER_FILE = 1024, 160              # BATCH.json allowance in the tar estimate
BATCHES_KEPT = 8                                        # a newer batch with the same digest is acknowledged the same way
STATES = ("sending", "sent", "rejected")
WARN_CODES = ("no_production_inventory", "insufficient_space")
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = 0, 1, 2
COUNTERS = ("backlog_records", "oldest_pending_age_s", "sent", "acked", "rejected", "retried", "deferred_heavy", "gap_requeued",
            "production_newer", "production_only", "record_too_large", "unsyncable", "source_entry_missing", "stranded", "batches")
SESSION_DIR = "sessions-raw"                            # the record folder the indexer finds through source_index.json


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return status.iso(moment)


def backoff(attempts: int) -> timedelta:
    return timedelta(hours=min(2 ** max(int(attempts) - 1, 0), 12))


def job_definition(path=None):
    try:
        job = registry.load(path).get(JOB_ID)
        if job:
            return job
    except registry.RegistryError:
        pass
    return registry.parse({"schema_version": 1, "jobs": [{
        "id": JOB_ID, "name": "Vault incremental sync (Mac)", "host": "mac",
        "schedule": {"text": "every 2 h while awake", "kind": "interval", "every_s": 7200},
        "expected_every_s": 7200, "missed_after_s": 21600, "stuck_after_s": 3600,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "core/memory_sync/sync.py"}]}).jobs[0]


# ── pending.json ──
def empty_state(dest: str | None = None) -> dict:
    return {"format": PENDING_FORMAT, "dest": dest, "pending": {}, "synced": {}, "backlog_since": {}, "inventory": None}


def load_state(path: Path) -> dict:
    """The pending document, or an empty one when there is none yet. A file that exists and does not parse is ``pending_unreadable``
    (failed): silently starting over would forget what was sent and confirmed."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return empty_state()
    doc = None if path.is_symlink() else V.read_json(path)
    try:
        ok = (isinstance(doc, dict) and doc.get("format") == PENDING_FORMAT and all(isinstance(doc.get(k), dict) for k in ("pending", "synced", "backlog_since"))
              and all(isinstance(e, dict) and V.HEX64.match(str(e.get("digest", ""))) and e.get("state") in STATES and isinstance(e.get("batches"), list)
                      for e in doc["pending"].values())
              and all(isinstance(e, dict) and V.HEX64.match(str(e.get("digest", ""))) for e in doc["synced"].values()))
    except (TypeError, AttributeError):
        ok = False
    if not ok:
        raise BackupError("pending_unreadable")
    return doc


def save_state(path: Path, state: dict) -> None:
    V.write_json_atomic(path, state)


# ── reading what production says ──
def read_inventory(d: B.Dest, work: Path) -> tuple[dict, dict] | None:
    """(record id -> entry, the document) or ``None`` when production has written no inventory yet."""
    local = work / "inventory.json"
    if not d.get_optional(f"{V.ACK_PREFIX}/{V.INVENTORY_NAME}", local):
        return None
    doc = V.read_json(local)
    local.unlink(missing_ok=True)
    try:
        entries = V.parse_inventory(doc)
    except ValueError:
        raise BackupError("inventory_malformed") from None
    through = doc.get("through_batch")
    if through is not None and not (isinstance(through, str) and B.ATTEMPT.match(through)):
        raise BackupError("inventory_malformed")
    return entries, doc


def _ack_status_ok(value) -> bool:
    if value in V.CLEARED:
        return True
    return isinstance(value, str) and value.startswith(f"{V.REJECTED}:") and bool(V.CODE.match(value.split(":", 1)[1]))


def read_ack(d: B.Dest, batch_id: str, work: Path) -> dict | None:
    local = work / f"ack-{batch_id}.json"
    if not d.get_optional(f"{V.ACK_PREFIX}/{batch_id}.json", local):
        return None
    doc = V.read_json(local)
    local.unlink(missing_ok=True)
    try:
        ok = (isinstance(doc, dict) and doc.get("format") == V.ACK_FORMAT and doc.get("batch_id") == batch_id and isinstance(doc.get("versions"), list)
              and all(isinstance(e, dict) and V.ULID.match(str(e.get("record_id", ""))) and V.HEX64.match(str(e.get("digest", "")))
                      and _ack_status_ok(e.get("status")) for e in doc["versions"]))
    except TypeError:
        ok = False
    if not ok:
        raise BackupError("ack_malformed")
    return doc


def apply_acks(state: dict, acks: dict, counts: dict) -> None:
    """Fold acknowledgements (batch id -> document) into the state. Re-reading the same acknowledgement changes nothing."""
    pending, synced = state["pending"], state["synced"]
    for bid in sorted(acks):
        for e in acks[bid]["versions"]:
            rid, digest, st = e["record_id"], e["digest"], e["status"]
            entry = pending.get(rid)
            if st in V.CLEARED:
                prev = synced.get(rid)
                if not prev or (prev.get("batch_id") or "") <= bid:
                    synced[rid] = {"digest": digest, "batch_id": bid}
                if entry and entry["digest"] == digest:
                    del pending[rid]
                    counts["acked"] += 1
            elif entry and entry["digest"] == digest and entry.get("batch_id") == bid and entry.get("state") == "sent":
                # only the latest send's verdict counts, once: an older batch's rejection re-read later is history
                entry["state"], entry["reason"] = "rejected", st.split(":", 1)[1]
                counts["rejected"] += 1


def production_view(inventory: dict) -> dict:
    """Record id -> what production holds now: production's own inventory, and nothing else.

    Remembered acknowledgements never stand in for it (Codex, 17 Oct): an acknowledgement newer than the inventory used to win, so after
    production was reset or reseeded (an inventory with ``through_batch`` None or older than what the Mac remembers) every version it had
    once acknowledged looked held, and the gaps stayed hidden forever (backlog 0, nothing re-queued). Acknowledgements still clear
    ``pending`` and feed ``synced`` (which tells a lost version, ``gap_requeued``, from one never sent); what is in sync is decided only
    by the inventory. The cost is a rare duplicate send when an acknowledgement is read before the inventory that follows it (a crash
    between the two on EC2): production acknowledges that re-send ``stored`` without reapplying it."""
    return {rid: dict(e) for rid, e in inventory.items()}


def reconcile(state: dict, local: dict, inventory: dict, through: str | None, now: datetime, counts: dict) -> dict:
    """Diff the shelf against production's view, update the state, and return the work lists. Mutates ``state`` and ``counts``."""
    pending, synced = state["pending"], state["synced"]
    for rid, e in inventory.items():                    # the inventory confirms what the shelf holds: remember it (gap detection)
        v = local.get(rid)
        if v is not None and e["digest"] == v.digest:
            synced[rid] = {"digest": e["digest"], "batch_id": through}
    for rid in [r for r in synced if r not in local]:
        del synced[rid]
    view = production_view(inventory)
    for rid in [r for r, e in pending.items() if view.get(r, {}).get("digest") == e["digest"]]:
        del pending[rid]                                # production holds exactly this version
        counts["acked"] += 1
    due, waiting, changed = [], [], []
    for rid in sorted(local):
        v, p, entry = local[rid], view.get(rid), pending.get(rid)
        if p and p["digest"] == v.digest:
            pending.pop(rid, None)                      # production holds the current version: nothing left to send
            continue
        if p and p.get("edition") is not None and p["edition"] > v.edition:
            counts["production_newer"] += 1
            pending.pop(rid, None)                      # production holds a later edition (what ``superseded`` would say)
            continue
        if entry and entry["digest"] == v.digest:
            retry_at = status.parse_time(entry.get("next_retry_utc"))
            (due if entry["state"] == "sending" or retry_at is None or retry_at <= now else waiting).append(rid)
            continue
        changed.append(rid)
        s = synced.get(rid)
        if s and s["digest"] == v.digest:
            counts["gap_requeued"] += 1                 # production confirmed this exact version once and no longer holds it
    counts["production_only"] += sum(1 for rid in view if rid not in local)
    # A pending version whose record is no longer a syncable local record (its folder became damaged or was removed) cannot be sent
    # again. It is never dropped from pending.json (it rejoins the normal flow if the record comes back, and clears if production
    # confirms it), but it is reported apart as ``stranded`` (the run warns) rather than held in the backlog, where it would keep the
    # backlog above 0 and the job red forever with nothing the sync could do about it.
    stranded = [rid for rid in pending if rid not in local]
    counts["stranded"] = len(stranded)
    backlog = set(due) | set(waiting) | set(changed)
    since = state["backlog_since"]
    for rid in [r for r in since if r not in backlog]:
        del since[rid]
    for rid in backlog:
        since.setdefault(rid, iso(now))
    counts["backlog_records"] = len(backlog)
    due.sort(key=lambda r: (pending[r].get("first_sent_utc") or "", r))
    return {"due": due, "waiting": waiting, "changed": changed, "backlog": backlog}


def shelf_sources(shelf_root: Path) -> dict:
    """``source_index.json`` as the shelf itself reads it: when a write crashed between a record and the index (``index.dirty``) or the
    file is missing, the index is rebuilt from the records' own front matter (read-only), so a record is never sent without its entry."""
    from core.memory_shelf.shelf import Shelf
    try:
        doc = Shelf(shelf_root, min_free_bytes=None).index()
    except (OSError, ValueError):
        raise BackupError("source_index_unreadable") from None
    if not isinstance(doc, dict):
        raise BackupError("source_index_unreadable")
    return doc


def entry_ok(v, sources: dict) -> bool:
    """A sessions-raw version travels only with its own source-index entry (production's indexer finds records through it)."""
    if v.kind != SESSION_DIR:
        return True
    entry = sources.get(v.source_key) if v.source_key else None
    return isinstance(entry, dict) and entry.get("id") == v.record_id and entry.get("dir") == v.name


# ── choosing what to send ──
def tar_need(vs) -> int:
    return B.tar_estimate(sum(v.bytes for v in vs) + sum(DOC_PER_VERSION + DOC_PER_FILE * len(v.files) for v in vs),
                          sum(len(v.files) for v in vs) + 1)


def too_large(v) -> bool:
    return B.ciphertext_bound(tar_need([v])) > B.MAX_CONDITIONAL_PUT


def plan_batches(candidates: list, local: dict, mode: str, *, light_bytes: int, light_records: int, heavy_bytes: int) -> tuple[list, list]:
    """(batches as lists of record ids, deferred record ids). ``candidates`` excludes records too large to send at all."""
    if mode == "light":
        chosen, used, deferred = [], 0, []
        for rid in candidates:
            size = local[rid].bytes
            if size <= light_bytes - used and len(chosen) < light_records:
                chosen.append(rid)
                used += size
            else:
                deferred.append(rid)                    # skip it and keep looking for smaller ones
        return ([chosen] if chosen else []), deferred
    batches, rest = [], list(candidates)
    while rest:
        cur, used, left = [], 0, []
        for rid in rest:
            size = local[rid].bytes
            if not cur or used + size <= heavy_bytes:   # a record larger than the bound goes alone
                cur.append(rid)
                used += size
            else:
                left.append(rid)
        batches.append(cur)
        rest = left
    return batches, []


# ── one run ──
class _Run:
    def __init__(self, *, shelf_root, dest, recipient, age_bin, expect_age, state_dir, light_bytes, light_records, heavy_bytes, now, put, usage):
        self.shelf, self.dest, self.recipient, self.age_bin, self.expect_age = Path(shelf_root), dest, recipient, age_bin, expect_age
        self.state_dir = Path(state_dir)
        self.work = self.state_dir / WORK_NAME
        self.pending_path = self.state_dir / PENDING_NAME
        self.light_bytes, self.light_records, self.heavy_bytes = int(light_bytes), int(light_records), int(heavy_bytes)
        self.now, self.put, self.usage = now, put, usage
        self.counts = dict.fromkeys(COUNTERS, 0)
        self.state, self.diffed, self.mode, self.age_version, self.hash_misses = None, False, None, None, 0
        self.state_reset, self.production_reset = None, False
        self.code = "ok"

    def prepare(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.state_dir.is_symlink():
            raise BackupError("state_dir_unsafe")
        os.chmod(self.state_dir, 0o700)
        if (self.work.exists() or self.work.is_symlink()) and not self._wipe_work():
            raise BackupError("cleanup_failed")
        self.work.mkdir(mode=0o700)

    def _wipe_work(self) -> bool:
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
        if not self.shelf.is_dir():
            raise BackupError("shelf_missing")
        self.state = load_state(self.pending_path)
        if self.state.get("dest") not in (None, self.dest):
            # All sync state belongs to one destination: batches sent, acknowledgements read and versions confirmed elsewhere say nothing
            # about this one. A changed destination starts fresh (its own inventory decides what to send).
            self.state, self.state_reset = empty_state(self.dest), "destination_changed"
        self.state["dest"] = self.dest
        cache = V.HashCache(self.state_dir / CACHE_NAME)
        local, problems = V.record_versions(self.shelf, cache)
        cache.save()
        self.hash_misses = cache.misses
        self.counts["unsyncable"] = sum(problems.values())
        d = B.Dest(self.dest)
        got = read_inventory(d, self.work)
        if got is None:
            self.code = "no_production_inventory"
            self.state = None                           # nothing changed; nothing to save
            return
        inventory, doc = got
        through = doc.get("through_batch")
        acks = {}
        for bid in sorted({b for e in self.state["pending"].values() for b in e["batches"]}):
            ack = read_ack(d, bid, self.work)
            if ack is not None:
                acks[bid] = ack
        apply_acks(self.state, acks, self.counts)
        now = self.now()
        self.mode = "heavy" if B.in_window(now) else "light"
        before = (self.state.get("inventory") or {}).get("through_batch")
        if before and (through is None or through < before):
            self.production_reset = True                # production forgot batches it had applied (reset, reseed or rollback): visible
        work = reconcile(self.state, local, inventory, through, now, self.counts)
        self.state["inventory"] = {"generated_utc": doc.get("generated_utc"), "through_batch": through, "count": len(inventory),
                                   "release": doc.get("release")}
        self.diffed = True
        save_state(self.pending_path, self.state)
        sources = shelf_sources(self.shelf)
        candidates = []
        for rid in work["due"] + work["changed"]:
            if too_large(local[rid]):
                self.counts["record_too_large"] += 1
            elif not entry_ok(local[rid], sources):
                self.counts["source_entry_missing"] += 1    # held back: production could store it but never index it
            else:
                candidates.append(rid)
        batches, deferred = plan_batches(candidates, local, self.mode, light_bytes=self.light_bytes, light_records=self.light_records,
                                         heavy_bytes=self.heavy_bytes)
        self.counts["deferred_heavy"] += len(deferred)
        if not batches:
            return
        for i, rids in enumerate(batches):
            if self.mode == "heavy" and not B.in_window(self.now()):
                self.counts["deferred_heavy"] += sum(len(b) for b in batches[i:])
                break
            if self.age_version is None:
                self.age_version = B.check_age(self.age_bin, self.expect_age)
            vs = [local[r] for r in rids]
            need = tar_need(vs)
            try:
                B.ensure_space(self.work, need + B.ciphertext_bound(need) + SLACK, usage=self.usage)
            except BackupError as exc:
                if exc.code != "insufficient_space":
                    raise
                self.code = "insufficient_space"
                return
            self._send(d, vs, sources)

    def _send(self, d: B.Dest, vs: list, sources: dict) -> None:
        created = self.now()
        bid = B.new_attempt_id(created)
        plain, cipher = self.work / f"{bid}.tar.gz", self.work / f"{bid}.tar.gz.age"
        mpath, cpath = self.work / f"{bid}-{B.MANIFEST}", self.work / f"{bid}-{B.COMPLETE}"
        try:
            res = BT.write_tar(self.shelf, vs, sources, bid, plain)
            chosen = res["versions"]                    # a record that changed while read stays changed for the next run
            if not chosen:
                return
            plain_sha, plain_bytes = B.sha256_file(plain), plain.stat().st_size
            B.encrypt(self.age_bin, self.recipient, plain, cipher)
        finally:
            if (plain.exists() or plain.is_symlink()) and not B.wipe_file(plain):
                raise BackupError("cleanup_failed")
        m = BT.manifest(bid, chosen, created=created, cipher=cipher, plain_sha=plain_sha, plain_bytes=plain_bytes,
                        age_version=self.age_version, recipient=self.recipient, mode=self.mode)
        V.write_json_atomic(mpath, m)
        V.write_json_atomic(cpath, BT.complete(m, B.sha256_file(mpath)))
        pending, at = self.state["pending"], iso(created)
        for v in chosen:
            entry = pending.get(v.record_id)
            if entry and entry["digest"] == v.digest:
                self.counts["retried"] += 1
            else:                                       # a new version replaces the older pending one only now, as it is sent
                entry = {"digest": v.digest, "edition": v.edition, "edition_hash": v.edition_hash, "batches": list((entry or {}).get("batches", [])),
                         "attempts": 0, "reason": None, "first_sent_utc": at, "sent_utc": None, "next_retry_utc": None}
            entry["batch_id"], entry["state"] = bid, "sending"
            entry["batches"] = (entry["batches"] + [bid])[-BATCHES_KEPT:]   # listed before the upload: an upload whose reply was lost is still read
            pending[v.record_id] = entry
        save_state(self.pending_path, self.state)
        BT.upload(d, bid, cipher, mpath, cpath, put=self.put)
        done = self.now()
        for v in chosen:
            entry = pending[v.record_id]
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry["state"], entry["sent_utc"] = "sent", iso(done)
            entry["next_retry_utc"] = iso(done + backoff(entry["attempts"]))
        save_state(self.pending_path, self.state)
        self.counts["sent"] += len(chosen)
        self.counts["batches"] += 1

    def results(self) -> dict:
        out = {"sync": self.code}
        if self.mode:
            out["mode"] = self.mode
        if self.state_reset:
            out["state_reset"] = self.state_reset
        if self.production_reset:
            out["production_reset"] = "yes"
        if self.diffed:
            out.update({k: str(self.counts[k]) for k in COUNTERS})
        elif self.code == "no_production_inventory":
            out["unsyncable"] = str(self.counts["unsyncable"])
        return out

    def oldest_age(self, now: datetime) -> int:
        times = [status.parse_time(t) for t in (self.state or {}).get("backlog_since", {}).values()]
        times = [t for t in times if t]
        return max(0, int((now - min(times)).total_seconds())) if times else 0


def run_sync(*, shelf_root, dest, recipient, age_bin, expect_age, jobs_root, state_dir, registry_path=None,
             light_bytes: int = LIGHT_BYTES, light_records: int = LIGHT_RECORDS, heavy_bytes: int = HEAVY_BYTES, take_lock: bool = True,
             now=utcnow, put=None, usage=shutil.disk_usage, alert_send=None) -> dict:
    """One ``vault-sync`` run. Returns ``{"exit", "state", "results", "summary", "hash_misses"}``.
    ``now`` is the clock (injected in tests), ``put`` the upload hook (fault injection), ``usage`` the free-space probe.

    **One admission for the whole run.** The shared ``vault-batch`` lock is taken FIRST and held across the job-status start, the work and
    the job-status finish, so the status file always describes the run that holds the lock. A competitor refused at the lock writes
    nothing at all, not even a status (Codex, 17 Oct: a refused run used to overwrite the active run's receipt). It returns
    ``state`` None and ``sync=busy``; the next 2-hourly run catches up."""
    alert_send = alert_send or alert.send
    fd = None
    if take_lock:
        status.ensure_root(jobs_root)
        fd = os.open(str(Path(jobs_root) / LOCK_NAME), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return {"exit": EXIT_OK, "state": None, "results": {"sync": "busy"}, "summary": "busy: another vault batch job holds the lock",
                    "hash_misses": 0}
    try:
        return _run_admitted(shelf_root=shelf_root, dest=dest, recipient=recipient, age_bin=age_bin, expect_age=expect_age,
                             jobs_root=jobs_root, state_dir=state_dir, registry_path=registry_path, light_bytes=light_bytes,
                             light_records=light_records, heavy_bytes=heavy_bytes, now=now, put=put, usage=usage, alert_send=alert_send)
    finally:
        if fd is not None:
            os.close(fd)


def _run_admitted(*, shelf_root, dest, recipient, age_bin, expect_age, jobs_root, state_dir, registry_path, light_bytes, light_records,
                  heavy_bytes, now, put, usage, alert_send) -> dict:
    """The run itself, for a caller that holds the admission: status start, work, status finish."""
    job = job_definition(registry_path)
    started = now()
    run_id = f"{started.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(jobs_root, job, run_id, started)
    except Exception:                                                       # noqa: BLE001
        return {"exit": EXIT_REFUSED, "state": None, "results": {}, "summary": "refused: status_file_not_writable", "hash_misses": 0}
    r = _Run(shelf_root=shelf_root, dest=dest, recipient=recipient, age_bin=age_bin, expect_age=expect_age, state_dir=state_dir,
             light_bytes=light_bytes, light_records=light_records, heavy_bytes=heavy_bytes, now=now, put=put, usage=usage)
    failed = False
    try:
        r.go()
    except BackupError as exc:
        r.code, failed = exc.code, True
    except Exception as exc:                                                # noqa: BLE001 - a fixed code, never the message
        r.code, failed = f"exception_{type(exc).__name__.lower()}"[:40], True
    if r.state is not None and r.diffed:
        try:
            save_state(r.pending_path, r.state)         # what was sent and confirmed before a failure stays recorded
        except OSError:
            if not failed:
                r.code, failed = "pending_not_writable", True
    if not r._wipe_work() and not failed:
        r.code, failed = "cleanup_failed", True
    finished = now()
    if r.diffed:
        r.counts["oldest_pending_age_s"] = r.oldest_age(finished)
    results = r.results()
    backlog = r.counts["backlog_records"]
    if failed:
        state = "failed"
    elif r.code in WARN_CODES or r.counts["rejected"] > 0 or r.counts["stranded"] > 0:
        state = "warn"
    else:
        state = "ok"
    if r.diffed:
        summary = (f"{r.code}: backlog {backlog}, sent {r.counts['sent']}, acked {r.counts['acked']}, "
                   f"rejected {r.counts['rejected']}, deferred {r.counts['deferred_heavy']}")
    else:
        summary = f"{'failed' if failed else 'refused'}: {r.code}"
    success = (not failed) and r.code == "ok" and r.diffed and backlog == 0
    return _finish(jobs_root, job, running, run_id, finished, state, results, summary, success=success, alert_send=alert_send,
                   hash_misses=r.hash_misses)


def _finish(jobs_root, job, running, run_id, finished, state, results, summary, *, success, alert_send, hash_misses) -> dict:
    try:
        next_due = schedule.next_fire(job.schedule, finished)
    except Exception:                                                       # noqa: BLE001
        next_due = None
    alert_result = "not_needed"
    if state == "failed":
        try:
            alert_result = alert_send(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {results.get('sync')}")
        except Exception:                                                   # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    code = EXIT_FAIL if state == "failed" else EXIT_OK
    status.finish_run(jobs_root, job, running, state=state, now=finished, exit_code=code, summary=summary[: status.SUMMARY_MAX],
                      results=results, next_due_by=next_due, alert=alert_result, success=success)
    return {"exit": code, "state": state, "results": results, "summary": summary, "hash_misses": hash_misses}


# ── read-only plan ──
def plan_sync(*, shelf_root, state_dir, dest=None, check_remote=False, now=utcnow, light_bytes: int = LIGHT_BYTES,
              light_records: int = LIGHT_RECORDS, heavy_bytes: int = HEAVY_BYTES) -> dict:
    """What a run would do, writing nothing: no cache save, no pending change, no upload. ``check_remote`` reads the inventory and the
    pending batches' acknowledgements into a temporary folder."""
    import tempfile
    state_dir = Path(state_dir)
    moment = now()
    out = {"job": JOB_ID, "inside_window_now": B.in_window(moment), "mode": "heavy" if B.in_window(moment) else "light",
           "shelf_root_exists": Path(shelf_root).is_dir(), "state_dir_exists": state_dir.is_dir()}
    try:
        state = load_state(state_dir / PENDING_NAME)
    except BackupError as exc:
        out["pending"] = exc.code
        return out
    out["pending_by_state"] = {s: sum(1 for e in state["pending"].values() if e["state"] == s) for s in STATES}
    out["backlog_records_last_run"] = len(state["backlog_since"])
    out["last_inventory"] = state.get("inventory")
    if not out["shelf_root_exists"]:
        return out
    local, problems = V.record_versions(shelf_root, V.HashCache(state_dir / CACHE_NAME))
    out["local_records"], out["unsyncable"] = len(local), sum(problems.values())
    if not check_remote:
        return out
    state = copy.deepcopy(state)
    counts = dict.fromkeys(COUNTERS, 0)
    with tempfile.TemporaryDirectory(prefix="vault-sync-plan-") as tmp:
        d = B.Dest(dest)
        try:
            got = read_inventory(d, Path(tmp))
            if got is None:
                out["production_inventory"] = "missing"
                return out
            inventory, doc = got
            acks = {}
            for bid in sorted({b for e in state["pending"].values() for b in e["batches"]}):
                ack = read_ack(d, bid, Path(tmp))
                if ack is not None:
                    acks[bid] = ack
        except BackupError as exc:
            out["production_inventory"] = exc.code
            return out
    apply_acks(state, acks, counts)
    work = reconcile(state, local, inventory, doc.get("through_batch"), moment, counts)
    sources = shelf_sources(Path(shelf_root))
    candidates = [r for r in work["due"] + work["changed"] if not too_large(local[r]) and entry_ok(local[r], sources)]
    batches, deferred = plan_batches(candidates, local, out["mode"], light_bytes=light_bytes, light_records=light_records, heavy_bytes=heavy_bytes)
    out.update({"production_inventory": "present", "production_records": len(inventory), "through_batch": doc.get("through_batch"),
                "would_send": sum(len(b) for b in batches), "would_batches": len(batches), "would_defer": len(deferred),
                "due": len(work["due"]), "waiting": len(work["waiting"]), "changed": len(work["changed"]),
                **{k: counts[k] for k in ("backlog_records", "acked", "rejected", "gap_requeued", "production_newer", "production_only")}})
    return out
