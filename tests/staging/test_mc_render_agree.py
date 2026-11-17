"""The update gate allows the measured Dev drift between bin/mc.sh and the 5 Nov MC operator."""
from __future__ import annotations

import pytest

import test_beta_dev_deploy as base

agree = base.op.mc_update_services_agree


def _agent(env, volumes=None, image="img:a"):
    svc = {"image": image, "pull_policy": "never", "environment": dict(env), "command": ["node"]}
    if volumes is not None:
        svc["volumes"] = volumes
    return svc


def test_the_measured_dev_drift_is_allowed():
    reviewed = _agent({"MC_MODEL_GATEWAY_KEY": "", "HOME": "/app"}, ["/state:/home/node"])
    wrapper = _agent(
        {
            "MC_MODEL_GATEWAY_KEY": "live-key",
            "MC_FAILOVER_ROUTES": "a,b",
            "MC_METERED_ROUTES": "c",
            "MC_PRIMARY_ROUTE": "plan",
            "MINIMOI_DEPTH_PROOF": "1",
            "MINIMOI_RUNTIME_PROOF_MODULE": "proof",
            "HOME": "/app",
        },
        ["/state:/home/node", "/opt/minimoi/planning/tree:/minimoi/planning:ro"],
        image="img:live",
    )
    agree(reviewed, wrapper, "mc-agent", True)
    relay_old = _agent({"MC_RELAY_ROUTES": "old"})
    relay_live = _agent(
        {"MC_RELAY_ROUTES": "old,new", "MC_RELAY_DEPTH_PROOFS": "on", "MC_RELAY_TOOL_STARTS": "on"},
        image="img:live",
    )
    agree(relay_old, relay_live, "mc-relay", True)


def test_an_unknown_env_key_still_fails():
    reviewed = _agent({"HOME": "/app"})
    wrapper = _agent({"HOME": "/app", "MC_SOMETHING_NEW": "1"})
    with pytest.raises(AssertionError, match="mc-agent"):
        agree(reviewed, wrapper, "mc-agent", True)


def test_a_command_change_still_fails():
    reviewed = _agent({"HOME": "/app"})
    wrapper = _agent({"HOME": "/app"})
    wrapper["command"] = ["other"]
    with pytest.raises(AssertionError, match="mc-agent"):
        agree(reviewed, wrapper, "mc-agent", True)


def test_an_unexpected_volume_still_fails():
    reviewed = _agent({"HOME": "/app"}, ["/state:/home/node"])
    wrapper = _agent({"HOME": "/app"}, ["/state:/home/node", "/tmp/extra:/minimoi/extra:ro"])
    with pytest.raises(AssertionError, match="mc-agent"):
        agree(reviewed, wrapper, "mc-agent", True)


def test_image_is_compared_when_the_override_does_not_name_the_service():
    reviewed = _agent({"HOME": "/app"})
    wrapper = _agent({"HOME": "/app"}, image="img:other")
    with pytest.raises(AssertionError, match="mc-egress"):
        agree(reviewed, wrapper, "mc-egress", False)


def test_metered_routes_without_a_gateway_key_fail():
    reviewed = _agent({"MC_MODEL_GATEWAY_KEY": "", "HOME": "/app"})
    wrapper = _agent({"MC_MODEL_GATEWAY_KEY": "", "MC_METERED_ROUTES": "minimoi-gateway/minimoi-xai-strong", "HOME": "/app"})
    with pytest.raises(AssertionError, match="MC_METERED_ROUTES is set and MC_MODEL_GATEWAY_KEY is empty"):
        agree(reviewed, wrapper, "mc-agent", True)


def test_empty_metered_routes_may_have_an_empty_gateway_key():
    reviewed = _agent({"MC_MODEL_GATEWAY_KEY": "", "HOME": "/app"})
    wrapper = _agent({"MC_MODEL_GATEWAY_KEY": "", "MC_METERED_ROUTES": "", "HOME": "/app"})
    agree(reviewed, wrapper, "mc-agent", True)


def test_a_shared_env_value_change_still_fails():
    reviewed = _agent({"HOME": "/app"})
    wrapper = _agent({"HOME": "/elsewhere"})
    with pytest.raises(AssertionError, match="mc-agent"):
        agree(reviewed, wrapper, "mc-agent", True)
