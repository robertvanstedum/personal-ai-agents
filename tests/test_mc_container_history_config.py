"""#553 step 1: MC's container config keeps history and the prompt lean, in the keys OpenClaw 2026.9.6 documents
(gateway/config-agents/heartbeat-compaction-and-streaming.md, workspace-and-bootstrap.md, tools/skills.md)."""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = json.loads((REPO / "docker/mc-agent-container/openclaw.json").read_text())


def test_compaction_is_safeguard_with_a_20k_tail_and_no_unread_memory_flush():
    d = CONFIG["agents"]["defaults"]
    assert d["compaction"] == {"mode": "safeguard", "keepRecentTokens": 20000, "memoryFlush": {"enabled": False}}
    # Old tool results are trimmed only once a conversation has been idle past the cache's life.
    assert d["contextPruning"] == {"mode": "cache-ttl", "ttl": "30m"}
    assert d["skipBootstrap"] is True
    assert "contextInjection" not in d                                # never "never": the base persona stays


def test_the_gateway_models_compact_near_60k_not_at_the_window():
    script = (f"const m=require({json.dumps(str(REPO / 'docker/mc-agent-container/model-access.cjs'))});"
              "console.log(JSON.stringify(m.overlay(process.env).models.providers['minimoi-gateway'].models));")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "MC_RUNTIME_MODEL_ACCESS": "plan",
           "MC_PLAN_MODEL": "anthropic/claude-opus-5", "MC_MODEL_GATEWAY_KEY": "sk-" + "a" * 20,
           "MC_METERED_ROUTES": "minimoi-gateway/minimoi-xai-strong"}
    r = subprocess.run(["node", "-e", script], env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    (model,) = json.loads(r.stdout)
    assert model["contextWindow"] == 131072 and model["contextTokens"] == 80000 and model["maxTokens"] == 8192


def test_the_conversation_lane_keeps_its_safety_limits():
    chat = CONFIG["agents"]["entries"]["mc-chat"]["tools"]
    assert "group:runtime" in chat["deny"] and chat["fs"]["workspaceOnly"] is True
    assert CONFIG["agents"]["entries"]["mc-chat"]["memory"]["search"]["enabled"] is False
