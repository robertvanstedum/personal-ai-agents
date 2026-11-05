"""Read canonical CoS history without opening its writer or inventing links.

The source journal contains successive versions of a turn. Its last complete
version wins within a snapshot; the shelf preserves previous snapshot editions.
Private conversations and foreign environments fail closed before turn reads.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from pathlib import Path

from core.memory_shelf.sessions import Parsed, HUMAN, ASSISTANT, turn_time

PROVIDER = "minimoi-cos"
MAX_BYTES = 48 * 1024 * 1024
CID = re.compile(r"(?:cosc-[0-9a-f]{12,32}|cos-before-1-2)\Z")
TID = re.compile(r"t-[0-9a-f]{12,32}\Z")


class Refused(ValueError):
    pass


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


def read(root: Path, cid: str, environment: str = "dev", checkpoint: dict | None = None) -> tuple[Parsed, bytes]:
    if not CID.fullmatch(cid):
        raise Refused("bad_identity")
    # Parents are operator-pinned read-only mounts; never accept a linked subdirectory.
    if any((root / name).is_symlink() for name in ("conversations", "turns")):
        raise Refused("linked_directory")
    path = root / "conversations" / (cid + ".json")
    meta_raw = snapshot(path, 65536)
    meta = json.loads(meta_raw)
    if not isinstance(meta, dict) or meta.get("schema") != "minimoi.cos.conversation/1" or meta.get("conversation_id") != cid:
        raise Refused("bad_metadata")
    if meta.get("private") is not False:
        raise Refused("private")
    if meta.get("environment") != environment or meta.get("data_scope") != "personal":
        raise Refused("scope")
    try:
        raw = snapshot(root / "turns" / (cid + ".jsonl"))
    except FileNotFoundError:
        raw = b""  # a new conversation has metadata before its first turn
    if raw and not raw.endswith(b"\n"):
        raise Refused("partial_journal")
    if checkpoint and (len(raw) < checkpoint["bytes"] or
                       hashlib.sha256(raw[:checkpoint["bytes"]]).hexdigest() != checkpoint["sha256"]):
        raise Refused("journal_rewritten")
    if snapshot(path, 65536) != meta_raw:
        raise Refused("unstable")
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
    out = Parsed(PROVIDER, "CoS", source_id=f"{environment}:{cid}", started=meta.get("created_at"), normalizer=1,
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
    return out, meta_raw + b"\n" + raw
