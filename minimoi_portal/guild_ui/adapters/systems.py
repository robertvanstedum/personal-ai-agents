"""The Systems light's source: the Operations agent's /status, probed live.

2 s timeout, answers cached 60 s (failures too, so a dead agent is not
hammered). Unset URL: not configured. Any failure, timeout or unparsable
answer: unknown. The agent's ``open_escalations`` is carried as a reported
count only; it reads 0 when the agent's own database read fails (C22), so
it never counts toward green.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from .contract import INVALID, READ_FAILED, SourceResult, live_ok, live_unknown, not_configured

EVIDENCE = "Operations agent /status (live probe)"


def _default_get(url: str, timeout: float):
    import requests
    return requests.get(url, timeout=timeout)


class OperationsProbe:
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
            return not_configured("GUILD_OPERATIONS_STATUS_URL", "the Operations agent address")
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
            return live_unknown(READ_FAILED, f"Operations agent unreachable ({type(exc).__name__})", EVIDENCE)
        if getattr(response, "status_code", 200) != 200:
            return live_unknown(READ_FAILED, f"Operations agent answered {response.status_code}", EVIDENCE)
        try:
            body = response.json()
        except Exception:
            return live_unknown(INVALID, "Operations agent answer is not JSON", EVIDENCE)
        if not isinstance(body, dict) or not isinstance(body.get("state"), str):
            return live_unknown(INVALID, "Operations agent answer has no state", EVIDENCE)
        data = {
            "state": body.get("state"),
            "last_checkin": body.get("last_checkin"),
            "open_escalations": body.get("open_escalations"),
        }
        return live_ok(data, EVIDENCE, fresh_for_s=int(self.cache_s))
