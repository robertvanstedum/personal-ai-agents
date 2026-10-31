"""Canonical JSON and its digest (contracts/cos/v1/README.md, "Canonical digest").

Sorted keys, separators (",", ":"), UTF-8, ensure_ascii False. The same rule is used for command payloads, stored
records and activity records, so a digest computed anywhere in the platform compares equal.
"""
from __future__ import annotations

import hashlib
import json


def canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(obj: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(obj)).hexdigest()
