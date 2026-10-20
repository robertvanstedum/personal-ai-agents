"""Opt-in identity marker for an operator-isolated dev proving portal.

This binds the test client to one installation; it does not certify data or tool
isolation. The operator must verify those separately before enabling this flag.
Production origins can never expose a proving marker.
"""
import re

def marker(base_url, environ):
    enabled = environ.get("MINIMOI_PROVING_ENABLED") == "1"
    if not enabled:
        return None
    if base_url.rstrip("/") not in ("https://dev.minimoi.ai", "http://127.0.0.1:15001"):
        raise RuntimeError("proving marker requires an approved dev origin")
    instance_id = environ.get("MINIMOI_PROVING_INSTANCE_ID", "")
    if not re.fullmatch(r"[0-9a-f]{32}", instance_id):
        raise RuntimeError("proving instance id must be 32 lowercase hex characters")
    return {"environment": "dev", "instance_id": instance_id}
