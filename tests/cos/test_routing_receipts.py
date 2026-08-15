"""Safety and synchronization tests for gateway routing receipts."""

import json
import stat
import threading
import time
from uuid import uuid4

import pytest

from domains.cos.routing_receipts import RoutingReceiptStore, validate_receipt


def _receipt(receipt_id=None):
    return {
        "receipt_id": receipt_id or str(uuid4()),
        "occurred_at": "2026-08-15T20:00:00+00:00",
        "logical_model": "minimoi-cos-agent",
        "deployment_id": "cos-anthropic-secondary",
        "served_provider": "anthropic",
        "served_model": "anthropic/claude-sonnet-4-6",
        "latency_ms": 123.5,
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cost_usd": 0.0001,
        "fallback_position": 2,
        "fallback_reason": "prior route failed",
    }


def test_receipt_rejects_unknown_fields_so_prompts_cannot_be_persisted():
    receipt = _receipt()
    receipt["messages"] = [{"role": "user", "content": "private"}]

    with pytest.raises(ValueError, match="unknown fields"):
        validate_receipt(receipt)


def test_store_persists_only_normalized_receipt_and_wakes_waiter(tmp_path):
    store = RoutingReceiptStore(tmp_path / "receipts.jsonl")
    receipt = _receipt()
    result = []

    waiter = threading.Thread(
        target=lambda: result.append(store.wait_for(receipt["receipt_id"], 1.0))
    )
    waiter.start()
    time.sleep(0.01)
    store.record(receipt)
    waiter.join()

    assert result == [receipt]
    assert json.loads(store.path.read_text()) == receipt
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_missing_receipt_times_out_without_inventing_metadata(tmp_path):
    store = RoutingReceiptStore(tmp_path / "receipts.jsonl")
    assert store.wait_for(str(uuid4()), timeout_seconds=0.01) is None
