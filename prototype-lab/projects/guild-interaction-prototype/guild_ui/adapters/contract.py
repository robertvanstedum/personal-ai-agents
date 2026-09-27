"""The one adapter contract: every read returns a SourceResult.

source   live | sample | not_instrumented   (where the value came from)
status   ok | stale | unknown                (whether it can be trusted now)
reason   None | not_configured | read_failed | invalid
A failed or invalid live read is always status "unknown" with data None;
it never falls back to sample and never becomes zero (SPEC §7.1).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

LIVE, SAMPLE, NOT_INSTRUMENTED = "live", "sample", "not_instrumented"
OK, STALE, UNKNOWN = "ok", "stale", "unknown"
NOT_CONFIGURED, READ_FAILED, INVALID = "not_configured", "read_failed", "invalid"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceResult:
    source: str
    status: str
    data: Any = None
    observed_at: str | None = None
    fresh_for_s: int | None = None
    evidence: str = ""
    reason: str | None = None
    error: str | None = None
    note: str | None = None      # e.g. "1 row with unknown status" (rows kept, badged per row)

    @property
    def ok(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict:
        return asdict(self)


def live_ok(data, evidence: str, fresh_for_s: int | None = None) -> SourceResult:
    return SourceResult(LIVE, OK, data, now_iso(), fresh_for_s, evidence)


def live_unknown(reason: str, error: str, evidence: str) -> SourceResult:
    return SourceResult(LIVE, UNKNOWN, None, now_iso(), None, evidence, reason, error)


def sample(data, evidence: str, observed_at: str | None = None, status: str = OK) -> SourceResult:
    return SourceResult(SAMPLE, status, data, observed_at, None, evidence)


def not_configured(evidence: str, what: str) -> SourceResult:
    return SourceResult(NOT_INSTRUMENTED, UNKNOWN, None, None, None, evidence,
                        NOT_CONFIGURED, f"{what} is not configured")
