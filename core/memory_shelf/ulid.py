"""ULIDs and the planning-studio filename convention ``<slug>--<short-ulid>.md``.

26 Crockford-base32 characters: 48 bits of millisecond time, then 80 random
bits. They sort by creation time. The file suffix is the last 8 characters,
lowercased (``planning-studio/governance/artifact-retention-and-naming-practice``).
"""
from __future__ import annotations

import os
import re
import time
from typing import Callable

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def new_ulid(now_ms: int | None = None, rand: Callable[[int], bytes] = os.urandom) -> str:
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not 0 <= ms < 2 ** 48:
        raise ValueError("timestamp out of range")
    value = (ms << 80) | int.from_bytes(rand(10), "big")
    return "".join(_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def is_ulid(text: object) -> bool:
    return isinstance(text, str) and bool(_PATTERN.match(text))


def short(ulid: str) -> str:
    if not is_ulid(ulid):
        raise ValueError("not a ULID")
    return ulid[-8:].lower()


def slugify(title: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:limit].strip("-")
    return slug or "record"


def filename(title: str, ulid: str, ext: str = "md") -> str:
    return f"{slugify(title)}--{short(ulid)}.{ext.lstrip('.')}"


__all__ = ["new_ulid", "is_ulid", "short", "slugify", "filename"]
