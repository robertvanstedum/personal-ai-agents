"""Usage records for the CoS loops' Tavily searches (usage-record U2).

``tavily_search(client, loop, query, **kwargs)`` is ``client.search(query,
**kwargs)`` plus one usage record (a search, no cost on the free tier). The
search's result and any exception are exactly what ``client.search`` gives:
recording never changes the loop's behaviour, and never raises.
"""
from __future__ import annotations

import time

try:
    from services.usage import direct as _usage
except Exception:                       # the helper is optional: no record, same search
    _usage = None


def tavily_search(client, loop: str, query, **kwargs):
    started = time.monotonic()
    try:
        response = client.search(query, **kwargs)
    except Exception as exc:
        _record(loop, started, exc)
        raise
    _record(loop, started, None)
    return response


def _record(loop: str, started: float, error) -> None:
    if _usage is None:
        return
    try:
        _usage.search(name=loop, actor="cos", provider="tavily",
                      latency_ms=(time.monotonic() - started) * 1000, error=error)
    except Exception:
        pass
