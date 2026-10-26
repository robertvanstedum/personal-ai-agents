"""MC's build proposal (docs/plans/PLAN_mc_chat_yes_build_handoff_2026-10-26.md): Robert's yes starts a build job from his
own note, through the same delegation pipe as research. Only research and build scopes exist; build is tier 3 only."""
import json
from pathlib import Path

import pytest

from core.mc_delegation_dispatcher.relay import DelegationRelay
from core.mc_tools import effects
from core.mc_tools.policy import HANDLERS

ROOT = Path(__file__).resolve().parents[2]


class Store:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, **kw):
        self.enqueued.append(kw)
        return "accepted"


def relay():
    store = Store()
    return DelegationRelay(store, ref_for=lambda repo: "a" * 40), store


@pytest.mark.parametrize("scope", ["research", "build"])
def test_a_job_names_its_scope_and_the_relay_enqueues_exactly_that(scope):
    r, store = relay()
    assert r.start({"job_id": "j-1", "agent": "claude-code", "scope": scope, "task": "t"})[0] == "accepted"
    assert store.enqueued[0]["scope"] == scope


def test_no_scope_means_research_and_any_other_scope_is_refused():
    r, store = relay()
    r.start({"job_id": "j-1", "agent": "claude-code", "task": "t"})
    assert store.enqueued[0]["scope"] == "research"
    assert r.start({"job_id": "j-2", "agent": "claude-code", "scope": "deploy", "task": "t"}) == ("refused", {"reason": "scope_invalid"})
    assert len(store.enqueued) == 1


def test_the_jobs_manager_refuses_an_unknown_scope_before_anything_is_created():
    from minimoi_portal.guild_ui.jobs import JobManager
    mgr = JobManager.__new__(JobManager)            # the scope check runs first: no store, relay or folder needed
    job, refusal = mgr.start({"id": "c-x"}, "robert", {"text": "t"}, "n-1", scope="deploy")
    assert job is None and refusal[0] == "scope_invalid"


def test_starting_a_build_is_an_exact_approval_at_tier_3_only():
    assert "delegate.propose_build" in effects.PROPOSAL_CAPABILITIES
    assert "delegate.start_build" in effects.EXACT_CAPABILITIES
    h = HANDLERS["build_v1"]
    assert (h.tool, h.capability, h.tiers, h.egress) == ("minimoi_delegate_build", "delegate.start_build", frozenset({3}), "delegation")


def test_the_tool_takes_no_task_text_only_a_target_and_a_title():
    tools = json.loads((ROOT / "core/mc_tools/tools.json").read_text())
    tools = tools["tools"] if isinstance(tools, dict) else tools
    spec = next(t for t in tools if t["name"] == "minimoi_delegate_build")
    assert spec["capability"] == "delegate.propose_build"
    assert set(spec["parameters"]["properties"]) == {"agent", "title"}
    assert spec["parameters"]["properties"]["agent"]["enum"] == ["claude-code"]
    assert spec["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("policy", ["mc-policy.prod.json", "mc-policy.example.json"])
def test_the_build_rule_mirrors_research_except_its_capability_and_handler(policy):
    rules = json.loads((ROOT / "services/mc_operations/policy" / policy).read_text())["rules"]
    research = next(r for r in rules if r["capability"] == "delegate.start_research")
    build = next(r for r in rules if r["capability"] == "delegate.start_build")
    assert {k: v for k, v in build.items() if k not in ("capability", "handler")} == \
           {k: v for k, v in research.items() if k not in ("capability", "handler")}
    assert build["handler"] == "build_v1" and build["approval"] == "exact" and build["tier"] == 3
