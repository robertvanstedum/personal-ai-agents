"""One wrapper for every model call a helper makes (spec v0.4 H05, §3.2). The marker is reserved before the provider is called, so
a crash can't lead to a second call: a resume that finds the marker stops and says so. Caps count this call's bound and every
earlier reservation whose cost isn't known, so a crash can't hide spend. Figures are estimates and labelled so."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


class CapReached(Exception):
    pass


class AlreadyCalled(Exception):
    """A marker exists for this call: it ran, or may have. Never call again."""


class Ledger:
    def __init__(self, root, *, daily_cap: float, clock=time.time):
        self.root, self.daily_cap, self.clock = Path(root), daily_cap, clock
        self.root.mkdir(parents=True, exist_ok=True)

    def _day(self) -> Path:
        d = self.root / time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        d.mkdir(exist_ok=True)
        return d

    def spent_today(self) -> float:
        total = 0.0
        for p in self._day().glob("*.json"):
            try:
                row = json.loads(p.read_text())
            except ValueError:
                row = {}
            total += float(row.get("actual_usd") if row.get("actual_usd") is not None else row.get("bound_usd", 0.0))
        return total

    def reserve(self, key: str, *, bound_usd: float, task_spent: float, task_cap: float, purpose: str, task_id: str) -> Path:
        if task_spent + bound_usd > task_cap:
            raise CapReached("task")
        if self.spent_today() + bound_usd > self.daily_cap:
            raise CapReached("day")
        path = self._day() / f"{key}.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise AlreadyCalled(key) from None
        with os.fdopen(fd, "w") as fh:
            json.dump({"task_id": task_id, "purpose": purpose, "bound_usd": bound_usd, "actual_usd": None, "estimate": True}, fh)
        return path

    @staticmethod
    def settle(path: Path, actual_usd: float) -> None:
        row = json.loads(path.read_text())
        row["actual_usd"] = actual_usd
        path.write_text(json.dumps(row))
