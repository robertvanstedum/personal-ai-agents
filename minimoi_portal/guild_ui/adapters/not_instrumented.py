"""Sources that do not exist yet. Each answers "not instrumented", never a value."""
from __future__ import annotations

from .contract import SourceResult, not_configured

# What each grey light or tile is waiting for, in one short line.
WAITING_FOR = {
    "agents": "no agent work store yet",
    "usage": "Usage watch arrives in B2",
    "rollouts": "rollout counts arrive with Records",
}


class NotInstrumented:
    def __init__(self, what: str):
        self.what = what

    def read(self) -> SourceResult:
        res = not_configured(self.what, self.what)
        res.error = WAITING_FOR.get(self.what, "not instrumented")
        return res
