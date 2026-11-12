"""Production CoS conversations to the Mac (11 Nov 2026, #509): the batch family and the export file both sides use.

The same trip as Master Craftsman's (``core/memory_sync/mc_batch.py``, whose code this reuses as the family ``COS``): EC2's
``vault-cos-export`` writes encrypted batches under ``<dest>/sync/cos-export/<batch_id>/`` and the Mac's ``vault-cos-pull`` acknowledges
each under ``<dest>/sync/cos-export-acks/<batch_id>.json``. IAM keeps the prefixes apart exactly as MC's: the EC2 role writes
``sync/cos-export/``, the Mac writes ``sync/cos-export-acks/``.

**The export file.** One file per conversation, ``<environment>--<conversation id>.json`` (``prod--cosc-<hex>.json``), one compact JSON
line::

    {"schema": "minimoi.cos-conversation-export/1", "environment": "prod", "conversation_id": "cosc-...", "source_id": "prod:cosc-...",
     "conversation": "<the metadata file's bytes, as text>", "turns": "<the turn journal's bytes, as text>"}

The two history files travel byte for byte, so the Mac reads them by exactly the rules the Dev feed uses (``core/memory_shelf/cos.py``).
``export_doc`` is deterministic (sorted keys, ASCII), so an unchanged conversation is the same bytes and the same sha256 every run.

**Private never travels** (owner decision D-G1, 11 Nov). Three independent checks, each failing closed: the exporter sends a conversation
only when its metadata says ``"private": false`` exactly (``cos_export.select``); the Mac's pull rejects a placed file whose packed
metadata is anything else (``doc_ok`` here, ``rejected:not_an_export``); the watcher's parser refuses it again (``cos.parse``).

Standard library only (the EC2 tools bundle). ``FILE`` is kept equal to ``core/memory_shelf/cos.py`` ``FILE`` by a test.
"""
from __future__ import annotations

import json
import re

from core.memory_sync import mc_batch as MB

FORMAT = "vault-cos-export/1"
ACK_FORMAT = "vault-cos-export-ack/1"
EXPORT_PREFIX = "sync/cos-export"
ACK_PREFIX = "sync/cos-export-acks"
EXPORT_SCHEMA = "minimoi.cos-conversation-export/1"                 # == core/memory_shelf/cos.py EXPORT_SCHEMA
CONVERSATION_SCHEMA = "minimoi.cos.conversation/1"
ENVIRONMENTS = ("dev", "staging", "prod")
FILE = re.compile(r"^(dev|staging|prod)--(cosc-[0-9a-f]{12,32}|cos-before-1-2)\.json$")     # == core/memory_shelf/cos.py FILE
CID = re.compile(r"^(?:cosc-[0-9a-f]{12,32}|cos-before-1-2)$")
META_MAX = 65536                                                    # == core/memory_shelf/cos.py META_MAX
MAX_FILE_BYTES = MB.MAX_FILE_BYTES                                  # == core/memory_shelf/cos.py MAX_BYTES


def meta_ok(meta, environment: str, conversation_id: str) -> bool:
    """The conversation may travel: its own schema and id, ``private`` exactly ``false`` (absent, null or anything else is private),
    this environment and the personal data scope."""
    return isinstance(meta, dict) and meta.get("schema") == CONVERSATION_SCHEMA and meta.get("conversation_id") == conversation_id \
        and meta.get("private") is False and meta.get("environment") == environment and meta.get("data_scope") == "personal"


def doc_ok(doc, instance: str, conversation_id: str) -> bool:
    """``check_file``'s shape check for one export file: the export schema for its own name, and packed metadata that may travel."""
    if not (isinstance(doc, dict) and doc.get("schema") == EXPORT_SCHEMA and doc.get("environment") == instance
            and doc.get("conversation_id") == conversation_id and doc.get("source_id") == f"{instance}:{conversation_id}"
            and isinstance(doc.get("conversation"), str) and isinstance(doc.get("turns"), str)
            and len(doc["conversation"].encode("utf-8")) <= META_MAX):
        return False
    try:
        meta = json.loads(doc["conversation"])
    except ValueError:
        return False
    return meta_ok(meta, instance, conversation_id)


def export_doc(environment: str, conversation_id: str, meta_raw: bytes, journal_raw: bytes) -> bytes:
    """The export file's bytes. Raises ``UnicodeDecodeError`` when either history file is not UTF-8 (the caller skips it)."""
    doc = {"schema": EXPORT_SCHEMA, "environment": environment, "conversation_id": conversation_id,
           "source_id": f"{environment}:{conversation_id}", "conversation": meta_raw.decode("utf-8"), "turns": journal_raw.decode("utf-8")}
    return (json.dumps(doc, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")


COS = MB.Family(name="cos", fmt=FORMAT, ack_fmt=ACK_FORMAT, export_prefix=EXPORT_PREFIX, ack_prefix=ACK_PREFIX, file=FILE, doc_ok=doc_ok,
                max_file_bytes=MAX_FILE_BYTES)
