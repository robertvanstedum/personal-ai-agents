"""Contract tests for reusable gateway cost aggregation."""

from datetime import datetime, timezone
import json

import pytest

from services.model_gateway.cost_reporting import (
    CostPolicy,
    build_cost_checkpoint,
    checkpoint_from_files,
    load_cost_policy,
    load_receipts,
)


NOW = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)


def _receipt(provider="xai", position=0, cost=0.01):
    return {
        "receipt_id": "447e1bb2-7d79-44d7-966c-d7fcb3a92822",
        "occurred_at": "2026-08-15T19:00:00+00:00",
        "logical_model": "minimoi-cos-agent",
        "served_provider": provider,
        "served_model": "model",
        "latency_ms": 200,
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cost_usd": cost,
        "fallback_position": position,
        "fallback_reason": "prior route failed" if position else None,
    }


def test_checkpoint_aggregates_actual_routes_costs_tokens_and_fallbacks():
    checkpoint = build_cost_checkpoint(
        [_receipt(), _receipt("anthropic", 2, 0.02), _receipt("ollama_chat", 3, 0.0)],
        CostPolicy(fallback_rate_warn=0.5, minimum_requests_for_fallback_rate=2),
        now=NOW,
    )

    assert checkpoint["daily"]["requests"] == 3
    assert checkpoint["daily"]["cost_usd"] == pytest.approx(0.03)
    assert checkpoint["daily"]["total_tokens"] == 45
    assert checkpoint["daily"]["fallback_count"] == 2
    assert checkpoint["daily"]["local_requests"] == 1
    assert checkpoint["daily"]["by_provider"]["anthropic"]["requests"] == 1
    assert checkpoint["alerts"] == [{
        "type": "fallback_rate",
        "actual": 0.6667,
        "threshold": 0.5,
    }]


def test_unpriced_and_corrupt_receipts_are_visible_instead_of_hidden(tmp_path):
    receipt_path = tmp_path / "receipts.jsonl"
    receipt_path.write_text(
        json.dumps(_receipt(cost=None)) + "\nnot-json\n" + json.dumps({"wrong": True}) + "\n"
    )
    receipts, invalid_lines = load_receipts(receipt_path)
    checkpoint = build_cost_checkpoint(
        receipts,
        CostPolicy(unpriced_request_warn=1),
        now=NOW,
        invalid_lines=invalid_lines,
    )

    assert checkpoint["daily"]["unpriced_requests"] == 1
    assert invalid_lines == 2
    assert {alert["type"] for alert in checkpoint["alerts"]} == {
        "unpriced_requests",
        "invalid_receipt_lines",
    }


def test_budget_warnings_are_disabled_until_robert_sets_thresholds():
    checkpoint = build_cost_checkpoint(
        [_receipt(cost=999.0)],
        CostPolicy(daily_warn_usd=None, monthly_warn_usd=None),
        now=NOW,
    )

    assert checkpoint["budget_thresholds_configured"] is False
    assert not {"daily_spend", "monthly_spend"} & {
        alert["type"] for alert in checkpoint["alerts"]
    }


def test_policy_rejects_unknown_or_invalid_thresholds(tmp_path):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"fallback_rate_warn": 1.5}))
    with pytest.raises(ValueError, match="must not exceed 1"):
        load_cost_policy(policy_path)

    policy_path.write_text(json.dumps({"surprise": 1}))
    with pytest.raises(ValueError, match="unknown fields"):
        load_cost_policy(policy_path)


def test_checkpoint_from_files_uses_committed_policy_shape(tmp_path):
    receipts = tmp_path / "receipts.jsonl"
    receipts.write_text(json.dumps(_receipt()) + "\n")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "daily_warn_usd": 0.005,
        "monthly_warn_usd": None,
        "fallback_rate_warn": None,
        "minimum_requests_for_fallback_rate": 10,
        "unpriced_request_warn": 1,
    }))

    checkpoint = checkpoint_from_files(receipts, policy, now=NOW)

    assert checkpoint["alerts"] == [{
        "type": "daily_spend",
        "actual": 0.01,
        "threshold": 0.005,
    }]
