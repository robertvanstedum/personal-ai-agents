"""The agent-memory overlay (Spec 160, M1): inert until wired, and when added it gives
cos-scheduler only the copier switch and its folders. Production never enables it."""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "docker-compose.staging-agent-memory.yml"


def test_the_overlay_touches_only_cos_scheduler_and_only_the_copier():
    doc = yaml.safe_load(OVERLAY.read_text())
    assert list(doc["services"]) == ["cos-scheduler"]
    cos = doc["services"]["cos-scheduler"]
    assert "AGENT_MEMORY_COPIER=1" in cos["environment"]
    mounts = {re.search(r":(/app/[^:]+(?::ro)?)$", m).group(1) for m in cos["volumes"]}
    assert mounts == {"/app/data/agent-memory",
                      "/app/data/agent-memory-inbox/claude-code:ro",
                      "/app/config/agent_memory_sources.json:ro"}                  # the inbox and config are read-only


def test_the_mac_inbox_and_the_config_are_mounted_read_only():
    cos = yaml.safe_load(OVERLAY.read_text())["services"]["cos-scheduler"]
    for volume in cos["volumes"]:
        if "inbox" in volume or "agent_memory_sources" in volume:
            assert volume.endswith(":ro")


def test_production_and_cos_bot_never_enable_the_copier():
    assert "AGENT_MEMORY_COPIER" not in (REPO / "docker-compose.prod.yml").read_text()
    assert "AGENT_MEMORY_COPIER" not in (REPO / "docker-compose.staging-cos-turns.yml").read_text()


def test_the_overlay_is_not_wired_into_the_staging_scripts_yet():
    lib = (REPO / "scripts" / "staging" / "lib.sh").read_text()
    assert "docker-compose.staging-agent-memory.yml" not in lib      # wiring is a separate reviewed change
