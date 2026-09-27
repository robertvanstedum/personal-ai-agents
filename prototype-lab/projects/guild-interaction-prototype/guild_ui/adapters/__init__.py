"""Adapter registry: one contract, a live and a sample implementation per source."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .activity import LiveActivity, SampleActivity
from .build_queue import ACTIVE, STATUSES, LiveBuildQueue, SampleBuildQueue
from .operate import SampleOperate
from .sessions import LiveSessions, SampleSessions

PROJECT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT / "fixtures"
REPO_ROOT = PROJECT.parents[2]


@dataclass
class Sources:
    mode: str
    queue: object
    activity: object
    sessions: object
    operate: object

    def matrix(self) -> list[dict]:
        return [
            {"source": "build_queue", "adapter": type(self.queue).__name__},
            {"source": "activity", "adapter": type(self.activity).__name__},
            {"source": "sessions", "adapter": type(self.sessions).__name__},
            {"source": "operate", "adapter": type(self.operate).__name__},
        ]


def build_sources(mode: str = "live", queue_path=None, repo_root=None, records_db=None) -> Sources:
    if mode not in ("live", "sample"):
        raise ValueError("sources must be 'live' or 'sample'")
    operate = SampleOperate(FIXTURES / "operate.sample.json")  # no live implementation yet
    if mode == "sample":
        return Sources(mode, SampleBuildQueue(FIXTURES / "queue.sample.json"),
                       SampleActivity(FIXTURES / "activity.sample.json"),
                       SampleSessions(FIXTURES / "sessions.sample.json"), operate)
    repo = Path(repo_root) if repo_root else REPO_ROOT
    return Sources(mode,
                   LiveBuildQueue(Path(queue_path) if queue_path else repo / "data" / "guild" / "build_queue.json"),
                   LiveActivity(repo),
                   LiveSessions(records_db),
                   operate)


__all__ = ["ACTIVE", "STATUSES", "Sources", "build_sources", "SampleSessions", "FIXTURES"]
