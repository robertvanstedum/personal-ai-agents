"""#584: Master Craftsman's web fetch and egress.

MC's container keeps model-provider-only egress (the proxy's allowlist), so the job agent's exec gains no open web.
Web pages are fetched by mc-fetch, its own container outside MC's network, which checks every hop's resolved address.
The node rigs (tests/mc_egress/*.test.mjs; fake DNS, local servers only, no network) cover the address check, the
proxy and the fetch service; the config checks below hold the OpenClaw side."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RIGS = sorted((REPO / "tests" / "mc_egress").glob("*.test.mjs"))
CONFIG = REPO / "docker" / "mc-agent-container" / "openclaw.json"
DOCKERFILE = REPO / "docker" / "mc-agent-container" / "Dockerfile"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("rig", RIGS, ids=lambda p: p.name)
def test_node_rig(rig):
    run = subprocess.run(["node", "--test", str(rig)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]


def test_rigs_present():
    assert {p.name for p in RIGS} >= {"egress_proxy.test.mjs", "mc_fetch.test.mjs"}


def test_openclaw_built_in_web_fetch_stays_off():
    # OpenClaw's own web_fetch would go out through MC's container (and so would need the proxy opened to the web).
    config = json.loads(CONFIG.read_text())
    assert config["tools"]["web"]["fetch"]["enabled"] is False
    for name, agent in config["agents"]["entries"].items():
        assert "web_fetch" in agent["tools"]["deny"], name


def test_fetch_service_ships_in_the_image():
    assert "docker/mc-agent-container/mc-fetch.mjs" in DOCKERFILE.read_text()
