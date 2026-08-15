"""LiteLLM callback that emits sanitized, correlation-safe routing receipts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import re
import urllib.request

from litellm.integrations.custom_logger import CustomLogger


_RECEIPT_PATTERN = re.compile(
    r"minimoi-routing-receipt:([0-9a-fA-F-]{36});"
    r"logical-model:([a-z0-9._-]+)"
)


def _model_dump(value) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _message_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


def _receipt_identity(kwargs: dict) -> tuple[str, str] | None:
    candidates = [kwargs.get("messages")]
    litellm_params = kwargs.get("litellm_params") or {}
    proxy_request = litellm_params.get("proxy_server_request") or {}
    candidates.append((proxy_request.get("body") or {}).get("messages"))
    for messages in candidates:
        if not isinstance(messages, list):
            continue
        for message in messages:
            match = _RECEIPT_PATTERN.search(_message_text(message))
            if match:
                return match.group(1).lower(), match.group(2)
    return None


def _provider_and_model(kwargs: dict) -> tuple[str, str]:
    served_model = str(kwargs.get("model") or "").strip()
    litellm_params = kwargs.get("litellm_params") or {}
    provider = str(litellm_params.get("custom_llm_provider") or "").strip()
    if not provider and "/" in served_model:
        provider = served_model.split("/", 1)[0]
    return provider or "unknown", served_model or "unknown"


def build_receipt(kwargs: dict, response_obj, start_time, end_time) -> dict | None:
    """Build a strict receipt without retaining messages or response content."""
    identity = _receipt_identity(kwargs)
    if identity is None:
        return None
    receipt_id, logical_model = identity
    litellm_params = kwargs.get("litellm_params") or {}
    metadata = litellm_params.get("metadata") or {}
    model_info = litellm_params.get("model_info") or {}
    provider, served_model = _provider_and_model(kwargs)
    response = _model_dump(response_obj)
    usage = _model_dump(response.get("usage") or getattr(response_obj, "usage", {}))
    latency_ms = max(0.0, (end_time - start_time).total_seconds() * 1000)
    return {
        "receipt_id": receipt_id,
        "occurred_at": end_time.astimezone(timezone.utc).isoformat(),
        "logical_model": logical_model,
        "deployment_id": (
            str(model_info.get("id")) if model_info.get("id") is not None else None
        ),
        "served_provider": provider,
        "served_model": served_model,
        "latency_ms": round(latency_ms, 3),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cost_usd": kwargs.get("response_cost"),
        "fallback_position": model_info.get("fallback_position"),
        "fallback_reason": (
            "prior route failed"
            if isinstance(model_info.get("fallback_position"), int)
            and model_info["fallback_position"] > 0
            else None
        ),
    }


class MinimoIRoutingReceiptCallback(CustomLogger):
    """Send one authenticated receipt after the serving route succeeds."""

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        receipt = build_receipt(kwargs, response_obj, start_time, end_time)
        endpoint = os.environ.get("MINIMOI_RECEIPT_ENDPOINT", "")
        token = os.environ.get("MINIMOI_RECEIPT_KEY", "")
        if receipt is None or not endpoint or not token:
            return

        def _post() -> None:
            body = json.dumps(receipt, separators=(",", ":")).encode("utf-8")
            request = urllib.request.Request(
                endpoint,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read(256)

        await asyncio.to_thread(_post)


receipt_callback = MinimoIRoutingReceiptCallback()
