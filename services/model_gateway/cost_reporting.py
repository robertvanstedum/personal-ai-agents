"""Aggregate sanitized LiteLLM receipts into reusable cost checkpoints.

This module reads operational receipts only. It never receives prompts,
responses, credentials, identity files, or domain memory. Both the CLI cost
report and COS consume this one aggregation contract.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


@dataclass(frozen=True)
class CostPolicy:
    """Human-controlled warning thresholds; ``None`` disables spend warnings."""

    daily_warn_usd: float | None = None
    monthly_warn_usd: float | None = None
    fallback_rate_warn: float | None = 0.25
    minimum_requests_for_fallback_rate: int = 10
    unpriced_request_warn: int | None = 1


def _optional_nonnegative_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be a non-negative number or null")
    return float(value)


def load_cost_policy(path: Path) -> CostPolicy:
    """Load and validate the committed, non-secret cost policy."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("model gateway cost policy must be an object")
    allowed = {
        "daily_warn_usd",
        "monthly_warn_usd",
        "fallback_rate_warn",
        "minimum_requests_for_fallback_rate",
        "unpriced_request_warn",
    }
    if set(raw) - allowed:
        raise ValueError("model gateway cost policy contains unknown fields")

    fallback_rate = _optional_nonnegative_number(
        raw.get("fallback_rate_warn"), "fallback_rate_warn"
    )
    if fallback_rate is not None and fallback_rate > 1:
        raise ValueError("fallback_rate_warn must not exceed 1")
    minimum_requests = raw.get("minimum_requests_for_fallback_rate", 10)
    unpriced_warn = raw.get("unpriced_request_warn", 1)
    for value, name in (
        (minimum_requests, "minimum_requests_for_fallback_rate"),
        (unpriced_warn, "unpriced_request_warn"),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative integer or null")

    return CostPolicy(
        daily_warn_usd=_optional_nonnegative_number(
            raw.get("daily_warn_usd"), "daily_warn_usd"
        ),
        monthly_warn_usd=_optional_nonnegative_number(
            raw.get("monthly_warn_usd"), "monthly_warn_usd"
        ),
        fallback_rate_warn=fallback_rate,
        minimum_requests_for_fallback_rate=minimum_requests,
        unpriced_request_warn=unpriced_warn,
    )


def load_receipts(path: Path) -> tuple[list[dict], int]:
    """Load JSONL receipts, returning valid objects and a corruption count."""
    if not path.exists():
        return [], 0
    receipts: list[dict] = []
    invalid_lines = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                receipt = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(receipt, dict) or not isinstance(receipt.get("occurred_at"), str):
                invalid_lines += 1
                continue
            receipts.append(receipt)
    return receipts, invalid_lines


def _period_summary(receipts: list[dict], date_prefix: str) -> dict:
    selected = [
        receipt
        for receipt in receipts
        if str(receipt.get("occurred_at", "")).startswith(date_prefix)
    ]
    by_provider: dict[str, dict] = defaultdict(
        lambda: {"requests": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
    )
    priced_requests = 0
    total_cost = 0.0
    input_tokens = output_tokens = total_tokens = 0
    fallback_count = local_requests = 0
    latency_values: list[float] = []

    for receipt in selected:
        provider = str(receipt.get("served_provider") or "unknown")
        provider_totals = by_provider[provider]
        provider_totals["requests"] += 1

        cost = receipt.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            priced_requests += 1
            total_cost += float(cost)
            provider_totals["cost_usd"] += float(cost)
        for field in ("input_tokens", "output_tokens"):
            value = receipt.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                provider_totals[field] += value
        input_value = receipt.get("input_tokens")
        output_value = receipt.get("output_tokens")
        total_value = receipt.get("total_tokens")
        input_tokens += input_value if isinstance(input_value, int) and not isinstance(input_value, bool) else 0
        output_tokens += output_value if isinstance(output_value, int) and not isinstance(output_value, bool) else 0
        total_tokens += total_value if isinstance(total_value, int) and not isinstance(total_value, bool) else 0

        position = receipt.get("fallback_position")
        if isinstance(position, int) and not isinstance(position, bool) and position > 0:
            fallback_count += 1
        if provider.casefold() in {"ollama", "ollama_chat"}:
            local_requests += 1
        latency = receipt.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latency_values.append(float(latency))

    request_count = len(selected)
    return {
        "requests": request_count,
        "priced_requests": priced_requests,
        "unpriced_requests": request_count - priced_requests,
        "cost_usd": round(total_cost, 8),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / request_count, 4) if request_count else 0.0,
        "local_requests": local_requests,
        "average_latency_ms": (
            round(sum(latency_values) / len(latency_values), 3)
            if latency_values
            else None
        ),
        "by_provider": {
            provider: {
                **totals,
                "cost_usd": round(totals["cost_usd"], 8),
            }
            for provider, totals in sorted(by_provider.items())
        },
    }


def build_cost_checkpoint(
    receipts: list[dict],
    policy: CostPolicy,
    *,
    now: datetime | None = None,
    invalid_lines: int = 0,
) -> dict:
    """Return today's/month's costs and deterministic warning conditions."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("cost checkpoint now must be timezone-aware")
    today = current.astimezone(timezone.utc).strftime("%Y-%m-%d")
    month = today[:7]
    daily = _period_summary(receipts, today)
    monthly = _period_summary(receipts, month)
    alerts: list[dict] = []

    if policy.daily_warn_usd is not None and daily["cost_usd"] >= policy.daily_warn_usd:
        alerts.append({"type": "daily_spend", "actual": daily["cost_usd"], "threshold": policy.daily_warn_usd})
    if policy.monthly_warn_usd is not None and monthly["cost_usd"] >= policy.monthly_warn_usd:
        alerts.append({"type": "monthly_spend", "actual": monthly["cost_usd"], "threshold": policy.monthly_warn_usd})
    if (
        policy.fallback_rate_warn is not None
        and daily["requests"] >= policy.minimum_requests_for_fallback_rate
        and daily["fallback_rate"] >= policy.fallback_rate_warn
    ):
        alerts.append({"type": "fallback_rate", "actual": daily["fallback_rate"], "threshold": policy.fallback_rate_warn})
    if (
        policy.unpriced_request_warn is not None
        and daily["unpriced_requests"] >= policy.unpriced_request_warn
    ):
        alerts.append({"type": "unpriced_requests", "actual": daily["unpriced_requests"], "threshold": policy.unpriced_request_warn})
    if invalid_lines:
        alerts.append({"type": "invalid_receipt_lines", "actual": invalid_lines, "threshold": 0})

    return {
        "generated_at": current.astimezone(timezone.utc).isoformat(),
        "currency": "USD",
        "daily": daily,
        "monthly": monthly,
        "alerts": alerts,
        "invalid_receipt_lines": invalid_lines,
        "budget_thresholds_configured": (
            policy.daily_warn_usd is not None or policy.monthly_warn_usd is not None
        ),
        "limitations": [
            "Successful serving receipts only; failed-attempt cost is not yet captured.",
            "Local model API cost is zero but host compute cost is not estimated.",
        ],
    }


def checkpoint_from_files(
    receipt_path: Path,
    policy_path: Path,
    *,
    now: datetime | None = None,
) -> dict:
    """Build a checkpoint from the durable receipt log and committed policy."""
    receipts, invalid_lines = load_receipts(receipt_path)
    policy = load_cost_policy(policy_path)
    return build_cost_checkpoint(
        receipts,
        policy,
        now=now,
        invalid_lines=invalid_lines,
    )
