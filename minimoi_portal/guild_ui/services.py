"""The service bundle the real Shop floor is registered with, and its ready() check.

ready() checks configuration only (review S9): the queue path is set, its
folder may be written by this process (the queue store's own rule, M2), and
the folder is writable for the journal and lock. Whether the queue file can
be read right now is a run-time question, answered on every read as live or
unknown, so a bad file shows *unknown* instead of taking the page down.

The floor store (notes, post-its, Continue) is never touched here: a
database that is down or not yet migrated makes those zones read
"unavailable" at run time, and the rest of the floor, Save included, keeps
working.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from domains.guild import queue_store as qs

from .adapters import DbHistory, LiveBuildQueue, LiveSessions, NotInstrumented, OperationsProbe
from .stores import DEFAULT_FLOOR, FloorStores

GREY_SOURCES = ("agents", "usage", "rollouts")


@dataclass
class Services:
    store: "qs.QueueStore"
    queue: LiveBuildQueue
    history: DbHistory
    systems: OperationsProbe
    sessions: LiveSessions
    floor: FloorStores | None = None
    not_instrumented: dict = field(default_factory=dict)
    audit: Callable | None = None   # (item_id, old, new, note) -> "ok" | "skipped"; may raise

    def ready(self) -> list[str]:
        problems = []
        if not self.store.path:
            return ["GUILD_QUEUE_PATH is not set"]
        problem = self.store.write_problem()
        if problem:
            problems.append(problem)
        elif not os.access(self.store.folder, os.W_OK):
            problems.append("the queue folder is not writable (journal and lock)")
        for name in GREY_SOURCES:
            if name not in self.not_instrumented:
                problems.append(f"no source bound for the {name} light")
        if self.floor is None:
            problems.append("no floor store bound (notes, post-its, Continue)")
        return problems


def build_services(*, queue_path: str | None, operations_status_url: str | None = None,
                   records_db: str | None = None, database_url: Callable[[], str | None] = lambda: None,
                   audit: Callable | None = None, http_get: Callable | None = None,
                   db_connect: Callable | None = None, store: "qs.QueueStore | None" = None,
                   floor: FloorStores | None = None, floor_key: str = DEFAULT_FLOOR) -> Services:
    store = store or qs.QueueStore(queue_path)
    return Services(
        store=store,
        queue=LiveBuildQueue(store),
        history=DbHistory(database_url, connect=db_connect),
        systems=OperationsProbe(operations_status_url, http_get=http_get),
        sessions=LiveSessions(records_db),
        floor=floor or FloorStores(database_url, connect=db_connect, floor=floor_key),
        not_instrumented={name: NotInstrumented(name) for name in GREY_SOURCES},
        audit=audit,
    )
