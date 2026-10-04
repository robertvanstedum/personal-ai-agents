"""From parsed turns to a shelf record (v0.5 B2/B3; amendment R3, decision D8).

* **Body:** each turn is framed ``<!-- turn N | speaker | line L | bytes B | sha256 H -->`` and then exactly
  ``B`` bytes of text, so a turn that contains anything (even a fake frame) reads back unchanged.
  Omitted parts appear as one-line ``[omitted: kind, source line L]`` pointers between turns (a file
  name may follow, sanitized); a conversation's alternate branches are introduced by a ``=== Branch ... ===`` line.
* **Scrub first (D8):** the payment scrub and the credential guard run on each turn's text before it
  is hashed or stored; the record says how many redactions, and carries the **unscrubbed source file's**
  sha256. The edition is a canonical JSONL of the scrubbed turns, a sanitized derivative with provenance.
  The session file itself stays where it is (``~/.claude``, ``~/.codex``).
* ``ordered_turns`` is what the fidelity check compares: ``(ordinal, speaker, sha256(text))``; for a turn that has a stable
  speaker id (a room) the speaker element is ``role:id``, so two agents are never the same speaker.
* **Additive attributes** (formats with several humans or agents): a turn frame may end with ``| key=value`` pairs
  (``who``, ``seq``, ``kind``, ``rid``, ``reply``, ``corrects``, ``ts``, ``ing``), each value a short machine-safe token
  validated at render time, never free text. Typed notes and references follow the turns as ``note`` and ``ref`` blocks
  framed the same way (declared bytes and sha256). Frames without attributes are byte-for-byte what they always were.
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


# A value that is *entirely* a machine identifier (uuid, 32 or 64 hex, an ISO time) is provenance, not prose: the payment
# scrub reads a run of digits in a uuid as a card number and would rewrite the id, breaking every link to it.
_MACHINE_ID = re.compile(r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}|[0-9a-f]{64}"
                         r"|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))$")


def _scrub_value(value, counter: list):
    """Scrub every free-text string inside a JSON-like value; count the strings that changed. A string that is wholly a
    machine identifier is left alone (see ``_MACHINE_ID``); anything else, including text that contains an id, is scrubbed."""
    if isinstance(value, str) and _MACHINE_ID.match(value):
        return value
    if isinstance(value, str):
        text, was = scrub_text(value)
        counter[0] += was
        return text
    if isinstance(value, list):
        return [_scrub_value(v, counter) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v, counter) for k, v in value.items()}
    return value


def scrub_parsed(parsed: Parsed) -> tuple[Parsed, int]:
    """A copy whose turn texts (and, for formats that have them, labels, notes, references and participant labels)
    are scrubbed (D8), and how many turns were changed. Redactions outside the turn texts are counted separately in
    the manifest as ``redacted_fields``, so the provenance says what was touched."""
    turns, changed, extra = [], 0, [0]
    for turn in parsed.turns:
        text, was = scrub_text(turn.text)
        changed += was
        # frame attributes are validated machine tokens (ids, a sequence, a time); everything else a turn carries is text
        attrs = ({k: (v if k in FRAME_KEYS else _scrub_value(v, extra)) for k, v in turn.attrs.items()}
                 if turn.attrs else turn.attrs)
        turns.append(replace(turn, text=text, attrs=attrs))
    notes = [_scrub_value(n, extra) for n in parsed.notes]
    refs = [_scrub_value(r, extra) for r in parsed.references]
    people = [_scrub_value(p, extra) for p in parsed.participants]
    manifest = {**parsed.manifest, **({"redacted_fields": extra[0]} if extra[0] else {})}
    return replace(parsed, turns=turns, notes=notes, references=refs, participants=people, manifest=manifest), changed


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


FRAME_KEYS = ("who", "seq", "kind", "rid", "reply", "corrects", "ts", "ing", "turn", "class", "to")
BLOCK_KEYS = ("id", "author", "kind", "version", "seq", "ref")
_ATTR = re.compile(r"^[A-Za-z0-9_:.+-]{1,64}$")


def _attr_suffix(attrs: dict | None, keys: tuple, *, strict: bool) -> str:
    out = []
    for key in keys:
        value = (attrs or {}).get(key)
        if value is None:
            continue
        value = str(value)
        if not _ATTR.match(value):
            if strict:
                raise ValueError("a frame attribute is not a machine-safe token")
            continue                                   # block attributes are optional hints: omit what is not safe
        out.append(f" | {key}={value}")
    return "".join(out)


def render_body(parsed: Parsed) -> str:
    chunks, pointers = [], list(parsed.pointers)
    for turn in parsed.turns:
        while pointers and pointers[0][0] < turn.ordinal:
            chunks.append(_pointer_line(pointers.pop(0)))
        raw = turn.text.encode("utf-8")
        chunks.append(f"<!-- turn {turn.ordinal} | {turn.speaker} | line {turn.line} | bytes {len(raw)} | "
                      f"sha256 {hashlib.sha256(raw).hexdigest()}{_attr_suffix(turn.attrs, FRAME_KEYS, strict=True)} -->\n"
                      f"{turn.text}\n\n")
    for pointer in pointers:
        chunks.append(_pointer_line(pointer))
    for index, note in enumerate(parsed.notes, 1):
        raw = str(note.get("text", "")).encode("utf-8")
        attrs = {"id": note.get("note_id"), "author": note.get("author"), "kind": note.get("kind"),
                 "version": note.get("version"), "seq": note.get("source_through_seq")}
        chunks.append(f"<!-- note {index} | bytes {len(raw)} | sha256 {hashlib.sha256(raw).hexdigest()}"
                      f"{_attr_suffix(attrs, BLOCK_KEYS, strict=False)} -->\n{raw.decode('utf-8')}\n\n")
    for index, ref in enumerate(parsed.references, 1):
        raw = _ref_text(ref).encode("utf-8")
        attrs = {"id": ref.get("reference_id"), "kind": ref.get("kind"), "ref": ref.get("source_record_id")}
        chunks.append(f"<!-- ref {index} | bytes {len(raw)} | sha256 {hashlib.sha256(raw).hexdigest()}"
                      f"{_attr_suffix(attrs, BLOCK_KEYS, strict=False)} -->\n{raw.decode('utf-8')}\n\n")
    return "".join(chunks)


def _ref_text(ref: dict) -> str:
    """A reference as canonical JSON text: inert data, never followed."""
    return json.dumps(ref, ensure_ascii=False, sort_keys=True)


_FRAME = re.compile(rb"<!-- turn (\d+) \| (human|assistant|system|coordination) \| line (\d+) \| bytes (\d+) \| sha256 ([0-9a-f]{64})"
                    rb"((?: \| [a-z]+=[A-Za-z0-9_:.+-]{1,64})*) -->\n")
_BLOCK = re.compile(rb"<!-- (note|ref) (\d+) \| bytes (\d+) \| sha256 ([0-9a-f]{64})((?: \| [a-z]+=[A-Za-z0-9_:.+-]{1,64})*) -->\n")


def _attrs_of(raw: bytes) -> dict | None:
    pairs = [part.split("=", 1) for part in raw.decode().split(" | ") if part]
    return {k: v for k, v in pairs} or None


def parse_body(body: str) -> list[Turn]:
    """Read turns back by their declared byte lengths; a frame that lies about its length raises."""
    return parse_blocks(body)[0]


def parse_blocks(body: str) -> tuple[list[Turn], list[tuple[str, int, str, dict | None]]]:
    """(turns, [(kind, index, text, attrs)] for the note and ref blocks). Every frame is checked against its own
    declared length and sha256; the first that lies raises ``InvalidRecord``."""
    data, pos = body.encode("utf-8"), 0
    turns: list[Turn] = []
    blocks: list[tuple[str, int, str, dict | None]] = []
    while True:
        match, block = _FRAME.search(data, pos), _BLOCK.search(data, pos)
        if match and (not block or match.start() < block.start()):
            start, size = match.end(), int(match.group(4))
            chunk = data[start:start + size]
            if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != match.group(5).decode():
                raise record.InvalidRecord("a turn does not match its frame")
            turns.append(Turn(int(match.group(1)), match.group(2).decode(), chunk.decode("utf-8"), int(match.group(3)),
                              "shelf", _attrs_of(match.group(6))))
            pos = start + size
        elif block:
            start, size = block.end(), int(block.group(3))
            chunk = data[start:start + size]
            if len(chunk) != size or hashlib.sha256(chunk).hexdigest() != block.group(4).decode():
                raise record.InvalidRecord("a block does not match its frame")
            blocks.append((block.group(1).decode(), int(block.group(2)), chunk.decode("utf-8"), _attrs_of(block.group(5))))
            pos = start + size
        else:
            return turns, blocks


NON_DIALOGUE_CLASSES = frozenset({"approval_review", "subagent_task", "subagent", "handoff"})


def dialogue_turns(turns) -> list[Turn]:
    """The turns that are the owner's dialogue with an assistant: ``human`` and ``assistant`` turns that are not part of
    automatic approval review, a subagent thread or an agent-to-agent handoff. This is what ordinary conversational
    retrieval should read; coordination and system turns stay in the record as attributed operational evidence."""
    return [t for t in turns if t.speaker in ("human", "assistant") and (t.attrs or {}).get("class") not in NON_DIALOGUE_CLASSES]


def ordered_turns(turns) -> list[tuple[int, str, str]]:
    return [(t.ordinal, f"{t.speaker}:{t.who}" if t.who else t.speaker, sha256_text(t.text)) for t in turns]


def edition_bytes(parsed: Parsed, source_sha256: str, source_size: int, redacted_turns: int) -> bytes:
    head = {"format": FORMAT, "provider": parsed.provider, "source_id": parsed.source_id,
            "source_sha256": source_sha256, "source_bytes": source_size, "redacted_turns": redacted_turns,
            "identity": parsed.identity, "malformed_lines": parsed.malformed,
            "omitted": dict(sorted(parsed.omitted.items()))}
    if parsed.manifest:                     # only when a format has structure beyond turns, so older editions keep their hash
        head["manifest"] = dict(sorted(parsed.manifest.items()))
    lines = [json.dumps(head, ensure_ascii=False, sort_keys=True)]
    for t in parsed.turns:
        row = {"ordinal": t.ordinal, "speaker": t.speaker, "line": t.line, "basis": t.basis,
               "sha256": sha256_text(t.text), "text": t.text}
        if t.attrs:
            row["attrs"] = t.attrs
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    for index, note in enumerate(parsed.notes, 1):
        lines.append(json.dumps({"type": "note", "index": index, **note, "sha256": sha256_text(str(note.get("text", "")))},
                                ensure_ascii=False, sort_keys=True))
    for index, ref in enumerate(parsed.references, 1):
        lines.append(json.dumps({"type": "ref", "index": index, "sha256": sha256_text(_ref_text(ref)), "text": _ref_text(ref)},
                                ensure_ascii=False, sort_keys=True))
    return ("\n".join(lines) + "\n").encode("utf-8")


def to_shelf(parsed: Parsed, source_sha256: str, source_size: int, *, created: str, title: str = "session",
             tags: list[str] | None = None) -> tuple[dict, str, bytes]:
    """(front matter, body, edition bytes) for a raw-tier session record."""
    clean, redacted = scrub_parsed(parsed)
    meta = record.new_front_matter(
        kind="session", chair=parsed.chair, source=f"{parsed.provider}:{parsed.source_id or 'unknown'}",
        source_hash=source_sha256, created=created, tier="raw", scope="robert",
        tags=[parsed.provider, *(tags or []), *([f"class:{parsed.session_class}"] if parsed.session_class != "dialogue" else [])],
        title=title)
    meta["edition"] = 1
    meta["edition_hash"] = hashlib.sha256(edition_bytes(clean, source_sha256, source_size, redacted)).hexdigest()
    meta["normalized"] = {"turns": len(clean.turns), "redacted_turns": redacted, "malformed_lines": clean.malformed,
                          "identity": clean.identity, "omitted": dict(sorted(clean.omitted.items())),
                          "normalizer": clean.normalizer, **({"coverage": clean.coverage} if clean.coverage else {}),
                          **clean.manifest}
    return meta, render_body(clean), edition_bytes(clean, source_sha256, source_size, redacted)


__all__ = ["render_body", "parse_body", "parse_blocks", "dialogue_turns", "ordered_turns", "edition_bytes", "to_shelf", "hash_file", "scrub_parsed"]
