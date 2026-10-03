"""What is never taken (amendment §2, v0.5 B4): the owner's explicit private mark.

``private_marked`` is deliberately blunt and fails closed: a file is private when
its **name** carries ``private`` as a word (``notes.private.md``, ``private-draft.txt``)
or its first non-empty line is exactly ``[private]``, ``#private`` or ``private: true``.
Anything marked is excluded whole (reason ``private``); nothing is kept "minus the
private part". Only the name and the first line are looked at.
"""
from __future__ import annotations

import re

_NAME = re.compile(r"(?:^|[^a-z0-9])private(?:[^a-z0-9]|$)", re.IGNORECASE)
_FIRST = re.compile(r"^\s*(?:\[private\]|#private|private\s*:\s*true)\s*$", re.IGNORECASE)


def private_marked(name: str, first_line: str = "") -> bool:
    return bool(_NAME.search(name)) or bool(_FIRST.match(first_line))


def first_line_of(path, limit: int = 512) -> str:
    """The first non-empty line, at most ``limit`` bytes read."""
    with open(path, "rb") as handle:
        head = handle.read(limit).decode("utf-8", errors="replace")
    return next((l for l in head.splitlines() if l.strip()), "")
