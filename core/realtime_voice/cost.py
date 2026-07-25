"""
core/realtime_voice/cost.py — thin wrapper routing realtime voice sessions
through the shared utils/cost_log.py path.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 12:
one shared cost path, not a separate voice-only logger. This module exists
only to fix the use_type/model bookkeeping for realtime voice calls -- all
actual cost computation and persistence lives in utils/cost_log.py.
"""
from utils.cost_log import log_ai_cost

USE_TYPE = "realtime_voice"


def log_realtime_voice_cost(
    *,
    domain: str,
    provider: str,
    model: str,
    session_id: str,
    duration_seconds: float,
    provider_usage: dict | None = None,
    cost_usd: float | None = None,
) -> dict:
    return log_ai_cost(
        domain=domain,
        model=model,
        use_type=USE_TYPE,
        duration_seconds=duration_seconds,
        cost_usd=cost_usd,
        provider_usage={**(provider_usage or {}), "provider": provider},
        session_id=session_id,
    )
