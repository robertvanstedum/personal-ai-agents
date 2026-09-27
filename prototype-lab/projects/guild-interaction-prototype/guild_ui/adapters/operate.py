"""Operate tiles, rollout, evidence and journey health. No live implementation
exists yet: each tile is sample by explicit configuration in layout.json, or
not instrumented. The Build queue tile is computed from the queue adapter."""
from __future__ import annotations

import json
from pathlib import Path

from .contract import SourceResult, not_configured, sample


class SampleOperate:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.evidence = f"fixtures/{self.path.name}"

    def tile(self, tile_id: str) -> SourceResult:
        t = self.doc["tiles"].get(tile_id)
        if t is None:
            return not_configured("layout.json tile has no source", f"tile '{tile_id}'")
        status = "stale" if t.get("stale") else "ok"
        return sample(t, self.evidence, t.get("observed"), status)

    def rollout(self) -> SourceResult:
        return sample(self.doc["rollout"], self.evidence, self.doc["rollout"].get("observed"))

    def evidence_marks(self) -> SourceResult:
        return sample(self.doc["evidence"], self.evidence)

    def journeys(self) -> SourceResult:
        return sample(self.doc["journeys"], self.evidence)

    def usage(self) -> SourceResult:
        """Usage & limits (rev 3.1): plans and prepaid balances, sample only."""
        path = self.path.parent / "usage.sample.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        return sample(doc, f"fixtures/{path.name}")

    def next_steps(self) -> SourceResult:
        return sample(self.doc["next_steps"], self.evidence)
