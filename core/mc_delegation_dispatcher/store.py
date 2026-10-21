"""Dispatcher store: delegated work in PostgreSQL, spoken to by the runner protocol
(core/mc_delegation_runner/runner.py) and by the portal's delegation relay.

Runner protocol (what ``run_once`` calls):
  heartbeat(runner_id, state, capabilities)          liveness only, never pickup
  claim(runner_id, capabilities) -> envelope | None  oldest queued job this runner can run; lease + new generation
  progress(request_id, generation, phase)            running
  cancel_requested(request_id, generation) -> bool   the owner pressed Stop
  renew(request_id, generation) -> bool              False: the claim is lost (stale generation, cancelled, expired)
  result(request_id, generation, *, event_id, status, artifacts, detail, output_text=None)
        -> {state: accepted|already_applied|refused, event_id}

Every connection is used for one transaction and closed. A stale generation, a lapsed lease or a
cancelled job can never record a completion.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from core.mc_delegation_runner import envelope as env_mod

LEASE_SECONDS = 120
RESULT_STATES = ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted")
OUTPUT_MAX = 64 * 1024


def request_id_for(job_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-delegation-job:{job_id}"))


class DispatcherStore:
    def __init__(self, connect, *, principal: str, environment: str):
        self._connect, self.principal, self.environment = connect, principal, environment

    @contextmanager
    def _tx(self):
        from psycopg2.extras import RealDictCursor
        conn = self._connect()
        try:
            with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
        finally:
            conn.close()

    # ── portal side (the delegation relay) ─────────────────────────────────
    def enqueue(self, *, job_id, actor, scope, repository, ref, brief, cost_cap_usd, deadline_s) -> str:
        """'accepted' or 'duplicate' (this job id is already known: never queued twice)."""
        with self._tx() as cur:
            cur.execute("""INSERT INTO guild.mc_delegations (request_id, job_id, principal_id, environment, actor, scope,
                repository, ref, brief, cost_cap_usd, deadline)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp() + %s * interval '1 second')
                ON CONFLICT (job_id) DO NOTHING RETURNING request_id""",
                (request_id_for(job_id), job_id, self.principal, self.environment, actor, scope, repository, ref,
                 brief, cost_cap_usd, int(deadline_s)))
            return "accepted" if cur.fetchone() else "duplicate"

    def by_job(self, job_id) -> dict | None:
        with self._tx() as cur:
            cur.execute("SELECT *, clock_timestamp() AS now FROM guild.mc_delegations WHERE job_id=%s", (job_id,))
            return cur.fetchone()

    def stop(self, job_id) -> str:
        """'confirmed' (never started), 'requested' (the runner stops at its next check), 'not_running', 'not_found'."""
        with self._tx() as cur:
            cur.execute("SELECT state FROM guild.mc_delegations WHERE job_id=%s FOR UPDATE", (job_id,))
            row = cur.fetchone()
            if row is None:
                return "not_found"
            if row["state"] == "queued":
                cur.execute("""UPDATE guild.mc_delegations SET state='cancelled', cancel_requested=TRUE,
                    finished_at=clock_timestamp() WHERE job_id=%s""", (job_id,))
                return "confirmed"
            if row["state"] in ("claimed", "running"):
                cur.execute("UPDATE guild.mc_delegations SET cancel_requested=TRUE WHERE job_id=%s", (job_id,))
                return "requested"
            return "not_running"

    # ── runner protocol ────────────────────────────────────────────────────
    def heartbeat(self, runner_id, state, capabilities):
        with self._tx() as cur:
            cur.execute("""INSERT INTO guild.mc_delegation_runners (runner_id, state, capabilities, seen_at)
                VALUES (%s,%s,%s::jsonb, clock_timestamp()) ON CONFLICT (runner_id) DO UPDATE
                SET state=EXCLUDED.state, capabilities=EXCLUDED.capabilities, seen_at=EXCLUDED.seen_at""",
                (runner_id, state, json.dumps(capabilities)))

    def claim(self, runner_id, capabilities):
        actors = list(capabilities.get("cost_enforced_actors") or [])
        scopes = list(capabilities.get("scopes") or [])
        repos = list(capabilities.get("repositories") or [])
        if not actors or not scopes or not repos:
            return None
        with self._tx() as cur:
            cur.execute("""SELECT * FROM guild.mc_delegations
                WHERE state='queued' AND NOT cancel_requested AND principal_id=%s AND environment=%s
                  AND actor = ANY(%s) AND scope = ANY(%s) AND repository = ANY(%s) AND deadline > clock_timestamp()
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                (self.principal, self.environment, actors, scopes, repos))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("""UPDATE guild.mc_delegations SET state='claimed', generation=generation+1, runner_id=%s,
                lease_until=clock_timestamp() + %s * interval '1 second', heartbeat_at=clock_timestamp()
                WHERE request_id=%s RETURNING generation""", (runner_id, LEASE_SECONDS, row["request_id"]))
            generation = cur.fetchone()["generation"]
        deadline = row["deadline"].astimezone(timezone.utc).isoformat()
        return {"schema": env_mod.SCHEMA, "request_id": str(row["request_id"]), "work_id": row["job_id"],
                "generation": generation, "actor": row["actor"], "principal_id": row["principal_id"],
                "environment": row["environment"], "scope": row["scope"], "repository": row["repository"],
                "ref": row["ref"], "brief": row["brief"], "input_artifacts": [], "deadline": deadline,
                "cost_cap_usd": float(row["cost_cap_usd"])}

    def _current(self, cur, request_id, generation, *, lock=True):
        cur.execute("SELECT *, clock_timestamp() AS now FROM guild.mc_delegations WHERE request_id=%s"
                    + (" FOR UPDATE" if lock else ""), (str(request_id),))
        row = cur.fetchone()
        if row is None or row["generation"] != generation or row["state"] not in ("claimed", "running") \
                or row["lease_until"] is None or row["lease_until"] <= row["now"]:
            return None
        return row

    def progress(self, request_id, generation, phase):
        with self._tx() as cur:
            if self._current(cur, request_id, generation) is not None:
                cur.execute("""UPDATE guild.mc_delegations SET state='running',
                    started_at=COALESCE(started_at, clock_timestamp()), heartbeat_at=clock_timestamp()
                    WHERE request_id=%s""", (str(request_id),))

    def cancel_requested(self, request_id, generation) -> bool:
        with self._tx() as cur:
            row = self._current(cur, request_id, generation, lock=False)
            return row is None or bool(row["cancel_requested"])          # a lost claim also means stop

    def renew(self, request_id, generation) -> bool:
        with self._tx() as cur:
            row = self._current(cur, request_id, generation)
            if row is None or row["cancel_requested"]:
                return False
            cur.execute("""UPDATE guild.mc_delegations SET lease_until=clock_timestamp() + %s * interval '1 second',
                heartbeat_at=clock_timestamp() WHERE request_id=%s""", (LEASE_SECONDS, str(request_id)))
            return True

    def result(self, request_id, generation, *, event_id, status, artifacts, detail, output_text=None) -> dict:
        if status not in RESULT_STATES:
            return {"state": "refused", "event_id": event_id, "code": "status_invalid"}
        with self._tx() as cur:
            cur.execute("SELECT * FROM guild.mc_delegations WHERE request_id=%s FOR UPDATE", (str(request_id),))
            row = cur.fetchone()
            if row is None:
                return {"state": "refused", "event_id": event_id, "code": "unknown_request"}
            if row["result_event_id"] is not None:
                same = str(row["result_event_id"]) == str(event_id)
                return {"state": "already_applied" if same else "refused", "event_id": event_id,
                        **({} if same else {"code": "different_result_already_recorded"})}
            # Refusals may arrive without a claim (an envelope the runner rejected). Anything else needs the
            # current generation; a stale worker never completes someone else's claim.
            if row["generation"] != generation and status != "refused":
                return {"state": "refused", "event_id": event_id, "code": "stale_generation"}
            if row["cancel_requested"] and status == "completed":
                status = "cancelled"                       # Stop was pressed: the result is kept, not claimed as done
            text = output_text[:OUTPUT_MAX] if isinstance(output_text, str) else None
            cur.execute("""UPDATE guild.mc_delegations SET state=%s, result_event_id=%s, result_detail=%s::jsonb,
                artifacts=%s::jsonb, output_text=%s, finished_at=clock_timestamp(), lease_until=NULL
                WHERE request_id=%s""", (status, str(event_id), json.dumps(detail or {}), json.dumps(artifacts or []),
                                         text, str(request_id)))
            return {"state": "accepted", "event_id": event_id}
