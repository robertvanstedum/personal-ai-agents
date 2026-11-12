"""Read canonical CoS history without opening its writer or inventing links.

The source journal contains successive versions of a turn. Its last complete
version wins within a snapshot; the shelf preserves previous snapshot editions.
Private conversations and foreign environments fail closed before turn reads.

Two ways in, one reading:

- ``read(root, cid)``: the history folder itself (the Dev feed, ``scripts/memory/dev_feed.py``).
- ``parse(lines)``: one **export file** (watcher kind ``cos``, provider ``minimoi-cos``; 11 Nov 2026, #509). Production's
  ``vault-cos-export`` (``core/memory_sync/cos_export.py``) packs one conversation's metadata and turn journal, byte for byte, into
  ``<environment>--<conversation id>.json``; ``vault-cos-pull`` places it in the Mac's ``minimoi-cos`` watch folder; the approval-gated
  watcher reads it here. The same checks as ``read`` run again on the packed bytes: a private conversation, another environment or
  another data scope is refused (``BadExport`` with a fixed code), so a file that should never have left production is never captured.

Identity is ``minimoi-cos:<environment>:<conversation id>`` either way, so the Dev feed's records and the watcher's are the same source
shape.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Iterable

from core.memory_shelf.mc import BadExport as _McBadExport
from core.memory_shelf.sessions import Parsed, HUMAN, ASSISTANT, NORMALIZER_VERSION, turn_time

PROVIDER = "minimoi-cos"
KIND = "cos"
CHAIR = "CoS"
NORMALIZER = NORMALIZER_VERSION[PROVIDER]
MAX_BYTES = 48 * 1024 * 1024
META_MAX = 65536
CID = re.compile(r"(?:cosc-[0-9a-f]{12,32}|cos-before-1-2)\Z")
TID = re.compile(r"t-[0-9a-f]{12,32}\Z")
ENVIRONMENTS = ("dev", "staging", "prod")
EXPORT_SCHEMA = "minimoi.cos-conversation-export/1"
# == core/memory_sync/cos_batch.py FILE (kept equal by a test; that module stays stdlib-only for the EC2 tools bundle).
FILE = re.compile(r"^(dev|staging|prod)--(cosc-[0-9a-f]{12,32}|cos-before-1-2)\.json$")


class Refused(ValueError):
    pass


class BadExport(_McBadExport):
    """An export file that is not the shape, or that the reader refuses (private, scope, ...). The message is a fixed code."""


def snapshot(path: Path, limit: int = MAX_BYTES) -> bytes:
    """Bounded, regular, non-symlink file read, including a replacement check."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise Refused("source_limit")
        raw = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    current = path.lstat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) > limit or signature(before) != signature(after) or signature(after) != signature(current):
        raise Refused("unstable")
    return raw


def check_meta(meta_raw: bytes, cid: str, environment: str) -> dict:
    """The conversation's metadata, or ``Refused``: not the schema, another id, private (anything but an explicit ``false``), another
    environment or another data scope."""
    meta = json.loads(meta_raw)
    if not isinstance(meta, dict) or meta.get("schema") != "minimoi.cos.conversation/1" or meta.get("conversation_id") != cid:
        raise Refused("bad_metadata")
    if meta.get("private") is not False:
        raise Refused("private")
    if meta.get("environment") != environment or meta.get("data_scope") != "personal":
        raise Refused("scope")
    return meta


def read(root: Path, cid: str, environment: str = "dev", checkpoint: dict | None = None) -> tuple[Parsed, bytes]:
    if not CID.fullmatch(cid):
        raise Refused("bad_identity")
    # Parents are operator-pinned read-only mounts; never accept a linked subdirectory.
    if any((root / name).is_symlink() for name in ("conversations", "turns")):
        raise Refused("linked_directory")
    path = root / "conversations" / (cid + ".json")
    meta_raw = snapshot(path, META_MAX)
    meta = check_meta(meta_raw, cid, environment)
    try:
        raw = snapshot(root / "turns" / (cid + ".jsonl"))
    except FileNotFoundError:
        raw = b""  # a new conversation has metadata before its first turn
    if raw and not raw.endswith(b"\n"):
        raise Refused("partial_journal")
    if checkpoint and (len(raw) < checkpoint["bytes"] or
                       hashlib.sha256(raw[:checkpoint["bytes"]]).hexdigest() != checkpoint["sha256"]):
        raise Refused("journal_rewritten")
    if snapshot(path, META_MAX) != meta_raw:
        raise Refused("unstable")
    return parse_journal(meta, raw, cid, environment), meta_raw + b"\n" + raw


def parse_journal(meta: dict, raw: bytes, cid: str, environment: str) -> Parsed:
    """The turns of one checked conversation (``check_meta``) from its journal bytes. Raises ``Refused``."""
    latest = {}
    events, evidence = [], []
    for line, data in enumerate(raw.splitlines(), 1):
        row = json.loads(data)
        if not isinstance(row, dict) or row.get("schema") != "minimoi.cos.turn/1" or row.get("conversation_id") != cid:
            raise Refused("bad_turn")
        tid, seq = row.get("turn_id"), row.get("seq")
        if not isinstance(tid, str) or not TID.fullmatch(tid) or type(seq) is not int or seq < 1:
            raise Refused("bad_turn")
        if row.get("status") not in ("pending", "answered", "failed", "recovered") or not isinstance(row.get("user_text"), str):
            raise Refused("bad_turn")
        if row.get("reply_text") is not None and not isinstance(row["reply_text"], str):
            raise Refused("bad_turn")
        if tid in latest and (latest[tid][1]["seq"] != seq or latest[tid][1]["user_text"] != row["user_text"]):
            raise Refused("identity_conflict")
        latest[tid] = (line, row)
        # The manifest is not a text-scrubbing surface. Keep only structural ids.
        events.append({"turn_id": tid, "seq": seq, "status": row["status"]})
        evidence.append({key: row[key] for key in ("turn_id", "conversation_id", "seq", "status", "channel",
                         "context_ref", "commitment_receipts", "picture_revision", "created_at", "answered_at") if key in row})
        if len(events) > 4000:
            raise Refused("event_limit")
    seqs = [row["seq"] for _, row in latest.values()]
    if len(seqs) != len(set(seqs)):
        raise Refused("identity_conflict")
    out = Parsed(PROVIDER, CHAIR, source_id=f"{environment}:{cid}", started=meta.get("created_at"), normalizer=NORMALIZER,
                 designatable=False)
    for line, row in sorted(latest.values(), key=lambda item: item[1]["seq"]):
        attrs = {"rid": cid, "turn": row["turn_id"], "seq": row["seq"], "kind": row["status"]}
        at = turn_time(row.get("created_at"))
        if at:
            attrs["ts"] = at
        out.add(HUMAN, row["user_text"], line, "cos:user_text", {**attrs, "who": "owner"})
        if row.get("reply_text"):
            reply = {**attrs, "who": "cos"}
            at = turn_time(row.get("answered_at"))
            if at:
                reply["ts"] = at
            out.add(ASSISTANT, row["reply_text"], line, "cos:reply_text", reply)
    out.lines = len(events)
    # Scrubbed non-dialogue evidence: source-stated relationships, never inferred.
    out.notes = [{"note_id": f"event-{number}", "author": "cos-history", "kind": "source-evidence",
                  "text": json.dumps(item, sort_keys=True)} for number, item in enumerate(evidence, 1)]
    out.manifest = {"cos_environment": environment, "cos_conversation": cid, "cos_events": events,
                    "cos_journal_checkpoint": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                    "coverage_limit": "conversation_turns_only_no_notes_or_work_journals"}
    out.coverage = {"seen": {"turns": len(latest)}, "taken": {"turns": len(latest)}, "gap": {"turns": 0}, "flags": []}
    return out


# ── export files (watcher kind ``cos``) ──
def wanted(rel: str) -> bool:
    """An export file sits at the top of the watch folder and is named ``<environment>--<conversation id>.json``."""
    return "/" not in rel and bool(FILE.match(rel))


def load_export(lines: Iterable[bytes | str]) -> tuple[str, str, bytes, bytes]:
    """``(environment, conversation id, metadata bytes, journal bytes)`` from one export file, after its shape check. ``BadExport``."""
    chunks, total = [], 0
    for part in lines:
        part = part.encode("utf-8") if isinstance(part, str) else part
        total += len(part)
        if total > MAX_BYTES:
            raise BadExport("too_large")
        chunks.append(part)
    try:
        doc = json.loads(b"".join(chunks).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadExport("bad_export") from None
    ok = isinstance(doc, dict) and doc.get("schema") == EXPORT_SCHEMA and doc.get("environment") in ENVIRONMENTS \
        and isinstance(doc.get("conversation_id"), str) and bool(CID.fullmatch(doc["conversation_id"])) \
        and doc.get("source_id") == f"{doc.get('environment')}:{doc.get('conversation_id')}" \
        and isinstance(doc.get("conversation"), str) and isinstance(doc.get("turns"), str)
    if not ok:
        raise BadExport("bad_export")
    meta_raw, raw = doc["conversation"].encode("utf-8"), doc["turns"].encode("utf-8")
    if len(meta_raw) > META_MAX:
        raise BadExport("bad_export")
    return doc["environment"], doc["conversation_id"], meta_raw, raw


def parse(lines: Iterable[bytes | str]) -> Parsed:
    """One export file as ordered turns, by exactly ``read``'s rules. Raises ``BadExport`` (``private``, ``scope``, ``bad_turn``, ...)."""
    environment, cid, meta_raw, raw = load_export(lines)
    try:
        meta = check_meta(meta_raw, cid, environment)
        if raw and not raw.endswith(b"\n"):
            raise Refused("partial_journal")
        return parse_journal(meta, raw, cid, environment)
    except Refused as exc:
        raise BadExport(str(exc)) from None
    except (ValueError, TypeError, KeyError):
        raise BadExport("bad_export") from None


__all__ = ["read", "parse", "load_export", "wanted", "check_meta", "parse_journal", "snapshot", "Refused", "BadExport", "PROVIDER",
           "KIND", "FILE", "EXPORT_SCHEMA"]
