"""Authenticated, sanitized routing receipts delivered by the model gateway."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID


ALLOWED_RECEIPT_KEYS = frozenset({
    "receipt_id",
    "occurred_at",
    "logical_model",
    "deployment_id",
    "served_provider",
    "served_model",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "fallback_position",
    "fallback_reason",
})


def validate_receipt(payload: object) -> dict:
    """Return a normalized receipt, rejecting prompts and unknown fields."""
    if not isinstance(payload, dict):
        raise ValueError("routing receipt must be an object")
    unknown = set(payload) - ALLOWED_RECEIPT_KEYS
    if unknown:
        raise ValueError("routing receipt contains unknown fields")

    receipt_id = str(UUID(str(payload.get("receipt_id", ""))))
    required_strings = ("occurred_at", "logical_model", "served_provider", "served_model")
    normalized = {"receipt_id": receipt_id}
    for name in required_strings:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"routing receipt {name} is required")
        normalized[name] = value.strip()

    deployment_id = payload.get("deployment_id")
    normalized["deployment_id"] = (
        deployment_id.strip() if isinstance(deployment_id, str) and deployment_id.strip() else None
    )
    for name in ("latency_ms", "input_tokens", "output_tokens", "total_tokens", "cost_usd"):
        value = payload.get(name)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"routing receipt {name} must be numeric")
        normalized[name] = value
    fallback_position = payload.get("fallback_position")
    if fallback_position is not None and (
        not isinstance(fallback_position, int) or isinstance(fallback_position, bool)
    ):
        raise ValueError("routing receipt fallback_position must be an integer")
    normalized["fallback_position"] = fallback_position
    fallback_reason = payload.get("fallback_reason")
    if fallback_reason is not None and not isinstance(fallback_reason, str):
        raise ValueError("routing receipt fallback_reason must be text")
    normalized["fallback_reason"] = fallback_reason
    return normalized


@dataclass
class RoutingReceiptStore:
    """Small synchronized cache plus append-only sanitized cost evidence."""

    path: Path

    def __post_init__(self) -> None:
        self._condition = threading.Condition()
        self._receipts: dict[str, dict] = {}

    def record(self, payload: object) -> dict:
        receipt = validate_receipt(payload)
        line = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._condition:
            # A receipt contains no prompts or responses, but cost and routing
            # evidence is still private operational data. Create it owner-only.
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, line.encode("utf-8"))
            finally:
                os.close(descriptor)
            self._receipts[receipt["receipt_id"]] = receipt
            self._condition.notify_all()
        return receipt

    def wait_for(self, receipt_id: str, timeout_seconds: float = 1.0) -> dict | None:
        """Wait briefly for LiteLLM's asynchronous success callback."""
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while receipt_id not in self._receipts:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return dict(self._receipts[receipt_id])
