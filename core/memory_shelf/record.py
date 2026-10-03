"""One shelf record: YAML front matter + body, written atomically (v0.5 B1/B2).

``<descriptive-slug>--<short-ulid>.md``. Key order is fixed so the same record
always serialises to the same bytes. Stored keys are *facts*; nothing here
stores a trust level. ``tier`` is ``raw`` or ``curated``; ``scope`` is ``robert``
or ``mandate:<id>`` and **never** a guest.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from core.memory_shelf import events as ev
from core.memory_shelf import ulid as ulids

KEYS = ("id", "kind", "created", "chair", "source", "source_hash", "scope", "tier", "tags",
        "edition", "edition_hash", "events", "embedding", "type")
CHAIRS = frozenset({"CoS", "Claude", "Claude Code", "Codex", "Grok", "Robert"})
TIERS = frozenset({"raw", "curated"})
_SCOPE = re.compile(r"^(robert|mandate:[0-9A-HJKMNP-TV-Z]{26})$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class InvalidRecord(ValueError):
    """The record breaks the contract. Messages never quote record text."""


def new_front_matter(*, kind: str, chair: str, source: str, source_hash: str, created: str,
                     tier: str = "raw", scope: str = "robert", tags: list[str] | None = None,
                     title: str = "record", now_ms: int | None = None) -> dict:
    meta = {
        "id": ulids.new_ulid(now_ms), "kind": kind, "created": created, "chair": chair,
        "source": source, "source_hash": source_hash, "scope": scope, "tier": tier,
        "tags": list(tags or []), "events": [],
    }
    check(meta)
    return meta


def check(meta: dict) -> dict:
    if not ulids.is_ulid(meta.get("id")):
        raise InvalidRecord("id must be a ULID")
    if meta.get("chair") not in CHAIRS:
        raise InvalidRecord("chair must name a chair, never a model")
    if meta.get("tier") not in TIERS:
        raise InvalidRecord("tier must be raw or curated")
    scope = meta.get("scope")
    if not isinstance(scope, str) or not _SCOPE.match(scope):
        raise InvalidRecord("scope must be robert or mandate:<id> (never a guest)")
    if not _SHA.match(str(meta.get("source_hash", ""))):
        raise InvalidRecord("source_hash must be a sha256 of the original")
    if not isinstance(meta.get("created"), str) or not meta["created"].endswith("Z"):
        raise InvalidRecord("created must be UTC")
    try:
        ev.check_events(meta.get("events", []))
    except ev.EventRefused as exc:
        raise InvalidRecord(str(exc)) from exc
    return meta


def dump(meta: dict, body: str) -> str:
    check(meta)
    ordered = {k: meta[k] for k in KEYS if k in meta}
    extra = sorted(set(meta) - set(ordered))
    ordered.update({k: meta[k] for k in extra})
    head = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{head}---\n\n{body.rstrip()}\n"


def load(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise InvalidRecord("no front matter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise InvalidRecord("front matter not closed")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise InvalidRecord("front matter is not valid YAML") from exc
    if not isinstance(meta, dict):
        raise InvalidRecord("front matter must be a mapping")
    return check(meta), text[end + 5:].lstrip("\n")


def write(path: Path, meta: dict, body: str) -> None:
    """Atomic: temp file in the same folder, fsync, rename."""
    data = dump(meta, body).encode("utf-8")
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def append_event(path: Path, event: dict) -> dict:
    """Append one already-built event; every earlier event is left byte-for-byte as it was."""
    meta, body = load(Path(path).read_text(encoding="utf-8"))
    meta["events"] = [*meta.get("events", []), event]
    write(path, meta, body)
    return meta


__all__ = ["new_front_matter", "check", "dump", "load", "write", "append_event", "InvalidRecord", "CHAIRS"]
