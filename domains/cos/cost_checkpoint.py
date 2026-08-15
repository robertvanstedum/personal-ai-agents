"""Chief of Staff checkpoint over shared model-gateway cost evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from services.model_gateway.cost_reporting import checkpoint_from_files


def format_checkpoint_alert(checkpoint: dict) -> str:
    """Create a concise operational message without prompt or response data."""
    daily = checkpoint["daily"]
    monthly = checkpoint["monthly"]
    alert_types = ", ".join(alert["type"] for alert in checkpoint["alerts"])
    return (
        "💰 <b>Model gateway cost checkpoint</b>\n"
        f"Today: ${daily['cost_usd']:.4f} across {daily['requests']} request(s); "
        f"{daily['fallback_count']} fallback(s)\n"
        f"Month: ${monthly['cost_usd']:.4f} across {monthly['requests']} request(s)\n"
        f"Attention: {alert_types}"
    )


def run_cost_checkpoint(
    receipt_path: Path,
    policy_path: Path,
    *,
    production: bool,
    notify: Callable[[str], None] | None = None,
) -> dict:
    """Build the checkpoint and notify only for alerts on production."""
    checkpoint = checkpoint_from_files(receipt_path, policy_path)
    checkpoint["notification"] = "not_needed"
    if checkpoint["alerts"]:
        if production and notify is not None:
            notify(format_checkpoint_alert(checkpoint))
            checkpoint["notification"] = "sent"
        else:
            checkpoint["notification"] = "suppressed_nonproduction"
    return checkpoint
