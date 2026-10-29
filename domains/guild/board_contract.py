"""Transport-independent Board operation context and refusal type.

A context describes the authenticated caller; it does not grant admission.
BoardActions still validates scope/capabilities and owns operation admission.
"""
from __future__ import annotations

from dataclasses import dataclass


LABELS = ("decide", "blocked", "remember", "followup", "idea", "fyi")


class BoardRefused(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class BoardContext:
    principal: str
    environment: str
    board: str
    executor: str
    source_ref: str
    capabilities: frozenset[str]
    owner_session: bool = False
