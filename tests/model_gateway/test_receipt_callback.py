"""Unit tests for LiteLLM's sanitized routing receipt callback."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types


def _load_callback_module():
    """Load the callback without requiring LiteLLM in the application venv."""
    litellm = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    sys.modules.setdefault("litellm", litellm)
    sys.modules.setdefault("litellm.integrations", integrations)
    sys.modules.setdefault("litellm.integrations.custom_logger", custom_logger)

    path = Path(__file__).parents[2] / "services/model_gateway/receipt_callback.py"
    spec = importlib.util.spec_from_file_location("receipt_callback_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_receipt_correlates_actual_fallback_without_retaining_content():
    callback = _load_callback_module()
    receipt_id = "447e1bb2-7d79-44d7-966c-d7fcb3a92822"
    private_text = "private user content must never enter the receipt"
    start = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
    end = start + timedelta(milliseconds=250)
    kwargs = {
        "model": "anthropic/claude-sonnet-4-6",
        "messages": [
            {
                "role": "system",
                "content": (
                    f"<!-- minimoi-routing-receipt:{receipt_id};"
                    "logical-model:minimoi-cos-agent -->"
                ),
            },
            {"role": "user", "content": private_text},
        ],
        "litellm_params": {
            "custom_llm_provider": "anthropic",
            "model_info": {
                "id": "cos-anthropic-secondary",
                "fallback_position": 2,
            },
        },
        "response_cost": 0.0001,
    }
    response = {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        },
        "choices": [{"message": {"content": "private response"}}],
    }

    receipt = callback.build_receipt(kwargs, response, start, end)

    assert receipt == {
        "receipt_id": receipt_id,
        "occurred_at": end.isoformat(),
        "logical_model": "minimoi-cos-agent",
        "deployment_id": "cos-anthropic-secondary",
        "served_provider": "anthropic",
        "served_model": "anthropic/claude-sonnet-4-6",
        "latency_ms": 250.0,
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cost_usd": 0.0001,
        "fallback_position": 2,
        "fallback_reason": "prior route failed",
    }
    assert private_text not in repr(receipt)
    assert "private response" not in repr(receipt)


def test_build_receipt_ignores_uncorrelated_calls():
    callback = _load_callback_module()
    moment = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
    kwargs = {
        "model": "xai/grok-4.3",
        "messages": [{"role": "user", "content": "ordinary call"}],
    }

    assert callback.build_receipt(kwargs, {}, moment, moment) is None


def test_build_receipt_accounts_for_bounded_search_without_retaining_content():
    callback = _load_callback_module()
    private_query = "private query must never enter the receipt"
    private_answer = "private search answer must never enter the receipt"
    start = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
    end = start + timedelta(milliseconds=400)
    kwargs = {
        "model": "xai/grok-4.3",
        "litellm_params": {
            "model_info": {
                "id": "cos-xai-bounded-web-search",
                "base_model": "xai/grok-4.3",
            },
            "proxy_server_request": {
                "body": {"input": private_query},
            },
        },
        "response_cost": 0.0025,
    }
    response = {
        "id": "resp_search_123",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        },
        "output": [{"content": [{"type": "output_text", "text": private_answer}]}],
    }

    receipt = callback.build_receipt(kwargs, response, start, end)

    assert receipt is not None
    assert receipt["logical_model"] == "minimoi-cos-web-search"
    assert receipt["deployment_id"] == "cos-xai-bounded-web-search"
    assert receipt["served_provider"] == "xai"
    assert receipt["served_model"] == "xai/grok-4.3"
    assert receipt["input_tokens"] == 120
    assert receipt["output_tokens"] == 30
    assert receipt["total_tokens"] == 150
    assert receipt["cost_usd"] == 0.0025
    assert private_query not in repr(receipt)
    assert private_answer not in repr(receipt)

    duplicate = callback.build_receipt(kwargs, response, start, end)
    assert duplicate is not None
    assert duplicate["receipt_id"] == receipt["receipt_id"]
