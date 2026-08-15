"""Tests for COS's read-only scheduled model-cost checkpoint."""

import json

from domains.cos.cost_checkpoint import run_cost_checkpoint


def _files(tmp_path, *, unpriced=False):
    receipts = tmp_path / "receipts.jsonl"
    receipts.write_text(json.dumps({
        "occurred_at": "2026-08-15T19:00:00+00:00",
        "served_provider": "xai",
        "cost_usd": None if unpriced else 0.01,
        "fallback_position": 0,
    }) + "\n")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "daily_warn_usd": None,
        "monthly_warn_usd": None,
        "fallback_rate_warn": None,
        "minimum_requests_for_fallback_rate": 10,
        "unpriced_request_warn": 1,
    }))
    return receipts, policy


def test_nonproduction_checkpoint_never_sends_external_alert(tmp_path):
    receipts, policy = _files(tmp_path, unpriced=True)
    messages = []

    checkpoint = run_cost_checkpoint(
        receipts,
        policy,
        production=False,
        notify=messages.append,
    )

    assert checkpoint["alerts"][0]["type"] == "unpriced_requests"
    assert checkpoint["notification"] == "suppressed_nonproduction"
    assert messages == []


def test_production_alert_is_summary_only(tmp_path):
    receipts, policy = _files(tmp_path, unpriced=True)
    messages = []

    checkpoint = run_cost_checkpoint(
        receipts,
        policy,
        production=True,
        notify=messages.append,
    )

    assert checkpoint["notification"] == "sent"
    assert len(messages) == 1
    assert "unpriced_requests" in messages[0]
    assert "prompt" not in messages[0].casefold()
