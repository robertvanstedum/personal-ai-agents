"""The pointing rule (v0.5 C6, amendment R3): no hit is shown until the file on disk says it is true.

For every candidate the index proposes, in this order:

1. **path**: the stored file reference resolves (symlinks followed) to a record main file under one of the shelf's
   record folders; anything else is ``path_not_permitted``;
2. **caller gate**: the record's own front matter (not the index's copy) allows this caller's tier, scope and canary
   choice, and the record is still retrievable (``policy.record_gate``). This runs **before** any body text is read;
3. **edition**: the record's current ``edition_hash`` is the one the chunk was made from;
4. **text**: the sha256 of exactly the cited lines equals the chunk's ``text_hash``;
5. **identity from the source, not the index**: the record is chunked again from the bytes just read (the same
   reader and turn rule the indexer used) and the cited range must be exactly one of the chunks of a retrievable
   dialogue turn. The turn number and speaker returned come from that chunk, never from the index row. A row that
   points at a system or coordination turn, a turn header, or lines that straddle turns, or that claims another
   speaker or turn, is dropped (``not_retrievable`` / ``turn_mismatch``). The index is a pointer cache; it is not an
   authority for who spoke or what may be retrieved.

Any failure drops the hit and yields an ``unresolved`` entry carrying only the chunk id, which list it came from
and a fixed reason code: never other content, never a fallback to the index. The passage returned is read from
disk, and its provenance line is derived at read time from the record's facts (``weight.derive``).
"""
from __future__ import annotations

from pathlib import Path

from core.memory_index import chunker, policy
from core.memory_shelf import record, weight
from core.memory_shelf.shelf import RECORD_KINDS, read_front_matter

PATH_NOT_PERMITTED, FILE_MISSING, UNREADABLE, RECORD_MISMATCH = (
    "path_not_permitted", "file_missing", "unreadable", "record_mismatch")
NOT_RETRIEVABLE, EDITION_CHANGED, LINES_OUT_OF_RANGE, TEXT_CHANGED, TURN_MISMATCH = (
    "not_retrievable", "edition_changed", "lines_out_of_range", "text_changed", "turn_mismatch")
_TURN_ATTRIBUTION = {
    "client_input": (
        "Submitted through the app client. This does not establish who originally "
        "authored quoted or relayed text, or that the owner approved it."
    ),
    "client_reply": (
        "Assistant reply in an app-client exchange. This is not an owner decision or approval."
    ),
}


def permitted(shelf_root: Path, rel: str) -> Path | None:
    root = Path(shelf_root).resolve()
    try:
        path = (root / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if path.suffix != ".md" or not path.is_file():
        return None
    for kind in RECORD_KINDS:
        base = root / kind
        if path.is_relative_to(base) and path != base:
            return path
    return None


def resolve(shelf_root: Path, row: dict, caller: policy.Caller, source: str,
            parsed: dict | None = None) -> tuple[dict | None, dict | None]:
    """``(hit, None)`` or ``(None, unresolved)`` for one index row. ``parsed`` is an optional per-query cache of
    the source-derived chunks, so several hits in one record parse it once."""
    def fail(reason: str):
        return None, {"chunk_id": row["chunk_id"], "list": source, "reason": reason}

    if not (Path(shelf_root) / row["rel"]).exists():
        return fail(FILE_MISSING)
    path = permitted(shelf_root, row["rel"])
    if path is None:
        return fail(PATH_NOT_PERMITTED)
    try:
        meta = read_front_matter(path)
    except (OSError, record.InvalidRecord, ValueError):
        return fail(UNREADABLE)
    if meta.get("id") != row["record_id"]:
        return fail(RECORD_MISMATCH)
    gate = policy.allowed(meta, caller)
    if gate:
        return fail(gate)
    if policy.record_gate(meta):
        return fail(NOT_RETRIEVABLE)
    if meta.get("edition_hash") != row["edition_hash"]:
        return fail(EDITION_CHANGED)
    try:
        data = path.read_bytes()
    except OSError:
        return fail(UNREADABLE)
    piece = chunker.slice_lines(data, row["start_line"], row["end_line"])
    if piece is None:
        return fail(LINES_OUT_OF_RANGE)
    if chunker.text_hash(piece) != row["text_hash"]:
        return fail(TEXT_CHANGED)
    cache_key = (row["rel"], chunker.text_hash(data))
    chunks = parsed.get(cache_key) if parsed is not None else None
    if chunks is None:
        try:
            chunks = chunker.chunk_record(data)[1]
        except chunker.ChunkRefused as exc:
            return fail(exc.code)
        if parsed is not None:
            parsed[cache_key] = chunks
    own = next((c for c in chunks if (c.start_line, c.end_line) == (row["start_line"], row["end_line"])), None)
    if own is None or own.text_hash != row["text_hash"]:
        return fail(NOT_RETRIEVABLE)                  # not a chunk of any retrievable dialogue turn of this record
    if (own.turn, own.speaker) != (row["turn"], row["speaker"]):
        return fail(TURN_MISMATCH)                    # right lines, but the index named another turn or speaker
    attribution = _TURN_ATTRIBUTION.get(own.turn_kind)
    decision = weight.derive(meta)
    provenance = decision["line"]
    decisive = next((e for e in reversed(meta.get("events") or []) if e["kind"] in weight.DECISIVE), None)
    owner_confirmed = bool(decisive and decisive.get("by") == "robert" and
        decisive["kind"] in ("approved-direct", "approved-under-mandate"))
    successor_evidence = None
    decision_gap = None
    if decision["weight"] == "superseded":
        # Resolve the successor under the same effective caller gate before
        # reading its original edition or disclosing an identity/range.
        try:
            from core.memory_shelf.shelf import Shelf
            from core.memory_shelf import approvals
            source_shelf = Shelf(shelf_root)
            import re
            event = decisive
            if not event or event.get("kind") != "superseded":
                raise approvals.ApprovalRefused("annotation_reference_invalid")
            approvals._annotation_ref_shape(event.get("source_ref"))
            approvals._annotation_ref_shape(event.get("successor_ref"))
            if (event["source_ref"]["record_id"] != meta["id"] or
                event["successor_ref"]["record_id"] != decision["successor"] or
                event["successor_ref"]["record_id"] == meta["id"] or
                not isinstance(event.get("request_id"), str) or
                not re.fullmatch("[0-9a-f]{32}", event["request_id"])):
                raise approvals.ApprovalRefused("annotation_identity_mismatch")
            successor_path, successor_meta, _ = approvals._annotation_record(source_shelf, event["successor_ref"]["record_id"])
            if policy.allowed(successor_meta, caller) or policy.record_gate(successor_meta):
                raise approvals.ApprovalRefused("annotation_successor_unavailable")
            if (meta.get("scope"),meta.get("tier"),meta.get("kind")=="canary") != (successor_meta.get("scope"),successor_meta.get("tier"),successor_meta.get("kind")=="canary"):
                raise approvals.ApprovalRefused("annotation_access_boundary")
            approvals.annotation_reference_evidence(source_shelf, event["source_ref"])
            successor_evidence = approvals.annotation_reference_evidence(source_shelf, event["successor_ref"])
            owner_confirmed = event.get("by") == "robert"
        except Exception:
            decision_gap = "successor_evidence_unavailable"
            provenance = f"written by {meta.get('chair', 'unknown')} · superseded; successor evidence unavailable in this caller scope"

    if attribution:
        provenance += " Turn attribution: " + attribution
    return {
        "chunk_id": row["chunk_id"], "record_id": row["record_id"], "file": row["rel"],
        "lines": [own.start_line, own.end_line], "turn": own.turn, "speaker": own.speaker,
        "date": str(meta.get("created", ""))[:10], "chair": meta.get("chair"), "tier": policy.effective_tier(meta),
        "provenance": provenance, "passage": piece.decode("utf-8", errors="replace"),
        "turn_kind": own.turn_kind, "attribution": attribution,
        "decision_status": decision["weight"],
        "successor_evidence": successor_evidence,
        "decision_gap": decision_gap,
        "decision_authority": "owner_event" if owner_confirmed else "record_event" if decisive else "not_approved",
        "score": row.get("score"),
    }, None


__all__ = ["resolve", "permitted"]
