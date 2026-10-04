"""The shelf writer (v0.5 B1, amendment R3, §4).

Layout under one configurable root (dev default ``~/minimoi-staging/data/memory/``,
never the repo)::

    sessions-raw/  sessions/  notes/  turns/  snapshots/  briefs/    record kinds
    outbox/   staged bundles waiting to be applied (kept in outbox/_done/ afterwards, never deleted)
    _ledger/  capture ledger, source index          _status/  dry runs, approvals, review queue, run status

A record is a folder ``<slug>--<short-ulid>/`` holding ``<slug>--<short-ulid>.md`` (the
normalized form of the *current* edition) and ``editions/<n>--<sha256-12>.jsonl``
(immutable, never overwritten). Folders 0700, files 0600, every write atomic, and
the headroom check runs first: below the floor the writer returns ``disk_low`` and
deletes nothing.

**Dedup (R3).** A bundle matches an existing record **only** on its exact source
key (``provider:source_id``). Same key and same source hash is a no-op; same key
with changed bytes adds an edition (``edition-added``) and refreshes the main file.
Title, date or first-turn overlap with a manual-submission source only raises a
``possible-same-conversation`` review item. Nothing is ever merged here.

**Designation (R2).** A source-identified marker adds ``designated-curated`` and
nothing else; a paste's marker becomes a review candidate. This module can build
no approval event: it never imports ``OwnerAuthority``.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.memory_shelf import codes, editions, fsio, ledger, review, ulid
from core.memory_shelf import events as ev
from core.memory_shelf import record
from core.memory_shelf.bundle import OWNER_ACTOR, Bundle

RECORD_KINDS = ("sessions-raw", "sessions", "notes", "turns", "snapshots", "briefs")
SESSION_DIR = "sessions-raw"
MANUAL_PROVIDERS = frozenset({"paste", "claude-ai"})        # B7: where title/date overlap is worth a flag
MAX_FLAGS = 5


@dataclass(frozen=True)
class Result:
    outcome: str
    key: str = ""
    record_id: str | None = None
    reason: str | None = None


def read_front_matter(path: Path, limit: int = 1 << 20) -> dict:
    """Front matter only (a record body can be large)."""
    buf = b""
    with open(path, "rb") as handle:
        while len(buf) < limit:
            chunk = handle.read(1 << 16)
            if not chunk:
                break
            buf += chunk
            if b"\n---\n" in buf[4:]:
                break
    text = buf.decode("utf-8", errors="replace")
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end == -1:
        raise record.InvalidRecord("front matter not found")
    import yaml
    meta = yaml.safe_load(text[4:end])
    if not isinstance(meta, dict):
        raise record.InvalidRecord("front matter must be a mapping")
    return meta


def edition_source_hash(path: Path) -> str | None:
    """The source sha256 declared on an edition file's first (header) line."""
    import json
    try:
        with open(path, "rb") as handle:
            head = json.loads(handle.readline(1 << 20))
        return head.get("source_sha256")
    except (OSError, ValueError, AttributeError):
        return None


class Shelf:
    def __init__(self, root: str | Path, *, min_free_bytes: int | None = fsio.DEFAULT_MIN_FREE_BYTES,
                 min_free_fraction: float | None = None, clock: Callable[[], datetime] | None = None):
        self.root = Path(root)
        self.min_free_bytes, self.min_free_fraction = min_free_bytes, min_free_fraction
        self.now = clock or (lambda: datetime.now(timezone.utc))

    # ── layout ────────────────────────────────────────────────────────────────
    @property
    def outbox(self) -> Path: return self.root / "outbox"
    @property
    def ledger_dir(self) -> Path: return self.root / "_ledger"
    @property
    def status_dir(self) -> Path: return self.root / "_status"
    @property
    def index_path(self) -> Path: return self.ledger_dir / "source_index.json"
    @property
    def _dirty(self) -> Path: return self.ledger_dir / "index.dirty"

    def init_layout(self) -> None:
        for name in (*RECORD_KINDS, "outbox", "_ledger", "_status"):
            fsio.ensure_dir(self.root / name)

    def headroom(self) -> str | None:
        return fsio.check_headroom(self.root, self.min_free_bytes, self.min_free_fraction)

    # ── outbox ────────────────────────────────────────────────────────────────
    def stage(self, bundle: Bundle) -> tuple[Path | None, str | None]:
        """Write a bundle to the outbox. ``(None, "disk_low")`` when there is no headroom."""
        if self.headroom():
            return None, codes.DISK_LOW
        path = self.outbox / f"{bundle.meta['id']}.json"
        fsio.write_atomic(path, bundle.to_json())
        return path, None

    def pending(self) -> list[Path]:
        return sorted(self.outbox.glob("*.json")) if self.outbox.is_dir() else []

    def drain(self) -> list[Result]:
        """Apply every staged bundle. Applied bundles move to ``outbox/_done/``; a bundle that cannot be
        applied (``disk_low``, unreadable) stays where it is for the next run."""
        results = []
        for path in self.pending():
            try:
                bundle = Bundle.from_json(path.read_bytes())
            except (OSError, ValueError, KeyError):
                results.append(Result(codes.FAILED, reason="bundle_unreadable"))
                continue
            result = self.ingest(bundle)
            results.append(result)
            if result.outcome in codes.OK_OUTCOMES:
                done = self.outbox / "_done"
                fsio.ensure_dir(done)
                os.replace(path, done / path.name)
        return results

    # ── source index ──────────────────────────────────────────────────────────
    def _entry_for(self, meta: dict, stem: str) -> dict:
        norm = meta.get("normalized") or {}
        return {"dir": stem, "id": meta["id"], "kind": meta.get("kind"), "provider": str(meta["source"]).split(":", 1)[0],
                "title": norm.get("title"), "date": str(meta.get("created", ""))[:10],
                "first_human": norm.get("first_human_sha256")}

    def scan_index(self) -> dict:
        """Rebuild the index from the records' own front matter (read-only; nothing is written)."""
        index = {}
        base = self.root / SESSION_DIR
        for main in sorted(base.glob("*/*.md")) if base.is_dir() else []:
            try:
                meta = read_front_matter(main)
                record.check(meta)
            except (record.InvalidRecord, OSError):
                continue
            if main.parent.name == main.stem:
                index[str(meta["source"])] = self._entry_for(meta, main.stem)
        return index

    def index(self) -> dict:
        """The source index. Missing, unreadable, or left dirty by a crashed write: rebuilt from the records.
        Reading never writes, so the read-only auditor can call it."""
        if self._dirty.exists() or not self.index_path.exists():
            return self.scan_index()
        doc = fsio.read_json(self.index_path)
        return doc if isinstance(doc, dict) else self.scan_index()

    def main_path(self, entry: dict) -> Path:
        return self.root / SESSION_DIR / entry["dir"] / f"{entry['dir']}.md"

    def find_record(self, record_id: str) -> Path | None:
        for entry in self.index().values():
            if entry["id"] == record_id:
                return self.main_path(entry)
        return None

    def resolve_id(self, token: str) -> tuple[str | None, str | None]:
        """A full ULID or the 8-character short id, matched **exactly** (never by prefix or similarity):
        ``(record id, None)``, or ``(None, "unknown_id" | "ambiguous_id")``."""
        found = sorted({e["id"] for e in self.index().values()
                        if e["id"] == token or (len(token) == 8 and ulid.is_ulid(e["id"]) and ulid.short(e["id"]) == token.lower())})
        if len(found) == 1:
            return found[0], None
        return None, ("ambiguous_id" if found else "unknown_id")

    def has_record(self, record_id: str) -> bool:
        path = self.find_record(record_id)
        return bool(path and path.is_file())

    def list_records(self, *, canary: bool = False) -> list[dict]:
        """Ordinary listing excludes canaries; they appear only when asked for by name (R4)."""
        return [e for e in self.index().values() if (e.get("kind") == "canary") == canary]

    # ── ingest ────────────────────────────────────────────────────────────────
    def ingest(self, bundle: Bundle) -> Result:
        code = self.headroom()
        if code:
            return Result(codes.DISK_LOW, bundle.key, reason=code)
        self.init_layout()
        index = self.index()                       # rebuilt first if an earlier run died mid-write
        fsio.write_atomic(self._dirty, b"1")
        try:
            entry = index.get(bundle.key)
            result = self._add_edition(bundle, entry) if entry else self._create(bundle, index)
        except Exception:                          # noqa: BLE001 - leave the dirty mark; fixed code only
            ledger.record(self, bundle.origin.split(":", 1)[-1], bundle.ledger_key or bundle.key[:16], codes.FAILED,
                          reason="write_failed", canary=bundle.kind == "canary", now=self.now())
            return Result(codes.FAILED, bundle.key, reason="write_failed")
        fsio.write_json(self.index_path, index)
        self._dirty.unlink(missing_ok=True)
        norm = bundle.meta.get("normalized") or {}
        ledger.record(self, bundle.origin.split(":", 1)[-1], bundle.ledger_key or bundle.key[:16], result.outcome,
                      record_id=result.record_id, canary=bundle.kind == "canary", turns=norm.get("turns", 0),
                      redacted_turns=norm.get("redacted_turns", 0), omitted=norm.get("omitted"), now=self.now())
        return result

    def _designate(self, meta: dict, bundle: Bundle) -> None:
        """Marker handling (R2): auto adds ``designated-curated`` only; a candidate goes to the owner."""
        marker = bundle.designation
        if not marker or bundle.kind == "canary":
            return
        if marker["mode"] == "auto":
            if not any(e["kind"] == "designated-curated" for e in meta["events"]):
                meta["events"].append(ev.make_event("designated-curated", OWNER_ACTOR, now=self.now(),
                                                    via="transcript-marker", note=f"turn {marker['ordinal']}"))
        else:
            review.add(self, "designation-candidate", ulid.short(meta["id"]),
                       {"record": meta["id"], "ordinal": marker["ordinal"]})

    def _create(self, bundle: Bundle, index: dict) -> Result:
        meta = dict(bundle.meta)
        meta["events"] = list(meta.get("events") or [])
        stem = ulid.filename(bundle.title, meta["id"], "md")[:-3]
        folder = self.root / SESSION_DIR / stem
        if folder.exists():
            raise FileExistsError                  # same short id, different record: refuse, never overwrite
        fsio.ensure_dir(folder)
        edition = editions.add_edition(folder, bundle.edition, "jsonl")
        meta["edition"], meta["edition_hash"] = edition.number, editions.digest(bundle.edition)
        if bundle.retained:
            meta["retained"] = bundle.retained          # where the original lives (for the fidelity re-parse)
        self._designate(meta, bundle)
        record.write(folder / f"{stem}.md", meta, bundle.body)
        self._flag_possible_same(bundle, meta, index)
        index[bundle.key] = self._entry_for(meta, stem)
        return Result(codes.CAPTURED, bundle.key, meta["id"])

    def _seen_hashes(self, folder: Path, meta: dict) -> set[str]:
        seen = {meta.get("source_hash")}
        seen.update(e.get("source_hash") for e in meta.get("events", []) if e.get("kind") == "edition-added")
        seen.update(edition_source_hash(e.path) for e in editions.list_editions(folder))
        seen.discard(None)
        return seen

    def _add_edition(self, bundle: Bundle, entry: dict) -> Result:
        main = self.main_path(entry)
        meta, _ = record.load(main.read_text("utf-8"))
        folder = main.parent
        if bundle.source_hash in self._seen_hashes(folder, meta):
            return Result(codes.UNCHANGED, bundle.key, meta["id"])
        edition = editions.add_edition(folder, bundle.edition, "jsonl")
        if not edition.added:
            return Result(codes.UNCHANGED, bundle.key, meta["id"])
        meta["edition"], meta["edition_hash"] = edition.number, editions.digest(bundle.edition)
        meta["source_hash"], meta["normalized"] = bundle.source_hash, bundle.meta["normalized"]
        if bundle.retained:
            meta["retained"] = bundle.retained
        meta["events"] = [*meta["events"], ev.make_event(
            "edition-added", bundle.origin, now=self.now(), edition=edition.number, source_hash=bundle.source_hash)]
        self._designate(meta, bundle)
        record.write(main, meta, bundle.body)
        return Result(codes.EDITION_ADDED, bundle.key, meta["id"])

    def _flag_possible_same(self, bundle: Bundle, meta: dict, index: dict) -> None:
        """Overlap is a flag for the owner, never a merge (R3)."""
        if bundle.kind == "canary":
            return
        norm, flagged = meta.get("normalized") or {}, 0
        for key, other in index.items():
            if key == bundle.key or other.get("kind") == "canary":
                continue
            if bundle.provider not in MANUAL_PROVIDERS and other.get("provider") not in MANUAL_PROVIDERS:
                continue
            reasons = []
            if norm.get("first_human_sha256") and norm["first_human_sha256"] == other.get("first_human"):
                reasons.append("first_turn")
            if norm.get("title") and norm["title"] == other.get("title") and meta["created"][:10] == other.get("date"):
                reasons.append("title_and_date")
            if reasons and flagged < MAX_FLAGS:
                a, b = sorted((meta["id"], other["id"]))
                review.add(self, "possible-same-conversation", f"{ulid.short(a)}-{ulid.short(b)}",
                           {"records": [a, b], "reasons": reasons})
                flagged += 1


__all__ = ["Shelf", "Result", "read_front_matter", "RECORD_KINDS"]
