"""cos_memory.md moves into the CoS store, and becomes its projection (spec v0.3 sections 4.2 and 4.5, memory layers).

The store is canonical for what CoS keeps about Robert's context. Each line of cos_memory.md becomes one standing fact
(facts.jsonl, contracts/cos/v1/store-records.schema.json#/$defs/standing_fact) with its real provenance:

- ``[chat]`` and ``[action]`` lines, and the untagged distilled prose under each section, were written by the model:
  ``agent_draft``, never ``robert_source``.
- ``[note]`` lines with their ``<!-- cos-operation:<id> -->`` marker are explicit notes Robert asked to save: ``approved``,
  citing that operation. Without the marker the line can't be traced, so it stays ``agent_draft``.
- No line becomes ``robert_source``: the file holds no turn the server can verify.

The import is one fact.record command per line, each with a receipt. Command ids are derived from the line, so a re-run
replays the first receipts and writes nothing twice. The original file is kept byte for byte at
``imports/cos_memory.md.<digest>.orig`` and ``imports/cos_memory.done`` marks the import complete.

After that, cos_memory.md is a derived projection of the facts, rewritten **in place** after every new fact: it is a
single-file bind mount in the scheduler and cos-bot, and a rename would leave the other container reading the old
inode. Until the import is done, nothing here writes cos_memory.md.

Rollback: stop the scheduler, copy the .orig file back over cos_memory.md, remove imports/cos_memory.done. The facts
stay in the store and are ignored until the next import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core import durable_files as fsio
from core.canonical_json import digest
from domains.cos.store.writer import StoreWriter

MEMORY_CAP = 7_500
MAX_TEXT = 4_000
DEFAULT_SECTION = "General"
NEW_SECTION = "Recent"
PROJECTION_HEADER = ("# CoS Memory (a projection of the CoS store's standing facts; it is rewritten from the store, "
                     "so change it through CoS, not here)")
DONE_MARKER = "imports/cos_memory.done"
IMPORT_MANIFEST = "imports/cos_memory.import.json"


class ImportRefused(Exception):
    """The import would duplicate facts: it already ran, or the file is already the store's projection."""

_TAGGED = re.compile(r"^\[([a-z_]+)\] (?:([0-9]{4}-[0-9]{2}-[0-9]{2}): )?(.*)$")
_PLACEHOLDER = re.compile(r"^\[[^\]]*\]$")
_OPERATION = re.compile(r"^<!-- cos-operation:([A-Za-z0-9][A-Za-z0-9._:@-]{0,127}) -->$")
_FILE_TAGS = {"chat", "note", "action"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bounded(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def parse(text: str) -> list[dict]:
    """The file's entries, in order: section, tag, entry_date, text, operation_id and the digest of the raw line."""
    entries, section, operation = [], DEFAULT_SECTION, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("# ") or line.lower().startswith("last distilled:"):
            continue
        if line.startswith("## "):
            section = line[3:].strip()[:120] or DEFAULT_SECTION
            continue
        marker = _OPERATION.match(line)
        if marker:
            operation = marker.group(1)
            continue
        if _PLACEHOLDER.match(line):
            continue                                    # the template's own "[What is always true …]" guidance
        tagged = _TAGGED.match(line)
        if tagged and tagged.group(1) in _FILE_TAGS and tagged.group(3).strip():
            tag, date, body = tagged.group(1), tagged.group(2), tagged.group(3).strip()
        else:
            tag, date, body = "distilled", None, line[2:].strip() if line.startswith("- ") else line
        entry = {"section": section, "tag": tag, "entry_date": date, "text": _bounded(body),
                 "line_digest": digest(raw.rstrip("\n"))}
        if tag != "distilled" and operation:
            entry["operation_id"] = operation
        operation = None
        entries.append(entry)
    return entries


def fact_for(entry: dict, environment: str, *, kind: str = "cos_memory_md", at: str | None = None) -> dict:
    """The standing_fact for one entry, with its real provenance."""
    seed = json.dumps([kind, entry["section"], entry["tag"], entry.get("entry_date"), entry["text"],
                       entry.get("operation_id"), entry.get("line_digest")], ensure_ascii=False)
    fact_id = "FACT-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    if entry["tag"] == "note" and entry.get("operation_id"):
        text = {"text": entry["text"], "provenance": "approved",
                "ref": {"type": "cos.memory_note", "id": entry["operation_id"]}}
    else:
        text = {"text": entry["text"], "provenance": "agent_draft"}
    source = {"kind": kind, "tag": entry["tag"], "entry_date": entry.get("entry_date")}
    if entry.get("operation_id"):
        source["operation_id"] = entry["operation_id"]
    if entry.get("line_digest"):
        source["line_digest"] = entry["line_digest"]
    when = at or (f"{entry['entry_date']}T00:00:00+00:00" if entry.get("entry_date") else _now())
    return {"schema": "minimoi.cos.standing_fact/1", "data_scope": "personal", "environment": environment,
            "id": fact_id, "at": when, "section": entry["section"], "entry": text, "source": source,
            "state": "active"}


def record(writer: StoreWriter, fact: dict, *, origin: str, actor: str, basis_ref: dict | None = None) -> dict:
    """One fact.record command; the command id is derived from the fact, so a retry replays its receipt."""
    current = len(writer.rows("cos.fact"))
    command = {"schema": "minimoi.cos.store_command/1", "command_id": "cmd-fact-" + fact["id"][5:],
               "kind": "fact.record", "target": {"type": "cos.fact", "id": fact["id"]},
               "expected_revision": current, "actor": actor, "seat": None, "origin": origin, "basis_ref": basis_ref,
               "payload": fact, "payload_digest": digest(fact), "data_scope": "personal",
               "environment": writer.environment}
    return writer.submit(command)


def imported(writer: StoreWriter) -> bool:
    return (writer.root / DONE_MARKER).exists()


def _import_time(writer: StoreWriter) -> str:
    """One timestamp for every undated line, fixed on the first run and reused, so a re-run sends identical payloads
    and replays its receipts (review M1)."""
    manifest = writer.root / IMPORT_MANIFEST
    try:
        return json.loads(manifest.read_text("utf-8"))["at"]
    except (FileNotFoundError, ValueError, KeyError):
        at = _now()
        fsio.write_bytes(manifest, json.dumps({"at": at}).encode("utf-8") + b"\n")
        return at


def import_file(writer: StoreWriter, memory_file: Path | str) -> dict:
    """Move cos_memory.md into the store. Stops at the first fact not accepted, and marks done only when all are.
    Refuses (ImportRefused) once the import is done or the file is already the projection: importing the projection
    would add every fact a second time, and the store is append-only."""
    path = Path(memory_file)
    if imported(writer):
        raise ImportRefused("already imported: imports/cos_memory.done exists")
    raw = path.read_bytes()
    if raw.decode("utf-8", errors="replace").lstrip().startswith(PROJECTION_HEADER):
        raise ImportRefused("cos_memory.md is already the store's projection")
    undated_at = _import_time(writer)
    original = writer.root / "imports" / f"cos_memory.md.{hashlib.sha256(raw).hexdigest()[:16]}.orig"
    if not original.exists():
        fsio.write_bytes(original, raw)
    entries = parse(raw.decode("utf-8"))
    counts = {"entries": len(entries), "accepted": 0, "replayed": 0, "approved_notes": 0}
    for entry in entries:
        fact = fact_for(entry, writer.environment, at=None if entry.get("entry_date") else undated_at)
        result = record(writer, fact, origin="import", actor="scheduler")
        if result["outcome"] != "accepted":
            counts["stopped"] = {"fact": fact["id"], "outcome": result["outcome"],
                                 "code": result.get("code") or result.get("current_revision")}
            return counts
        counts["replayed" if result.get("replayed") else "accepted"] += 1
        counts["approved_notes"] += fact["entry"]["provenance"] == "approved"
    fsio.write_bytes(writer.root / DONE_MARKER, json.dumps(
        {"original": original.name, "entries": len(entries), "at": _now()}).encode("utf-8") + b"\n")
    return counts


def render(facts: list[dict], cap: int = MEMORY_CAP) -> str:
    """The projection: sections in first-seen order, each fact as the file wrote it. Over the cap, the oldest facts
    are left out and counted, never cut mid-line; all of them stay in the store."""
    def line(fact: dict) -> str:
        tag, date, text = fact["source"]["tag"], fact["source"]["entry_date"], fact["entry"]["text"]
        if tag == "distilled":
            return text
        return f"[{tag}] {date}: {text}" if date else f"[{tag}] {text}"

    def build(kept: list[dict], left_out: int) -> str:
        sections: dict[str, list[str]] = {}
        for fact in kept:
            sections.setdefault(fact["section"], []).append(line(fact))
        out = [PROJECTION_HEADER, ""]
        if left_out:
            out += [f"({left_out} older facts are kept in the CoS store and left out here.)", ""]
        for name, lines in sections.items():
            out += [f"## {name}", *lines, ""]
        return "\n".join(out)

    kept = list(facts)
    text = build(kept, 0)
    order = sorted(range(len(kept)), key=lambda i: kept[i]["at"])
    dropped: set[int] = set()
    while len(text) > cap and len(dropped) < len(kept):
        dropped.add(order[len(dropped)])
        text = build([f for i, f in enumerate(kept) if i not in dropped], len(dropped))
    return text


def write_projection(writer: StoreWriter, memory_file: Path | str) -> str:
    """Rewrite cos_memory.md from the store, in place (same inode), fsynced."""
    text = render(writer.rows("cos.fact"))
    data = text.encode("utf-8")
    fd = os.open(memory_file, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        os.ftruncate(fd, 0)
        fsio.write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    return text


def remember(writer: StoreWriter, memory_file: Path | str, text: str, *, kind: str,
             operation_id: str | None = None, clock: Callable[[], str] = _now) -> dict:
    """A new fact after the import: the model's write_memory (agent_draft) or Robert's explicit note (approved, citing
    its operation). Returns {"saved", "deduplicated"} like the file writer it replaces."""
    when = clock()
    entry = {"section": NEW_SECTION, "tag": "note" if kind == "explicit_note" else "chat",
             "entry_date": when[:10], "text": _bounded(" ".join(str(text).split()))}
    if operation_id:
        entry["operation_id"] = operation_id
    if kind == "explicit_note" and not operation_id:
        raise ValueError("an explicit note needs its operation id")
    # A note is keyed by its operation alone, so a retried note replays even on another day.
    fact = fact_for(entry, writer.environment, kind=kind, at=when)
    if operation_id:
        fact["id"] = "FACT-" + hashlib.sha256(f"{kind}:{operation_id}".encode("utf-8")).hexdigest()[:24]
    # The same note retried, or the model writing the same line twice in a day, is already kept (review S2).
    if any(f["id"] == fact["id"] for f in writer.rows("cos.fact")):
        return {"saved": True, "deduplicated": True}
    # Both happen inside a turn Robert started. The note cites its own operation; the model's write cites itself,
    # because the Grok backend's write_memory call carries no turn id.
    basis = ({"type": "cos.memory_note", "id": operation_id} if kind == "explicit_note"
             else {"type": "cos.memory_write", "id": fact["id"]})
    result = record(writer, fact, origin="owner_turn", actor="cos", basis_ref=basis)
    if result["outcome"] != "accepted":
        return {"saved": False, "deduplicated": False, "code": result.get("code", result["outcome"])}
    write_projection(writer, memory_file)
    return {"saved": True, "deduplicated": bool(result.get("replayed"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move cos_memory.md into the CoS store (spec v0.3 section 4.5).")
    parser.add_argument("--store", required=True, help="the CoS store folder (production: /opt/minimoi/cos/store)")
    parser.add_argument("--environment", required=True, choices=["dev", "staging", "prod"])
    parser.add_argument("--memory", required=True, help="cos_memory.md")
    parser.add_argument("--project", action="store_true", help="after a complete import, rewrite the file as the projection")
    args = parser.parse_args(argv)
    writer = StoreWriter(args.store, args.environment)
    try:
        counts = import_file(writer, args.memory)
    except ImportRefused as exc:
        print(json.dumps({"refused": str(exc)}))
        return 2
    if "stopped" not in counts and args.project:
        write_projection(writer, args.memory)
        counts["projected"] = True
    print(json.dumps(counts))
    return 1 if "stopped" in counts else 0


if __name__ == "__main__":
    sys.exit(main())
