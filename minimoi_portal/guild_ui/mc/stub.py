"""The scripted stub (MC spec v0.5 §2.3, H2): a fixed reply, ``backend_kind``
"stub", no citations and no blocks. Never an agent, never a model call, and
never a fallback for "unavailable". Its kept rows carry the stub's author
(backend.reply_author), so they read as the stub forever."""
from __future__ import annotations

from .backend import Health, MasterCraftsmanBackend, TurnResult

STUB_REPLY = ("Stub reply (scripted, not Master Craftsman): your note is kept. "
              "No agent read it and nothing was checked.")


class StubBackend(MasterCraftsmanBackend):
    kind = "stub"

    def health(self) -> Health:
        return Health("stub")

    def turn(self, req, cancel=None) -> TurnResult:
        if cancel is not None and cancel.is_set():
            return TurnResult("cancelled", self.kind)
        return TurnResult("answered", self.kind, text=STUB_REPLY)
