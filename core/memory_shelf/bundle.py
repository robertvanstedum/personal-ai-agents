"""One captured conversation, staged in the outbox before it reaches the shelf (B5, B6).

A bundle is the output of the format-only normalizer: the record's front matter
and body, the immutable edition bytes, the exact source identity (``key``), and a
possible designation. It is one JSON file, written atomically, so it can be
shipped and applied later (B5 sync) and re-applied safely: the record id is
fixed when the bundle is built, so a retry never makes a second record.

**Identity.** ``key`` is ``provider:source_id`` from the source's own structure
(a CLI session id, a claude.ai conversation uuid). A paste has no conversation
id, so its key is ``paste:<sha256 of the paste>``: only an identical paste
matches, anything else is a *possible* match for the owner (never a merge).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.memory_shelf import render, sessions, ulid
from core.memory_shelf.sessions import HUMAN, Parsed

VERSION = 1
OWNER_ACTOR = "robert"


def utc(text: str | None, fallback: datetime | None = None) -> str:
    """Normalise an ISO timestamp to ``YYYY-MM-DDTHH:MM:SSZ`` (UTC); fall back to ``fallback`` or now."""
    if isinstance(text, str):
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return (fallback or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def title_slug(text: str) -> str:
    scrubbed, _ = render.scrub_text(text)
    return ulid.slugify(scrubbed)


@dataclass
class Bundle:
    key: str
    provider: str
    origin: str                      # e.g. "watcher:claude-code", "inbox"
    kind: str                        # "session" | "canary"
    title: str
    meta: dict
    body: str
    edition: bytes
    source_hash: str
    designation: dict | None = None
    retained: dict = field(default_factory=dict)
    ledger_key: str = ""
    export_sha256: str | None = None

    def to_json(self) -> bytes:
        doc = {"v": VERSION, "key": self.key, "provider": self.provider, "origin": self.origin, "kind": self.kind,
               "title": self.title, "meta": self.meta, "body": self.body, "source_hash": self.source_hash,
               "edition_b64": base64.b64encode(self.edition).decode("ascii"), "designation": self.designation,
               "retained": self.retained, "ledger_key": self.ledger_key, "export_sha256": self.export_sha256}
        return (json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "Bundle":
        doc = json.loads(raw)
        if doc.get("v") != VERSION:
            raise ValueError("bundle version")
        return cls(doc["key"], doc["provider"], doc["origin"], doc["kind"], doc["title"], doc["meta"], doc["body"],
                   base64.b64decode(doc["edition_b64"]), doc["source_hash"], doc.get("designation"),
                   doc.get("retained") or {}, doc.get("ledger_key", ""), doc.get("export_sha256"))


def from_parsed(parsed: Parsed, source_sha256: str, source_size: int, *, key: str, title: str, origin: str,
                created: str, retained: dict | None = None, ledger_key: str = "", kind: str = "session",
                export_sha256: str | None = None, tags: list[str] | None = None) -> Bundle:
    """Normalize one parsed conversation (scrubbed, D8) into a staged bundle."""
    meta, body, edition = render.to_shelf(parsed, source_sha256, source_size, created=created, title=title, tags=tags)
    meta["kind"] = kind
    meta["source"] = key
    first = next((t for t in parsed.turns if t.speaker == HUMAN), None)
    meta["normalized"]["first_human_sha256"] = render.sha256_text(render.scrub_text(first.text)[0]) if first else None
    meta["normalized"]["title"] = title_slug(title)
    return Bundle(key, parsed.provider, origin, kind, title, meta, body, edition, source_sha256,
                  sessions.designation(parsed), retained or {}, ledger_key, export_sha256)


def ledger_key_for(source: str, name: str) -> str:
    """An opaque stable id for one source item, so ledgers never carry file names."""
    return hashlib.sha256(f"{source}\0{name}".encode()).hexdigest()[:16]


_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def safe_name(name: str) -> str:
    return _SAFE.sub("_", name)[:120] or "item"


__all__ = ["Bundle", "from_parsed", "utc", "title_slug", "ledger_key_for", "safe_name", "OWNER_ACTOR"]
