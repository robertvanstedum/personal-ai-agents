"""The Agents light's source: the CoS scheduler's /agent-memory/status, probed live.

Same shape as the Systems probe: 2 s timeout, answers cached 60 s (failures
too). Unset URL: not configured (grey). Any failure, timeout or unparsable
answer: unknown, never a made-up green. Only whitelisted keys pass through,
and the scheduler itself sends codes, times and counts, never file names or text.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .contract import INVALID, READ_FAILED, SourceResult, live_ok, live_unknown, not_configured

EVIDENCE = "CoS scheduler /agent-memory/status (live probe)"
STATES = {"green", "yellow", "red", "unknown"}


def _default_get(url: str, timeout: float):
    import requests
    return requests.get(url, timeout=timeout)


def _clean_sources(raw) -> dict:
    out = {}
    if isinstance(raw, dict):
        for name, row in list(raw.items())[:12]:
            if isinstance(row, dict) and row.get("state") in STATES:
                out[str(name)[:40]] = {"state": row["state"], "reason": str(row.get("reason", ""))[:60]}
    return out


def _clean_turn_log(raw) -> dict:
    out = {}
    if isinstance(raw, dict):
        for name, row in list(raw.items())[:6]:
            if isinstance(row, dict):
                out[str(name)[:40]] = {k: (str(row.get(k))[:40] if row.get(k) is not None else None)
                                       for k in ("last_success_at", "last_failure_at", "last_failure_code")}
    return out


class MemoryCopyProbe:
    def __init__(self, url: str | None, *, timeout_s: float = 2.0, cache_s: float = 60.0,
                 http_get: Callable | None = None, monotonic: Callable[[], float] = time.monotonic):
        self.url = url or None
        self.timeout_s = timeout_s
        self.cache_s = cache_s
        self._get = http_get or _default_get
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cached: tuple[float, SourceResult] | None = None

    def status(self) -> SourceResult:
        if not self.url:
            return not_configured("GUILD_MEMORY_STATUS_URL", "the memory copy status address")
        with self._lock:
            now = self._monotonic()
            if self._cached and now - self._cached[0] < self.cache_s:
                return self._cached[1]
            result = self._probe()
            self._cached = (now, result)
            return result

    def _probe(self) -> SourceResult:
        try:
            response = self._get(self.url, self.timeout_s)
        except Exception as exc:
            return live_unknown(READ_FAILED, f"memory status unreachable ({type(exc).__name__})", EVIDENCE)
        if getattr(response, "status_code", 200) != 200:
            return live_unknown(READ_FAILED, f"memory status answered {response.status_code}", EVIDENCE)
        try:
            body = response.json()
        except Exception:
            return live_unknown(INVALID, "memory status is not JSON", EVIDENCE)
        if not isinstance(body, dict) or body.get("state") not in STATES or not isinstance(body.get("enabled"), bool):
            return live_unknown(INVALID, "memory status has no usable state", EVIDENCE)
        data = {"enabled": body["enabled"], "state": body["state"], "reason": str(body.get("reason", ""))[:60],
                "as_of": body.get("as_of") if isinstance(body.get("as_of"), str) else None,
                "sources": _clean_sources(body.get("sources")), "turn_log": _clean_turn_log(body.get("turn_log"))}
        return live_ok(data, EVIDENCE, fresh_for_s=int(self.cache_s))
