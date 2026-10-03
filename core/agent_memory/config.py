"""Source configuration (agent-memory v0.4 §3.3, §3.5).

``config/agent_memory_sources.json`` lists each source with its ``include`` and
``never_copy``. Only labels are stored: the Mac inbox path comes from an
environment variable named in the config (``inbox_env``), so no absolute path
of Robert's home appears in any file or output.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .headroom import DEFAULT_MIN_FREE_BYTES
from .selection import DEFAULT_PATTERNS, Rules
from .sources import DirectorySource, DockerArchiveSource, Source

DEFAULT_CONFIG = Path("config/agent_memory_sources.json")
KINDS = ("docker_workspace", "mac_folder")


class ConfigError(ValueError):
    """The config file is unusable. The message never includes file content."""


@dataclass(frozen=True)
class Headroom:
    """Mac default: 5 GB. On EC2 set ``min_free_fraction`` to 0.10 (amendment §4)."""
    min_free_bytes: int | None = DEFAULT_MIN_FREE_BYTES
    min_free_fraction: float | None = None


@dataclass(frozen=True)
class SourceConfig:
    name: str
    agent: str
    runtime: str
    runtime_version: str
    source_kind: str
    enabled: bool = True
    patterns: tuple[str, ...] = DEFAULT_PATTERNS
    include: tuple[str, ...] = ()
    never_copy: tuple[str, ...] = ()
    container: str = ""
    workspace_path: str = ""
    inbox_env: str = ""

    def rules(self) -> Rules:
        return Rules(self.patterns, self.include, self.never_copy)

    def meta(self) -> dict[str, str]:
        """The manifest's ``source`` block: labels only (§3.2)."""
        return {"agent": self.agent, "runtime": self.runtime,
                "runtime_version": self.runtime_version, "source_kind": self.source_kind}


@dataclass(frozen=True)
class AppConfig:
    data_root: str
    headroom: Headroom = field(default_factory=Headroom)
    sources: dict[str, SourceConfig] = field(default_factory=dict)


def _strings(raw: object, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ConfigError(f"{key} must be a list of strings")
    return tuple(raw)


def parse_config(doc: object) -> AppConfig:
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("sources"), dict):
        raise ConfigError("not an agent memory config")
    head = doc.get("headroom") or {}
    headroom = Headroom(head.get("min_free_bytes", DEFAULT_MIN_FREE_BYTES),
                        head.get("min_free_fraction", None))
    sources: dict[str, SourceConfig] = {}
    for name, raw in doc["sources"].items():
        if not isinstance(raw, dict) or raw.get("source_kind") not in KINDS:
            raise ConfigError("a source needs a known source_kind")
        sources[name] = SourceConfig(
            name=name, agent=str(raw.get("agent", name)), runtime=str(raw.get("runtime", "")),
            runtime_version=str(raw.get("runtime_version", "")), source_kind=raw["source_kind"],
            enabled=bool(raw.get("enabled", True)),
            patterns=_strings(raw.get("patterns"), "patterns") or DEFAULT_PATTERNS,
            include=_strings(raw.get("include"), "include"),
            never_copy=_strings(raw.get("never_copy"), "never_copy"),
            container=str(raw.get("container", "")), workspace_path=str(raw.get("workspace_path", "")),
            inbox_env=str(raw.get("inbox_env", "")))
    return AppConfig(str(doc.get("data_root", "data/agent-memory")), headroom, sources)


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    target = Path(path) if path else DEFAULT_CONFIG
    try:
        return parse_config(json.loads(target.read_text("utf-8")))
    except (OSError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError("config file unreadable or not JSON") from None


def build_source(cfg: SourceConfig, env: Mapping[str, str] | None = None) -> Source:
    """The reader for a configured source. The Docker reader is a stub until M1 integration."""
    env = os.environ if env is None else env
    if cfg.source_kind == "mac_folder":
        inbox = env.get(cfg.inbox_env, "") if cfg.inbox_env else ""
        if not inbox:
            raise ConfigError("the Mac inbox location is not set in the environment")
        return DirectorySource(inbox, require_copy_manifest=True)
    return DockerArchiveSource(cfg.container, cfg.workspace_path)
