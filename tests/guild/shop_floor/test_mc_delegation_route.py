"""Delegation on the real MC routes: 'Ask Claude Code to research: …' starts a job for the delegate with no model call,
the job follows the delegate to its result note, and MC's own jobs never depend on the delegation dispatcher."""
from __future__ import annotations

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import API, _keep, turned  # noqa: F401
from test_jobs_api import _ask, _rig, jobbed, JobRuntime  # noqa: F401

from core.mc_delegation_dispatcher.relay import RoutingRelay
from minimoi_portal.guild_ui.jobs_wiring import _with_delegation


class FakeDelegation:
    """The DelegationRelay surface; the PostgreSQL-backed one is covered in tests/mc_domain_actions."""

    connected = True

    def __init__(self):
        self.jobs, self.reachable = {}, True

    def start(self, spec):
        self.jobs[spec["job_id"]] = {"spec": spec, "state": "running", "text": None}
        return "accepted", {}

    def status(self, job_id):
        if not self.reachable:
            return "unreachable", None
        j = self.jobs.get(job_id)
        if j is None:
            return "not_found", None
        from minimoi_portal.guild_ui.mc.job_relay import clean_status
        return "ok", clean_status({"state": j["state"], "elapsed_s": 1, "heartbeat_age_s": 0,
                                   "result": {"text": j["text"]} if j["text"] else None,
                                   "audit": {"available": False}, "writes": [], "error_class": None})

    def stop(self, job_id):
        if not self.reachable:
            return "unreachable"
        if job_id not in self.jobs:
            return "not_found"
        self.jobs[job_id]["state"] = "stopped"
        return "confirmed"


@pytest.fixture
def delegating(jobbed, monkeypatch):
    ext = jobbed.app.extensions["guild_ui_next"]                       # a copy, restored after the test
    monkeypatch.setitem(ext, "feature_environ", {**(ext.get("feature_environ") or {}), "MINIMOI_MC_DELEGATION": "1"})
    mgr = jobbed.extra["manager"]
    fake = FakeDelegation()
    mgr.relay = RoutingRelay(mgr.relay, fake)
    jobbed.extra["delegation"] = fake
    return jobbed


def test_ask_claude_code_starts_a_delegated_job_without_a_model_and_its_answer_comes_back(delegating):
    client, token, cid, mgr, runtime = _rig(delegating)
    fake = delegating.extra["delegation"]
    note = _keep(client, token, "Ask Claude Code to research: which modules read planning-studio files?", conversation_id=cid)
    body = _ask(client, token, note, conversation_id=cid).get_json()
    assert body["job_command"] == "delegate" and "Claude Code" in body["message"] and runtime.sent == []
    assert body["job"]["title"].startswith("Claude Code: which modules")
    mgr.tick()
    [jid] = fake.jobs
    assert fake.jobs[jid]["spec"]["agent"] == "claude-code" and runtime.jobs.starts == []     # not MC's runtime
    fake.jobs[jid].update(state="completed", text="references.py and pages.py read them.")
    mgr.tick()
    [done] = client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"]
    assert done["state"] == "completed" and done["result_note_id"]
    shown = client.get(f"{API}/conversations/{cid}/jobs/{jid}/note").get_json()["note"]
    assert shown["text"].startswith("references.py and pages.py")


def test_stop_reaches_the_delegate(delegating):
    client, token, cid, mgr, runtime = _rig(delegating)
    fake = delegating.extra["delegation"]
    _ask(client, token, _keep(client, token, "Ask Codex to look into: the flaky ledger test", conversation_id=cid),
         conversation_id=cid)
    mgr.tick()
    body = _ask(client, token, _keep(client, token, "stop", conversation_id=cid), conversation_id=cid).get_json()
    assert body["job_command"] == "stop" and body["job"]["state"] == "stopped"
    assert [j["state"] for j in fake.jobs.values()] == ["stopped"] and runtime.jobs.stops == []


@pytest.mark.parametrize("text", ['Ask Claude Code to research: "quoted"', "Tell Claude Code to research: x y z w",
                                  "Ask Claude Code to research: hi", "> Ask Codex to investigate: something long"])
def test_only_the_exact_owner_phrase_delegates(delegating, text):
    client, token, cid, mgr, runtime = _rig(delegating)
    body = _ask(client, token, _keep(client, token, text, conversation_id=cid), conversation_id=cid).get_json()
    assert body.get("job_command") != "delegate" and delegating.extra["delegation"].jobs == {}


def test_off_unless_the_flag_is_on(delegating):
    delegating.app.extensions["guild_ui_next"]["feature_environ"]["MINIMOI_MC_DELEGATION"] = "0"
    client, token, cid, mgr, runtime = _rig(delegating)
    note = _keep(client, token, "Ask Claude Code to research: which modules read planning files?", conversation_id=cid)
    body = _ask(client, token, note, conversation_id=cid).get_json()
    assert body.get("job_command") != "delegate" and delegating.extra["delegation"].jobs == {}


def test_mc_jobs_keep_working_while_the_dispatcher_is_unreachable(delegating):
    client, token, cid, mgr, runtime = _rig(delegating)
    delegating.extra["delegation"].reachable = False
    note = _keep(client, token, "Compare my resumes. Run this as a job.", conversation_id=cid)
    _ask(client, token, note, conversation_id=cid)
    mgr.tick()
    [job] = client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"]
    assert job["state"] == "running"
    runtime.jobs.finish(job["id"], "Done.")
    mgr.tick()
    assert client.get(f"{API}/conversations/{cid}/jobs").get_json()["jobs"][0]["state"] == "completed"


def test_routing_says_unreachable_only_when_neither_side_knows_the_job():
    fake = FakeDelegation()
    fake.reachable = False

    class Mc:
        def status(self, job_id):
            return "not_found", None

        def stop(self, job_id):
            return "not_found"
    routing = RoutingRelay(Mc(), fake)
    assert routing.status("j-0123456789abcdef") == ("unreachable", None)
    assert routing.stop("j-0123456789abcdef") == "unreachable"


@pytest.mark.parametrize("env", [{}, {"MINIMOI_MC_DELEGATION": "1"},
                                 {"MINIMOI_MC_DELEGATION": "1", "MINIMOI_MC_DELEGATION_PRINCIPAL": "robert",
                                  "MINIMOI_MC_DELEGATION_ENV": "staging", "DATABASE_URL": "postgresql://x"}])
def test_wiring_stays_off_unless_fully_configured(env):
    relay = object()
    assert _with_delegation(relay, env) is relay


def test_wiring_routes_when_fully_configured():
    relay = object()
    routed = _with_delegation(relay, {"MINIMOI_MC_DELEGATION": "1", "MINIMOI_MC_DELEGATION_PRINCIPAL": "robert",
                                      "MINIMOI_MC_DELEGATION_ENV": "dev", "DATABASE_URL": "postgresql://x"})
    assert isinstance(routed, RoutingRelay) and routed.mc is relay
