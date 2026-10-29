"""Malformed receipt measurements must not erase otherwise valid cost evidence."""

from datetime import datetime, timezone
import json

import pytest

from domains.cos.routing_receipts import RoutingReceiptStore, validate_receipt
from services.model_gateway.cost_reporting import CostPolicy, build_cost_checkpoint, checkpoint_from_files

NOW = datetime(2026, 8, 15, 20, tzinfo=timezone.utc)

def receipt(**updates):
    value = {
        "receipt_id": "447e1bb2-7d79-44d7-966c-d7fcb3a92822",
        "occurred_at": NOW.isoformat(), "logical_model": "synthetic",
        "served_provider": "synthetic", "served_model": "synthetic",
        "cost_usd": 2.0, "latency_ms": 200, "input_tokens": 10,
        "output_tokens": 5, "total_tokens": 15, "fallback_position": 0,
    }
    return {**value, **updates}

@pytest.mark.parametrize("field", ["cost_usd", "latency_ms", "input_tokens", "output_tokens", "total_tokens", "fallback_position"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -1, True, "1"])
def test_invalid_new_measurement_is_rejected_before_store_creation(tmp_path, field, bad):
    store = RoutingReceiptStore(tmp_path / "new" / "receipts.jsonl")
    with pytest.raises(ValueError):
        store.record(receipt(**{field: bad}))
    assert not store.path.parent.exists()

@pytest.mark.parametrize("field", ["input_tokens", "output_tokens", "total_tokens", "fallback_position"])
def test_counts_are_whole_numbers(field):
    with pytest.raises(ValueError):
        validate_receipt(receipt(**{field: 1.5}))

@pytest.mark.parametrize("field", ["cost_usd", "latency_ms"])
def test_unrepresentable_numeric_measurement_is_rejected(field):
    with pytest.raises(ValueError):
        validate_receipt(receipt(**{field: 10**400}))

@pytest.mark.parametrize("value", [None, 0])
def test_unknown_and_zero_measurements_remain_supported(value):
    fields = ["cost_usd", "latency_ms", "input_tokens", "output_tokens", "total_tokens", "fallback_position"]
    result = validate_receipt(receipt(**dict.fromkeys(fields, value)))
    assert all(result[field] == value for field in fields)

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -1, 10**400, True, "1"])
def test_bad_historical_cost_is_unpriced_and_cannot_hide_valid_spend(tmp_path, bad):
    path = tmp_path / "receipts.jsonl"
    original = "\n".join(json.dumps(r) for r in [receipt(), receipt(cost_usd=bad)])
    path.write_text(original)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"daily_warn_usd": 1, "unpriced_request_warn": 1}))
    result = checkpoint_from_files(path, policy, now=NOW)
    for period in ["daily", "monthly"]:
        assert result[period]["requests"] == 2
        assert result[period]["priced_requests"] == 1
        assert result[period]["unpriced_requests"] == 1
        assert result[period]["cost_usd"] == 2.0
        assert result[period]["by_provider"]["synthetic"]["cost_usd"] == 2.0
    assert {a["type"] for a in result["alerts"]} >= {"daily_spend", "unpriced_requests"}
    json.dumps(result, allow_nan=False)
    assert path.read_text() == original

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 10**400, True, "1"])
def test_bad_historical_latency_does_not_poison_valid_average(bad):
    result = build_cost_checkpoint([receipt(), receipt(latency_ms=bad)], CostPolicy(), now=NOW)
    assert result["daily"]["average_latency_ms"] == 200
    json.dumps(result, allow_nan=False)

@pytest.mark.parametrize("bad", [-1, 1.5, True, "1", float("nan")])
def test_bad_historical_token_counts_do_not_reduce_valid_totals(bad):
    result = build_cost_checkpoint([receipt(), receipt(input_tokens=bad, output_tokens=bad, total_tokens=bad)], CostPolicy(), now=NOW)
    assert result["daily"]["input_tokens"] == 10
    assert result["daily"]["output_tokens"] == 5
    assert result["daily"]["total_tokens"] == 15
    assert result["daily"]["by_provider"]["synthetic"]["input_tokens"] == 10
