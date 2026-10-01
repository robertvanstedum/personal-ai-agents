"""The direct-call helper: usage records for calls that do not go through the
model gateway (usage-record U2): Tavily searches and CoS's direct Grok backend.

Same record, same store, same guarantees as the gateway recorder: fire and
forget, never raises, never blocks; nothing is written unless the environment
has a usage store (MINIMOI_USAGE_DIR; staging today). No query, prompt,
result or key is ever recorded.

These records go to this writer's own folder, <store>/<MINIMOI_USAGE_WRITER>/
(usage_record.own_folder), never to the shared monthly file: that file is the
non-root gateway's, and a root writer creating it first would lock the gateway
out for the month.
"""
from __future__ import annotations

import logging

from services.usage import usage_record

_log = logging.getLogger("minimoi.usage")


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:              # a usage record never breaks the caller
            _log.warning("usage: direct record not made (%s)", type(exc).__name__)
            return False
    return wrapper


def _usage_numbers(response) -> tuple[int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None, None, None
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    details = get("prompt_tokens_details")
    cached = (details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", None)) \
        if details is not None else None

    def count(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    return count(get("prompt_tokens")), count(get("completion_tokens")), count(cached)


@_safe
def model_call(*, name: str, actor: str, route: str, provider: str, model: str, response=None,
               latency_ms: float | None = None, error: Exception | None = None) -> bool:
    """One direct model call. ``response`` is the SDK's answer (tokens are read
    from its ``usage``); pass ``error`` instead when the call raised. Cost is
    not priced here yet (cost_source "none"): tokens are the metric."""
    tokens_in, tokens_out, cached = _usage_numbers(response) if error is None else (None, None, None)
    code = getattr(error, "status_code", None) if error is not None else 200
    return usage_record.record(
        usage_record.own_folder(),
        emitter=f"helper:{name}", actor=actor, kind="model", route=route, provider=provider, model=model,
        status="ok" if error is None else ("refused" if code in (401, 403, 429) else "error"),
        http_status=code if isinstance(code, int) else None,
        error_class=type(error).__name__[:80] if error is not None else None,
        latency_ms=round(latency_ms, 3) if isinstance(latency_ms, (int, float)) and latency_ms >= 0 else None,
        input_tokens=tokens_in, output_tokens=tokens_out, cached_tokens=cached,
        cost_usd=None, cost_source="none")


@_safe
def search(*, name: str, actor: str, provider: str = "tavily", searches: int = 1,
           latency_ms: float | None = None, error: Exception | None = None) -> bool:
    """One search call (Tavily's free tier: no cost, counted in units)."""
    return usage_record.record(
        usage_record.own_folder(),
        emitter=f"helper:{name}", actor=actor, kind="search", route=f"{provider}:search", provider=provider,
        status="ok" if error is None else "error",
        error_class=type(error).__name__[:80] if error is not None else None,
        latency_ms=round(latency_ms, 3) if isinstance(latency_ms, (int, float)) and latency_ms >= 0 else None,
        units={"searches": int(searches)}, cost_usd=None, cost_source="none")


__all__ = ["model_call", "search"]
