"""The local Workshop (4a): the file-first record, the reducer, the host
observer and its admission verdict, and the CLI. Model free, read only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from minimoi_portal.workshop.observer import Observation, changed, classify, health_event, observe, verdict  # noqa: E402
from minimoi_portal.workshop.record import Workshop, load_state, reduce, validate  # noqa: E402


def _ev(**over):
    base = {"workshop": "mac", "actor": "claude-code", "kind": "progress", "item": "queue:146", "text": "working"}
    base.update(over)
    return base


# ── events ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"secret": "x"}, {"kind": "launched"}, {"actor": "someone"}, {"item": "anything"}, {"text": "x" * 281},
    {"text": "token sk-abcdefghijklmnop"}, {"text": "see https://user:pass@example.com"}, {"stage": "later"},
    {"next_actor": "anyone"}, {"health": {"x": 1}},
])
def test_events_refuse_unknown_fields_bad_values_and_credentials(bad):
    with pytest.raises(ValueError):
        validate(_ev(**bad))


def test_append_is_one_line_owner_only_and_state_is_derived(tmp_path):
    ws = Workshop(str(tmp_path), "mac")
    ws.append(_ev(kind="started", item="queue:146", stage="build", next_actor="codex"))
    ws.append(_ev(kind="needs_you", item="pr:265", stage="review", text="Review slice 2", next_actor="robert"))
    ws.append(_ev(kind="next", item="spec:streaming", stage="design", text="Streaming S1"))
    assert oct(os.stat(ws.events_path).st_mode & 0o777) == "0o600"
    state = json.loads(Path(ws.state_path).read_text())
    assert [i["item"] for i in state["in_progress"]] == ["queue:146", "pr:265"]
    assert [n["item"] for n in state["needs_you"]] == ["pr:265"]
    assert [n["item"] for n in state["next"]] == ["spec:streaming"]
    assert state["items"]["queue:146"]["next_actor"] == "codex"
    ws.append(_ev(actor="robert", kind="decision", item="pr:265", text="Approved"))
    assert json.loads(Path(ws.state_path).read_text())["needs_you"] == []        # decided
    ws.append(_ev(kind="done", item="queue:146", text="merged"))
    assert "queue:146" not in [i["item"] for i in json.loads(Path(ws.state_path).read_text())["in_progress"]]


def test_a_torn_line_is_skipped_never_fatal(tmp_path):
    ws = Workshop(str(tmp_path), "mac")
    ws.append(_ev())
    with open(ws.events_path, "a") as f:
        f.write('{"v": 1, "torn')
    assert len(ws.events()) == 1 and ws.write_state()["events"] == 1


def test_load_state_is_honest_missing_unreadable_stale(tmp_path):
    assert load_state(str(tmp_path), "mac") == (None, "missing")
    ws = Workshop(str(tmp_path), "mac")
    ws.append(_ev())
    assert load_state(str(tmp_path), "mac")[1] == "stale"                        # no host reading at all
    obs = Observation(observed_at="2026-09-29T10:00:00+00:00", memory_free_pct=50, swap_used_gb=1, disk_free_gb=50,
                      clients=[], clients_known=True)
    ws.append({**health_event(obs, "mac"), "at": "2026-09-29T10:00:00+00:00"})
    assert load_state(str(tmp_path), "mac")[1] == "stale"                        # an old reading is not current
    fresh = observe(run=_fake_run())
    ws.append(health_event(fresh, "mac"))
    assert load_state(str(tmp_path), "mac")[1] == "ok"
    Path(ws.state_path).write_text("{ not json")
    assert load_state(str(tmp_path), "mac") == (None, "unreadable")


# ── the observer ──────────────────────────────────────────────────────────────

PS = """  101 01:02:03 /Applications/Claude.app/Contents/MacOS/Claude /Applications/Claude.app/Contents/MacOS/Claude
  102 00:10 /Users/x/Library/Application Support/Claude/claude-code/2.1.284/claude.app/Contents/MacOS/claude /Users/x/.../claude --resume sk-secret-token-abcdefgh
  103 05:00 /Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex codex exec
  104 01:00 /Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/1/Helpers/Codex (Service).app/Contents/MacOS/Codex (Service) x
  105 00:30 /opt/homebrew/bin/node node /usr/local/lib/node_modules/openclaw/dist/index.js gateway
  106 00:30 /opt/homebrew/bin/node node /some/other/app.js
"""


def _fake_run(ps=PS, mem="System-wide memory free percentage: 44%", swap="total = 6144.00M  used = 1024.00M  free = 5120.00M"):
    def run(cmd, timeout=5.0):
        if cmd[0] == "memory_pressure":
            return mem
        if cmd[0] == "sysctl":
            return swap
        if cmd[0] == "ps":
            if ps is None:
                raise subprocess.CalledProcessError(1, cmd)
            return ps
        raise AssertionError(cmd)
    return run


def test_the_observer_classifies_clients_and_never_keeps_arguments():
    obs = observe(run=_fake_run())
    assert sorted(c["kind"] for c in obs.clients) == ["claude-code", "codex", "openclaw"]   # not the apps' helpers
    assert obs.memory_free_pct == 44 and obs.swap_used_gb == 1.0 and obs.clients_known
    text = json.dumps(health_event(obs, "mac"))
    assert "sk-secret" not in text and "--resume" not in text and "/Users/x" not in text
    assert classify("/x/claude", "") == "claude-code" and classify("/x/Claude", "") is None


def test_the_admission_verdict_ok_tight_blocked_and_unknown():
    ok = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=1, disk_free_gb=60, clients=[], clients_known=True)
    assert verdict(ok) == {"verdict": "ok", "reasons": ["room for one run"]}
    tight = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=1, disk_free_gb=60,
                        clients=[{"kind": "claude-code", "pid": 1, "elapsed": "1"}], clients_known=True)
    assert verdict(tight)["verdict"] == "tight" and "a new run would share the host" in verdict(tight)["reasons"][0]
    blocked = Observation(observed_at="t", memory_free_pct=8, swap_used_gb=1, disk_free_gb=60, clients=[], clients_known=True)
    assert verdict(blocked)["verdict"] == "blocked" and "memory free 8%" in verdict(blocked)["reasons"][0]
    disk = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=1, disk_free_gb=9.5, clients=[], clients_known=True)
    assert verdict(disk)["verdict"] == "blocked"
    unknown = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=None, disk_free_gb=60, clients=[], clients_known=False)
    v = verdict(unknown)
    assert v["verdict"] == "unknown" and "running agent clients unknown" in v["reasons"]   # never "nothing running"
    obs = observe(run=_fake_run(ps=None, mem="garbage"))
    assert verdict(obs)["verdict"] == "unknown" and not obs.clients_known


def test_health_events_are_written_on_change_only():
    a = health_event(observe(run=_fake_run()), "mac")
    assert not changed(a["health"], health_event(observe(run=_fake_run()), "mac"))
    b = health_event(observe(run=_fake_run(ps="")), "mac")
    assert changed(a["health"], b)


def test_the_cli_records_observes_and_syncs_one_way(tmp_path):
    home, dest = tmp_path / "ws", tmp_path / "staging"
    cli = [sys.executable, str(REPO / "scripts/workshop/workshop.py"), "--home", str(home)]
    r = subprocess.run(cli + ["event", "--actor", "claude-code", "--item", "pr:265", "--kind", "needs_you",
                              "--text", "Review slice 2", "--next-actor", "robert", "--ref", "pr=265"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    bad = subprocess.run(cli + ["event", "--actor", "claude-code", "--item", "pr:265", "--kind", "needs_you",
                                "--text", "key sk-abcdefghijklmnop"], capture_output=True, text=True)
    assert bad.returncode == 2 and "credential" in bad.stderr
    assert subprocess.run(cli + ["sync", "--to", str(dest)], capture_output=True, text=True).returncode == 0
    first = (dest / "mac/events.jsonl").read_text()
    subprocess.run(cli + ["event", "--actor", "robert", "--item", "pr:265", "--kind", "decision", "--text", "OK"],
                   capture_output=True, text=True)
    subprocess.run(cli + ["sync", "--to", str(dest)], capture_output=True, text=True)
    second = (dest / "mac/events.jsonl").read_text()
    assert second.startswith(first) and second.count("\n") == 2                    # appended, not rewritten
    assert json.loads((dest / "mac/state.json").read_text())["needs_you"] == []
    assert sorted(p.name for p in (dest / "mac").iterdir()) == ["events.jsonl", "state.json"]   # only these two go


def test_the_workshop_code_never_reaches_a_model():
    for path in list((REPO / "minimoi_portal/workshop").glob("*.py")) + [REPO / "scripts/workshop/workshop.py",
                                                                       REPO / "minimoi_portal/guild_ui/workshop_view.py"]:
        text = path.read_text()
        for word in ("anthropic", "openai", "litellm", "requests", "urllib.request", "http.client", "model-gateway",
                     "mc.turn", "run_mc_turn"):
            assert word not in text, (path.name, word)


def test_the_heartbeat_keeps_a_quiet_host_fresh_on_the_page():
    import importlib.util
    from minimoi_portal.workshop.record import STALE_AFTER
    spec = importlib.util.spec_from_file_location("workshop_cli", REPO / "scripts/workshop/workshop.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.HEARTBEAT < STALE_AFTER            # an unchanged host still writes before the page calls it stale
