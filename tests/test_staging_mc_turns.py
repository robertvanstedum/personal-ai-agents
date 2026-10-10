"""The Master Craftsman turn log on staging (memory build, v0.5.1 §8):
docker-compose.staging-mc-turns.yml gives the portal MC_TURNS_DIR and the
data/mc-turns mount. It is opt-in and, for now, nothing in scripts/staging
includes it (Codex owns those scripts): the overlay is a file a staging
script can add. Fake docker only; production never sets MC_TURNS_DIR."""
from __future__ import annotations

from pathlib import Path

import yaml

from test_staging_environment import PROD, STAGING, _compose_config, _load  # noqa: F401

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "docker-compose.staging-mc-turns.yml"


def test_the_overlay_adds_only_the_portals_turn_log():
    assert _load(OVERLAY) == {"services": {"portal": {
        "environment": ["MC_TURNS_DIR=/app/data/mc-turns"],
        "volumes": ["${MINIMOI_ROOT:?}/data/mc-turns:/app/data/mc-turns"],
    }}}
    extra = {"MINIMOI_ROOT": "/Users/x/minimoi-staging", "MINIMOI_IMAGE_TAG": "abc1234"}
    with_log = yaml.safe_load(_compose_config(PROD, STAGING, OVERLAY, env_extra=extra))["services"]
    without = yaml.safe_load(_compose_config(PROD, STAGING, env_extra=extra))["services"]
    portal = with_log["portal"]
    assert portal["environment"]["MC_TURNS_DIR"] == "/app/data/mc-turns"
    mounts = {(v["source"], v["target"]) for v in portal["volumes"]}
    assert ("/Users/x/minimoi-staging/data/mc-turns", "/app/data/mc-turns") in mounts
    # the staging mounts the portal already had are all still there
    assert {(v["source"], v["target"]) for v in without["portal"]["volumes"]} <= mounts
    for name in without:
        if name != "portal":
            assert with_log[name] == without[name], name
    assert "MC_TURNS_DIR" not in without["portal"]["environment"]


def test_production_does_not_write_the_mc_turn_log():
    assert "MC_TURNS_DIR" not in PROD.read_text()
    assert "MC_TURNS_DIR" not in (REPO / "docker-compose.staging.yml").read_text()


def test_the_release_classifies_the_overlay_as_staging_only():
    from scripts.ci.classify_release import classify
    assert classify(["docker-compose.staging-mc-turns.yml"]) == ("documents", ())
