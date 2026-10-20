"""The reviewed watch registry (standing spec §4.1): ``config/mc_standing/watchlist.json``.

Strict: unknown fields, unknown adapters, executable-looking bindings or a missing approval are
configuration errors, never "do something sensible". The worker never edits this file; changes go
through review and deployment like any other config.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

SCHEMA = "mc-standing-watchlist/1"
ADAPTERS = ("job_status", "health_snapshot", "host_snapshot", "guest_summary", "release_status")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_FIELDS = {
    "id", "version", "label", "environment", "host_scope", "adapter_id", "source_binding",
    "stale_after_s", "expected_run_rule", "severity_rules", "max_read_bytes", "timeout_s",
    "required_for_coverage", "approved_by", "approval_ref", "review_by",
}
_REQUIRED = _FIELDS - {"expected_run_rule", "severity_rules"}
_RULE_FIELDS = {"expected_every_s", "grace_s", "missed_after_s", "stuck_after_s"}
MAX_READ_BYTES = 64 * 1024


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Watch:
    id: str
    version: int
    label: str
    environment: str
    host_scope: str
    adapter_id: str
    source_binding: str
    stale_after_s: int
    max_read_bytes: int
    timeout_s: float
    required_for_coverage: bool
    approved_by: str
    approval_ref: str
    review_by: date
    expected_run_rule: dict = field(default_factory=dict)
    severity_rules: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Watchlist:
    watches: tuple
    sha256: str


def _binding(value: object, where: str) -> str:
    """A relative path under the configured read-only source root. No URLs, no absolute paths,
    no parent references, no shell metacharacters."""
    if not isinstance(value, str) or not value or len(value) > 200:
        raise RegistryError(f"{where}: source_binding invalid")
    if "://" in value or value.startswith("/") or re.search(r"[\s;|&$`<>\\*?]", value):
        raise RegistryError(f"{where}: source_binding must be a plain relative path")
    parts = PurePosixPath(value).parts
    if any(p in ("..", ".", "") for p in parts):
        raise RegistryError(f"{where}: source_binding must not traverse")
    return value


def _number(raw, key, where, *, minimum=1, cast=int):
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise RegistryError(f"{where}: {key} invalid")
    return cast(value)


def _rules(raw: object, where: str, allowed: set | None) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or (allowed is not None and not set(raw) <= allowed):
        raise RegistryError(f"{where}: rules invalid")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in raw.values()):
        raise RegistryError(f"{where}: rule values must be non-negative numbers")
    return dict(raw)


def parse(doc: object, *, raw_bytes: bytes = b"") -> Watchlist:
    if not isinstance(doc, dict) or set(doc) != {"schema", "watches"} or doc["schema"] != SCHEMA:
        raise RegistryError("watchlist: schema invalid")
    if not isinstance(doc["watches"], list) or not doc["watches"]:
        raise RegistryError("watchlist: no watches")
    watches, seen = [], set()
    for i, raw in enumerate(doc["watches"]):
        where = f"watch[{i}]"
        if not isinstance(raw, dict):
            raise RegistryError(f"{where}: not an object")
        if not set(raw) <= _FIELDS or not _REQUIRED <= set(raw):
            raise RegistryError(f"{where}: fields invalid")
        wid = raw["id"]
        if not isinstance(wid, str) or not _ID.match(wid) or wid in seen:
            raise RegistryError(f"{where}: id invalid or duplicate")
        seen.add(wid)
        if raw["adapter_id"] not in ADAPTERS:
            raise RegistryError(f"{where}: unknown adapter")
        if raw["environment"] not in ("dev", "prod") or not _ID.match(str(raw["host_scope"])):
            raise RegistryError(f"{where}: environment/host invalid")
        for key in ("label", "approved_by", "approval_ref"):
            if not isinstance(raw[key], str) or not raw[key].strip() or len(raw[key]) > 120:
                raise RegistryError(f"{where}: {key} invalid")
        try:
            review_by = date.fromisoformat(raw["review_by"])
        except (TypeError, ValueError):
            raise RegistryError(f"{where}: review_by invalid") from None
        if not isinstance(raw["required_for_coverage"], bool):
            raise RegistryError(f"{where}: required_for_coverage invalid")
        max_bytes = _number(raw, "max_read_bytes", where)
        if max_bytes > MAX_READ_BYTES:
            raise RegistryError(f"{where}: max_read_bytes above {MAX_READ_BYTES}")
        rule = _rules(raw.get("expected_run_rule"), where, _RULE_FIELDS)
        if raw["adapter_id"] == "job_status" and set(rule) != _RULE_FIELDS:
            raise RegistryError(f"{where}: job_status needs a complete expected_run_rule")
        watches.append(Watch(
            id=wid, version=_number(raw, "version", where), label=raw["label"].strip(),
            environment=raw["environment"], host_scope=raw["host_scope"], adapter_id=raw["adapter_id"],
            source_binding=_binding(raw["source_binding"], where),
            stale_after_s=_number(raw, "stale_after_s", where), max_read_bytes=max_bytes,
            timeout_s=_number(raw, "timeout_s", where, cast=float),
            required_for_coverage=raw["required_for_coverage"], approved_by=raw["approved_by"],
            approval_ref=raw["approval_ref"], review_by=review_by, expected_run_rule=rule,
            severity_rules=_rules(raw.get("severity_rules"), where, None),
        ))
    return Watchlist(tuple(watches), hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else "")


def load(path: str | Path) -> Watchlist:
    raw = Path(path).read_bytes()
    try:
        doc = json.loads(raw)
    except ValueError:
        raise RegistryError("watchlist: not JSON") from None
    return parse(doc, raw_bytes=raw)
