"""Master Craftsman conversation exports on the shared memory path (watcher kind ``mc``, provider ``minimoi-mc``).

The portal's exporter (``scripts/vault/export_mc_conversations.py``) writes one JSON file per kept, on-record
conversation of the owner: ``<instance>--<conversation id>.json`` in a private folder. This module reads those files,
**read-only**, as a format-only parser for the standard watcher path (``source-file`` retention, size + mtime + sha256
change detection, ``Bundle -> scrub -> Shelf.ingest -> ledger``), exactly like a Claude Code or Codex session file. It
never opens the conversation store or the floor database and never runs the exporter.

**What is in a file.** Only what the portal kept on the record: owner notes, agent replies (Master Craftsman, or another
agent that wrote into the conversation) and platform notes (receipts, retained voice transcripts), each with its note id,
time and author kind. Private (off-record) questions are never in the conversation store or the floor database
(``security.py`` refuses every off-record write; ``guild.floor_messages`` has ``CHECK (record_mode = 'on_record')``;
``api.mc_private`` keeps nothing), so they cannot reach a file. Another owner's notes are dropped by the exporter.

**Speakers come from the store's structure, never from the text.** ``author_kind`` ``owner`` is the owner (human);
``agent`` is an assistant; ``platform`` is system, except a retained voice transcript, whose side (owner ASR or assistant
generated) the exporter takes from the platform-written note header and author id (the owner cannot write a platform
note). An author kind this reader does not know is a system turn, counted and flagged (``unknown_kind``), never a guess.

**Identity.** ``minimoi-mc:<instance>:<conversation id>``: one record per conversation per portal instance (Dev and
production are different stores, so the same id on two instances is never the same conversation). A changed export adds
an edition; earlier editions stay.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from core.memory_shelf.sessions import ASSISTANT, HUMAN, NORMALIZER_VERSION, SYSTEM, Parsed, turn_time

PROVIDER = "minimoi-mc"
CHAIR = "MC"
KIND = "mc"
NORMALIZER = NORMALIZER_VERSION[PROVIDER]     # 2: the note's time is normalized to UTC (1 kept it only when already a token)
SCHEMA = "minimoi.mc-conversation/1"
MAX_BYTES = 48 * 1024 * 1024                  # below the watcher's 64 MiB source cap; the exporter refuses anything bigger
AUTHOR_KINDS = frozenset({"owner", "agent", "platform"})
VOICE = {"owner_asr": (HUMAN, "voice_asr"), "assistant_generated": (ASSISTANT, "voice_generated")}

INSTANCE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
CONVERSATION = re.compile(r"^(c-[0-9a-f]{12}|legacy-[0-9a-f]{16})$")
FILE = re.compile(r"^([a-z][a-z0-9-]{0,31})--(c-[0-9a-f]{12}|legacy-[0-9a-f]{16})\.json$")
TOKEN = re.compile(r"^[A-Za-z0-9_:.+-]{1,64}$")


class BadExport(ValueError):
    """The file is not the export shape. The message is a fixed code; it never carries content."""


def wanted(rel: str) -> bool:
    """An export file sits at the top of the source folder and is named ``<instance>--<conversation id>.json``."""
    return "/" not in rel and bool(FILE.match(rel))


def _need(ok: bool) -> None:
    if not ok:
        raise BadExport("bad_export")


def _str(value) -> bool:
    return isinstance(value, str)


def load(lines: Iterable[bytes | str]) -> dict:
    """The export document after its shape check. ``lines`` is the watcher's bounded line reader."""
    chunks, total = [], 0
    for raw in lines:
        part = raw.encode("utf-8") if isinstance(raw, str) else raw
        total += len(part)
        if total > MAX_BYTES:
            raise BadExport("too_large")
        chunks.append(part)
    try:
        doc = json.loads(b"".join(chunks).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadExport("bad_export") from None
    _need(isinstance(doc, dict) and doc.get("schema") == SCHEMA)
    _need(_str(doc.get("instance")) and bool(INSTANCE.match(doc["instance"])))
    _need(_str(doc.get("conversation_id")) and bool(CONVERSATION.match(doc["conversation_id"])))
    _need(doc.get("source_id") == f"{doc['instance']}:{doc['conversation_id']}")
    _need(isinstance(doc.get("archived"), bool) and isinstance(doc.get("legacy"), bool))
    _need(_str(doc.get("created_at")) and _str(doc.get("updated_at")))
    _need(doc.get("scope") is None or (_str(doc["scope"]) and bool(TOKEN.match(doc["scope"]))))
    _need(isinstance(doc.get("turns"), list))
    last = 0
    for turn in doc["turns"]:
        _need(isinstance(turn, dict) and isinstance(turn.get("note_id"), int) and not isinstance(turn["note_id"], bool))
        _need(turn["note_id"] > last)                       # strictly increasing: the store's own order, no repeats
        last = turn["note_id"]
        _need(_str(turn.get("created_at")) and _str(turn.get("author_kind")) and _str(turn.get("text")))
        _need(_str(turn.get("who")) and bool(TOKEN.match(turn["who"])) and _str(turn.get("label")))
        _need(turn.get("voice") is None or turn["voice"] in VOICE)
        _need(turn.get("voice") is None or turn["author_kind"] == "platform")
    return doc


def parse(lines: Iterable[bytes | str]) -> Parsed:
    """One export file as ordered turns (no summarising, inferring or dropping). Raises ``BadExport``."""
    doc = load(lines)
    out = Parsed(PROVIDER, CHAIR, source_id=doc["source_id"], started=doc["created_at"], identity="source",
                 normalizer=NORMALIZER)
    unknown = empties = 0
    for number, turn in enumerate(doc["turns"], 1):
        out.lines = number
        kind, voice = turn["author_kind"], turn.get("voice")
        if not turn["text"].strip():
            out.skip(f"empty:{kind if kind in AUTHOR_KINDS else 'unknown'}", number)
            empties += 1
            continue
        if voice:
            speaker, typed = VOICE[voice]
            basis = f"mc:voice:{voice}"
        elif kind == "owner":
            speaker, typed, basis = HUMAN, "owner", "mc:owner"
        elif kind == "agent":
            speaker, typed, basis = ASSISTANT, "agent", "mc:agent"
        elif kind == "platform":
            speaker, typed, basis = SYSTEM, "platform", "mc:platform"
        else:
            speaker, typed, basis = SYSTEM, "unknown", "mc:unknown"
            unknown += 1
        attrs = {"who": turn["who"], "seq": turn["note_id"], "kind": typed, "label": turn["label"]}
        at = turn_time(turn["created_at"])             # Postgres writes "2026-10-17 12:00:00.123+00:00": a space, never a token
        if at:
            attrs["ts"] = at
        out.add(speaker, turn["text"], number, basis, attrs)
    out.manifest = {"mc_instance": doc["instance"], "mc_conversation": doc["conversation_id"],
                    "mc_archived": doc["archived"], "mc_legacy": doc["legacy"], "mc_export_schema": SCHEMA,
                    "mc_notes": len(doc["turns"]), **({"mc_scope": doc["scope"]} if doc.get("scope") else {}),
                    **({"unknown_author_kind": unknown} if unknown else {})}
    from core.memory_shelf import coverage
    taken = len(out.turns) + empties
    out.coverage = {"seen": {"notes": len(doc["turns"])}, "taken": {"notes": taken},
                    "gap": {"notes": max(0, len(doc["turns"]) - taken)},
                    "flags": [coverage.UNKNOWN_KIND] if unknown else []}
    return out


__all__ = ["parse", "load", "wanted", "BadExport", "PROVIDER", "CHAIR", "KIND", "NORMALIZER", "SCHEMA", "FILE"]
