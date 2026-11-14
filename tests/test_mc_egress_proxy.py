"""#584: Master Craftsman's egress proxy and web_fetch config.

The proxy's address check and redirect handling run under node (tests/mc_egress/egress_proxy.test.mjs; fake DNS,
local servers only, no network). The config checks below hold the OpenClaw side: web_fetch on, routed through the
proxy, off both deny lists."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RIG = REPO / "tests" / "mc_egress" / "egress_proxy.test.mjs"
CONFIG = REPO / "docker" / "mc-agent-container" / "openclaw.json"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_egress_proxy_address_and_redirect_checks():
    run = subprocess.run(["node", "--test", str(RIG)], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]


def test_web_fetch_on_through_the_checking_proxy():
    config = json.loads(CONFIG.read_text())
    fetch = config["tools"]["web"]["fetch"]
    assert fetch["enabled"] is True
    # Without this OpenClaw ignores HTTPS_PROXY and tries to connect directly (MC has no route out); with it the egress
    # proxy resolves and checks every hop. No fallback provider: a failed fetch must not go round the proxy.
    assert fetch["useTrustedEnvProxy"] is True
    assert "provider" not in fetch
    assert 0 < fetch["maxRedirects"] <= 5
    for name, agent in config["agents"]["entries"].items():
        assert "web_fetch" not in agent["tools"]["deny"], name
    assert "web_fetch" in config["agents"]["entries"]["mc-chat"]["tools"]["alsoAllow"]
    # Fetch is the only new door: the conversation agent still has no runtime (exec) tools.
    assert "group:runtime" in config["agents"]["entries"]["mc-chat"]["tools"]["deny"]
