"""One helper seat's worker: one process per seat under a file lock; a second replica is a failed start (spec v0.4 §3). Each pass takes
the tasks this seat holds, works the chartered ones, and submits a result to the lead. Results are recorded in the task service
as this seat's own source events, so no new store is needed and the lead reads them with its own token.

python -m services.helper_seats.worker --seat apprentice
Env: PARTNERSHIP_TASKS_URL, PARTNERSHIP_ENVIRONMENT, HELPER_TOKEN_FILE, HELPER_CHARTER_APPROVALS, HELPER_STATE_DIR, HELPER_REPO_ROOT,
GUILD_QUEUE_PATH, HELPER_GATEWAY_URL, HELPER_GATEWAY_KEY_FILE, HELPER_MODEL."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from pathlib import Path

from core.canonical_json import digest
from services.helper_seats import spend, types as T
from services.partnership_tasks.client import TaskServiceUnavailable, command

_log = logging.getLogger("helper")
MAX_RESULT = 7800
IDLE_S = 3
MAX_REQUEST_BYTES = 200_000
MAX_OUTPUT_TOKENS = 400


class RequestTooLarge(ValueError):
    pass


class Worker:
    def __init__(self, client, seat: str, charter, *, environment: str, ledger, ctx_for, scope: str = "personal"):
        self.client, self.seat, self.charter, self.env, self.ledger, self.ctx_for, self.scope = client, seat, charter, environment, ledger, ctx_for, scope

    def _cmd(self, kind, ttype, tid, payload, cid, expected=None, parent=None):
        return command(kind, ttype, tid, payload, command_id=cid[:80], environment=self.env, scope=self.scope, expected_revision=expected,
                       causal_parent=parent, session_id=None, runtime={"kind": "deterministic", "model": None, "provider": None, "build": f"helper/{self.seat}/1", "config_version": str(self.charter.version)})

    def _send(self, cmd):
        out = self.client.submit(cmd)
        if out.get("outcome") != "accepted":
            raise RuntimeError(f"{cmd['kind']} refused: {out.get('code')} {out.get('detail')}")

    def due(self, task) -> bool:
        """Mine, chartered, and waiting on me: not started, or the lead asked for changes on the current set."""
        if task.get("holder") != self.seat or (task.get("charter_ref") or {}).get("seat") != self.seat or task["state"] not in ("submitted", "working"):
            return False
        if not task["artifact_sets"]:
            return True
        kind = self.charter.types.get((task.get("charter_ref") or {}).get("type_id"))
        if kind is None or len(task["artifact_sets"]) > kind["max_revisions"]:      # the task service would refuse another set
            return False
        current = task["artifact_sets"][-1]["digest"]
        return any(r["reviewed_set_digest"] == current and r["verdict"] == "changes_requested" for r in task["reviews"])

    def pass_once(self) -> int:
        n = 0
        for tid in self.client.held():
            task = self.client.get("task", tid)
            if task and self.due(task):
                self.work(task)
                n += 1
        return n

    def inputs_of(self, task) -> dict:
        """What the lead asked, as data: JSON in the task's done-when line, kept by the lead's tool."""
        try:
            value = json.loads(task["done_when"])
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def work(self, task) -> None:
        tid, ref = task["task_id"], task["charter_ref"]
        kind = self.charter.types[ref["type_id"]]
        round_no = len(task["artifact_sets"]) + 1
        if task["state"] == "submitted":
            source = self.client.get("request", task["request_ref"])["source_event_id"]
            self._send(self._cmd("task.transition", "task", tid, {"to": "working", "reason": "picked up", "waiting": None, "fence_generation": task["fence_generation"],
                                                                   "evidence_refs": [{"store": "task_service", "id": source, "edition": None, "digest": None}]},
                                 f"cmd-h-work-{tid}", expected=task["revision"]))
            task = self.client.get("task", tid)
        try:
            ctx = self.ctx_for(task, kind, round_no)
            outcome = T.TYPES[ref["type_id"]](ctx, self.inputs_of(task))
        except RequestTooLarge:
            outcome = {"stopped": "request_too_large", "note": "The model request exceeded the input limit; no call or charge was made."}
        except spend.AlreadyCalled:
            outcome = {"stopped": "already_called", "note": "This call may already have run before a restart, so it was not repeated."}
        except spend.CapReached as cap:
            outcome = {"stopped": "cap_reached", "cap": str(cap), "note": "Stopped at the budget in the charter."}
        except Exception as exc:
            _log.exception("helper %s: %s failed", self.seat, ref["type_id"])
            outcome = {"stopped": "failed", "note": type(exc).__name__}
        result = json.dumps({"type_id": ref["type_id"], "charter": ref, "drafted_by": self.seat, "round": round_no, "outcome": outcome}, ensure_ascii=False, sort_keys=True)
        if len(result) > MAX_RESULT:
            outcome = {"stopped": "too_long", "note": "The result was too long to record."}
            result = json.dumps({"type_id": ref["type_id"], "charter": ref, "drafted_by": self.seat, "round": round_no, "outcome": outcome}, sort_keys=True)
        sid = f"SE-h-{digest([tid, round_no])[7:39]}"
        if self.client.get("source_event", sid) is None:
            self._send(self._cmd("source_event.record", "source_event", sid, {"source": {"store": "helper-result", "id": f"{tid}:{round_no}", "position": round_no},
                                                                               "content": result, "correction_of": None}, f"cmd-h-res-{tid}-{round_no}"))
        task = self.client.get("task", tid)
        self._send(self._cmd("task.submit_artifacts", "task", tid, {"artifacts": [{"store": "task_service", "id": sid, "edition": "1", "digest": digest(result)}], "fence_generation": task["fence_generation"]},
                             f"cmd-h-sub-{tid}-{round_no}", expected=task["revision"]))


def reserve_model_call(ledger, *, task_id, key, task_cap, purpose, environ):
    """Reserve before dispatch; opt-in review funds cannot lower an assumed cost.

    A shared-budget rollout must supply a separately justified call bound. Merely
    subtracting a review reserve from the old bound is not pricing evidence.
    """
    bound, part = task_cap, None
    if "HELPER_LEAD_REVIEW_RESERVE_USD" in environ:
        if "HELPER_MODEL_CALL_BOUND_USD" not in environ:
            raise ValueError("shared review allowance requires a proved helper call bound")
        bound = spend._amount(float(environ["HELPER_MODEL_CALL_BOUND_USD"]))
        if bound <= 0:
            raise spend.CapReached("helper")
        ledger.allocate_task(task_id, task_cap=task_cap,
                             review_reserve=float(environ["HELPER_LEAD_REVIEW_RESERVE_USD"]))
        part = "helper"
    marker = ledger.reserve(key, bound_usd=bound, task_spent=0.0, task_cap=task_cap,
                            purpose=purpose, task_id=task_id, part=part)
    return marker, bound



def call_model(ledger, *, prompt, purpose, task_id, key, task_cap, environ, post=None):
    """Cap the exact serialized request before any reservation or provider call."""
    body = {"model": environ["HELPER_MODEL"], "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_OUTPUT_TOKENS}
    raw = json.dumps(body, ensure_ascii=True, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestTooLarge('helper_request_too_large')
    marker, bound = reserve_model_call(ledger, task_id=task_id, key=key, task_cap=task_cap,
                                       purpose=purpose, environ=environ)
    if post is None:
        import requests
        post = requests.post
    credential = Path(environ["HELPER_GATEWAY_KEY_FILE"]).read_text().strip()
    response = post(environ["HELPER_GATEWAY_URL"].rstrip("/")+"/chat/completions",
                    timeout=(3,25), allow_redirects=False, data=raw,
                    headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"})
    response.raise_for_status()
    body = response.json()
    ledger.settle(marker, bound)
    return body["choices"][0]["message"]["content"]


def main(argv=None):
    from domains.guild.queue_store import QueueStore
    from services.partnership_tasks.charters import Charters
    from services.partnership_tasks.client import TaskClient, read_token
    p = argparse.ArgumentParser()
    p.add_argument("--seat", required=True)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    state = Path(os.environ["HELPER_STATE_DIR"]) / args.seat
    state.mkdir(parents=True, exist_ok=True)
    lock = open(state / "worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"another {args.seat} worker is running: a second replica is a failed start")
    charters = Charters.load(os.environ.get("HELPER_CHARTERS_DIR") or "/app/config/partnership/charters", os.environ["HELPER_CHARTER_APPROVALS"])
    charter = charters.get(args.seat)
    if charter is None:
        sys.exit(f"no approved charter for {args.seat}")
    token_file = os.environ["HELPER_TOKEN_FILE"]
    client = TaskClient(os.environ["PARTNERSHIP_TASKS_URL"], lambda: read_token(token_file))
    ledger = spend.Ledger(state / "spend", daily_cap=charter.daily_budget_usd)
    queue = QueueStore(os.environ["GUILD_QUEUE_PATH"]) if os.environ.get("GUILD_QUEUE_PATH") else None

    def ctx_for(task, kind, round_no):
        counter = {"n": 0}

        def ask_model(prompt, purpose):
            counter["n"] += 1
            return call_model(ledger, prompt=prompt, purpose=purpose, task_id=task['task_id'],
                key=f"{task['task_id']}-{round_no}-{counter['n']}", task_cap=kind["per_task_budget_usd"],
                environ=os.environ)
        return T.Ctx(repo_root=os.environ.get("HELPER_REPO_ROOT"), read_items=queue.read_items if queue else None, ask_model=ask_model)

    worker = Worker(client, args.seat, charter, environment=os.environ["PARTNERSHIP_ENVIRONMENT"], ledger=ledger, ctx_for=ctx_for)
    while True:
        try:
            worker.pass_once()
        except TaskServiceUnavailable:
            _log.warning("helper %s: task service unavailable", args.seat)
        except Exception:
            _log.exception("helper %s: pass failed", args.seat)
        time.sleep(IDLE_S)


if __name__ == "__main__":
    main()
