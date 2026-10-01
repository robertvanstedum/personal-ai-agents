"""The local Workshop (4a): the file-first record, the reducer, the host
observer and its admission verdict, and the CLI. Model free, read only."""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from minimoi_portal.workshop.observer import (Observation, changed, classify, health_event,  # noqa: E402
                                              limits_text, observe, verdict)
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
    fresh = observe(disk_usage=_disk, run=_fake_run())
    ws.append(health_event(fresh, "mac"))
    assert load_state(str(tmp_path), "mac")[1] == "ok"
    Path(ws.state_path).write_text("{ not json")
    assert load_state(str(tmp_path), "mac") == (None, "unreadable")


def test_a_reading_dated_ahead_of_this_clock_is_not_current(tmp_path):
    """PR #289 review F4: the Mac's and the server's clocks can disagree; a
    reading dated more than two minutes ahead is never "fresh"."""
    from datetime import datetime, timedelta, timezone
    ws = Workshop(str(tmp_path), "mac")
    for ahead, status in ((timedelta(minutes=1), "ok"), (timedelta(hours=3), "stale")):
        at = (datetime.now(timezone.utc) + ahead).isoformat(timespec="seconds")
        obs = Observation(observed_at=at, memory_free_pct=50, swap_used_gb=1, disk_free_gb=50, clients=[],
                          clients_known=True)
        ws.append({**health_event(obs, "mac"), "at": at})
        assert load_state(str(tmp_path), "mac")[1] == status, ahead


# ── the observer ──────────────────────────────────────────────────────────────
# Real macOS shapes: `ps -axo pid=,ppid=,etime=,comm=` puts the full executable
# path LAST (with the old order macOS truncated it to 16 characters). Captured
# from this Mac on 2026-09-29 and sanitized: executable paths and parents only,
# the home folder replaced, no arguments. Node scripts are stand-in install paths.

MAC_DESKTOP = """17958     1 02:49:14 /Users/USER/.nvm/versions/node/v24.18.0/bin/node
48963     1 05:24:46 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT
48972 48963 05:24:45 /Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/154.0.8037.57/Helpers/Codex (Service).app/Contents/MacOS/Codex (Service)
49328 48963 05:24:40 /Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex
49346 48963 05:24:40 /Applications/ChatGPT.app/Contents/Frameworks/Codex Framework.framework/Versions/154.0.8037.57/Helpers/Codex (Renderer).app/Contents/MacOS/Codex (Renderer)
49374 48963 05:24:37 /Users/USER/.codex/computer-use/Codex Computer Use.app/Contents/MacOS/SkyComputerUseService
60297 49328 03:34:40 /Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex-code-mode-host
61396 49328 03:28:11 /Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node
61397 49328 03:28:11 /Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node
60417     1 03:29:08 /Applications/Claude.app/Contents/MacOS/Claude
60440 60417 03:29:04 /Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper
60950 60417 03:28:45 /Applications/Claude.app/Contents/Helpers/disclaimer
60951 60950 03:28:45 /Users/USER/Library/Application Support/Claude/claude-code/2.1.284/claude.app/Contents/MacOS/claude
50127 50095 05:08:44 /Applications/Claude.app/Contents/Helpers/chrome-native-host
"""
MAC_DESKTOP_SCRIPTS = {17958: "/Users/USER/.nvm/versions/node/v24.18.0/lib/node_modules/openclaw/dist/index.js",
                       61396: "/Applications/ChatGPT.app/Contents/Resources/cua/server.mjs",
                       61397: "/Applications/ChatGPT.app/Contents/Resources/cua/cua-repl.mjs"}

TERMINALS = """  300   299 00:10:00 claude
  301   300 00:02:00 claude
  310     1 00:20:00 /Users/USER/.local/share/claude/versions/2.1.284
  320   319 00:30:00 /opt/homebrew/bin/node
  330   329 00:40:00 node
  331   330 00:40:00 /Users/USER/.codex/packages/standalone/current/bin/codex
  340     1 00:50:00 codex
  350     1 01:00:00 /opt/homebrew/bin/node
"""
TERMINAL_SCRIPTS = {320: "/opt/homebrew/bin/claude", 330: "/opt/homebrew/bin/codex",
                    350: "/opt/homebrew/lib/node_modules/some-other-tool/index.js"}


def _fake_run(ps=MAC_DESKTOP, scripts=MAC_DESKTOP_SCRIPTS, mem="System-wide memory free percentage: 44%",
              swap="total = 6144.00M  used = 1024.00M  free = 5120.00M", asked=None):
    def run(cmd, timeout=5.0):
        if cmd[0] == "memory_pressure":
            return mem
        if cmd[0] == "sysctl":
            return swap
        if cmd[:2] == ["ps", "-axo"]:
            assert cmd[2] == "pid=,ppid=,etime=,comm=", cmd       # comm last: never truncated
            if ps is None:
                raise subprocess.CalledProcessError(1, cmd)
            return ps
        if cmd[:2] == ["ps", "-o"]:
            assert cmd[2] == "pid=,args="
            pids = [int(p) for p in cmd[4].split(",")]
            if asked is not None:
                asked.extend(pids)
            # the real args carry flags and tokens; the observer must keep none of them
            return "\n".join(f"{p} node {scripts[p]} --token sk-secret-abcdefghijkl --cwd /Users/USER/private"
                             for p in pids if p in scripts)
        raise AssertionError(cmd)
    return run


def _disk(path):
    return shutil._ntuple_diskusage(500 * 1024 ** 3, 420 * 1024 ** 3, 80 * 1024 ** 3)


def _kinds(obs, counted=None):
    return sorted(c["kind"] for c in obs.clients if counted is None or c["counted"] == counted)


def test_this_macs_desktop_setup_is_one_session_plus_labelled_background_apps():
    asked = []
    obs = observe(disk_usage=_disk, run=_fake_run(asked=asked))
    assert _kinds(obs, counted=True) == ["claude-code"]                  # disclaimer + claude: one session
    assert _kinds(obs, counted=False) == ["codex-desktop", "openclaw"]   # the ChatGPT app's Codex; the gateway
    assert sorted(asked) == [17958, 61396, 61397]                        # args only for node processes
    v = verdict(obs)
    assert v["verdict"] == "tight"
    assert v["reasons"] == ["1 agent session running (claude-code; tight at 1): a new run would share the host"]
    labels = {c["kind"]: c["label"] for c in obs.clients}
    assert labels["codex-desktop"] == "Codex desktop (ChatGPT app)" and labels["openclaw"] == "OpenClaw (background)"


def test_terminal_clis_native_and_npm_are_found_and_children_collapse():
    obs = observe(disk_usage=_disk, run=_fake_run(ps=TERMINALS, scripts=TERMINAL_SCRIPTS))
    found = {c["pid"]: c["kind"] for c in obs.clients}
    assert found == {300: "claude-code", 310: "claude-code", 320: "claude-code", 330: "codex", 340: "codex"}
    assert 301 not in found and 331 not in found                          # a child of the same client is its session
    v = verdict(obs)
    assert v["verdict"] == "blocked" and v["reasons"][0] == "5 agent sessions running (claude-code, codex; blocked at 3)"


def test_arguments_are_never_kept():
    for ps, scripts in ((MAC_DESKTOP, MAC_DESKTOP_SCRIPTS), (TERMINALS, TERMINAL_SCRIPTS)):
        obs = observe(disk_usage=_disk, run=_fake_run(ps=ps, scripts=scripts))
        text = json.dumps(health_event(obs, "mac")) + json.dumps(obs.clients)
        for leak in ("sk-secret", "--token", "--cwd", "private", "node_modules", "/opt/homebrew/bin/claude", "USER"):
            assert leak not in text, leak


def test_classify_by_executable_not_by_a_path_in_the_arguments():
    assert classify("/Applications/Claude.app/Contents/Helpers/disclaimer") is None
    assert classify("/Applications/Claude.app/Contents/MacOS/Claude") is None
    assert classify("claude") == "claude-code" and classify("/x/claude/versions/2.1.3") == "claude-code"
    assert classify("/Users/USER/.codex/packages/standalone/current/bin/codex") == "codex"
    assert classify("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex") == "codex-desktop"
    assert classify("node", "/x/lib/node_modules/@anthropic-ai/claude-code/cli.js") == "claude-code"
    assert classify("node", "/x/lib/node_modules/@openai/codex/bin/codex.js") == "codex"
    assert classify("node", "/x/server.mjs") is None and classify("node") is None


def test_the_admission_verdict_ok_tight_blocked_and_unknown_names_its_limits():
    ok = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=1, disk_free_gb=60, clients=[], clients_known=True)
    assert verdict(ok) == {"verdict": "ok", "reasons": [
        "room for one run (memory free 20% or more, swap 3 GB or less, disk free 20 GB or more, no agent session running)"]}
    bg = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=1, disk_free_gb=60, clients_known=True,
                     clients=[{"kind": "codex-desktop", "pid": 1, "elapsed": "1", "counted": False}])
    assert verdict(bg)["verdict"] == "ok"                                   # a background app is not a build session
    blocked = Observation(observed_at="t", memory_free_pct=8, swap_used_gb=1, disk_free_gb=60, clients=[], clients_known=True)
    assert verdict(blocked)["verdict"] == "blocked" and "memory free 8% (under 10%)" in verdict(blocked)["reasons"][0]
    disk = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=1, disk_free_gb=9.5, clients=[], clients_known=True)
    assert verdict(disk)["verdict"] == "blocked"
    unknown = Observation(observed_at="t", memory_free_pct=50, swap_used_gb=None, disk_free_gb=60, clients=[], clients_known=False)
    v = verdict(unknown)
    assert v["verdict"] == "unknown" and "running agent sessions unknown" in v["reasons"]   # never "nothing running"
    obs = observe(disk_usage=_disk, run=_fake_run(ps=None, mem="garbage"))
    assert verdict(obs)["verdict"] == "unknown" and not obs.clients_known
    assert limits_text() == ("Limits: memory free tight under 20%, blocked under 10% · swap used tight over 3 GB, "
                             "blocked over 6 GB · disk free tight under 20 GB, blocked under 10 GB · agent sessions "
                             "tight at 1, blocked at 3")


def test_health_events_are_written_on_change_only():
    a = health_event(observe(disk_usage=_disk, run=_fake_run()), "mac")
    assert not changed(a["health"], health_event(observe(disk_usage=_disk, run=_fake_run()), "mac"))
    b = health_event(observe(disk_usage=_disk, run=_fake_run(ps="", scripts={})), "mac")
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
    banned = ("anthropic", "openai", "litellm", "requests", "httpx", "urllib", "http", "socket", "aiohttp")
    for path in list((REPO / "minimoi_portal/workshop").glob("*.py")) + [REPO / "scripts/workshop/workshop.py",
                                                                       REPO / "minimoi_portal/guild_ui/workshop_view.py"]:
        text = path.read_text()
        for node in ast.walk(ast.parse(text)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert name.split(".")[0] not in banned, (path.name, name)
        for word in ("model-gateway", "run_mc_turn", "/mc/turns", "api_key"):
            assert word not in text, (path.name, word)


def test_the_heartbeat_keeps_a_quiet_host_fresh_on_the_page():
    import importlib.util
    from minimoi_portal.workshop.record import STALE_AFTER
    spec = importlib.util.spec_from_file_location("workshop_cli", REPO / "scripts/workshop/workshop.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.HEARTBEAT < STALE_AFTER            # an unchanged host still writes before the page calls it stale
