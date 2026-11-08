"""Spoken lookups: MC turns whose tools may only read (Robert, 8 Nov 2026: "I don't need you to ask me for permission to
read things, maybe just to write things").

The voice runs a lookup without his yes. Its owner note is marked here, in this process, before the turn is sent; the
tool service (a thread of the same portal process, see fence.py) admits on such a turn only the tools in READ_TOOLS, and
refuses every other one before it is recorded, so nothing changes and no quota is spent. A portal restart forgets the
marks, and it also ends every live turn (tool_portal_restarted), so a lookup's turn never outlives its mark.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from .policy import HANDLERS

_LOCK = threading.Lock()
_MARKED: "OrderedDict[str, None]" = OrderedDict()
_MAX = 10000

# Tools that only read: tier 1 alone, with no read-back to verify (nothing is written). Clarify asks him a question on
# the Board and recover restores a file, so neither is a lookup.
READ_TOOLS = frozenset(h.tool for h in HANDLERS.values() if h.tiers == frozenset({1}) and h.verify is None) - {
    "minimoi_clarify_action", "minimoi_artifact_recover"}


def mark(note_request_id: str) -> None:
    if not isinstance(note_request_id, str) or not note_request_id:
        return
    with _LOCK:
        _MARKED[note_request_id] = None
        _MARKED.move_to_end(note_request_id)
        while len(_MARKED) > _MAX:
            _MARKED.popitem(last=False)


def marked(note_request_id) -> bool:
    with _LOCK:
        return note_request_id in _MARKED


def allowed(note_request_id, tool: str) -> bool:
    """Whether ``tool`` may run on the turn of this owner note."""
    return tool in READ_TOOLS or not marked(note_request_id)
