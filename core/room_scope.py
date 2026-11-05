"""Which Guildhall rooms an agent may bring the owner's private context and tools into (F2 D8).

A room is owner-private when it has members and every member is the owner, MC or CoS. This is the rule MC's room
bridge has applied since its first deployment; CoS uses the same function so the two cannot drift. Members must come
from the authoritative Records snapshot, never from a worker's request body or a model.

F2 meeting scopes for CoS:
- ``owner_private``: owner-private reads, no writes;
- ``closed``: any other room, or one whose members were not verified. No tools and no owner context, until F10 adds
  audiences.
"""
from __future__ import annotations

TEAM = frozenset({"mc", "cos"})
OWNER_PRIVATE = "owner_private"
CLOSED = "closed"


def owner_private(members, principal: str = "robert") -> bool:
    """True only for a non-empty member list drawn from {principal, mc, cos}. Anything malformed is not private."""
    if not isinstance(members, (list, tuple)) or not members or not isinstance(principal, str) or not principal:
        return False
    allowed = TEAM | {principal}
    return all(isinstance(m, dict) and m.get("id") in allowed for m in members)


def meeting_scope(members, principal: str = "robert") -> str:
    return OWNER_PRIVATE if owner_private(members, principal) else CLOSED
