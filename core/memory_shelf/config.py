"""Shelf configuration (amendment v0.5.1 §2 M2, §3).

Paths are written in config but nothing here touches them: loading only parses
JSON and expands ``~`` as text. Defaults point at the **staging** shelf on the
Mac, never at the repo and never at production. A source is a *kind* of local
session store (``claude-code`` or ``codex``), its root, and its own
``never_copy`` list; a global ``never_copy`` applies to every source.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.memory_shelf.fsio import DEFAULT_MIN_FREE_BYTES

SOURCE_KINDS = ("claude-code", "codex", "rooms")
DEFAULT_CONFIG = Path("config/memory_shelf.json")


class ConfigError(ValueError):
    """The config is unusable. The message never includes file content."""


@dataclass(frozen=True)
class SourceCfg:
    name: str
    kind: str
    root: Path
    never_copy: tuple[str, ...] = ()
    # Rooms only: who the owner is (trusted participant ids, D2) and which Records store the bundles may come from.
    owner_ids: tuple[str, ...] = ()
    source_instance_id: str = ""

    def fingerprint(self) -> str:
        """What an approval binds to: the source's kind, root and never_copy list; for Rooms also the trusted owner ids,
        the source instance and the selection-policy version, so changing who counts as the owner, or pointing the same
        name at another store, needs a new approval. (Other kinds hash exactly as before: their approvals stay valid.)"""
        doc = {"kind": self.kind, "root": str(self.root), "never_copy": sorted(self.never_copy)}
        if self.kind == "rooms":
            doc.update({"owner_ids": sorted(self.owner_ids), "source_instance_id": self.source_instance_id,
                        "policy": "rooms-d2-v1"})
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    shelf_root: Path = Path("~/minimoi-staging/data/memory").expanduser()
    inbox_root: Path = Path("~/minimoi-inbox").expanduser()
    repo_root: Path = Path(".")
    min_free_bytes: int | None = DEFAULT_MIN_FREE_BYTES
    min_free_fraction: float | None = None
    never_copy: tuple[str, ...] = ()
    sources: dict[str, SourceCfg] = field(default_factory=dict)


def default_sources(global_never_copy: tuple[str, ...] = ()) -> dict[str, SourceCfg]:
    return {
        "claude-code": SourceCfg("claude-code", "claude-code", Path("~/.claude/projects").expanduser(), global_never_copy),
        "codex": SourceCfg("codex", "codex", Path("~/.codex/sessions").expanduser(), global_never_copy),
    }


def _strings(raw: object, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"{key} must be a list of strings")
    return tuple(raw)


def parse(doc: object) -> Config:
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ConfigError("not a memory shelf config")
    glob = _strings(doc.get("never_copy"), "never_copy")
    base = Config()
    head = doc.get("headroom") or {}
    sources = {}
    for name, raw in (doc.get("sources") or default_sources()).items():
        if isinstance(raw, SourceCfg):
            sources[name] = SourceCfg(raw.name, raw.kind, raw.root, tuple(dict.fromkeys(glob + raw.never_copy)),
                                      raw.owner_ids, raw.source_instance_id)
            continue
        if not isinstance(raw, dict) or raw.get("kind") not in SOURCE_KINDS or not isinstance(raw.get("root"), str):
            raise ConfigError("a source needs kind and root")
        own = _strings(raw.get("never_copy"), "never_copy")
        owners = _strings(raw.get("owner_ids"), "owner_ids")
        instance = raw.get("source_instance_id", "")
        if not isinstance(instance, str):
            raise ConfigError("source_instance_id must be a string")
        sources[str(name)] = SourceCfg(str(name), raw["kind"], Path(raw["root"]).expanduser(),
                                       tuple(dict.fromkeys(glob + own)), owners, instance)
    return Config(
        shelf_root=Path(doc.get("shelf_root", base.shelf_root)).expanduser(),
        inbox_root=Path(doc.get("inbox_root", base.inbox_root)).expanduser(),
        repo_root=Path(doc.get("repo_root", ".")).expanduser(),
        min_free_bytes=head.get("min_free_bytes", DEFAULT_MIN_FREE_BYTES),
        min_free_fraction=head.get("min_free_fraction"),
        never_copy=glob, sources=sources)


def load(path: str | Path | None = None) -> Config:
    """Read the config file if one exists; otherwise the staging defaults (nothing is touched)."""
    target = Path(path) if path else DEFAULT_CONFIG
    if not target.is_file():
        if path:
            raise ConfigError("config file not found")
        return Config(sources=default_sources())
    try:
        return parse(json.loads(target.read_text("utf-8")))
    except ValueError as exc:
        raise ConfigError("config is not valid JSON") from exc


def glob_match(pattern: str, path: str) -> bool:
    """``*`` stays in one folder; ``**`` crosses folders."""
    import re
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None


def never_copied(rel: str, entries: tuple[str, ...]) -> bool:
    """A relative path is never copied if it equals an entry, sits under one, names one of its
    folders, or matches it as a glob (on the whole path or the file name)."""
    parts = rel.split("/")
    for entry in entries:
        e = entry.strip().strip("/")
        if not e:
            continue
        if rel == e or rel.startswith(e + "/") or ("/" not in e and e in parts):
            return True
        if glob_match(e, rel) or glob_match(e, parts[-1]):
            return True
    return False
