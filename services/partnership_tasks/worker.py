"""The partnership worker (B4 execution design v0.4 §4, v0.5 M3/S3/S4, v0.6): one process, its own container.

Each pass:
1. Outbox. For every queued entry (``GET /v1/outbox/pending`` with the drainer seat):
   - an admission whose task has moved on goes to ``drain_once``, which closes it ``fenced`` without a send;
   - a pending admission goes only when Robert's approval is present, unspent, for its own digest, and has at least
     60 s left (v0.6 Should 3); otherwise it waits (the view asks him again when it expires);
   - fences, notices, and entries already attempted or unknown always go (lookups settle them).
2. Runner door: harness results become task-service commands under each child's holder seat.

Holds the drainer seat and the runner seats (claude-code, codex) only; the portal holds none of them (ends R-S4).
One process: a file lock in the store directory. The idle sleep is 5 s, with a jittered backoff on errors. This is a
worker loop doing its job, not a monitor of anyone's work.

Configuration (env): PARTNERSHIP_TASKS_URL, PARTNERSHIP_ENVIRONMENT, PARTNERSHIP_DRAINER_TOKEN_FILE,
PARTNERSHIP_RUNNER_SEATS_FILE (JSON {seat: token_file}), DATABASE_URL_FILE (a private file holding the dispatcher
database URL for the least-privilege role; DATABASE_URL only for local runs), MINIMOI_MC_DELEGATION_ENV (dev), MINIMOI_MC_DELEGATION_PRINCIPAL, MINIMOI_MC_DELEGATION_AUTHORITY_FILE,
PARTNERSHIP_NOTICES_URL + PARTNERSHIP_NOTICES_TOKEN_FILE (optional; without them notices stay pending, never
dropped), PARTNERSHIP_WORKER_LOCK.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from services.partnership_tasks.outbox import drain_once

_log = logging.getLogger("partnership.worker")
MIN_APPROVAL_LEFT_S = 60
APPROVAL_WINDOW_S = 900


def approval_ready(row, now) -> bool:
    a = row.get("approval")
    if not a or a.get("spent_at") is not None or a.get("card_digest") != row["bundle_digest"]:
        return False
    approved = datetime.fromisoformat(a["approved_at"].replace("Z", "+00:00"))
    return (now - approved).total_seconds() <= APPROVAL_WINDOW_S - MIN_APPROVAL_LEFT_S


def choose(rows, now) -> list:
    """Which queued entries this pass sends to the drainer, in order (fences first, so a cancel reaches the authority
    before anything else for that task)."""
    out = []
    for r in rows:
        if r["operation"] == "admit_bundle" and r["state"] == "pending":
            if r["stale"] or approval_ready(r, now):
                out.append(r)
        else:
            out.append(r)
    return sorted(out, key=lambda r: (r["operation"] != "set_fence", r["created_at"]))


class ServiceOverHttp:
    """What ``drain_once`` needs from the task service (get, submit, environment), over the drainer's TaskClient."""
    def __init__(self, client, environment):
        self.client, self.environment = client, environment

    def get(self, target_type, target_id, token=None):
        return self.client.get(target_type, target_id)

    def submit(self, cmd, token=None):
        return self.client.submit(cmd)


def one_pass(*, drainer, svc, harness, notices, door, token=None,
             clock=lambda: datetime.now(timezone.utc), lead_wake=None) -> dict:
    """``token`` is the drainer token for an in-process service; over HTTP the client already holds it."""
    report = {"drained": [], "door": []}
    for row in choose(drainer.outbox_pending(), clock()):
        try:
            state = drain_once(svc, token, harness, row["operation_id"], notices, lead_wake=lead_wake)
            report["drained"].append((row["operation_id"], state))
        except Exception:
            _log.exception("partnership worker: drain failed for %s", row["operation_id"])
    if hasattr(harness, "stop_stale_cards"):
        try:
            report["revoked"] = harness.stop_stale_cards()
        except Exception:
            _log.exception("partnership worker: stale-card revoke failed; the next pass tries again")
    if door is not None:
        report["door"] = door.pass_once()
    return report


def main() -> int:  # pragma: no cover - wiring, exercised on Dev by the package probes
    import psycopg2
    from core.mc_delegation_dispatcher import production
    from core.mc_delegation_dispatcher.store import Authority
    from services.partnership_tasks.client import TaskClient, read_token
    from services.partnership_tasks.harness_port import HarnessDispatcher
    from services.partnership_tasks.notice_port import PortalNotices
    from services.partnership_tasks.runner_door import RunnerDoor
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    env = os.environ
    lock = open(env.get("PARTNERSHIP_WORKER_LOCK", "/data/worker.lock"), "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)                 # one worker process only
    url, task_env = env["PARTNERSHIP_TASKS_URL"], env["PARTNERSHIP_ENVIRONMENT"]
    drainer = TaskClient(url, lambda: read_token(env["PARTNERSHIP_DRAINER_TOKEN_FILE"]))
    seats = {seat: TaskClient(url, (lambda f: (lambda: read_token(f)))(path))
             for seat, path in json.loads(open(env["PARTNERSHIP_RUNNER_SEATS_FILE"]).read()).items()}
    # The database URL (least-privilege role partnership_worker) is read from a private file, never the environment.
    dsn = (open(env["DATABASE_URL_FILE"]).read().strip() if env.get("DATABASE_URL_FILE") else env["DATABASE_URL"])
    connect = lambda: psycopg2.connect(dsn, connect_timeout=3,  # noqa: E731
                                       options="-c statement_timeout=5000 -c lock_timeout=3000")
    principal, delegation_env = env.get("MINIMOI_MC_DELEGATION_PRINCIPAL", "robert"), env["MINIMOI_MC_DELEGATION_ENV"]
    # Exactly production's version-2 store: the authority plus its own grant catalogue (delegate.card and the rest).
    store = production.store(connect, principal=principal, environment=delegation_env,
                             authority=Authority.load(env["MINIMOI_MC_DELEGATION_AUTHORITY_FILE"]))
    harness = HarnessDispatcher(store, lambda op: drainer.get("outbox", op),
                                unreachable=(psycopg2.OperationalError, psycopg2.InterfaceError))
    notices = None
    if env.get("PARTNERSHIP_NOTICES_URL") and env.get("PARTNERSHIP_NOTICES_TOKEN_FILE"):
        notices = PortalNotices(env["PARTNERSHIP_NOTICES_URL"], lambda: read_token(env["PARTNERSHIP_NOTICES_TOKEN_FILE"]),
                                lambda op: drainer.get("outbox", op))
    door = RunnerDoor(connect, principal=principal, environment=delegation_env, reader=drainer, seats=seats,
                      task_environment=task_env)
    svc = ServiceOverHttp(drainer, task_env)
    _log.info("partnership worker up (%s; runner seats %s; notices %s)", task_env, sorted(seats),
              "on" if notices else "off")
    delay = 5.0
    while True:
        try:
            report = one_pass(drainer=drainer, svc=svc, harness=harness, notices=notices, door=door)
            if report["drained"] or report["door"] or report.get("revoked"):
                _log.info("pass: %s", json.dumps(report, default=str)[:2000])
            delay = 5.0
        except Exception:
            _log.exception("partnership worker: pass failed")
            delay = min(60.0, delay * 2) * (0.8 + 0.4 * random.random())
        time.sleep(delay)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
