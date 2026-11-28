"""Lock ownership and orphan traps for the Vault jobs (WS3 C1.3). Read-only by construction; never cleans anything up.

**The real lock mechanisms** (read from the code at 2f167cff, 28 Nov 2026). Every one is an advisory ``flock`` on a
file that persists after release, so a lock file's age or existence proves nothing (Codex review finding 3):

    vault_refresh  <vault>/.refresh.lock                       production.Admission / _Lock (apply, seed, refresh)
    seed_index     <vault>-work/meaning-seed/memory.sqlite.lock  indexer.index_lock (the seed's index step)
    cos_export     <vault>-sync/cos-export.lock                  cos_export (non-blocking; busy: cos_export_busy)
    mc_export      <vault>-sync/mc-export.lock                   mc_export.Admission

**The probe** (``probe``): open the existing file read-only with ``O_NOFOLLOW`` (never ``O_CREAT``, never unlink, never
rename), try a *shared* non-blocking ``flock``, and release it at once. A live owner holds an exclusive lock, so the
probe cannot acquire it and reports ``held``; the probe never takes an exclusive lock, never blocks, and never alters
the file. The one side effect: an owner that tries to take its lock in the same instant the probe holds the shared
lock is refused once, as busy (``apply_busy``), and records that refusal; it retries on its next schedule. Results:
``held``, ``free``, ``absent`` (no file yet), ``unknown`` (anything else, e.g. a symlink or a permission error).

**Orphans** (``classify``): the seed container has a fixed name (``minimoi-vault-seed``) and index steps the prefix
``minimoi-vault-index-`` (#705). The host process that starts either holds ``vault_refresh`` for the whole run. So a
container is an **orphan** only when all three hold: it is older than its approved budget plus five minutes, the
``vault_refresh`` lock is known not held (free or absent), and the owner jobs' statuses are readable and not
``running``. A held lock or a running owner is a legitimate long step (reported, not an orphan); an unknown lock state
or an unreadable owner status makes it **undetermined**, never an orphan and never fine. A container with no recognised name but a
``vault-work`` mount is an **unknown holder** (the pre-#705 kind). A count alone is an inventory figure, never proof.
Cleanup stays manual (runbook); nothing here removes a container.
"""
from __future__ import annotations

import errno
import fcntl
import os
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SEED_CONTAINER, INDEX_PREFIX = "minimoi-vault-seed", "minimoi-vault-index-"
SEED_BUDGET_S = 420 + 300        # apply.SEED_SLICE_S plus the client's own margin (DockerRunner.seed)
INDEX_BUDGET_S = 3600            # the longest index step the apply allows before it removes its own container
ORPHAN_MARGIN_S = 300
LOCKS = ("vault_refresh", "seed_index", "cos_export", "mc_export")


def lock_paths(vault_root: str | Path) -> dict:
    root = Path(vault_root)
    work, sync = root.parent / f"{root.name}-work", root.parent / f"{root.name}-sync"
    return {"vault_refresh": root / ".refresh.lock", "seed_index": work / "meaning-seed" / "memory.sqlite.lock",
            "cos_export": sync / "cos-export.lock", "mc_export": sync / "mc-export.lock"}


def probe(path: str | Path) -> str:
    try:
        # O_NONBLOCK: a FIFO or device in the lock's place must not block the exporter (Codex locks review 2).
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unknown"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "unknown"                             # only a regular file is a lock file
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            return "held" if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES) else "unknown"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "free"
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Container:
    name: str
    started_at: datetime | None
    mounts: tuple = ()               # destination:source strings


def list_containers(run=subprocess.run, docker: str = "docker") -> list[Container] | None:
    """Running containers with their start time and mounts, read-only (``docker ps`` + ``docker inspect``). ``None``
    when docker could not say: an unknown, never an empty (healthy-looking) list."""
    try:
        ids = run([docker, "ps", "-q"], capture_output=True, text=True, timeout=30)
        if ids.returncode != 0:
            return None
        found = (ids.stdout or "").split()
        if not found:
            return []
        out = run([docker, "inspect", "--format",
                   "{{.Name}}|{{.State.StartedAt}}|{{range .Mounts}}{{.Destination}}:{{.Source}};{{end}}", *found],
                  capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    from core.jobs.status import parse_time
    rows = []
    for line in (out.stdout or "").splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3 or not parts[0].startswith("/"):
            return None                                  # a malformed row: the listing is partial, so unknown
        name, started, mounts = parts
        moment = parse_time(started.replace(" +0000 UTC", "Z"))
        if moment is None:
            return None
        rows.append(Container(name.lstrip("/"), moment, tuple(m for m in mounts.split(";") if m)))
    if len(rows) != len(found):
        return None                                      # not every running container was described: unknown, never zero
    return rows


def classify(containers: list[Container] | None, refresh_lock: str, owner_running: bool | None, now: datetime) -> dict:
    """Counts by kind for the exporter: {kind: {"count", "orphans", "undetermined", "oldest_s"}} with kinds seed,
    index_step and unknown_holder, or ``None`` when docker could not be read (unknown, not zero).

    The owner is whichever Vault job holds ``vault_refresh`` (apply or seed; they exclude each other), judged as a
    whole, not per container. An over-budget container is an **orphan** only when the lock is ``free`` or ``absent``
    **and** ``owner_running`` is ``False`` (both owner statuses readable, neither running). When the lock state is
    ``unknown`` or the owner status cannot be read (``owner_running is None``) it is **undetermined**, never an orphan
    and never fine, even when the other input looks live. With both inputs known, a held lock or a running owner means
    no orphan claim."""
    if containers is None:
        return None
    out = {k: {"count": 0, "orphans": 0, "undetermined": 0, "oldest_s": 0} for k in ("seed", "index_step", "unknown_holder")}
    lock_known_free = refresh_lock in ("free", "absent")
    lock_unknown = refresh_lock not in ("free", "absent", "held")
    for c in containers:
        if c.name == SEED_CONTAINER:
            kind, budget = "seed", SEED_BUDGET_S
        elif c.name.startswith(INDEX_PREFIX):
            kind, budget = "index_step", INDEX_BUDGET_S
        elif any("vault-work" in m for m in c.mounts):
            kind, budget = "unknown_holder", None
        else:
            continue
        age = int((now - c.started_at).total_seconds()) if c.started_at else None
        slot = out[kind]
        slot["count"] += 1
        if age is not None:
            slot["oldest_s"] = max(slot["oldest_s"], age)
        if budget is None or age is None or age <= budget + ORPHAN_MARGIN_S:
            continue
        if lock_unknown or owner_running is None:
            slot["undetermined"] += 1                    # any unknown input keeps the uncertainty, even beside a live sign
            continue
        if refresh_lock == "held" or owner_running is True:
            continue                                     # a live owner, fully known: a long step, not an orphan
        if lock_known_free and owner_running is False:
            slot["orphans"] += 1
    return out
