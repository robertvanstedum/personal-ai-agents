"""#553 step 1: MC's container config keeps history and the prompt lean, in the keys OpenClaw 2026.9.6 documents
(gateway/config-agents/heartbeat-compaction-and-streaming.md, workspace-and-bootstrap.md, tools/skills.md)."""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = json.loads((REPO / "docker/mc-agent-container/openclaw.json").read_text())


def test_proactive_compaction_is_off_until_recall_exists_and_nothing_is_dropped():
    """#553 review: with no memory flush and no MiniMoi recall yet, compaction would make long chats forget silently.
    Threshold compaction is off; OpenClaw's overflow recovery (a real context-overflow error) still compacts, in safeguard
    mode with a 20k verbatim tail. It comes back once capture -> notes and recall (step 2) work."""
    d = CONFIG["agents"]["defaults"]
    assert d["compaction"] == {"enabled": False, "mode": "safeguard", "keepRecentTokens": 20000, "memoryFlush": {"enabled": False}}
    # Pruning only soft-trims OLD tool results (over 4,000 chars: first and last 1,500 kept) after 30 min idle, in the
    # in-memory context sent to the model; conversation text, images and the transcript on disk are never touched, and
    # the hard-clear step that replaces whole results is off.
    assert d["contextPruning"] == {"mode": "cache-ttl", "ttl": "30m", "hardClear": {"enabled": False}}
    assert d["skipBootstrap"] is True
    assert "contextInjection" not in d                                # never "never": the base persona stays


def test_the_gateway_models_set_no_compaction_budget():
    script = (f"const m=require({json.dumps(str(REPO / 'docker/mc-agent-container/model-access.cjs'))});"
              "console.log(JSON.stringify(m.overlay(process.env).models.providers['minimoi-gateway'].models));")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "MC_RUNTIME_MODEL_ACCESS": "plan",
           "MC_PLAN_MODEL": "anthropic/claude-opus-5", "MC_MODEL_GATEWAY_KEY": "sk-" + "a" * 20,
           "MC_METERED_ROUTES": "minimoi-gateway/minimoi-xai-strong"}
    r = subprocess.run(["node", "-e", script], env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    (model,) = json.loads(r.stdout)
    assert model["contextWindow"] == 131072 and model["maxTokens"] == 8192 and "contextTokens" not in model


def test_the_conversation_lane_keeps_its_safety_limits():
    chat = CONFIG["agents"]["entries"]["mc-chat"]["tools"]
    assert "group:runtime" in chat["deny"] and chat["fs"]["workspaceOnly"] is True
    assert CONFIG["agents"]["entries"]["mc-chat"]["memory"]["search"]["enabled"] is False


BUILT_INS_KEPT = ["read", "ls", "web_search", "view_image", "session_status"]
BUILT_INS_DROPPED = ["write", "edit", "apply_patch", "skill_workshop", "sessions", "sessions_list", "sessions_history",
                     "sessions_search", "sessions_yield", "agents_wait", "conversations_list", "conversations_send",
                     "conversations_turn", "progress_card", "create_goal", "get_goal", "update_goal", "intent", "ask_user",
                     "tts", "github_identity_status"]


def test_the_conversation_lane_runs_cos_s_minimal_pattern_with_every_minimoi_tool():
    """Robert, 12 Nov ("Yes, both"): OpenClaw's built-ins trimmed to five, every MiniMoi tool kept (no usage data yet),
    the open-card tool included. Measured on 9.6: 77 tools / 67,036 schema chars -> 57 / 40,451."""
    chat = CONFIG["agents"]["entries"]["mc-chat"]["tools"]
    minimoi = CONFIG["plugins"]["entries"]["minimoi-tools"]["config"]["tools"]
    assert chat["profile"] == "minimal"
    assert chat["alsoAllow"] == BUILT_INS_KEPT + minimoi
    assert "minimoi_open_card" in minimoi and len(minimoi) == 52
    assert not set(BUILT_INS_DROPPED) & set(chat["alsoAllow"])
    # The cards, the persona and the handbook name no dropped tool.
    texts = [p.read_text() for p in (REPO / "services/mc_operations/personas/mc-chat/releases/0.1.34").rglob("*.md")]
    texts += [p.read_text() for p in (REPO / "services/mc_operations/handbook/v0.1.3").rglob("*.md")]
    for name in BUILT_INS_DROPPED:
        if name in ("write", "edit", "intent", "sessions"):        # ordinary English words; check them as tool names
            name = f"`{name}`"
        assert not any(name in t for t in texts), name
    # The plan/agent lane is unchanged by this trim.
    assert CONFIG["agents"]["entries"]["mc-agent"]["tools"]["profile"] == "full"
