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
``vault_refresh`` lock is not held, and the owning job's status is not ``running``. A container whose owner is held
and running is a legitimate long step (reported, not an orphan). A container with no recognised name but a
``vault-work`` mount is an **unknown holder** (the pre-#705 kind). A count alone is an inventory figure, never proof.
Cleanup stays manual (runbook); nothing here removes a container.
"""
from __future__ import annotations

import errno
import fcntl
import os
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
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unknown"
    try:
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
        if len(parts) != 3:
            continue
        name, started, mounts = parts
        rows.append(Container(name.lstrip("/"), parse_time(started.replace(" +0000 UTC", "Z")),
                              tuple(m for m in mounts.split(";") if m)))
    return rows


def classify(containers: list[Container] | None, refresh_lock: str, owner_running: bool, now: datetime) -> dict:
    """Counts by kind for the exporter: {kind: {"count", "orphans", "oldest_s"}} with kinds seed, index_step and
    unknown_holder, or ``None`` when docker could not be read (unknown, not zero)."""
    if containers is None:
        return None
    out = {k: {"count": 0, "orphans": 0, "oldest_s": 0} for k in ("seed", "index_step", "unknown_holder")}
    owner_alive = refresh_lock == "held" and owner_running
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
        if budget is not None and age is not None and age > budget + ORPHAN_MARGIN_S and not owner_alive:
            slot["orphans"] += 1
    return out
