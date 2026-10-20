"""The immutable observation record (``mc-observation/1``, standing spec §4.2).

One observation per watch per cycle. ``state`` is healthy | degraded | failed | unknown |
not_applicable; ``reason`` is a fixed code; ``measurements`` hold only small typed values (counts,
bytes, ages), never log text or free-form source content. Silence, an empty file or a file that
cannot be parsed is ``unknown`` with ``coverage`` partial or unavailable, never healthy.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SCHEMA = "mc-observation/1"
STATES = ("healthy", "degraded", "failed", "unknown", "not_applicable")
COVERAGE = ("complete", "partial", "unavailable")
_CODE = re.compile(r"^[a-z0-9][a-z0-9_:.-]{0,63}$")
_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
MAX_MEASUREMENTS = 32


class ObservationError(ValueError):
    """An observation that breaks the contract; never written or rendered."""


def utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scalar(value) -> bool:
    return value is None or isinstance(value, (bool, int, float)) or (
        isinstance(value, str) and len(value) <= 64 and bool(_CODE.match(value)))


@dataclass(frozen=True)
class Observation:
    cycle_key: str
    watch_id: str
    watch_version: int
    environment: str
    host: str
    state: str
    reason: str
    coverage: str
    collected_at: str
    observed_at: str | None = None
    measurements: dict = field(default_factory=dict)
    evidence: tuple = ()               # ((source_id, sha256), ...)
    collector_version: str = "p0b-1"

    def __post_init__(self):
        if self.state not in STATES:
            raise ObservationError("observation_state_invalid")
        if self.coverage not in COVERAGE:
            raise ObservationError("observation_coverage_invalid")
        if not _CODE.match(self.reason or ""):
            raise ObservationError("observation_reason_invalid")
        for text in (self.watch_id, self.environment, self.host):
            if not _KEY.match(text or ""):
                raise ObservationError("observation_identifier_invalid")
        if self.state == "healthy" and self.coverage != "complete":
            raise ObservationError("observation_green_without_coverage")
        if len(self.measurements) > MAX_MEASUREMENTS or not all(
                _KEY.match(k) and _scalar(v) for k, v in self.measurements.items()):
            raise ObservationError("observation_measurements_invalid")

    @property
    def observation_id(self) -> str:
        basis = json.dumps([self.cycle_key, self.watch_id, self.watch_version], separators=(",", ":"))
        return hashlib.sha256(basis.encode()).hexdigest()[:32]

    def to_dict(self) -> dict:
        doc = asdict(self)
        doc["schema"] = SCHEMA
        doc["observation_id"] = self.observation_id
        doc["evidence"] = [list(e) for e in self.evidence]
        return doc


def unknown(cycle_key: str, watch, reason: str, now: datetime, coverage: str = "unavailable",
            **measurements) -> Observation:
    """The one way to say "could not check" for any watch."""
    return Observation(cycle_key, watch.id, watch.version, watch.environment, watch.host_scope,
                       "unknown", reason, coverage, utc(now), measurements=measurements)
