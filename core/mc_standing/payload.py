"""Read-only resolver for the ingress service (Codex): a brief's text bytes by payload_ref.

The ingress verifies sha256(text) against the event's payload_digest. Only names the runner
itself writes are accepted (``<cycle key>.brief``); no paths, URLs or other spool areas.
"""
from __future__ import annotations

import re
from pathlib import Path

from .spool import Spool

_REF = re.compile(r"^(?:dev|prod)\.\d{4}-\d{2}-\d{2}\.[A-Za-z0-9_-]{1,40}\.morning\.brief$")


def load_brief_text(state_root: str | Path, payload_ref: str) -> bytes:
    if not isinstance(payload_ref, str) or not _REF.match(payload_ref):
        raise ValueError("payload_ref_invalid")
    record = Spool(state_root).read("delivery", payload_ref)
    if not record or not isinstance(record.get("text"), str):
        raise LookupError("payload_not_found")
    return record["text"].encode("utf-8")
