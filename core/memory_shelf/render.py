"""From parsed turns to a shelf record (v0.5 B2/B3; amendment R3, decision D8).

* **Body:** each turn is framed ``<!-- turn N | speaker | line L | bytes B | sha256 H -->`` and then exactly
  ``B`` bytes of text, so a turn that contains anything (even a fake frame) reads back unchanged.
  Omitted parts appear as one-line ``[omitted: kind, source line L]`` pointers between turns.
* **Scrub first (D8):** the payment scrub and the credential guard run on each turn's text before it
  is hashed or stored; the record says how many redactions, and carries the **unscrubbed source file's**
  sha256. The edition is a canonical JSONL of the scrubbed turns, a sanitized derivative with provenance.
  The session file itself stays where it is (``~/.claude``, ``~/.codex``).
* ``ordered_turns`` is what the fidelity check compares: ``(ordinal, speaker, sha256(text))``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from core.memory_shelf import record
from core.memory_shelf.sessions import MARK, Parsed, Turn
from utils.credential_scrub import scrub as scrub_credentials
from utils.payment_scrub import scrub as scrub_payment

FORMAT = "minimoi-session-turns/1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_file(path: str | Path) -> tuple[str, int]:
    """(sha256, size) of the original source file, streamed."""
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def scrub_text(text: str) -> tuple[str, bool]:
    first = scrub_payment(text)
    second, changed = scrub_credentials(first)
    return second, changed or first != text


def scrub_parsed(parsed: Parsed) -> tuple[Parsed, int]:
    """A copy whose turn texts are scrubbed, and how many turns were changed."""
    turns, changed = [], 0
    for turn in parsed.turns:
        text, was = scrub_text(turn.text)
        changed += was
        turns.append(replace(turn, text=text))
    return replace(parsed, turns=turns), changed


_UNSAFE = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")


def one_line(text: str, limit: int = 200) -> str:
    """Untrusted text made safe for a one-line pointer: no line breaks, no frame or comment markers."""
    return _UNSAFE.sub(" ", text).replace("<!--", "< !--").replace("-->", "-- >")[:limit]


def _pointer_line(pointer: tuple) -> str:
    _, kind, line, detail = pointer
    if kind == MARK:
        return f"{one_line(detail or '')}\n"
    tail = f": {one_line(detail)}" if detail else ""
    return f"[omitted: {one_line(kind, 80)}, source line {line}{tail}]\n"


def render_body(parsed: Parsed) -> str:
    chunks, pointers = [], list(parsed.pointers)
    for turn in parsed.turns:
        while pointers and pointers[0][0] < turn.ordinal:
            chunks.append(_pointer_line(pointers.pop(0)))
        raw = turn.text.encode("utf-8")
        chunks.append(f"<!-- turn {turn.ordinal} | {turn.speaker} | line {turn.line} | bytes {len(raw)} | "
                      f"sha256 {hashlib.sha256(raw).hexdigest()} -->\n{turn.text}\n\n")
    for pointer in pointers:
        chunks.append(_pointer_line(pointer))
    return "".join(chunks)


def parse_body(body: str) -> list[Turn]:
    """Read turns back by their declared byte lengths; a frame that lies about its length raises."""
    data, turns, pos = body.encode("utf-8"), [], 0
    frame = re.compile(rb"<!-- turn (\d+) \| (human|assistant|system) \| line (\d+) \| bytes (\d+) \| sha256 ([0-9a-f]{64}) -->\n")
    while True:
        match = frame.search(data, pos)
        if not match:
            return turns
        start, size = match.end(), int(match.group(4))
        chunk = data[start:start + size]
        if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != match.group(5).decode():
            raise record.InvalidRecord("a turn does not match its frame")
        turns.append(Turn(int(match.group(1)), match.group(2).decode(), chunk.decode("utf-8"), int(match.group(3)), "shelf"))
        pos = start + size


def ordered_turns(turns) -> list[tuple[int, str, str]]:
    return [(t.ordinal, t.speaker, sha256_text(t.text)) for t in turns]


def edition_bytes(parsed: Parsed, source_sha256: str, source_size: int, redacted_turns: int) -> bytes:
    head = {"format": FORMAT, "provider": parsed.provider, "source_id": parsed.source_id,
            "source_sha256": source_sha256, "source_bytes": source_size, "redacted_turns": redacted_turns,
            "identity": parsed.identity, "malformed_lines": parsed.malformed,
            "omitted": dict(sorted(parsed.omitted.items()))}
    if parsed.manifest:                     # only when a format has structure beyond turns, so older editions keep their hash
        head["manifest"] = dict(sorted(parsed.manifest.items()))
    lines = [json.dumps(head, ensure_ascii=False, sort_keys=True)]
    for t in parsed.turns:
        lines.append(json.dumps({"ordinal": t.ordinal, "speaker": t.speaker, "line": t.line, "basis": t.basis,
                                 "sha256": sha256_text(t.text), "text": t.text}, ensure_ascii=False, sort_keys=True))
    return ("\n".join(lines) + "\n").encode("utf-8")


def to_shelf(parsed: Parsed, source_sha256: str, source_size: int, *, created: str, title: str = "session",
             tags: list[str] | None = None) -> tuple[dict, str, bytes]:
    """(front matter, body, edition bytes) for a raw-tier session record."""
    clean, redacted = scrub_parsed(parsed)
    meta = record.new_front_matter(
        kind="session", chair=parsed.chair, source=f"{parsed.provider}:{parsed.source_id or 'unknown'}",
        source_hash=source_sha256, created=created, tier="raw", scope="robert",
        tags=[parsed.provider, *(tags or [])], title=title)
    meta["edition"] = 1
    meta["edition_hash"] = hashlib.sha256(edition_bytes(clean, source_sha256, source_size, redacted)).hexdigest()
    meta["normalized"] = {"turns": len(clean.turns), "redacted_turns": redacted, "malformed_lines": clean.malformed,
                          "identity": clean.identity, "omitted": dict(sorted(clean.omitted.items())), **clean.manifest}
    return meta, render_body(clean), edition_bytes(clean, source_sha256, source_size, redacted)


__all__ = ["render_body", "parse_body", "ordered_turns", "edition_bytes", "to_shelf", "hash_file", "scrub_parsed"]
