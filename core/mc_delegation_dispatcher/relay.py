"""The jobs-manager relay for delegated work (same interface as minimoi_portal/guild_ui/mc/job_relay.JobRelay).

The portal's JobManager already gives a delegated job everything the owner sees: a live line in the
conversation, Stop, a durable lifecycle and exactly one result note. This relay puts the job in the
dispatcher queue for the Mac runner instead of on MC's own runtime, and reports its state back.

``RoutingRelay`` sends a job to this relay when its spec names a delegate agent ("claude-code" or
"codex"), and everything else to MC's runtime relay, so ordinary MC jobs are unchanged.
"""
from __future__ import annotations

from .store import DispatcherStore

DELEGATE_AGENTS = ("claude-code", "codex")
_MAP = {"queued": "running", "claimed": "running", "running": "running", "completed": "completed",
        "cancelled": "stopped", "failed": "failed", "refused": "failed", "timed_out": "failed",
        "output_limit": "failed", "interrupted": "failed"}


class DelegationRelay:
    def __init__(self, store: DispatcherStore, *, ref_for, repository="personal-ai-agents", scope="research",
                 cost_cap_usd=2.0):
        """``ref_for(repository)`` returns the exact commit the delegate works from (e.g. main's head)."""
        self.store, self.ref_for, self.repository, self.scope, self.cost_cap = store, ref_for, repository, scope, cost_cap_usd

    connected = True

    def start(self, spec: dict) -> tuple[str, dict]:
        agent = spec.get("agent")
        if agent not in DELEGATE_AGENTS:
            return "refused", {"reason": "not_a_delegate"}
        try:
            ref = self.ref_for(self.repository)
        except Exception:
            return "refused", {"reason": "ref_unavailable"}
        try:
            outcome = self.store.enqueue(job_id=spec["job_id"], actor=agent, scope=self.scope, repository=self.repository,
                                         ref=ref, brief=spec["task"], cost_cap_usd=self.cost_cap,
                                         deadline_s=spec.get("deadline_s") or 1800)
        except Exception:
            return "ambiguous", {"reason": "dispatcher_unavailable"}
        return outcome, {}

    def status(self, job_id: str):
        try:
            row = self.store.by_job(job_id)
        except Exception:
            return "unreachable", None
        if row is None:
            return "not_found", None
        state = _MAP[row["state"]]
        error = None
        if row["state"] == "queued" and row["deadline"] <= row["now"]:
            state, error = "failed", "not_picked_up"
        elif state == "failed":
            error = row["state"] if row["state"] != "failed" else (row["result_detail"] or {}).get("code", "failed")
        elapsed = ((row["finished_at"] or row["now"]) - (row["started_at"] or row["created_at"])).total_seconds()
        hb = (row["now"] - row["heartbeat_at"]).total_seconds() if row["heartbeat_at"] else None
        text = None
        if row["state"] in ("completed", "cancelled", "failed", "timed_out", "output_limit", "interrupted"):
            text = row["output_text"] or _plain_outcome(row)
        from minimoi_portal.guild_ui.mc.job_relay import clean_status
        return "ok", clean_status({"state": state, "elapsed_s": max(0, elapsed), "heartbeat_age_s": hb,
                                   "result": {"text": text} if text is not None else None,
                                   "audit": {"available": False}, "writes": [], "error_class": error})

    def stop(self, job_id: str) -> str:
        try:
            return self.store.stop(job_id)
        except Exception:
            return "unreachable"


def _plain_outcome(row) -> str:
    who = "Claude Code" if row["actor"] == "claude-code" else "Codex"
    code = (row["result_detail"] or {}).get("code")
    return f"{who} did not complete this ({row['state'].replace('_', ' ')}{': ' + code if code else ''})."


class RoutingRelay:
    """MC's own jobs go to MC's runtime; jobs naming a delegate go to the dispatcher."""

    def __init__(self, mc_relay, delegation_relay: DelegationRelay):
        self.mc, self.delegation = mc_relay, delegation_relay

    @property
    def connected(self) -> bool:
        return bool(getattr(self.mc, "connected", False)) or self.delegation.connected

    def start(self, spec):
        return (self.delegation if spec.get("agent") in DELEGATE_AGENTS else self.mc).start(spec)

    # An unreachable dispatcher must not stall MC's own jobs: ask MC's runtime, and report unreachable only when it
    # does not know the job either (it may then be a delegation we cannot see right now).
    def status(self, job_id):
        outcome, st = self.delegation.status(job_id)
        if outcome not in ("not_found", "unreachable"):
            return outcome, st
        mc_outcome, mc_st = self.mc.status(job_id)
        return ("unreachable", None) if outcome == "unreachable" and mc_outcome == "not_found" else (mc_outcome, mc_st)

    def stop(self, job_id):
        outcome = self.delegation.stop(job_id)
        if outcome not in ("not_found", "unreachable"):
            return outcome
        mc_outcome = self.mc.stop(job_id)
        return "unreachable" if outcome == "unreachable" and mc_outcome == "not_found" else mc_outcome
