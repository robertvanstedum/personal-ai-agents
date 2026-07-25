"""
utils/cost_log.py — shared per-domain AI cost/usage logging.

Approved design: docs/specs/spec_ai_cost_tracking_2026-07-19.md (token-based
path). Extended per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md
Section 12 to support duration/audio-metered calls (realtime voice) while
preserving the original token-based contract for future callers.

One shared reporting path, one per-domain persisted log file -- not a
voice-only logger. Per-domain JSON files avoid cross-container write
contention and match the mount pattern each domain already has (see the
approved spec's "Design" section for why a single shared file was rejected).

Scope note: only the realtime-voice call sites built alongside this module
use it today. Migrating Curator/CoS/legacy German-Portuguese text-chat call
sites onto this module is the approved spec's own Phase 1/2 and is
explicitly out of scope for the realtime-voice build (Robert, 2026-07-24).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_LOG_PATHS = {
    "curator": _REPO_ROOT / "data" / "curator" / "costs.json",
    "german": _REPO_ROOT / "domains" / "german" / "data" / "costs.json",
    "portuguese": _REPO_ROOT / "domains" / "portuguese" / "data" / "costs.json",
    "cos": _REPO_ROOT / "domains" / "cos" / "data" / "costs.json",
}

# Token rates are $ per token (not per 1K) -- multiply directly by token count.
# Per-minute rates are $ per minute of session duration, for duration-metered
# calls (Whisper, realtime voice) where no per-token cost applies.
#
# xAI's Grok Voice Agent API rate ($3.00/hour = $0.05/minute) is verified
# directly against https://x.ai/api/voice (see
# docs/specs/spec_voice_realtime_architecture_2026-07-24.md Section, and
# _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 12).
#
# OpenAI's gpt-realtime-2 has no verified per-minute rate here -- callers for
# that model MUST pass cost_usd from the provider's own usage/cost report at
# session end. There is deliberately no fallback estimate for it: an unread
# guess is worse than a required, honest "the caller must supply this."
PRICING_VERSION = "2026-07-24"
PRICING = {
    "whisper-1": {"per_minute": 0.006},
    "grok-voice-agent": {"per_minute": 0.05},
}


def resolve_log_path(domain: str) -> Path:
    try:
        return _LOG_PATHS[domain]
    except KeyError:
        raise KeyError(
            f"Unknown cost-log domain: {domain!r}. Known domains: "
            f"{sorted(_LOG_PATHS)}"
        )


def _compute_token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        rates = PRICING[model]
    except KeyError:
        raise KeyError(
            f"No PRICING entry for model {model!r}. Add one to "
            f"utils/cost_log.py:PRICING, or pass cost_usd explicitly."
        )
    if "input" not in rates or "output" not in rates:
        raise KeyError(
            f"PRICING entry for {model!r} is not token-based "
            f"(no 'input'/'output' rate) -- pass duration_seconds and/or "
            f"cost_usd instead."
        )
    return input_tokens * rates["input"] + output_tokens * rates["output"]


def _compute_duration_cost(model: str, duration_seconds: float) -> float:
    try:
        rates = PRICING[model]
    except KeyError:
        raise ValueError(
            f"No PRICING entry for model {model!r} and no cost_usd was "
            f"given -- either add a 'per_minute' rate to "
            f"utils/cost_log.py:PRICING or pass cost_usd explicitly "
            f"(required for models with provider-reported usage only, "
            f"e.g. gpt-realtime-2)."
        )
    if "per_minute" not in rates:
        raise ValueError(
            f"PRICING entry for {model!r} has no 'per_minute' rate -- "
            f"pass cost_usd explicitly."
        )
    return (duration_seconds / 60.0) * rates["per_minute"]


def log_ai_cost(
    domain: str,
    model: str,
    use_type: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    *,
    duration_seconds: float | None = None,
    cost_usd: float | None = None,
    provider_usage: dict | None = None,
    session_id: str | None = None,
) -> dict:
    """Append one cost/usage record to `domain`'s per-domain cost log.

    Token-based callers (original approved contract): pass input_tokens/
    output_tokens; cost_usd is computed from PRICING and marked "estimated".

    Duration/audio-metered callers (realtime voice, Whisper): pass
    duration_seconds. If cost_usd is also given (provider reported it
    directly, e.g. a realtime session's usage event), that value is used
    as-is and marked "provider_reported" -- never overwritten by a
    wall-clock estimate. If cost_usd is omitted, a PRICING per-minute rate
    is used and the record is marked "estimated"; models with no verified
    per-minute rate (e.g. gpt-realtime-2) require an explicit cost_usd and
    raise ValueError otherwise.

    Returns the record that was written, for callers/tests that want to
    inspect it without re-reading the log file.
    """
    log_path = resolve_log_path(domain)

    if cost_usd is not None:
        resolved_cost = cost_usd
        cost_basis = "provider_reported"
        pricing_version = None
    elif duration_seconds is not None:
        resolved_cost = _compute_duration_cost(model, duration_seconds)
        cost_basis = "estimated"
        pricing_version = PRICING_VERSION
    elif input_tokens or output_tokens:
        resolved_cost = _compute_token_cost(model, input_tokens, output_tokens)
        cost_basis = "estimated"
        pricing_version = PRICING_VERSION
    else:
        raise ValueError(
            "log_ai_cost requires input_tokens/output_tokens, "
            "duration_seconds, or cost_usd -- none were given."
        )

    now = datetime.now(timezone.utc)
    record = {
        "domain": domain,
        "date": now.strftime("%Y-%m-%d"),
        "timestamp": now.isoformat(),
        "model": model,
        "use_type": use_type,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(resolved_cost, 6),
        "cost_basis": cost_basis,
    }
    if pricing_version is not None:
        record["pricing_table_version"] = pricing_version
    if duration_seconds is not None:
        record["duration_seconds"] = duration_seconds
    if provider_usage is not None:
        record["provider_usage"] = provider_usage
    if session_id is not None:
        record["session_id"] = session_id

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(log_path.read_text()) if log_path.exists() else {"runs": []}
        data["runs"].append(record)
        log_path.write_text(json.dumps(data, indent=2))
    except OSError as e:
        print(f"[cost_log] Warning: could not write cost record for {domain}: {e}")

    return record
