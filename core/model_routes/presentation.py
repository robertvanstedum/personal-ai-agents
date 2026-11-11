"""Presentation candidates, verified against canonical saved answers, never dispatch receipts.

Hosts call under their conversation metadata lock. A prepared candidate alone proves
nothing: a failed/save-failed turn cannot suppress a notice. No message content is
copied here except the platform-generated route notice. Bounded retention may repeat
an old notice, never invent delivery. The host supplies an owner-scoped answer lookup.
"""
from __future__ import annotations

import hashlib
import json

LIMIT = 128


def prepare(rows, reference, route, line, answer):
    """Return (replacement candidates, show notice). Call before output/save.

    The current turn first records its ordinary route, then replaces that candidate
    if failover occurs. Latest *saved* turn determines the semantic state; returning
    to the ordinary route therefore permits a later fallback notice again.
    """
    identity = {k: route.get(k) for k in (
        "route_id", "provider", "model", "reasoning_effort", "billing_mode",
        "switched_from", "switch_signal")}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    previous = [r for r in rows if r.get("reference") != reference]
    show, presented = bool(line), reference if line else None
    for row in reversed(previous):
        saved = answer(row["reference"])
        if saved is None:
            continue
        if line and row.get("key") == key and row.get("line") == line:
            prior = answer(row.get("presented") or row["reference"])
            # Exact full platform line, not a code/substring or model paraphrase.
            if prior is not None and line in prior.splitlines():
                show, presented = False, row.get("presented") or row["reference"]
        break
    candidate = {"reference": reference, "key": key, "line": line, "presented": presented}
    return (previous + [candidate])[-LIMIT:], show
