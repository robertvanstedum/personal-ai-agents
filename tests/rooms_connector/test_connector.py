"""Rooms R2 (docs/specs/minimoi-connected-work/ROOMS_R2.md): the Mac connector,
the Claude Code runner, the identity fence and the records-door sidecar.

No model and no network: the Claude CLI is tests/rooms_connector/fake_claude.py
behind a tiny wrapper per behaviour (the runner strips the environment, so the
mode is baked into each wrapper). Records is the real app on a temporary folder.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "guild" / "shop_floor"))
sys.path.insert(0, str(ROOT / "tests" / "rooms_worker"))
from rooms_helpers import RECORDS_DIR, records_app  # noqa: E402
from worker_helpers import BACKEND, Session  # noqa: E402

from services.rooms_connector import claude_runner as CR  # noqa: E402
from services.rooms_connector.claude_runner import ClaudeRunner, startup_problems  # noqa: E402
from services.rooms_worker.clients import Records  # noqa: E402
from services.rooms_worker.journal import TurnJournal  # noqa: E402
from services.rooms_worker.worker import Worker  # noqa: E402

FAKE = Path(__file__).with_name("fake_claude.py")


def _load(name, file):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RECORDS_DIR / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cli(tmp_path, mode="ok", log=None):
    path = tmp_path / f"claude-{mode}"
    lines = ["#!/bin/sh", f"FAKE_CLAUDE_MODE={mode}"]
    if log:
        lines.append(f"FAKE_CLAUDE_LOG={log}")
    lines.append(f"export FAKE_CLAUDE_MODE{' FAKE_CLAUDE_LOG' if log else ''}")
    lines.append(f'exec {sys.executable} {FAKE} "$@"')
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
    return h


@pytest.fixture(autouse=True)
def no_managed_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(CR, "MANAGED_DIR", tmp_path / "no-managed")


def runner(tmp_path, home, mode="ok", log=None, **kw):
    return ClaudeRunner(cli=cli(tmp_path, mode, log), home=str(home), state_dir=tmp_path / "state",
                        turns_root=str(tmp_path / "turns"), **kw)


MESSAGES = [{"role": "system", "content": "etiquette"}, {"role": "user", "content": "Transcript: []"},
            {"role": "user", "content": "What first?"}]
PROOF = {"id": "t-proof", "brief": {"kind": "proof"}}


def prove(r):
    """A real (fake-CLI) proof execution, then promotion: the only way to a proof record."""
    assert r.stream(MESSAGES, "u", uuid4().hex, turn=PROOF)["outcome"] == "done"
    assert r.record_proof(PROOF) is True


# ── the runner ──────────────────────────────────────────────────────────────

def test_the_command_has_no_tools_no_mcp_no_settings_a_stripped_env_and_an_empty_cwd(tmp_path, home):
    log = tmp_path / "argv.log"
    r = runner(tmp_path, home, log=log)
    out = r.stream(MESSAGES, "u", "c" * 32, turn=PROOF)
    assert out["outcome"] == "done" and out["text"] == "A short useful point from Claude Code."
    assert out["usage"] == {"prompt_tokens": 120, "completion_tokens": 14}
    call = [json.loads(line) for line in log.read_text().splitlines() if "-p" in json.loads(line)["argv"]][0]
    argv = call["argv"]
    assert argv[:12] == ["-p", "--output-format", "stream-json", "--verbose", "--tools", "", "--strict-mcp-config",
                         "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "", "--disable-slash-commands"]
    assert "--no-session-persistence" in argv and argv[argv.index("--system-prompt") + 1] == "etiquette"
    assert not any(k.startswith("ANTHROPIC") for k in call["env"])
    assert set(call["env"]) <= {"HOME", "PATH", "LANG", "USER", "LOGNAME", "TMPDIR", "CLAUDE_CODE_MAX_RETRIES", "FAKE_CLAUDE_MODE",
                                "FAKE_CLAUDE_LOG", "PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "OLDPWD"}
    assert call["cwd"].startswith(str((tmp_path / "turns").resolve())) or call["cwd"].startswith(str(tmp_path / "turns"))


@pytest.mark.parametrize("mode,detail", [("no_output", "exit_1"), ("bad_init", "runner_boundary"),
                                         ("error_result", "result_error"), ("exit_after_result", "exit_1"),
                                         ("double_init", "runner_boundary")])
def test_after_a_process_starts_anything_but_a_clean_result_is_an_error(tmp_path, home, mode, detail):
    r = runner(tmp_path, home, mode)
    out = r.stream(MESSAGES, "u", "d" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["detail"] == detail and out["text"] == ""


def test_a_rejected_sign_in_is_refused_as_signed_out_not_a_timeout(tmp_path, home):
    r = runner(tmp_path, home, "auth_rejected")
    out = r.stream(MESSAGES, "u", "e" * 32, turn=PROOF)
    assert out["outcome"] == "refused" and out["reason"] == "signed_out" and out["text"] == ""


def test_a_sign_in_rejected_after_the_model_worked_stays_uncertain(tmp_path, home):
    r = runner(tmp_path, home, "auth_late")
    out = r.stream(MESSAGES, "u", "f" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["reason"] == "signed_out_after_start" and out["text"] == ""
    assert out["usage"] == {"prompt_tokens": 100, "completion_tokens": 12}      # evidence kept, not erased


def test_output_cap_and_timeout_kill_the_process_group(tmp_path, home):
    out = runner(tmp_path, home, "huge", output_cap=100_000).stream(MESSAGES, "u", "e" * 32, turn=PROOF)
    assert (out["outcome"], out["detail"]) == ("error", "output_cap")
    started = time.time()
    out = runner(tmp_path, home, "slow", max_turn_s=1).stream(MESSAGES, "u", "f" * 32, turn=PROOF)
    assert (out["outcome"], out["detail"]) == ("error", "timeout") and time.time() - started < 15


def test_stop_during_a_turn_and_before_it_starts(tmp_path, home):
    r = runner(tmp_path, home, "slow")
    threading.Timer(0.7, lambda: r.stop("a" * 32)).start()
    out = r.stream(MESSAGES, "u", "a" * 32, turn=PROOF)
    assert out["outcome"] == "stopped"
    r2 = runner(tmp_path, home, "ok", log=tmp_path / "never.log")
    r2.stop("b" * 32)
    out = r2.stream(MESSAGES, "u", "b" * 32, turn=PROOF)
    assert out["outcome"] == "stopped" and out["detail"] == "stopped_before_start"
    assert not any("-p" in json.loads(l)["argv"] for l in (tmp_path / "never.log").read_text().splitlines())


@pytest.mark.parametrize("make", ["user_claude_md", "rules", "ancestor", "settings", "managed"])
def test_startup_inputs_refuse_before_any_process(tmp_path, home, monkeypatch, make):
    log = tmp_path / "argv.log"
    r = runner(tmp_path, home, log=log)
    if make == "user_claude_md":
        (home / ".claude" / "CLAUDE.md").write_text("private")
    elif make == "rules":
        (home / ".claude" / "rules").mkdir()
    elif make == "ancestor":
        (tmp_path / "CLAUDE.md").write_text("project instructions")      # an ancestor of the turns folder
    elif make == "settings":
        (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "x", "hooks": {}}))
    else:
        managed = tmp_path / "managed"; managed.mkdir()
        monkeypatch.setattr(CR, "MANAGED_DIR", managed)
    assert r.check(allow_unproven=True) == (False, "startup_inputs")
    out = r.stream(MESSAGES, "u", "g" * 32, turn=PROOF)
    assert out["outcome"] == "refused" and out["reason"] == "startup_inputs"
    assert not log.exists() or not any("-p" in json.loads(l)["argv"] for l in log.read_text().splitlines())


def test_an_unreadable_settings_file_fails_closed(tmp_path, home):
    (home / ".claude" / "settings.json").write_text("{not json")
    with pytest.raises(CR.StartupInputs):
        startup_problems(home, tmp_path)


def test_signed_out_is_refused_without_a_model_request(tmp_path, home):
    r = runner(tmp_path, home, "signed_out")
    assert r.check(allow_unproven=True) == (False, "signed_out")
    assert r.stream(MESSAGES, "u", "h" * 32, turn=PROOF)["outcome"] == "refused"


def test_proof_binding_to_binary_version_and_profile(tmp_path, home):
    r = runner(tmp_path, home)
    assert r.ready() is False and r.unready_reason == "not_proven_with_this_runner"
    ordinary = {"id": "t1", "brief": {"kind": "meeting"}}
    out = r.stream(MESSAGES, "u", "i" * 32, turn=ordinary)
    assert (out["outcome"], out["reason"]) == ("refused", "not_proven_with_this_runner")
    assert r.stream(MESSAGES, "u", "j" * 32, turn=PROOF)["outcome"] == "done"     # a proof turn may run
    r.record_proof({"id": "t1", "brief": {"kind": "meeting"}})                     # not a proof: ignored
    assert not r.proof_path().exists()
    r.record_proof(PROOF)
    assert r.ready() is True
    with open(r.cli, "a") as f:
        f.write("# changed\n")                                                     # a different binary
    assert r.ready() is False and r.unready_reason == "runner_changed_since_proof"


# ── through the worker and the real Records app ─────────────────────────────

@pytest.fixture
def env(tmp_path, home):
    app = records_app(tmp_path / "records")
    store = app.extensions["records_store"]
    manage = _load("records_poc_manage_for_connector_tests", "manage.py")
    mc_out, cc_out = tmp_path / "mc-out", tmp_path / "cc-out"
    manage.provision_rooms(store, "mc", "Master Craftsman", mc_out)
    store.add_principal("robert", "legacy-cc", dict(id="claude-code", label="Claude Code"))
    info = manage.provision_rooms(store, "claude-code", "Claude Code", cc_out, worker="rooms-connector-mac",
                                  revoke_legacy=True, manual=True)
    tok = lambda d, n: (d / f"{n}.token").read_text().strip()
    r = runner(tmp_path, home)
    worker = Worker(Records(BACKEND, tok(cc_out, "rooms-connector-mac"), session=Session(app)),
                    Records(BACKEND, tok(cc_out, "claude-code"), session=Session(app)), r,
                    TurnJournal(tmp_path / "journal"), teammate="claude-code", agent_id="claude-code",
                    runtime="Claude Code CLI 2.1.76")
    worker.on_committed = r.record_proof
    owner = app.test_client(use_cookies=False)
    oh = {"Authorization": "Bearer " + store.owner_key}

    def call(method, path, body=None, headers=None):
        h = dict(headers or oh)
        if method != "GET":
            h["Idempotency-Key"] = str(uuid4())
        resp = owner.open("/api/v1" + path, method=method, headers=h, json=body, base_url=BACKEND)
        return resp.status_code, (resp.json if resp.data else None)
    room = call("POST", "/rooms", dict(title="Connector", purpose="Synthetic", recording_acknowledged=True))[1]["result"]["id"]
    return SimpleNamespace(app=app, store=store, worker=worker, runner=r, call=call, room=room, info=info,
                           tok=tok, cc_out=cc_out, mc_out=mc_out)


def test_prove_then_answer_attributed_to_claude_code(env):
    status, body = env.call("POST", "/teammates/claude-code/prove", {"confirm": True})
    assert status == 202
    env.worker._last_hosted = 0
    env.worker.run_once()                                           # accept, claim the proof turn, answer
    assert env.call("GET", "/teammates/claude-code/prove")[1]["proven_at"]
    assert env.runner.proof_path().exists()                          # evidence written after Records accepted it
    env.call("POST", f"/rooms/{env.room}/invite", {"actor": "claude-code"})
    env.worker._last_hosted = 0
    env.worker.housekeeping()
    env.call("POST", f"/rooms/{env.room}/events", {"body": "@Claude what first?"})
    assert env.worker.run_once() is True
    events = env.call("GET", f"/rooms/{env.room}")[1]["events"]
    reply = [e for e in events if e["actor"] == "claude-code" and e["kind"] == "message"][-1]
    assert reply["origin"]["agent_id"] == "claude-code" and reply["origin"]["runtime"] == "Claude Code CLI 2.1.76"
    from transcript_snapshot import capture
    data, _ = capture(env.store, "robert", env.room)
    rec = [r for r in data["raw_transcript"] if r["record_id"] == reply["id"]][0]
    assert rec["agent_id"] == "claude-code" and rec["speaker_id"] == "claude-code"
    assert rec["execution"]["coordinating_installation"] == env.info["hosted"]["installation_id"]


def test_unready_reason_reaches_the_reach_badge(env):
    env.call("POST", f"/rooms/{env.room}/invite", {"actor": "claude-code"})
    env.worker.housekeeping()                                        # not proven with this runner
    mc = [p for p in env.call("GET", f"/rooms/{env.room}/turns")[1]["participants"] if p["id"] == "claude-code"][0]
    assert mc["reach"] == {"state": "away", "reason": "not yet proven on this connector — Prove first"}


def test_identity_fence_legacy_credential_and_manual_principal(env):
    legacy = env.store.add_principal  # noqa: F841 (the legacy token was revoked at provisioning)
    assert env.info["revoked_legacy_credentials"] == ["legacy:claude-code"]
    cc = {"Authorization": "Bearer " + env.tok(env.cc_out, "claude-code")}
    env.store.meetings  # card exists
    with env.store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00' WHERE principal='claude-code'")
    env.call("POST", f"/rooms/{env.room}/invite", {"actor": "claude-code"})
    env.call("POST", f"/rooms/{env.room}/rsvp", {"state": "accepted"}, headers=cc)
    assert env.call("POST", f"/rooms/{env.room}/events", {"body": "no claim"}, headers=cc)[0] == 409
    manual = {"Authorization": "Bearer " + env.tok(env.cc_out, "claude-code-manual")}
    env.store.membership("robert", "m-manual", env.room, dict(actor="claude-code-manual"))
    status, body = env.call("POST", f"/rooms/{env.room}/events", {"body": "a hand-posted note"}, headers=manual)
    assert status == 201 and body["result"]["actor"] == "claude-code-manual"


def test_workers_cannot_claim_each_others_teammates_and_recovery_is_filtered(env):
    with env.store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00'")
    for who, out in (("mc", env.mc_out), ("claude-code", env.cc_out)):
        env.call("POST", f"/rooms/{env.room}/invite", {"actor": who})
        env.call("POST", f"/rooms/{env.room}/rsvp", {"state": "accepted"},
                 headers={"Authorization": "Bearer " + env.tok(out, who)})
    env.call("POST", f"/rooms/{env.room}/events", {"body": "@MC hello"})
    status, data = env.worker.work.call("POST", "/turns/claim", {"addressees": ["mc"]}, key="x")
    assert data["turn"] is None                                         # the connector cannot claim MC's turn
    seen = []
    env.worker.recover = lambda item: seen.append(item["principal"])
    hosted = {"pending_invites": [], "uncertain_turns": [{"turn_id": "t", "principal": "mc", "prior_claim_id": "p"}]}
    env.worker.work.call = lambda *a, **k: (200, hosted) if a[1] == "/hosted-teammates" else (200, {})
    env.worker.housekeeping()
    assert seen == []                                                   # never another teammate's turn


def test_housekeeping_thread_runs_during_a_long_turn(env, monkeypatch):
    calls = []
    original = env.worker.housekeeping
    env.worker.housekeeping = lambda: calls.append(time.time())
    env.worker.start_housekeeping(every=0.1)
    time.sleep(0.6)
    env.worker.stop_housekeeping()
    env.worker.housekeeping = original
    assert len(calls) >= 3


# ── the door ────────────────────────────────────────────────────────────────

def test_the_door_forwards_only_to_its_fixed_target(monkeypatch):
    from services.records_door import door

    async def scenario():
        async def echo(reader, writer):
            data = await reader.read(100)
            writer.write(b"records:" + data)
            await writer.drain()
            writer.close()
        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        tport = target.sockets[0].getsockname()[1]
        monkeypatch.setattr(door, "TARGET_HOST", "127.0.0.1")
        monkeypatch.setattr(door, "TARGET_PORT", tport)
        front = await asyncio.start_server(door.handle, "127.0.0.1", 0)
        fport = front.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", fport)
        writer.write(b"GET /")
        await writer.drain()
        got = await asyncio.wait_for(reader.read(100), timeout=5)
        writer.close()
        target.close(); front.close()
        return got
    assert asyncio.run(scenario()) == b"records:GET /"
    text = (ROOT / "services" / "records_door" / "door.py").read_text()
    assert 'TARGET_HOST = "minimoi-records"' in text and "TARGET_PORT = 18880" in text and "environ.get(\"DOOR_TARGET" not in text



# ── Codex build review B2-01..B2-05 ─────────────────────────────────────────

def test_admission_is_asked_after_preparation_and_no_process_starts_without_it(tmp_path, home):
    log = tmp_path / "argv.log"
    r = runner(tmp_path, home, log=log)
    order = []
    original_check = r._check
    r._check = lambda allow_unproven=False: (order.append("prepare"), original_check(allow_unproven))[1]
    out = r.stream(MESSAGES, "u", "k" * 32, turn=PROOF, admit=lambda: (order.append("admit"), (False, "cancel_requested"))[1])
    assert order == ["prepare", "admit"]
    assert out == {"outcome": "not_admitted", "state": "cancel_requested", "text": "", "usage": None, "detail": "cancel_requested"}
    assert not log.exists() or not any("-p" in json.loads(l)["argv"] for l in log.read_text().splitlines())
    out = r.stream(MESSAGES, "u", "l" * 32, turn=PROOF, admit=lambda: (True, None))
    assert out["outcome"] == "done"


def test_a_slow_preparation_makes_the_worker_ask_again_before_spawning(env, monkeypatch):
    """B2-01 end to end: the first admission goes stale during a slow sign-in
    check; the runner's spawn-boundary admission asks again; one process."""
    with env.store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00' WHERE principal='claude-code'")
    prove(env.runner)
    env.call("POST", f"/rooms/{env.room}/invite", {"actor": "claude-code"})
    env.worker.housekeeping()
    env.call("POST", f"/rooms/{env.room}/events", {"body": "@Claude q"})
    asked = []
    original = env.worker.spawn_admission
    def spy(turn):
        result = original(turn)
        asked.append(result)
        return result
    monkeypatch.setattr(env.worker, "spawn_admission", spy)
    env.worker.run_once()
    assert asked and asked[-1] == (True, None)
    assert env.call("GET", f"/rooms/{env.room}/turns")[1]["turns"][0]["state"] == "committed"


def test_pause_during_preparation_starts_no_process(env, tmp_path, monkeypatch):
    with env.store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00' WHERE principal='claude-code'")
    prove(env.runner)
    env.call("POST", f"/rooms/{env.room}/invite", {"actor": "claude-code"})
    env.worker.housekeeping()
    env.call("POST", f"/rooms/{env.room}/events", {"body": "@Claude q"})
    env.worker._last_hosted = float("inf")            # no inline housekeeping in this pass
    original = env.runner._check
    def pause_then_check(allow_unproven=False):
        version = env.call("GET", f"/rooms/{env.room}")[1]["version"]
        env.call("POST", f"/rooms/{env.room}/state", {"state": "paused", "version": version, "checkpoint": "p"})
        return original(allow_unproven)
    env.runner._check = pause_then_check
    spawned = []
    real = env.runner.popen
    env.runner.popen = lambda *a, **k: (spawned.append(1), real(*a, **k))[1]
    env.worker.run_once()
    assert spawned == []
    t = env.call("GET", f"/rooms/{env.room}/turns")[1]["turns"][0]
    assert t["state"] == "cancelled" and t["stop_ack"] == "worker"


def test_output_beyond_the_cap_without_a_newline_is_cut_off(tmp_path, home):
    out = runner(tmp_path, home, "flood", output_cap=100_000).stream(MESSAGES, "u", "m" * 32, turn=PROOF)
    assert (out["outcome"], out["detail"]) == ("error", "output_cap")


def test_proof_evidence_is_the_binary_that_ran_even_if_it_changes_during_the_proof(tmp_path, home):
    r = runner(tmp_path, home)
    real = r.popen
    def popen_then_update(*a, **k):
        proc = real(*a, **k)
        with open(r.cli, "a") as f:
            f.write("# updated while the proof ran\n")
        return proc
    r.popen = popen_then_update
    assert r.stream(MESSAGES, "u", "n" * 32, turn=PROOF)["outcome"] == "done"
    assert r.record_proof(PROOF) is True
    assert r.ready() is False and r.unready_reason == "runner_changed_since_proof"


def test_no_proof_without_its_pending_evidence_and_never_from_the_current_binary(tmp_path, home):
    r = runner(tmp_path, home)
    assert r.record_proof(PROOF) is False and not r.proof_path().exists()


def test_proof_survives_a_crash_after_records_accepted_it(env):
    status, body = env.call("POST", "/teammates/claude-code/prove", {"confirm": True})
    env.worker._last_hosted = 0
    env.worker.on_committed = None                    # the connector died before writing its proof record
    env.worker.run_once()
    assert env.call("GET", "/teammates/claude-code/prove")[1]["proven_at"]
    assert not env.runner.proof_path().exists()
    assert list((env.runner.state_dir / "proof-pending").glob("*.json"))
    env.worker.on_committed = env.runner.record_proof  # restart
    env.worker.housekeeping()                          # receipt found: promoted
    assert env.runner.proof_path().exists() and env.runner.ready() is True


def test_proof_delivered_through_recovery_is_recorded(env):
    status, body = env.call("POST", "/teammates/claude-code/prove", {"confirm": True})
    env.worker.housekeeping()                          # accept the proof invitation first
    env.worker._last_hosted = float("inf")
    env.worker.mc.session.fail_next = "before"         # the reply never reached Records
    env.worker.run_once()
    with env.store.connect() as d:
        d.execute("UPDATE turns SET lease_until='2000-01-01T00:00:00+00:00'")
    env.call("GET", f"/rooms/{body['result']['proof_room']}/turns")
    env.worker.housekeeping()                          # saved reply delivered under a recovery lease
    assert env.call("GET", "/teammates/claude-code/prove")[1]["proven_at"]
    assert env.runner.proof_path().exists()


def test_connector_script_is_executable():
    assert os.access(ROOT / "scripts" / "staging" / "connector.sh", os.X_OK)
