"""Strict JSON for journal lines (v0.6 section 5): no duplicate keys, no NaN or Infinity, strict UTF-8, no lone surrogates.

``json.loads`` accepts duplicate keys (last wins), ``NaN``/``Infinity`` and ``1e999``; each of those would let two
readers disagree about one line, so each is refused here with a fixed reason.
"""
from __future__ import annotations

import json
import math
from typing import Any


class StrictJSONError(ValueError):
    """The text is not acceptable journal JSON. ``reason`` is a fixed code; the text is never quoted."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise StrictJSONError("duplicate_key")
        out[key] = value
    return out


def _constant(_name: str):
    raise StrictJSONError("non_finite_number")


def _float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise StrictJSONError("non_finite_number")
    return value


def loads(raw: bytes | str) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            raise StrictJSONError("invalid_utf8") from None
    else:
        text = raw
    if text.startswith("﻿"):
        raise StrictJSONError("byte_order_mark")
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except StrictJSONError:
        raise
    except (ValueError, RecursionError):
        raise StrictJSONError("invalid_json") from None


def dumps(obj: Any) -> str:
    """Canonical text: sorted keys, compact separators, ``ensure_ascii=False``, ``allow_nan=False``."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise StrictJSONError("not_serialisable") from None


def canonical_bytes(obj: Any) -> bytes:
    try:
        return dumps(obj).encode("utf-8")
    except UnicodeEncodeError:                       # a lone surrogate
        raise StrictJSONError("invalid_utf8") from None


__all__ = ["StrictJSONError", "loads", "dumps", "canonical_bytes"]
