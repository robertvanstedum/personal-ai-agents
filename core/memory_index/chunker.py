"""Records to line-bound chunks (v0.5 C7, amendment R3).

A chunk is a run of whole lines inside **one retrievable turn** of a record's main file (the normalized form of its
current edition). Its identity is ``{record_id}@{edition_hash12}#{ordinal}``, so a chunk can never be mistaken for
text of another edition. It carries the file's 1-based ``start_line``/``end_line`` and the sha256 of exactly those
bytes (``text_hash``), which the resolver checks against the file before anything is shown.

Turns are found with the shelf's own reader (``record.load`` + ``render.parse_body``, which checks every frame's
declared length and sha256); this module only locates each verified turn in the file to number its lines. If the
reader and the record's own ``normalized.turns`` count disagree (a frame this code does not know), the record is
refused with ``parse_mismatch``, never half-indexed.

Lines are split on ``\\n`` only (a turn may contain a lone CR). A turn longer than ``CAP_BYTES`` is split on line
boundaries with up to ``OVERLAP_BYTES`` of repeated lines; a single line longer than the cap is one chunk on its
own (keyword search sees all of it; the embedder sees the first ``EMBED_MAX_CHARS``).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.memory_index import policy
from core.memory_shelf import record, render

CHUNKER_VERSION = 1
CAP_BYTES = 1500
OVERLAP_BYTES = 200
EMBED_MAX_CHARS = 4000


class ChunkRefused(ValueError):
    """The record cannot be chunked. ``code`` is a fixed reason; the message never quotes record text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Chunk:
    ordinal: int              # 1-based within the record edition
    turn: int                 # the turn's ordinal in the record
    speaker: str
    start_line: int           # 1-based, inclusive, lines of the record's main file
    end_line: int
    text: str
    text_hash: str
    overlap_lines: int        # lines repeated from the previous chunk of the same turn
    turn_kind: str | None = None  # verified turn-frame provenance; never inferred from its words

    def chunk_id(self, record_id: str, edition_hash: str) -> str:
        return f"{record_id}@{edition_hash[:12]}#{self.ordinal}"


def text_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def slice_lines(data: bytes, start: int, end: int) -> bytes | None:
    """Exactly lines ``start..end`` (1-based, inclusive) of ``data``, joined by ``\\n``; ``None`` if out of range."""
    if start < 1 or end < start:
        return None
    lines = data.split(b"\n")
    if end > len(lines):
        return None
    return b"\n".join(lines[start - 1:end])


def _split(lines: list[bytes]) -> list[tuple[int, int, int]]:
    """(first, last, overlap) index ranges over ``lines``, each at most CAP_BYTES unless one line is longer."""
    out, i, n = [], 0, len(lines)
    overlap = 0
    while i < n:
        j, size = i, 0
        while j < n and (j == i or size + len(lines[j]) + 1 <= CAP_BYTES):
            size += len(lines[j]) + 1
            j += 1
        out.append((i, j - 1, overlap))
        if j >= n:
            break
        k, ov = 0, 0
        while k < j - i - 1 and ov + len(lines[j - 1 - k]) + 1 <= OVERLAP_BYTES:
            ov += len(lines[j - 1 - k]) + 1
            k += 1
        i, overlap = j - k, k
    return out


def chunk_record(data: bytes) -> tuple[dict, list[Chunk]]:
    """(front matter, chunks of the retrievable turns) for one record file's bytes. Raises ``ChunkRefused``."""
    try:
        text = data.decode("utf-8")
        meta, body = record.load(text)
        turns = render.parse_body(body)
    except (UnicodeDecodeError, record.InvalidRecord):
        raise ChunkRefused(policy.UNREADABLE) from None
    declared = (meta.get("normalized") or {}).get("turns")
    if declared is not None and int(declared) != len(turns):
        raise ChunkRefused(policy.PARSE_MISMATCH)
    keep = {t.ordinal for t in policy.retrievable_turns(turns)}
    body_bytes = body.encode("utf-8")
    body_start = len(data) - len(body_bytes)
    chunks: list[Chunk] = []
    pos, line_at, counted_to = 0, 1, 0
    for turn in turns:
        head = f"<!-- turn {turn.ordinal} | {turn.speaker} | ".encode()
        at = body_bytes.find(head, pos)
        close = body_bytes.find(b" -->\n", at) if at != -1 else -1
        raw = turn.text.encode("utf-8")
        if at == -1 or close == -1 or text_hash(raw).encode() not in body_bytes[at:close]:
            raise ChunkRefused(policy.PARSE_MISMATCH)
        start = close + 5
        if body_bytes[start:start + len(raw)] != raw:
            raise ChunkRefused(policy.PARSE_MISMATCH)
        pos = start + len(raw)
        if turn.ordinal not in keep:
            continue
        file_off = body_start + start
        line_at += data.count(b"\n", counted_to, file_off)
        counted_to = file_off
        lines = raw.split(b"\n")
        while lines and not lines[-1].strip():
            lines.pop()
        for first, last, overlap in _split(lines):
            piece = b"\n".join(lines[first:last + 1])
            if not piece.strip():
                continue
            chunks.append(Chunk(len(chunks) + 1, turn.ordinal, turn.speaker, line_at + first, line_at + last,
                                piece.decode("utf-8"), text_hash(piece), overlap, (turn.attrs or {}).get("kind")))
    return meta, chunks


__all__ = ["Chunk", "ChunkRefused", "chunk_record", "slice_lines", "text_hash", "CHUNKER_VERSION", "CAP_BYTES",
           "OVERLAP_BYTES", "EMBED_MAX_CHARS"]
