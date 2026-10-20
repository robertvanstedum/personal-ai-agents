"""The standing ingress acknowledgement (harness v0.3.1 §14.1), checked exactly.

``{schema: "mc-standing-ack/1", event_id, state: accepted|already_applied|deferred|refused, ...}``

Only an ack with the right schema, *this* event's ID and a known state counts. ``committed``
(accepted / already_applied) is the only state that completes a delivery; ``deferred`` stays
pending, ``refused`` is a visible failure, anything else is not an acknowledgement at all.
"""
from __future__ import annotations

SCHEMA = "mc-standing-ack/1"
STATES = ("accepted", "already_applied", "deferred", "refused")
COMMITTED = ("accepted", "already_applied")


def check(ack, event_id: str) -> dict | None:
    """The ack if it is exactly an acknowledgement of ``event_id``, else None."""
    if not isinstance(ack, dict) or ack.get("schema") != SCHEMA or ack.get("event_id") != event_id \
            or ack.get("state") not in STATES:
        return None
    return ack


def committed(ack, event_id: str) -> bool:
    valid = check(ack, event_id)
    return valid is not None and valid["state"] in COMMITTED
