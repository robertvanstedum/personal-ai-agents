"""Standing watchers for Claude Code and Codex session files (v0.5 B6, amendment §2/§3).

``run_source`` is a function (and a ``moi`` command); **no launchd job is installed**.
Per source:

* **Approval gate.** A source with no approval record never captures. Its first run
  only writes a **dry-run listing** for the owner: counts, session ids, sizes and
  dates, never content. ``moi approve-source`` switches it on, bound to the source's
  fingerprint (root and ``never_copy``): change either and it is ``stale`` until
  re-approved.
* **never_copy** is applied before a file is opened or hashed; excluded files are
  counted (``never_copy``) and nothing else.
* **Change detection** by size + mtime, then sha256 of the file (streamed), kept in
  a state file under ``_status/watch-state/``. A file whose size or mtime changed
  while it was being read is ``unstable`` and retried next run.
* **Normalize** (format-only parsers, line by line, bounded memory), **scrub** (D8:
  the shelf keeps the scrubbed edition with the unscrubbed original's sha256 and a
  redaction count), stage in the outbox, apply to the shelf.
* Every item gets a ledger row; a crash between "discovered" and an outcome reads as
  **missing**. Failures are fixed codes only (no exception text).
* Headroom first: below the floor the run stops with ``disk_low`` and deletes nothing.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import approvals, codes, fsio, ledger, render, sessions
from core.memory_shelf import bundle as bundles
from core.memory_shelf.config import SourceCfg, never_copied

PARSERS = {"claude-code": sessions.parse_claude_code, "codex": sessions.parse_codex}
_UUID_TAIL = re.compile(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")


@dataclass(frozen=True)
class Candidate:
    rel: str
    path: Path
    size: int
    mtime_ns: int


def _wanted(kind: str, rel: str) -> bool:
    parts = rel.split("/")
    if kind == "claude-code":                   # <project>/<session>.jsonl; subfolders (sidechains) are not sessions
        return len(parts) == 2 and parts[1].endswith(".jsonl")
    return parts[-1].startswith("rollout-") and parts[-1].endswith(".jsonl")


def discover(cfg: SourceCfg) -> list[Candidate]:
    """Session files under the source root. Symlinks and special files are never followed."""
    found = []
    root = Path(cfg.root)
    if not root.is_dir():
        return found
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for name in sorted(files):
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if not _wanted(cfg.kind, rel):
                continue
            try:
                st = path.lstat()
            except OSError:
                continue
            if path.is_symlink() or not stat.S_ISREG(st.st_mode):
                continue
            found.append(Candidate(rel, path, st.st_size, st.st_mtime_ns))
    return sorted(found, key=lambda c: c.rel)


def session_id_from_name(kind: str, rel: str) -> str:
    stem = Path(rel).stem
    if kind == "codex":
        m = _UUID_TAIL.search(stem)
        return m.group(1) if m else stem
    return stem


def _date(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


def build_listing(cfg: SourceCfg, now: datetime | None = None) -> dict:
    """Dry-run listing: ids, sizes, dates and counts. Reads no file content and names no file."""
    rows, excluded, total = [], {}, 0
    for cand in discover(cfg):
        if never_copied(cand.rel, cfg.never_copy):
            excluded[codes.NEVER_COPY] = excluded.get(codes.NEVER_COPY, 0) + 1
            continue
        if cand.size == 0:
            excluded[codes.EMPTY] = excluded.get(codes.EMPTY, 0) + 1
            continue
        rows.append({"id": session_id_from_name(cfg.kind, cand.rel), "bytes": cand.size, "date": _date(cand.mtime_ns),
                     "decision": "would-copy"})
        total += cand.size
    digest = hashlib.sha256("\n".join(f"{r['id']}\t{r['bytes']}" for r in rows).encode()).hexdigest()
    return {"source": cfg.name, "kind": cfg.kind, "fingerprint": cfg.fingerprint(),
            "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "counts": {"would_copy": len(rows), "excluded": excluded, "bytes": total},
            "sessions": rows, "listing_sha256": digest}


def dry_run(shelf, cfg: SourceCfg, now: datetime | None = None) -> dict:
    shelf.init_layout()
    listing = build_listing(cfg, now)
    fsio.write_json(approvals.dry_run_path(shelf, cfg.name), listing)
    return listing


def _state_path(shelf, name: str) -> Path:
    return Path(shelf.status_dir) / "watch-state" / f"{name}.json"


def _write_status(shelf, name: str, status: str, counts: dict, now: datetime) -> None:
    fsio.write_json(Path(shelf.status_dir) / f"watch-{name}.json",
                    {"last_run_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status, "counts": counts})


def run_source(shelf, cfg: SourceCfg, *, now: datetime | None = None, settle_seconds: float = 0) -> dict:
    """One watcher pass. Returns ``{"status": ..., "counts": {...}}``; never raises on bad session data."""
    now = now or datetime.now(timezone.utc)
    name, counts = cfg.name, {}
    status = approvals.source_status(shelf, name, cfg.fingerprint())
    if status != approvals.APPROVED:
        listing = fsio.read_json(approvals.dry_run_path(shelf, name)) if status != approvals.NO_DRY_RUN else None
        if not isinstance(listing, dict) or listing.get("fingerprint") != cfg.fingerprint():
            dry_run(shelf, cfg, now)
            _write_status(shelf, name, "dry_run_only", {}, now)
            return {"status": "dry_run_only", "counts": {}}
        _write_status(shelf, name, codes.NOT_APPROVED, {}, now)
        return {"status": codes.NOT_APPROVED, "counts": {}}
    shelf.init_layout()
    if shelf.headroom():
        _write_status(shelf, name, codes.DISK_LOW, {}, now)
        return {"status": codes.DISK_LOW, "counts": {}}
    doc = fsio.read_json(_state_path(shelf, name))
    state: dict = doc.get("files", {}) if isinstance(doc, dict) else {}

    def tally(outcome: str) -> None:
        counts[outcome] = counts.get(outcome, 0) + 1

    try:
        for cand in discover(cfg):
            lkey = bundles.ledger_key_for(name, cand.rel)
            prev = state.get(lkey) or {}
            if never_copied(cand.rel, cfg.never_copy) or cand.size == 0:
                reason = codes.NEVER_COPY if never_copied(cand.rel, cfg.never_copy) else codes.EMPTY
                if prev.get("outcome") != codes.EXCLUDED or prev.get("reason") != reason:
                    ledger.record(shelf, name, lkey, codes.EXCLUDED, reason=reason, now=now)
                state[lkey] = {"outcome": codes.EXCLUDED, "reason": reason}
                tally(codes.EXCLUDED)
                continue
            if (prev.get("outcome") in codes.OK_OUTCOMES and prev.get("size") == cand.size
                    and prev.get("mtime_ns") == cand.mtime_ns):
                tally("skipped_unchanged")
                continue
            ledger.record(shelf, name, lkey, codes.DISCOVERED, now=now)
            outcome, rid, sha = _capture(shelf, cfg, cand, lkey, prev, now, settle_seconds)
            state[lkey] = {"outcome": outcome, "size": cand.size, "mtime_ns": cand.mtime_ns, "sha256": sha,
                           "record": rid, "rel": cand.rel}
            tally(outcome)
    finally:
        fsio.write_json(_state_path(shelf, name), {"files": state})
    _write_status(shelf, name, "ok", counts, now)
    return {"status": "ok", "counts": counts}


def _capture(shelf, cfg: SourceCfg, cand: Candidate, lkey: str, prev: dict, now: datetime,
             settle_seconds: float) -> tuple[str, str | None, str | None]:
    name = cfg.name
    if settle_seconds and (now.timestamp() - cand.mtime_ns / 1e9) < settle_seconds:
        ledger.record(shelf, name, lkey, codes.UNSTABLE, now=now)
        return codes.UNSTABLE, None, None
    try:
        sha, size = render.hash_file(cand.path)
        with open(cand.path, "rb") as handle:
            parsed = PARSERS[cfg.kind](handle)
        after = cand.path.stat()
    except OSError:
        ledger.record(shelf, name, lkey, codes.FAILED, reason="read_failed", now=now)
        return codes.FAILED, None, None
    except Exception:                                    # noqa: BLE001 - fixed code only, never the message
        ledger.record(shelf, name, lkey, codes.FAILED, reason="parse_failed", now=now)
        return codes.FAILED, None, None
    if after.st_size != cand.size or after.st_mtime_ns != cand.mtime_ns or size != cand.size:
        ledger.record(shelf, name, lkey, codes.UNSTABLE, now=now)
        return codes.UNSTABLE, None, None
    if prev.get("sha256") == sha and prev.get("record") and prev.get("outcome") in codes.OK_OUTCOMES:
        ledger.record(shelf, name, lkey, codes.UNCHANGED, record_id=prev["record"], now=now)   # touched, not changed
        return codes.UNCHANGED, prev["record"], sha
    if not parsed.turns:
        ledger.record(shelf, name, lkey, codes.EXCLUDED, reason=codes.NO_TURNS, now=now)
        return codes.EXCLUDED, None, sha
    sid = parsed.source_id or session_id_from_name(cfg.kind, cand.rel)
    started_fallback = datetime.fromtimestamp(cand.mtime_ns / 1e9, tz=timezone.utc)
    created = bundles.utc(parsed.started, started_fallback)
    bundle = bundles.from_parsed(
        parsed, sha, size, key=f"{parsed.provider}:{sid}", title=f"{parsed.provider} session {created[:10]}",
        origin=f"watcher:{name}", created=created, retained={"kind": "source-file", "source": name, "rel": cand.rel},
        ledger_key=lkey)
    path, code = shelf.stage(bundle)
    if code:
        ledger.record(shelf, name, lkey, codes.DISK_LOW, now=now)
        return codes.DISK_LOW, None, sha
    result = next((r for r in shelf.drain() if r.key == bundle.key), None)
    if result is None:
        return codes.FAILED, None, sha
    return result.outcome, result.record_id, sha


__all__ = ["run_source", "dry_run", "build_listing", "discover", "session_id_from_name"]
