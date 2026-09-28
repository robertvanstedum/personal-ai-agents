"""Master Craftsman on Grok, without OpenClaw: NOT BUILT YET.

Robert, September 28 2026: "Master Craftsman should be able to run on grok.
That is for sure." The backend switch has the ``grok`` value now so the seam
is fixed; the implementation is built right after the OpenClaw backend works
(DECISIONS_MC_BACKEND_v0.2, last addendum), in the shape of CoS's
domains/cos/backends/grok_backend.py behind COS_BACKEND_TYPE.

Until then selecting it is honest: Master Craftsman shows "unavailable · the
Grok backend is not built yet" and every turn is refused as unavailable. It
never falls back to OpenClaw or to the stub.
"""
from __future__ import annotations

from .backend import Health, MasterCraftsmanBackend, TurnResult

NOT_BUILT = "not_built"


class GrokMasterCraftsman(MasterCraftsmanBackend):
    kind = "grok"
    built = False

    def health(self) -> Health:
        return Health("unavailable", NOT_BUILT)

    def turn(self, req, cancel=None) -> TurnResult:
        return TurnResult("unavailable", self.kind, failure_class=NOT_BUILT,
                          message="The Grok backend for Master Craftsman is not built yet")
