"""Standing watchers for Claude Code and Codex session files and MC conversation exports (v0.5 B6, amendment §2/§3).

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
* **Size (17 Oct 2026).** Session files are read as a stream, one line at a time, and only turn text is kept, so file
  size is no longer a memory guard (the old 64 MiB ``source_too_large`` hold is retired for capture; rows written with it
  stay readable). What remains are sanity ceilings, each with its own fixed reason: ``source_over_ceiling`` (file, or
  decompressed stream, over ``MOI_CAPTURE_SOURCE_CEILING_BYTES``, default 2 GiB) and ``normalized_too_large`` (kept turn
  text over ``MOI_CAPTURE_MAX_NORMALIZED_BYTES``, default 128 MiB; enforced **while** the file is parsed, through
  ``sessions.retained_budget``, so the read stops as soon as the kept text passes it). A single line over ``MOI_CAPTURE_MAX_LINE_BYTES``
  (default 32 MiB, the Codex line budget) is never decoded: it is drained in bounded chunks and replaced by a
  ``moi:oversized_line`` placeholder, which the parser records as an omission pointer at its source line and the
  coverage check flags as ``unknown_kind`` (a Watch, never silent). A file that yields **no turns** while the coverage
  check flags it (every line oversized, or a gap or format drift) is never a quiet ``no_turns`` exclusion: it is a failed
  item with ``no_turns_oversized`` (placeholders present: a resource limit) or ``no_turns_flagged``, keeping its coverage
  flags in the ledger, the state and the owner's review item, and the pass finishes ``warn``.
* **Codex archives** (``rollout-*.jsonl.zst``) are read with the standard library's streaming zstd decoder. Their
  identity is the uncompressed file: the ledger/state key is the path without ``.zst`` and the sha256 is of the
  decompressed bytes, so a session Codex compressed without changing is ``unchanged``, not a new edition. While both
  forms exist (compression in flight) only the plain ``.jsonl`` is read. ``never_copy`` matches either name.
* **Status.** A pass with failed, unstable or disk-low items finishes ``warn`` (with the counts), never ``ok``.
"""
from __future__ import annotations

from core.jobs import batch_window

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import approvals, codes, coverage, fsio, ledger, mc, render, review, rooms, sessions, capture_limits
from core.memory_shelf import bundle as bundles
from core.memory_shelf.config import SourceCfg, never_copied

# Legacy: the fidelity replay cap for inbox and Rooms sources (fidelity._limit) and the reason old ledger rows carry.
# No longer a capture limit for session files (see the module notes).
DEFAULT_MAX_SOURCE_BYTES = 64 << 20
SOURCE_TOO_LARGE = "source_too_large"

DEFAULT_SOURCE_CEILING_BYTES = 2 << 30
SOURCE_CEILING_ENV = "MOI_CAPTURE_SOURCE_CEILING_BYTES"
SOURCE_OVER_CEILING = "source_over_ceiling"           # the file, or its decompressed stream, is over the sanity ceiling
DEFAULT_MAX_LINE_BYTES = capture_limits.CodexLimits().line_bytes
MAX_LINE_ENV = "MOI_CAPTURE_MAX_LINE_BYTES"
OVERSIZED_LINE = "moi:oversized_line"                 # placeholder line type for a line too long to decode
DEFAULT_MAX_NORMALIZED_BYTES = 128 << 20
MAX_NORMALIZED_ENV = "MOI_CAPTURE_MAX_NORMALIZED_BYTES"
NORMALIZED_TOO_LARGE = "normalized_too_large"         # the kept turn text alone is over the ceiling: nothing staged
CODEC_UNAVAILABLE = "source_codec_unavailable"        # a .zst file and no zstd decoder in this Python
NO_TURNS_OVERSIZED = "no_turns_oversized"             # no turns, and lines were too long to read: a resource limit, not empty
NO_TURNS_FLAGGED = "no_turns_flagged"                 # no turns, and the coverage check flags the file (gap or format drift)
COMPRESSED_SUFFIX = ".zst"
HOLD_CODES = frozenset({SOURCE_TOO_LARGE, SOURCE_OVER_CEILING, NORMALIZED_TOO_LARGE, CODEC_UNAVAILABLE,
                        NO_TURNS_OVERSIZED, NO_TURNS_FLAGGED}) | capture_limits.CODES
STATE_CHECKPOINT_S = 30.0                             # a long pass writes its state at least this often


class SourceTooLarge(ValueError):
    """Resource hold: never copy source content into an error. ``code`` is the fixed reason."""

    def __init__(self, code: str = SOURCE_TOO_LARGE):
        self.code = code
        super().__init__(code)


class CodecUnavailable(OSError):
    pass


def _positive_env(name: str, default: int) -> int | None:
    """The env override if it is a positive integer, the default when unset, None when set to anything else."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return None
    return value if value > 0 else None


def source_ceiling() -> int | None:
    return _positive_env(SOURCE_CEILING_ENV, DEFAULT_SOURCE_CEILING_BYTES)


def max_line_bytes() -> int | None:
    return _positive_env(MAX_LINE_ENV, DEFAULT_MAX_LINE_BYTES)


def max_normalized_bytes() -> int | None:
    return _positive_env(MAX_NORMALIZED_ENV, DEFAULT_MAX_NORMALIZED_BYTES)


def is_compressed(rel: str | Path) -> bool:
    return str(rel).endswith(COMPRESSED_SUFFIX)


def logical_rel(rel: str) -> str:
    """The session file a path stands for: a Codex archive ``x.jsonl.zst`` is ``x.jsonl``."""
    return rel[: -len(COMPRESSED_SUFFIX)] if is_compressed(rel) else rel


def open_source(path: Path):
    """A binary, line-readable stream of the session file's own (uncompressed) bytes."""
    if is_compressed(path):
        try:
            from compression import zstd            # standard library from Python 3.14; no third-party dependency
        except ImportError:
            raise CodecUnavailable(CODEC_UNAVAILABLE) from None
        return zstd.open(path, "rb")
    return open(path, "rb")


def hash_source(path: Path, limit: int) -> tuple[str, int]:
    """(sha256, size) of the session file's uncompressed bytes, streamed. A plain file is hashed as it is on disk."""
    if not is_compressed(path):
        sha, size = render.hash_file(path)
        if size > limit:
            raise SourceTooLarge(SOURCE_OVER_CEILING)
        return sha, size
    digest, size = hashlib.sha256(), 0
    with open_source(path) as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            size += len(chunk)
            if size > limit:
                raise SourceTooLarge(SOURCE_OVER_CEILING)
            digest.update(chunk)
    return digest.hexdigest(), size


def _bounded_lines(handle, limit, max_line_bytes=None, code=SOURCE_TOO_LARGE):
    remaining = limit
    while True:
        raw = handle.readline(min(remaining, max_line_bytes) + 1 if max_line_bytes else remaining + 1)
        if max_line_bytes and len(raw) > max_line_bytes:
            raise capture_limits.ResourceLimit("source_line_limit")
        if len(raw) > remaining:
            raise SourceTooLarge(code)
        if not raw:
            return
        remaining -= len(raw)
        yield raw


def oversized_placeholder(size: int) -> bytes:
    return (json.dumps({"type": OVERSIZED_LINE, "bytes": size}) + "\n").encode()


def stream_lines(handle, limit: int, line_limit: int, code: str = SOURCE_OVER_CEILING):
    """Lines of a session stream with bounded memory: at most ``line_limit + 1`` bytes are held for one line. A longer
    line is drained in 1 MiB pieces and yielded as one ``oversized_placeholder`` (line numbers stay exact). More than
    ``limit`` bytes in all raises ``SourceTooLarge(code)``."""
    remaining = limit
    while True:
        raw = handle.readline(min(remaining, line_limit) + 1)
        if not raw:
            return
        if len(raw) > remaining:
            raise SourceTooLarge(code)
        remaining -= len(raw)
        if len(raw) <= line_limit:
            yield raw
            continue
        size, ended = len(raw), raw.endswith(b"\n")
        del raw
        while not ended:
            piece = handle.readline(min(remaining, 1 << 20) + 1)
            if not piece:
                break
            if len(piece) > remaining:
                raise SourceTooLarge(code)
            remaining -= len(piece)
            size += len(piece)
            ended = piece.endswith(b"\n")
        yield oversized_placeholder(size)


PARSERS = {"claude-code": sessions.parse_claude_code, "codex": sessions.parse_codex, mc.KIND: mc.parse}
# A watcher kind whose records carry another provider name (the parser-version tables are keyed by provider).
KIND_PROVIDER = {mc.KIND: mc.PROVIDER}


def normalizer_for(kind: str) -> int:
    return sessions.NORMALIZER_VERSION[KIND_PROVIDER.get(kind, kind)]


_UUID_TAIL = re.compile(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")


@dataclass(frozen=True)
class Candidate:
    rel: str
    path: Path
    size: int
    mtime_ns: int


def _wanted(kind: str, rel: str) -> bool:
    parts = rel.split("/")
    if kind == mc.KIND:                         # <instance>--<conversation id>.json at the top of the export folder
        return mc.wanted(rel)
    if kind == "claude-code":                   # <project>/<session>.jsonl; subfolders (sidechains) are not sessions
        return len(parts) == 2 and parts[1].endswith(".jsonl")
    return parts[-1].startswith("rollout-") and parts[-1].endswith((".jsonl", ".jsonl" + COMPRESSED_SUFFIX))


def excluded_by_never_copy(cfg: SourceCfg, rel: str) -> bool:
    """``never_copy`` matches the path as it is and as the session file it stands for (an archive keeps its exclusion)."""
    return never_copied(rel, cfg.never_copy) or never_copied(logical_rel(rel), cfg.never_copy)


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
    plain = {c.rel for c in found if not is_compressed(c.rel)}
    # Both forms present: the archive is being written. Read the live file; the archive is read once it is alone.
    found = [c for c in found if not (is_compressed(c.rel) and logical_rel(c.rel) in plain)]
    return sorted(found, key=lambda c: c.rel)


def session_id_from_name(kind: str, rel: str) -> str:
    stem = Path(logical_rel(rel)).stem
    if kind == "codex":
        m = _UUID_TAIL.search(stem)
        return m.group(1) if m else stem
    return stem


def _date(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


def build_listing(cfg: SourceCfg, now: datetime | None = None) -> dict:
    """Dry-run listing: ids, sizes, dates and counts. Reads no file content and names no file.
    (A Rooms source is checked bundle by bundle, D2 included, and lists the same way: ids and counts only.)"""
    if cfg.kind == "rooms":
        return rooms.build_listing(cfg, now)
    rows, excluded, total = [], {}, 0
    for cand in discover(cfg):
        if excluded_by_never_copy(cfg, cand.rel):
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


_PROBLEM_COUNTS = (codes.FAILED, codes.UNSTABLE, codes.DISK_LOW)


def _write_status(shelf, name: str, status: str, counts: dict, now: datetime) -> None:
    """The source's own status file. ``status`` is ``warn`` (never ``ok``) when any item failed, was unstable or hit
    disk-low; the counts say how many and why. ``last_run_at`` is when the pass ran; ``last_success_at`` moves **only** when the pass
    finished ``ok`` with no failed, unstable or disk-low item, and ``last_new_capture_at`` only when it also captured or
    added an edition. They are carried forward otherwise, so a failing source keeps showing when it last worked and a run
    that found nothing new never looks like a capture."""
    path = Path(shelf.status_dir) / f"watch-{name}.json"
    prev = fsio.read_json(path)
    prev = prev if isinstance(prev, dict) else {}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    success = status == "ok" and not any(counts.get(k) for k in _PROBLEM_COUNTS)
    new = bool(counts.get(codes.CAPTURED) or counts.get(codes.EDITION_ADDED))
    doc = {"last_run_at": stamp, "status": status, "counts": counts,
           "last_success_at": stamp if success else prev.get("last_success_at"),
           "last_new_capture_at": stamp if (success and new) else prev.get("last_new_capture_at")}
    fsio.write_json(path, {k: v for k, v in doc.items() if v is not None})


def run_source(shelf, cfg: SourceCfg, *, now: datetime | None = None, settle_seconds: float = 0,
               max_source_bytes: int | None = None, source_ids=None,
               codex_limits: capture_limits.CodexLimits | None = None) -> dict:
    """One watcher pass. Returns ``{"status": ..., "counts": {...}}``; never raises on bad session data.

    ``max_source_bytes`` overrides the sanity ceiling (``MOI_CAPTURE_SOURCE_CEILING_BYTES``, default 2 GiB) for this pass."""
    now = now or datetime.now(timezone.utc)
    name, counts = cfg.name, {}
    if max_source_bytes is None:
        max_source_bytes = source_ceiling()
    line_limit, normalized_limit = max_line_bytes(), max_normalized_bytes()
    if type(max_source_bytes) is not int or max_source_bytes <= 0 or line_limit is None or normalized_limit is None:
        return {"status": "capture_limit_invalid", "counts": {codes.FAILED: 1}}
    selected = source_ids is not None
    if selected and (not isinstance(source_ids, (tuple, list)) or not 1 <= len(source_ids) <= 20
                     or any(not isinstance(s, str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", s) for s in source_ids) or len(set(source_ids)) != len(source_ids)):
        return {"status": "source_selection_invalid", "counts": {codes.FAILED: 1}}
    if selected and cfg.kind != "codex":
        return {"status": "source_selection_unsupported", "counts": {codes.FAILED: 1}}
    if codex_limits is not None and (not isinstance(codex_limits, capture_limits.CodexLimits) or cfg.kind != "codex" or not selected):
        return {"status": "bounded_codex_selection_required", "counts": {codes.FAILED: 1}}
    status = approvals.source_status(shelf, name, cfg.fingerprint())
    if status != approvals.APPROVED:
        listing = fsio.read_json(approvals.dry_run_path(shelf, name)) if status != approvals.NO_DRY_RUN else None
        if not isinstance(listing, dict) or listing.get("fingerprint") != cfg.fingerprint():
            dry_run(shelf, cfg, now)
            _write_status(shelf, name, "dry_run_only", {}, now)
            return {"status": "dry_run_only", "counts": {}}
        _write_status(shelf, name, codes.NOT_APPROVED, {}, now)
        return {"status": codes.NOT_APPROVED, "counts": {}}
    candidates = discover(cfg) if cfg.kind != "rooms" else []
    if selected:
        if cfg.kind == "rooms":
            return {"status": "source_selection_unsupported", "counts": {codes.FAILED: 1}}
        matches = {sid: [c for c in candidates if session_id_from_name(cfg.kind, c.rel) == sid] for sid in source_ids}
        if any(len(v) != 1 for v in matches.values()):
            return {"status": "source_selection_missing_or_ambiguous", "counts": {codes.FAILED: 1}}
        candidates = [matches[sid][0] for sid in source_ids]
    shelf.init_layout()
    if shelf.headroom():
        _write_status(shelf, name, codes.DISK_LOW, {}, now)
        return {"status": codes.DISK_LOW, "counts": {}}
    doc = fsio.read_json(_state_path(shelf, name))
    state: dict = doc.get("files", {}) if isinstance(doc, dict) else {}

    def tally(outcome: str) -> None:
        counts[outcome] = counts.get(outcome, 0) + 1

    last_saved = time.monotonic()

    def checkpoint() -> None:
        """A pass over many large files can outlive its caller's timeout: keep what is done so far."""
        nonlocal last_saved
        if time.monotonic() - last_saved >= STATE_CHECKPOINT_S:
            fsio.write_json(_state_path(shelf, name), {"files": state})
            last_saved = time.monotonic()

    if cfg.kind == "rooms":
        try:
            _rooms_pass(shelf, cfg, state, counts, now, settle_seconds)
        finally:
            fsio.write_json(_state_path(shelf, name), {"files": state})
        return _finish(shelf, name, state, counts, now)
    try:
        for cand in candidates:
            try:
                batch_window.remaining()
            except batch_window.BatchDeadline as exc:
                counts[exc.code] = counts.get(exc.code, 0) + 1
                break
            lkey = bundles.ledger_key_for(name, logical_rel(cand.rel))     # an archive keeps its session's key
            prev = state.get(lkey) or {}
            if excluded_by_never_copy(cfg, cand.rel) or cand.size == 0:
                reason = codes.NEVER_COPY if excluded_by_never_copy(cfg, cand.rel) else codes.EMPTY
                if prev.get("outcome") != codes.EXCLUDED or prev.get("reason") != reason:
                    ledger.record(shelf, name, lkey, codes.EXCLUDED, reason=reason, now=now)
                state[lkey] = {"outcome": codes.EXCLUDED, "reason": reason}
                tally(codes.EXCLUDED)
                continue
            if cand.size > max_source_bytes:
                ledger.record(shelf, name, lkey, codes.FAILED, reason=SOURCE_OVER_CEILING, now=now)
                state[lkey] = {**prev, "outcome": codes.FAILED, "reason": SOURCE_OVER_CEILING,
                               "observed_size": cand.size, "limit_bytes": max_source_bytes}
                tally(codes.FAILED); tally(SOURCE_OVER_CEILING)
                continue
            current = normalizer_for(cfg.kind)
            if (not selected and prev.get("outcome") in codes.OK_OUTCOMES and prev.get("size") == cand.size
                    and prev.get("mtime_ns") == cand.mtime_ns
                    and prev.get("normalizer", sessions.DEFAULT_NORMALIZER) == current):
                tally("skipped_unchanged")
                continue
            ledger.record(shelf, name, lkey, codes.DISCOVERED, now=now)
            outcome, rid, sha, cov = _capture(shelf, cfg, cand, lkey, prev, now, settle_seconds, max_source_bytes, codex_limits,
                                                session_id_from_name(cfg.kind, cand.rel) if selected else None,
                                                line_limit=line_limit, normalized_limit=normalized_limit)
            if outcome in HOLD_CODES:
                state[lkey] = {**prev, "outcome": codes.FAILED, "reason": outcome,
                               "observed_size": cand.size, "limit_bytes": max_source_bytes}
                if cov is not None:                         # read in full and flagged (no turns): the flags stay visible
                    _sync_coverage(shelf, name, lkey, cand, cov)
                    if cov.get("flags"):
                        state[lkey]["flags"] = cov["flags"]
                tally(codes.FAILED); tally(outcome)
                checkpoint()
                continue
            if outcome in codes.OK_OUTCOMES and cov is not None:
                _sync_coverage(shelf, name, lkey, cand, cov)
                flags = cov.get("flags") or []
            else:
                flags = prev.get("flags") or []             # not re-read this pass: what we knew stays true
            state[lkey] = {"outcome": outcome, "size": cand.size, "mtime_ns": cand.mtime_ns, "sha256": sha,
                           "record": rid, "rel": cand.rel, "normalizer": current,
                           **({"flags": flags} if flags else {})}
            if outcome not in codes.OK_OUTCOMES:
                state[lkey]["normalizer"] = prev.get("normalizer", sessions.DEFAULT_NORMALIZER)
            tally(outcome)
            checkpoint()
    finally:
        fsio.write_json(_state_path(shelf, name), {"files": state})
    return _finish(shelf, name, state, counts, now, selected=selected)


def _finish(shelf, name: str, state: dict, counts: dict, now: datetime, *, selected=False) -> dict:
    for flag in (coverage.POSSIBLE_GAP, coverage.UNKNOWN_KIND):
        flagged = sum(1 for entry in state.values() if flag in (entry.get("flags") or []))
        if flagged:
            counts[flag] = flagged                          # files flagged now, not only this pass: a gap stays a warning
    status = "batch_deadline_invalid" if counts.get("batch_deadline_invalid") else ("outside_window" if counts.get("outside_window") else "ok")
    if status == "ok" and any(counts.get(k) for k in _PROBLEM_COUNTS):
        status = "warn"                                 # something was not captured: never report it as ok
    if selected:
        status = "selected_only"
        fsio.write_json(Path(shelf.status_dir) / f"watch-{name}-selected.json",
                        {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status, "counts": counts})
    else:
        _write_status(shelf, name, status, counts, now)
    return {"status": status, "counts": counts}


def _rooms_pass(shelf, cfg: SourceCfg, state: dict, counts: dict, now: datetime, settle_seconds: float) -> None:
    """One pass over a Rooms source's bundles, oldest revision of each session first. Bundles are immutable, so a
    bundle already judged (kept, excluded or refused) is not read again; its outcome is still counted every pass, so an
    exclusion or a refusal stays visible until someone deals with it."""
    name = cfg.name
    current = sessions.NORMALIZER_VERSION["rooms"]
    cands, passed = rooms.discover(cfg)
    for kind, n in passed.items():
        counts[f"passed_over_{kind}"] = n

    def tally(outcome: str, reason: str | None = None) -> None:
        counts[outcome] = counts.get(outcome, 0) + 1
        if reason and outcome in (codes.EXCLUDED, codes.REFUSED):
            counts[reason] = counts.get(reason, 0) + 1

    for cand in cands:
        try:
            batch_window.remaining()
        except batch_window.BatchDeadline as exc:
            counts[exc.code] = counts.get(exc.code, 0) + 1
            break
        skey = bundles.ledger_key_for(name, cand.rel)
        lkey = rooms.session_key(name, cand.session)
        prev = state.get(skey) or {}
        judged = prev.get("outcome") in (*codes.OK_OUTCOMES, codes.EXCLUDED, codes.REFUSED)
        if (judged and prev.get("size") == cand.size and prev.get("mtime_ns") == cand.mtime_ns
                and prev.get("normalizer", sessions.DEFAULT_NORMALIZER) == current):
            if prev["outcome"] in (codes.EXCLUDED, codes.REFUSED):
                tally(prev["outcome"], prev.get("reason"))
            else:
                tally("skipped_unchanged")
            continue
        ledger.record(shelf, name, lkey, codes.DISCOVERED, now=now)
        outcome, rid, sha, cov, reason = rooms.capture(shelf, cfg, cand, skey, prev, now, settle_seconds, lkey)
        if outcome in codes.OK_OUTCOMES:
            review.sync_rooms_exclusion(shelf, lkey, None)
            if cov is not None:
                _sync_coverage(shelf, name, lkey, cand, cov)
        flags = (cov or {}).get("flags") or prev.get("flags") or []
        state[skey] = {"outcome": outcome, "size": cand.size, "mtime_ns": cand.mtime_ns, "sha256": sha, "record": rid,
                       "rel": cand.rel, "normalizer": current if outcome in (*codes.OK_OUTCOMES, codes.EXCLUDED, codes.REFUSED)
                       else prev.get("normalizer", sessions.DEFAULT_NORMALIZER),
                       **({"reason": reason} if reason else {}), **({"flags": flags} if flags else {})}
        tally(outcome, reason)


def _sync_coverage(shelf, source: str, lkey: str, cand: Candidate, cov: dict) -> None:
    """Keep the owner's review item in step with the coverage check: open while flagged, resolved once clear."""
    flags = cov.get("flags") or []
    review.sync_coverage(shelf, lkey, {"source": source, "date": _date(cand.mtime_ns), "bytes": cand.size,
                                       "flags": flags, "gap": cov.get("gap") or {},
                                       "unknown": sorted((cov.get("unknown") or {}))[:10]} if flags else None)


def _capture(shelf, cfg: SourceCfg, cand: Candidate, lkey: str, prev: dict, now: datetime,
             settle_seconds: float, max_source_bytes: int = DEFAULT_SOURCE_CEILING_BYTES, codex_limits=None, expected_source_id=None,
             *, line_limit: int = DEFAULT_MAX_LINE_BYTES, normalized_limit: int = DEFAULT_MAX_NORMALIZED_BYTES
             ) -> tuple[str, str | None, str | None, dict | None]:
    """(outcome, record id, sha256, coverage). Coverage is None when the file was not re-read into a record.

    Memory is bounded by the longest line read (``line_limit``) plus the turn text kept, never by the file's size.
    ``sha256`` and the size checked against the ceiling are of the uncompressed session bytes."""
    name = cfg.name
    if settle_seconds and (now.timestamp() - cand.mtime_ns / 1e9) < settle_seconds:
        ledger.record(shelf, name, lkey, codes.UNSTABLE, now=now)
        return codes.UNSTABLE, None, None, None
    compressed = is_compressed(cand.rel)
    try:
        sha, size = hash_source(cand.path, max_source_bytes)
        with open_source(cand.path) as handle:
            effective_limits = codex_limits or (capture_limits.CodexLimits() if expected_source_id is not None else None)
            if effective_limits is not None:            # the explicit bounded Codex refresh: a long line refuses the source
                lines = _bounded_lines(handle, max_source_bytes, effective_limits.line_bytes, code=SOURCE_OVER_CEILING)
                lines = capture_limits.checked_codex_lines(lines, effective_limits, expected_source_id)
            else:
                # An MC export is one JSON document (indented); its own bound is the line limit, so no placeholder
                # can ever replace part of it.
                lines = stream_lines(handle, max_source_bytes, mc.MAX_BYTES + 1 if cfg.kind == mc.KIND else line_limit)
            # The kept turn text is charged as it is added: over ``normalized_limit`` the read stops at once.
            with sessions.retained_budget(normalized_limit):
                if cfg.kind == mc.KIND:
                    parsed = PARSERS[cfg.kind](lines)         # the export carries its own counts: the parser sets coverage
                else:
                    parsed = coverage.parse_with_coverage(cfg.kind, lines, PARSERS)
            if expected_source_id is not None and parsed.source_id != expected_source_id:
                raise capture_limits.ResourceLimit("source_identity_mismatch")
        after = cand.path.stat()
        if sum(len(t.text.encode("utf-8")) for t in parsed.turns) > normalized_limit:
            raise SourceTooLarge(NORMALIZED_TOO_LARGE)
    except capture_limits.ResourceLimit as exc:
        ledger.record(shelf, name, lkey, codes.FAILED, reason=exc.code, now=now)
        return exc.code, None, None, None
    except SourceTooLarge as exc:
        ledger.record(shelf, name, lkey, codes.FAILED, reason=exc.code, now=now)
        return exc.code, None, None, None
    except sessions.RetainedTooLarge:
        ledger.record(shelf, name, lkey, codes.FAILED, reason=NORMALIZED_TOO_LARGE, now=now)
        return NORMALIZED_TOO_LARGE, None, None, None
    except CodecUnavailable:
        ledger.record(shelf, name, lkey, codes.FAILED, reason=CODEC_UNAVAILABLE, now=now)
        return CODEC_UNAVAILABLE, None, None, None
    except OSError:
        ledger.record(shelf, name, lkey, codes.FAILED, reason="read_failed", now=now)
        return codes.FAILED, None, None, None
    except mc.BadExport as exc:                          # the message is a fixed code (bad_export | too_large)
        ledger.record(shelf, name, lkey, codes.FAILED, reason=str(exc), now=now)
        return codes.FAILED, None, None, None
    except Exception:                                    # noqa: BLE001 - fixed code only, never the message
        ledger.record(shelf, name, lkey, codes.FAILED, reason="parse_failed", now=now)
        return codes.FAILED, None, None, None
    if after.st_size != cand.size or after.st_mtime_ns != cand.mtime_ns or (not compressed and size != cand.size):
        ledger.record(shelf, name, lkey, codes.UNSTABLE, now=now)
        return codes.UNSTABLE, None, None, None
    if (prev.get("sha256") == sha and prev.get("record") and prev.get("outcome") in codes.OK_OUTCOMES
            and prev.get("normalizer", sessions.DEFAULT_NORMALIZER) == parsed.normalizer):
        ledger.record(shelf, name, lkey, codes.UNCHANGED, record_id=prev["record"], flags=prev.get("flags"), now=now)
        return codes.UNCHANGED, prev["record"], sha, None          # touched, not changed
    if not parsed.turns:
        cov = parsed.coverage or {}
        oversized = parsed.omitted.get(f"line:{OVERSIZED_LINE}", 0)
        if oversized or cov.get("flags"):
            # Not a clean empty session: the reader could not see everything (a resource limit or a flagged gap/drift).
            # Fail it, keeping the coverage flags, so it is a Watch and the pass warns; never a quiet no_turns exclusion.
            reason = NO_TURNS_OVERSIZED if oversized else NO_TURNS_FLAGGED
            if oversized and coverage.UNKNOWN_KIND not in (cov.get("flags") or []):
                cov = {**cov, "flags": [*(cov.get("flags") or []), coverage.UNKNOWN_KIND]}
            ledger.record(shelf, name, lkey, codes.FAILED, reason=reason, flags=cov.get("flags"), gap=cov.get("gap"),
                          now=now)
            return reason, None, sha, cov
        ledger.record(shelf, name, lkey, codes.EXCLUDED, reason=codes.NO_TURNS, now=now)
        return codes.EXCLUDED, None, sha, None
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
        return codes.DISK_LOW, None, sha, None
    result = next((r for r in shelf.drain() if r.key == bundle.key), None)
    if result is None and not path.exists():
        result = shelf.ingest(bundle)                  # another process's drain applied our staged bundle: ask again (idempotent)
    if result is None:
        return codes.FAILED, None, sha, None
    return result.outcome, result.record_id, sha, parsed.coverage


__all__ = ["run_source", "dry_run", "build_listing", "discover", "session_id_from_name", "open_source", "hash_source",
           "stream_lines", "source_ceiling", "max_line_bytes", "logical_rel"]
