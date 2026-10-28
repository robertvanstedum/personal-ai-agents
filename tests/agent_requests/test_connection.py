"""The connection manager (SCAFFOLDING until a real idle-to-MC round trip proves a provider): the lifecycle rules, against test doubles only.
No real Claude or Codex process, no network, no model."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
import uuid

import pytest

from agent_fakes import CONTROLS_PRELUDE, fake_controls, given_environments
from core import agent_requests as ar
from core.agent_requests.claude_stream import ClaudeStreamProvider
from core.agent_requests.connection import (BACKOFF_S, MAX_ATTEMPTS, ConnectionManager, Delivery, IdentityMismatch, LeaseHeld, Probe, Provider,
                                            connection_notice)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(REPO, "scripts", "agent_requests.py")
CONV = "c-132bc60f078e"
SID = "11111111-2222-3333-4444-555555555555"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Fake(Provider):
    """A scripted transport: ``state`` is what probe says; ``outcomes`` is consumed by deliver (default accepted)."""
    name = "fake"

    def __init__(self, state="idle", dedupes=False):
        self.state, self.outcomes, self.delivered, self.probes, self.dedupes_by_correlation = state, [], [], 0, dedupes
        self.attention = ""
        self.session_ref = SID

    def probe(self):
        self.probes += 1
        if isinstance(self.state, Exception):
            raise self.state
        return Probe(self.state, self.session_ref, self.attention)

    def deliver(self, notice, correlation):
        self.delivered.append((notice, correlation))
        out = self.outcomes.pop(0) if self.outcomes else Delivery("accepted", "native-1")
        if isinstance(out, BaseException):
            raise out
        if out.status == "accepted":
            self.state = "busy"                              # like a real session: busy from the moment it takes a turn, until the test ends that turn
        return out

    def end_turn(self):
        self.state = "idle"


@pytest.fixture
def root(tmp_path):
    return str(tmp_path / "ledger")


def ask(root, seed="a", to="claude-code", action="Do the thing. SENTINEL-REQUEST-WORDS", **kw):
    return ar.create_request(root, request_id=ar.request_id_for(seed), frm="mc", to=to, action=action, conversation_id=kw.pop("conversation_id", CONV), **kw)[0]["request_id"]


def mgr(root, provider, clock=None, agent="claude-code", **kw):
    return ConnectionManager(root, agent, provider, session_id=kw.pop("session_id", SID), clock=clock or Clock(), **kw)


def deliveries(root, rid):
    return [(e["state"], e.get("reason", "")) for e in ar.load(root, rid)["events"] if e["kind"] == "delivery" and e["actor"] == "connection"]


# ── delivery, receipts and what accepted means ───────────────────────────────

def test_an_empty_queue_makes_no_delivery_and_no_model_call_but_marks_a_live_listener(root):
    p, m = Fake(), None
    m = mgr(root, p)
    assert m.step() == {"state": "idle", "delivered": None, "held": {}, "attention": "", "gap_s": 0, "error": ""}
    assert p.delivered == [] and p.probes == 1                                                        # one cheap probe, nothing else
    assert ar.armed_age(root, "claude-code") is not None                                              # a live session is "listening"
    m.close()


def test_an_idle_session_gets_one_notice_with_tokens_only_and_accepted_is_not_a_receipt(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    out = m.step()
    assert out["delivered"] == {"request_id": rid, "status": "accepted"}
    [(notice, correlation)] = p.delivered
    assert correlation == rid and rid in notice and "not a message or approval from Robert" in notice and "receipt" in notice and "SENTINEL-REQUEST-WORDS" not in notice
    assert deliveries(root, rid) == [("accepted", "")]
    doc = ar.load(root, rid)
    assert doc["stage"] == "queued" and ar.pending(root, "claude-code")                                # transport acceptance moved no stage
    m.close()


def test_after_the_agents_own_receipt_nothing_more_is_delivered_and_a_repeat_pass_is_a_no_op(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    assert m.step()["delivered"] is None and len(p.delivered) == 1                                    # accepted, no receipt yet: never resent
    ar.receipt(root, rid, "claude-code", session_ref=SID)
    p.end_turn()
    assert m.step()["delivered"] is None and len(p.delivered) == 1
    ar.result(root, rid, "claude-code", "done", session_ref=SID)
    assert m.step()["delivered"] is None and len(p.delivered) == 1 and ar.load(root, rid)["stage"] == "returned"
    m.close()


def test_a_busy_session_is_never_interrupted_the_request_waits_and_goes_once_after_its_turn(root):
    rid = ask(root)
    p = Fake(state="busy")
    m = mgr(root, p)
    for _ in range(3):
        assert m.step()["state"] == "busy" and p.delivered == []
    assert ar.armed_age(root, "claude-code") is not None                                              # busy is still a live session
    p.state = "idle"
    assert m.step()["delivered"]["request_id"] == rid and len(p.delivered) == 1
    m.close()


def test_requests_go_in_order_one_per_pass_and_a_stuck_one_does_not_block_the_next(root):
    a, b = ask(root, "a"), ask(root, "b")
    p = Fake()
    m = mgr(root, p)
    assert m.step()["delivered"]["request_id"] == a and m.step()["state"] == "busy"
    ar.receipt(root, a, "claude-code", session_ref=SID)                                                  # a acknowledged; its turn ends
    p.end_turn()
    assert m.step()["delivered"] == {"request_id": b, "status": "accepted"}
    assert [c for _n, c in p.delivered] == [a, b]
    m.close()


def test_a_cancelled_request_and_another_agents_request_are_not_delivered(root):
    cancelled = ask(root, "c")
    ar.cancel(root, cancelled, "mc")
    ask(root, "x", to="codex", action="for codex")
    p = Fake()
    m = mgr(root, p)
    assert m.step()["delivered"] is None and p.delivered == []
    m.close()


# ── a notice that is taken but never acknowledged becomes visible attention ───

def test_a_turn_that_ends_without_a_receipt_becomes_visible_attention_once_and_is_never_resent(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    assert m.step()["held"] == {} and deliveries(root, rid) == [("accepted", "")]                      # busy: a long turn is not a problem
    p.end_turn()                                                                                        # the session went idle without ever calling the helper
    out = m.step()
    assert out["held"] == {rid: "needs_attention"} and ("needs_attention", "turn_ended_without_receipt") in deliveries(root, rid)
    for _ in range(3):
        assert m.step()["delivered"] is None
    assert [d for d in deliveries(root, rid) if d[0] == "needs_attention"] == [("needs_attention", "turn_ended_without_receipt")]   # recorded once
    assert len(p.delivered) == 1 and ar.load(root, rid)["stage"] == "queued"
    m.close()


def test_a_provider_reason_to_look_is_recorded_against_the_request_in_flight(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    p.attention = "approval_blocked:item/commandExecution/requestApproval"
    m.step()
    assert ("needs_attention", "approval_blocked:item/commandExecution/requestApproval") in deliveries(root, rid)
    assert json.load(open(os.path.join(root, "connection", "claude-code", "status.json")))["attention"].startswith("approval_blocked")
    m.close()


def test_an_acknowledged_request_is_never_flagged_when_its_turn_ends(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    ar.receipt(root, rid, "claude-code", session_ref=SID)
    ar.result(root, rid, "claude-code", "done", session_ref=SID)
    p.end_turn()
    m.step()
    assert deliveries(root, rid) == [("accepted", "")]
    m.close()


def test_the_manager_remembers_that_the_persistent_session_was_created_so_a_restart_resumes_it(root):
    m = mgr(root, Fake())
    assert m.identity().get("created") is None
    m.mark_created()
    m.close()
    m2 = mgr(root, Fake())
    assert m2.identity()["created"] is True and m2.identity()["session_id"] == SID
    m2.close()


# ── failures, retries and the rule against blind resends ─────────────────────

def test_a_refused_or_failed_notice_is_retried_on_the_backoff_schedule_at_most_five_times(root):
    rid = ask(root)
    clock, p = Clock(), Fake()
    p.outcomes = [Delivery("failed", reason="not_running")] * 10
    m = mgr(root, p, clock)
    sent = 0
    for _ in range(40):
        if m.step()["delivered"]:
            sent += 1
        clock.advance(1)
    assert sent == 3                                                                                    # 2 s, then 10 s, then (30 s not reached in 40 s)
    for _ in range(600):
        m.step()
        clock.advance(1)
    assert len(p.delivered) == MAX_ATTEMPTS and m.step()["held"][rid] == "attempts_exhausted"          # then it stops; it never loops
    assert [s for s, _r in deliveries(root, rid)] == ["failed"] * MAX_ATTEMPTS
    m.close()


def test_a_crash_between_the_intent_and_the_outcome_is_unknown_and_never_blindly_resent(root):
    rid = ask(root)
    p = Fake()
    p.outcomes = [KeyboardInterrupt()]
    m = mgr(root, p)
    with pytest.raises(KeyboardInterrupt):                                                              # the process dies mid-delivery: the intent is on disk
        m.step()
    m.close()
    q = Fake()
    m2 = mgr(root, q)                                                                                   # restart
    out = m2.step()
    assert q.delivered == [] and out["held"] == {rid: "needs_attention"}                                   # unknown, and the session is idle with no receipt: visible, not resent
    assert ("unknown", "crash_before_outcome") in deliveries(root, rid)
    ar.receipt(root, rid, "claude-code", session_ref=SID)                                                 # the agent did get it: reconciled by its own receipt
    assert m2.step()["held"] == {} and q.delivered == []
    m2.close()


def test_a_provider_that_cannot_tell_whether_it_took_the_notice_answers_unknown_and_is_not_resent(root):
    rid = ask(root)
    p = Fake()
    p.outcomes = [Delivery("unknown", reason="write_error")]
    m = mgr(root, p)
    m.step()
    assert m.step()["held"] == {rid: "needs_attention"} and len(p.delivered) == 1                         # unknown + idle + no receipt: flagged, never resent
    m.close()


def test_a_provider_that_deduplicates_by_correlation_gets_one_resend_of_the_same_correlation(root):
    rid = ask(root)
    p = Fake(dedupes=True)
    p.outcomes = [Delivery("unknown", reason="write_error"), Delivery("accepted")]
    m = mgr(root, p)
    m.step()
    m.step()
    m.step()
    assert [c for _n, c in p.delivered] == [rid, rid] and len(p.delivered) == 2                         # one resend, same correlation, then it waits for the receipt
    m.close()


def test_an_accepted_notice_with_no_receipt_is_never_resent_however_long_it_waits(root):
    rid = ask(root)
    clock, p = Clock(), Fake()
    m = mgr(root, p, clock)
    m.step()
    for _ in range(50):
        clock.advance(3600)
        assert m.step()["delivered"] is None                                                            # still busy (a long build): waiting, never resent
    assert len(p.delivered) == 1 and deliveries(root, rid) == [("accepted", "")]
    m.close()


def test_a_provider_error_in_probe_is_unknown_and_delivers_nothing(root):
    ask(root)
    p = Fake(state=RuntimeError("boom"))
    m = mgr(root, p)
    assert m.step()["state"] == "unknown" and p.delivered == [] and ar.armed_age(root, "claude-code") is None
    m.close()


# ── offline, restart and ownership ───────────────────────────────────────────

def test_offline_means_no_listener_the_request_stays_pending_and_is_delivered_once_after_it_returns(root):
    rid = ask(root)
    p = Fake(state="offline")
    m = mgr(root, p)
    assert m.step()["state"] == "offline" and p.delivered == [] and ar.armed_age(root, "claude-code") is None
    assert [d["request"]["request_id"] for d in ar.pending(root, "claude-code")] == [rid]
    m.close()
    p2 = Fake()                                                                                         # the manager restarts with the session back
    m2 = mgr(root, p2)
    assert m2.step()["delivered"]["request_id"] == rid and m2.step()["delivered"] is None and len(p2.delivered) == 1
    m2.close()


def test_a_second_manager_for_the_same_agent_is_refused_and_the_lease_is_released_on_close(root):
    m = mgr(root, Fake())
    with pytest.raises(LeaseHeld):
        mgr(root, Fake())
    mgr(root, Fake(), agent="codex").close()                                                            # another agent is independent
    m.close()
    mgr(root, Fake()).close()


def test_the_lease_is_released_when_the_owning_process_dies(root, tmp_path):
    code = textwrap.dedent(f"""
        import sys, os, time
        sys.path.insert(0, {REPO!r})
        from core.agent_requests.connection import ConnectionManager, Provider, Probe
        class P(Provider):
            def probe(self): return Probe("idle")
        ConnectionManager({root!r}, "claude-code", P(), session_id={SID!r})
        print("held", flush=True); time.sleep(30)""")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    with pytest.raises(LeaseHeld):
        mgr(root, Fake())
    proc.kill()
    proc.wait()
    mgr(root, Fake()).close()                                                                           # the OS dropped the lock; no stale lease to clean


def test_a_different_session_is_never_started_silently_adopting_is_explicit_and_recorded(root):
    mgr(root, Fake()).close()
    other = str(uuid.uuid4())
    with pytest.raises(IdentityMismatch):
        mgr(root, Fake(), session_id=other)
    m = mgr(root, Fake(), session_id=other, adopt_identity=True)
    ident = json.load(open(os.path.join(root, "connection", "claude-code", "identity.json")))
    assert ident["session_id"] == other and ident["history"][0]["session_id"] == SID
    m.close()
    for bad in ("bad id!", "x", "../etc/passwd", ""):
        with pytest.raises(ar.Refused):
            mgr(root, Fake(), session_id=bad)
    mgr(root, Fake(), session_id=other, agent="codex").close()          # a Codex thread id is any plain token, not necessarily a UUID


def test_the_connection_folder_refuses_a_link(root, tmp_path):
    os.makedirs(root)
    os.symlink(str(tmp_path), os.path.join(root, "connection"))
    with pytest.raises(ar.Refused):
        mgr(root, Fake())


# ── the Claude stream-json provider, against a test double of the CLI ────────

FAKE_CLAUDE = CONTROLS_PRELUDE + r'''
import json, os, re, shlex, subprocess, sys, time
mode = os.environ.get("FAKE_MODE", "ack")
sid = os.environ["FAKE_SID"]
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    text = msg["message"]["content"][0]["text"]
    m = re.search(r"read it with: (.*?) · acknowledge pickup with: (.*?) · when done, report with: (.*?) <your report>", text)
    show, receipt, result = (shlex.split(x) for x in m.groups())      # the session runs EXACTLY the commands in the notice: the root comes from the notice, nothing else
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "ffffffff-0000-0000-0000-000000000000" if mode == "mismatch" else sid}), flush=True)
    if mode == "die":
        os._exit(3)
    if mode == "deny":
        print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.01, "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t1"}]}), flush=True)
        continue
    if mode == "ack":
        subprocess.run(show, check=True, capture_output=True)
        subprocess.run(receipt, check=True, capture_output=True)
        time.sleep(float(os.environ.get("FAKE_DELAY", "0.3")))
        subprocess.run(result + ["Hello from the fake session."], check=True, capture_output=True)
    _n = int(os.environ.get("FAKE_TURNS", "0")) + 1
    print(json.dumps({"type": "result", "subtype": "success", "session_id": sid, "total_cost_usd": 0.01 * _n}), flush=True)
'''


@pytest.fixture
def fake_cli(tmp_path, root, monkeypatch):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE)
    wrapper = tmp_path / "claude"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    fake_controls(tmp_path, FAKE_SID=SID)
    monkeypatch.delenv("MINIMOI_AGENT_REQUESTS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))                     # the default ledger lives under HOME: a command that fell back to it would write there
    return str(wrapper)


class LivePipe:
    """A stdout that stays open and silent, like a live child's pipe."""
    def __iter__(self):
        return self

    def __next__(self):
        time.sleep(30)
        raise StopIteration


def provider(fake_cli, tmp_path, **kw):
    return ClaudeStreamProvider(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"],
                                ledger_root=kw.pop("ledger_root", str(tmp_path / "ledger")), **kw)


def wait_for(cond, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_the_claude_command_line_is_an_exact_allowlisted_array_and_bad_settings_are_refused(fake_cli, tmp_path):
    p = provider(fake_cli, tmp_path, max_budget_usd=2.5, display_name="claude-code (persistent)")
    assert p.argv() == [fake_cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--session-id", SID,
                        "--permission-mode", "default", "--allowedTools", "Read", "--max-budget-usd", "2.5",
                        "--name", "claude-code persistent"]
    assert "--resume" in provider(fake_cli, tmp_path, resume=True).argv() and "--session-id" not in provider(fake_cli, tmp_path, resume=True).argv()
    bad = [dict(claude_bin="claude"), dict(claude_bin=str(tmp_path / "missing")), dict(session_id="nope"), dict(cwd="relative"), dict(permission_mode="bypassPermissions"),
           dict(allowed_tools=[]), dict(ledger_root="relative/ledger"), dict(allowed_tools=["Read; rm -rf /"]), dict(allowed_tools=["Bash$(x)"]), dict(allowed_tools=["Bash(python3:*)"]), dict(max_budget_usd=0), dict(max_budget_usd=500)]
    base = dict(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=str(tmp_path / "l"))
    for change in bad:
        with pytest.raises(ValueError):
            ClaudeStreamProvider(**{**base, **change})


def test_a_provider_is_never_started_by_the_manager_only_by_the_operator(fake_cli, tmp_path, root):
    ask(root)
    p = provider(fake_cli, tmp_path)
    m = mgr(root, p)
    assert m.step()["state"] == "offline" and m.step()["delivered"] is None                            # a manager alone starts nothing
    assert p._proc is None
    m.close()


def test_a_real_round_trip_through_the_stream_provider_idle_to_receipt_to_result_once(fake_cli, tmp_path, root):
    rid = ask(root)
    p = provider(fake_cli, tmp_path)
    m = mgr(root, p)
    p.start()
    assert m.step()["delivered"] == {"request_id": rid, "status": "accepted"}
    assert wait_for(lambda: ar.load(root, rid)["stage"] == "returned")
    doc = ar.load(root, rid)
    receipts = [e for e in doc["events"] if e["kind"] == "receipt"]
    results = [e for e in doc["events"] if e["kind"] == "result"]
    assert len(receipts) == 1 and len(results) == 1 and results[0]["text"] == "Hello from the fake session."
    assert wait_for(lambda: p.probe().state == "idle")                                                  # the turn ended: ready again
    assert m.step()["delivered"] is None
    p.close()
    m.close()


def test_a_busy_stream_session_queues_the_second_request_until_its_turn_ends(fake_cli, tmp_path, root, monkeypatch):
    fake_controls(tmp_path, FAKE_DELAY="1.5")
    a, b = ask(root, "a"), ask(root, "b")
    p = provider(fake_cli, tmp_path)
    m = mgr(root, p)
    p.start()
    assert m.step()["delivered"]["request_id"] == a
    assert m.step()["state"] == "busy" and m.step()["delivered"] is None                                # second request waits
    assert wait_for(lambda: p.probe().state == "idle") and wait_for(lambda: ar.load(root, a)["stage"] == "returned")
    assert m.step()["delivered"]["request_id"] == b
    assert wait_for(lambda: ar.load(root, b)["stage"] == "returned")
    p.close()
    m.close()


def test_a_session_that_dies_mid_turn_is_offline_the_request_is_not_resent_and_a_restart_continues_the_same_session(fake_cli, tmp_path, root, monkeypatch):
    fake_controls(tmp_path, FAKE_MODE="die")
    rid = ask(root)
    p = provider(fake_cli, tmp_path)
    m = mgr(root, p)
    p.start()
    assert m.step()["delivered"]["status"] == "accepted"
    assert wait_for(lambda: p.probe().state == "offline")
    assert m.step()["state"] == "offline" and m.step()["delivered"] is None                              # nobody to hand anything to
    fake_controls(tmp_path, FAKE_MODE="ack")
    p.start()                                                                                           # the operator restarts it: the SAME session id, resumed
    assert "--resume" in p.argv() and SID in p.argv()
    out = m.step()
    assert out["state"] == "idle" and out["delivered"] is None and out["held"] == {rid: "needs_attention"}   # it may have taken the first notice: never resent blindly, and now visible
    p.close()
    m.close()


def test_the_notice_written_to_the_session_is_one_json_line_with_tokens_only(fake_cli, tmp_path, root):
    sent = []

    class Spy:
        def __init__(self, *a, **k):
            self.stdin = self
            self.stdout = LivePipe()

        def write(self, s):
            sent.append(s)

        def flush(self):
            pass

        def poll(self):
            return None

        def close(self):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    p = ClaudeStreamProvider(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=root, popen=lambda *a, **k: Spy())
    rid = ask(root)
    p.start()
    m = mgr(root, p)
    m.step()
    [line] = sent
    msg = json.loads(line)
    assert line.endswith("\n") and msg["type"] == "user" and msg["message"]["role"] == "user"
    text = msg["message"]["content"][0]["text"]
    assert rid in text and "SENTINEL-REQUEST-WORDS" not in text
    p.close()
    m.close()


def test_the_operator_script_prints_the_exact_command_and_starts_nothing(fake_cli, tmp_path):
    out = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "agent_connection.py"), "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake_cli,
                          "--cwd", str(tmp_path), "--allowed-tools", "Read", "--root", str(tmp_path / "ledger"), "--print-argv"], capture_output=True, text=True, timeout=30)
    argv = json.loads(out.stdout)
    assert out.returncode == 0 and argv[0] == fake_cli and "--session-id" in argv and not os.path.exists(os.path.join(str(tmp_path), "connection"))
    bad = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "agent_connection.py"), "--agent", "claude-code", "--provider", "claude-stream", "--session-id", "nope", "--claude-bin", fake_cli,
                          "--cwd", str(tmp_path), "--allowed-tools", "Read", "--root", str(tmp_path / "ledger"), "--print-argv"], capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2 and "refused" in bad.stderr


# ── one real session per agent, and the configured identity is enforced ──────

def test_a_provider_talking_to_a_different_session_fails_closed_nothing_is_armed_or_delivered(root):
    rid = ask(root)
    p = Fake()
    p.session_ref = "some-other-session-id"
    m = mgr(root, p)
    out = m.step()
    assert out["state"] == "unknown" and out["attention"].startswith("identity_mismatch") and p.delivered == []
    assert ar.armed_age(root, "claude-code") is None                                                      # not listening: it is the wrong session
    assert json.load(open(os.path.join(root, "connection", "claude-code", "status.json")))["attention"].startswith("identity_mismatch")
    p.session_ref = SID                                                                                   # the right session is back
    assert m.step()["delivered"]["request_id"] == rid
    m.close()


def test_while_a_managed_connection_holds_the_lease_another_session_cannot_acknowledge_or_answer_as_that_agent(root):
    rid = ask(root)
    m = mgr(root, Fake())
    assert ar.connection_owner(root, "claude-code")["session_id"] == SID
    for call in (lambda: ar.receipt(root, rid, "claude-code"), lambda: ar.receipt(root, rid, "claude-code", session_ref="a-desktop-session"),
                 lambda: ar.result(root, rid, "claude-code", "x"), lambda: ar.progress(root, rid, "claude-code", "x"), lambda: ar.needs_you(root, rid, "claude-code", "x")):
        with pytest.raises(ar.Refused) as e:
            call()
        assert e.value.code == "owned_by_managed_session"
    assert ar.load(root, rid)["stage"] == "queued"
    assert ar.receipt(root, rid, "claude-code", session_ref=SID)[1]                                      # the managed session itself may
    m.close()
    assert ar.connection_owner(root, "claude-code") is None
    ar.result(root, rid, "claude-code", "after the lease was released, the legacy path works again")


def test_the_helper_cli_refuses_a_second_watcher_and_a_stranger_but_not_the_managed_session(root):
    rid = ask(root)
    m = mgr(root, Fake())

    def cli(*args):
        return subprocess.run([sys.executable, HELPER, "--root", root, *args], capture_output=True, text=True, timeout=30)
    assert cli("watch", "--as", "claude-code", "--max-seconds", "0.5").returncode == 1
    assert "owned_by_managed_session" in cli("watch", "--as", "claude-code", "--max-seconds", "0.5").stderr
    assert cli("receipt", rid, "--as", "claude-code", "--session", "desktop").returncode == 1
    assert cli("receipt", rid, "--as", "claude-code", "--session", SID).returncode == 0
    assert cli("result", rid, "--as", "claude-code", "--session", SID, "done").returncode == 0
    assert cli("watch", "--as", "codex", "--max-seconds", "0.5").returncode == 0                          # another agent is unaffected
    m.close()


def test_the_notice_tells_the_session_to_use_its_own_session_id_for_every_write(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    notice = p.delivered[0][0]
    assert f"receipt {rid} --as claude-code --session {SID}" in notice and f"result {rid} --as claude-code --session {SID}" in notice
    m.close()


# ── the ledger root is bound into every command and the child's environment ──

def test_every_helper_command_in_the_notice_uses_the_managers_own_root_and_never_the_default(root, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MINIMOI_AGENT_REQUESTS_DIR", raising=False)
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    notice = p.delivered[0][0]
    import re, shlex
    show, receipt, result = (shlex.split(x) for x in re.search(r"read it with: (.*?) · acknowledge pickup with: (.*?) · when done, report with: (.*?) <your report>", notice).groups())
    for cmd in (show, receipt, result):
        assert os.path.isabs(cmd[0]) and os.path.isabs(cmd[1]) and cmd[2:4] == ["--root", root]          # absolute interpreter, absolute helper, this ledger
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ran = [subprocess.run(c, capture_output=True, text=True, cwd=str(elsewhere), env={"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")}, timeout=30)
           for c in (show, receipt, result + ["done"])]
    assert [r.returncode for r in ran] == [0, 0, 0], [r.stderr for r in ran]
    assert ar.load(root, rid)["stage"] == "returned"
    assert not os.path.exists(tmp_path / "home" / "minimoi-staging")                                      # nothing touched the default ledger
    m.close()


def test_a_manager_refuses_relative_paths_and_there_is_no_default_ledger(root, tmp_path):
    with pytest.raises(ar.Refused):
        ConnectionManager("relative/ledger", "claude-code", Fake(), session_id=SID)
    with pytest.raises(ar.Refused):
        ConnectionManager(root, "claude-code", Fake(), session_id=SID, helper="scripts/agent_requests.py")
    with pytest.raises(ar.Refused):
        ConnectionManager(root, "claude-code", Fake(), session_id=SID, python="python3")
    with pytest.raises(ar.Refused):
        ConnectionManager(root, "claude-code", Fake(), session_id=SID, helper=str(tmp_path / "missing.py"))


def test_the_child_process_gets_the_managers_ledger_in_its_environment(fake_cli, tmp_path, root):
    seen = {}

    class Spy:
        def __init__(self, argv, **kw):
            seen.update(kw)
            self.stdin = self
            self.stdout = LivePipe()

        def poll(self):
            return None

        def close(self):
            pass

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0
    p = ClaudeStreamProvider(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=root, popen=lambda *a, **k: Spy(*a, **k))
    p.start()
    assert seen["env"]["MINIMOI_AGENT_REQUESTS_DIR"] == root and seen["cwd"] == str(tmp_path)
    p.close()


# The credentials the brief names (operations, 28 Oct). A fake value only: nothing here is a real key.
PARENT_SECRETS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_BOT_TOKEN")
FAKE_VALUE = "sk-test-not-real"
# Every name a front-door connection may give its child (the launch policy's base list, plus the connection's exact extras).
CHILD_NAMES = {"PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL", "MINIMOI_AGENT_REQUESTS_DIR", "DISABLE_AUTOUPDATER"}


class EnvSpy:
    """A stand-in for Popen that keeps the keyword arguments it was given and stays 'alive' and silent."""
    def __init__(self, seen, argv, **kw):
        seen.update(kw)
        self.stdin = self
        self.stdout = LivePipe()

    def write(self, _):
        return 0

    def flush(self):
        pass

    def poll(self):
        return None

    def close(self):
        pass

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def test_provider_keys_and_tokens_in_the_managers_environment_never_reach_the_claude_session(fake_cli, tmp_path, root, monkeypatch):
    for name in PARENT_SECRETS:
        monkeypatch.setenv(name, FAKE_VALUE)
    monkeypatch.setenv("MINIMOI_AGENT_REQUESTS_DIR", str(tmp_path / "the-global-default"))          # a ledger in the manager's own environment is not the session's
    monkeypatch.setenv("NOT_ON_THE_LIST", "harmless")                                               # not a secret, and still not passed: an allowlist, not a filter
    seen = {}
    p = ClaudeStreamProvider(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=root, popen=lambda *a, **k: EnvSpy(seen, *a, **k))
    p.start()
    env = seen["env"]
    assert not set(PARENT_SECRETS) & set(env) and FAKE_VALUE not in env.values()
    assert env["MINIMOI_AGENT_REQUESTS_DIR"] == root                                               # the connection's own ledger, never the global default
    assert env["DISABLE_AUTOUPDATER"] == "1"                                                       # the pinned CLI never replaces itself
    assert set(env) <= CHILD_NAMES and "NOT_ON_THE_LIST" not in env
    assert env["HOME"] == os.environ["HOME"] and env["PATH"] == os.environ["PATH"]                 # the sign-in and the tools are still found
    p.close()


def test_the_claude_sessions_own_extras_go_through_the_policys_refusal(fake_cli, tmp_path, root, monkeypatch):
    from core.mc_delegation_runner import launch_env
    monkeypatch.setattr(launch_env, "REVIEWED_NAMES", {})                                          # without its review, the ledger name matches ^MINIMOI_ and is refused
    seen = {}
    p = ClaudeStreamProvider(claude_bin=fake_cli, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=root, popen=lambda *a, **k: EnvSpy(seen, *a, **k))
    with pytest.raises(launch_env.EnvRefused, match="MINIMOI_AGENT_REQUESTS_DIR"):
        p.start()
    assert seen == {}                                                                               # nothing was started


def test_a_stream_session_that_reports_a_different_session_id_fails_closed_and_is_never_armed_or_resent_to(fake_cli, tmp_path, root, monkeypatch):
    fake_controls(tmp_path, FAKE_MODE="mismatch")
    a = ask(root, "a")
    p = provider(fake_cli, tmp_path, ledger_root=root)
    m = mgr(root, p)
    p.start()
    assert m.step()["delivered"]["request_id"] == a                                                       # the first notice cannot know yet
    assert wait_for(lambda: p._ref != "")
    out = m.step()
    assert out["state"] == "unknown" and out["attention"].startswith("identity_mismatch")
    assert p.deliver("x", "y").status == "refused"                                                        # nothing more is ever written to the wrong session
    assert ("needs_attention", "identity_mismatch:ffffffff-0000-0000-0000-000000000000") in deliveries(root, a)
    p.close()
    m.close()


def test_a_permission_the_session_was_denied_is_visible_attention_never_auto_approved(fake_cli, tmp_path, root, monkeypatch):
    fake_controls(tmp_path, FAKE_MODE="deny")
    a = ask(root)
    p = provider(fake_cli, tmp_path, ledger_root=root)
    m = mgr(root, p)
    p.start()
    m.step()
    assert wait_for(lambda: p.probe().attention != "")
    m.step()
    assert ("needs_attention", "permission_denied:Bash") in deliveries(root, a)
    assert "--dangerously-skip-permissions" not in p.argv() and "bypassPermissions" not in p.argv()
    p.close()
    m.close()


def test_late_events_from_an_old_process_cannot_change_the_replacement_processs_state(fake_cli, tmp_path, root):
    p = provider(fake_cli, tmp_path, ledger_root=root)

    class Old:
        stdout = iter([json.dumps({"type": "system", "subtype": "init", "session_id": "other"}) + "\n", json.dumps({"type": "result", "subtype": "success"}) + "\n"])

    class New:
        def poll(self):
            return None
    p._proc, p._busy, p._ref = New(), True, SID                                                           # the replacement is mid-turn
    p._read(Old())                                                                                        # the old process's reader finishes late
    assert p._busy is True and p._ref == SID and p.probe().state == "busy"


# ── nothing taken by the session may sit silently unfinished ─────────────────

def _flags(root, rid):
    return [r for s_, r in deliveries(root, rid) if s_ == "needs_attention"]


def test_acknowledged_but_never_answered_is_flagged_when_the_turn_ends_and_not_when_it_is_still_working(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    ar.receipt(root, rid, "claude-code", session_ref=SID)
    ar.progress(root, rid, "claude-code", "reading it", session_ref=SID)
    for _ in range(3):
        m.step()
    assert _flags(root, rid) == []                                                                        # busy and working: fine, however long
    p.end_turn()
    m.step()
    m.step()
    assert _flags(root, rid) == ["acknowledged_without_result"]                                           # once
    ar.result(root, rid, "claude-code", "finally", session_ref=SID)
    assert ar.load(root, rid)["stage"] == "returned"
    m.close()


def test_a_session_that_goes_offline_after_acknowledging_is_flagged_once_across_crash_and_restart(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()
    ar.receipt(root, rid, "claude-code", session_ref=SID)
    p.state = "offline"
    m.step()
    m.step()
    assert _flags(root, rid) == ["session_offline_before_result"]
    m.close()
    m2 = mgr(root, Fake(state="offline"))                                                                 # the manager itself restarts: still one flag
    m2.step()
    assert _flags(root, rid) == ["session_offline_before_result"]
    m2.close()


def test_a_request_waiting_on_the_owner_or_closed_is_not_flagged(root):
    waiting, closed = ask(root, "w"), ask(root, "c")
    p = Fake()
    m = mgr(root, p)
    m.step()
    ar.receipt(root, waiting, "claude-code", session_ref=SID)
    ar.needs_you(root, waiting, "claude-code", "Which branch?", session_ref=SID)
    p.end_turn()
    out = m.step()
    assert _flags(root, waiting) == []                                                                    # the agent asked the owner a question: waiting, not stuck
    assert out["delivered"]["request_id"] == closed                                                       # and the next request goes
    ar.cancel(root, closed, "mc")
    p.end_turn()
    m.step()
    assert _flags(root, closed) == []
    m.close()


def test_a_running_watcher_stops_when_a_managed_session_takes_over_mid_loop(root):
    watcher = subprocess.Popen([sys.executable, HELPER, "--root", root, "watch", "--as", "claude-code", "--interval", "0.2"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    time.sleep(0.8)
    assert watcher.poll() is None                                                                         # no managed owner yet: the legacy watcher runs
    m = mgr(root, Fake())
    assert wait_for(lambda: watcher.poll() is not None, 10)
    assert watcher.returncode == 1 and "owned_by_managed_session" in watcher.stderr.read()
    m.close()


# ── failure modes: disk, clock, sleep, crash, restart, malformed output ──────

def test_a_disk_failure_while_recording_the_intent_delivers_nothing_reports_the_error_and_recovers(root, monkeypatch):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    real = ConnectionManager._append_attempt
    calls = {"n": 0}

    def failing(self, rec):
        calls["n"] += 1
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(ConnectionManager, "_append_attempt", failing)
    out = m.step()
    assert out["error"] == "OSError" and out["delivered"] is None and p.delivered == []                  # no intent on disk: nothing was handed to the session
    assert json.load(open(os.path.join(root, "connection", "claude-code", "status.json")))["error"] == "OSError"
    monkeypatch.setattr(ConnectionManager, "_append_attempt", real)
    assert m.step()["delivered"]["request_id"] == rid and len(p.delivered) == 1                           # the disk is back: exactly one delivery
    m.close()


def test_a_disk_failure_after_the_delivery_is_never_resent_it_reads_as_unknown_on_the_next_pass(root, monkeypatch):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    real = ConnectionManager._append_attempt

    def flaky(self, rec):
        if rec["status"] != "dispatching":
            raise OSError(28, "No space left on device")                                                  # the intent was written; the outcome cannot be
        return real(self, rec)
    monkeypatch.setattr(ConnectionManager, "_append_attempt", flaky)
    m.step()
    monkeypatch.setattr(ConnectionManager, "_append_attempt", real)
    m.close()
    q = Fake()
    m2 = mgr(root, q)
    out = m2.step()
    assert q.delivered == [] and out["held"] == {rid: "needs_attention"}
    assert ("unknown", "crash_before_outcome") in deliveries(root, rid)
    m2.close()


def test_a_clock_set_back_or_a_long_sleep_gap_neither_stalls_retries_nor_replays_anything(root):
    rid = ask(root)
    clock, p = Clock(), Fake()
    p.outcomes = [Delivery("failed", reason="not_running")]
    m = mgr(root, p, clock)
    m.step()
    clock.advance(-86_400)                                                                                # the wall clock is set back a day
    assert m.step()["held"] == {rid: "backoff"}
    clock.advance(3)
    assert m.step()["delivered"]["request_id"] == rid                                                     # retried within seconds, not after a day
    clock.advance(8 * 3600)                                                                               # the host slept eight hours
    out = m.step()
    assert out["gap_s"] >= 8 * 3600 - 1 and out["delivered"] is None and len(p.delivered) == 2          # the gap is reported; nothing is replayed
    assert m.step()["gap_s"] == 0
    m.close()


def test_the_loop_survives_errors_with_bounded_backoff_and_ends_only_when_told(root, monkeypatch):
    import threading
    ask(root)
    p = Fake()
    m = mgr(root, p)
    stop = threading.Event()
    seen = []
    waits = []

    class Stop:
        def is_set(self):
            return stop.is_set()

        def wait(self, delay):
            waits.append(delay)
            if len(waits) >= 8:
                stop.set()

    real = m._step
    monkeypatch.setattr(m, "_step", lambda: (_ for _ in ()).throw(OSError("disk")))
    from core.agent_requests.connection import run_forever
    run_forever(m, Stop(), interval=1.0, max_backoff=8.0, on_pass=seen.append)
    assert len(seen) == 8 and all(o["error"] == "OSError" for o in seen)
    assert waits == [2.0, 4.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0]                                              # bounded exponential backoff
    stop.clear()
    monkeypatch.setattr(m, "_step", real)
    waits.clear()
    run_forever(m, Stop(), interval=1.0, max_backoff=8.0, on_pass=seen.append)
    assert seen[-1]["error"] == "" and waits[0] == 1.0                                                    # recovered: back to the normal interval
    m.close()


def test_a_provider_that_raises_in_probe_or_deliver_never_crashes_the_pass_or_loses_the_request(root):
    rid = ask(root)
    p = Fake()
    p.outcomes = [RuntimeError("boom")]
    m = mgr(root, p)
    out = m.step()
    assert out["delivered"] == {"request_id": rid, "status": "unknown"} and out["error"] == ""
    assert ("unknown", "provider_error:RuntimeError") in deliveries(root, rid)
    assert ar.pending(root, "claude-code")                                                                # still queued: never lost
    m.close()


def _attempts_file(root):
    return os.path.join(root, "connection", "claude-code", "attempts.jsonl")


def test_a_torn_final_line_is_safe_but_a_damaged_line_inside_the_history_blocks_everything_never_a_replay(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()                                                                                              # accepted: on disk
    m.close()
    with open(_attempts_file(root), "a") as fh:
        fh.write('{"request_id": "' + rid + '", "attempt_id": "a-x"')                                    # a torn last line: a crash mid-write
    q = Fake()
    m2 = mgr(root, q)
    p2 = m2.step()
    assert p2["error"] == "" and q.delivered == [] and p2["held"] == {rid: "needs_attention"}            # still never resent (and visible: idle, no receipt)
    m2.close()
    lines = open(_attempts_file(root)).read().split("\n")
    lines.insert(1, "this line is damaged")                                                              # damage INSIDE the history
    open(_attempts_file(root), "w").write("\n".join(lines) + "\n")
    r = Fake()
    m3 = mgr(root, r)
    out = m3.step()
    assert out["error"].startswith("blocked:") and "damaged line" in out["error"] and r.delivered == []  # fail closed: nothing delivered on an unreadable history
    assert out["delivered"] is None and ar.pending(root, "claude-code")                                  # and the request is still queued, not lost
    m3.close()


def test_an_invalid_record_or_an_unreadable_or_oversized_history_blocks_and_nothing_is_guessed(root, monkeypatch):
    ask(root)
    m = mgr(root, Fake())
    open(_attempts_file(root), "w").write(json.dumps({"request_id": "bad", "status": "accepted"}) + "\n")
    assert m.step()["error"].startswith("blocked:") and "invalid record" in m.step()["error"]
    open(_attempts_file(root), "w").write("")
    assert m.step()["error"] == ""                                                                        # empty is fine: nothing was ever attempted
    os.chmod(_attempts_file(root), 0)
    try:
        out = m.step()
        assert out["error"].startswith("blocked:") and "could not be read" in out["error"] and out["delivered"] is None
    finally:
        os.chmod(_attempts_file(root), 0o600)
    import core.agent_requests.connection as C
    monkeypatch.setattr(C, "MAX_HISTORY_BYTES", 10)
    open(_attempts_file(root), "w").write("x" * 50)
    assert m.step()["error"].startswith("blocked:") and "too large" in m.step()["error"]                 # never a prefix: it says so
    m.close()


def test_the_whole_history_is_read_even_when_it_is_long_so_an_old_accepted_attempt_is_never_forgotten(root):
    rid = ask(root)
    p = Fake()
    m = mgr(root, p)
    m.step()                                                                                              # the accepted attempt is the FIRST record
    other = ask(root, "z")
    pad = "".join(json.dumps({"request_id": other, "attempt_id": f"a-{i}", "at": 1.0, "status": "refused", "reason": "pad" * 40}) + "\n" for i in range(60_000))
    with open(_attempts_file(root), "a") as fh:
        fh.write(pad)
    assert os.path.getsize(_attempts_file(root)) > 8 * 1024 * 1024                                       # bigger than the old 8 MiB prefix
    p.end_turn()
    out = m.step()
    assert rid in out["held"] and len([c for _n, c in p.delivered if c == rid]) == 1                      # the old accepted attempt is still seen: no replay
    m.close()


def test_a_corrupt_or_unreadable_identity_is_never_overwritten_the_manager_refuses_to_start(root):
    mgr(root, Fake()).close()
    ident = os.path.join(root, "connection", "claude-code", "identity.json")
    open(ident, "w").write("{broken")
    from core.agent_requests.connection import StateUnreadable
    with pytest.raises(StateUnreadable):
        mgr(root, Fake())
    assert open(ident).read() == "{broken"                                                                # untouched: the evidence is kept
    open(ident, "w").write("[1, 2]")
    with pytest.raises(StateUnreadable):
        mgr(root, Fake())
    os.remove(ident)
    mgr(root, Fake()).close()                                                                             # only a MISSING identity means new


def test_a_status_file_that_is_corrupt_or_missing_does_not_stop_the_manager(root):
    ask(root)
    m = mgr(root, Fake())
    open(os.path.join(root, "connection", "claude-code", "status.json"), "w").write("{broken")
    assert m.step()["error"] == "" and json.load(open(os.path.join(root, "connection", "claude-code", "status.json")))["state"] == "idle"
    m.close()


# ── the Claude reader: unexpected shapes and a closed transport ──────────────

def test_claude_events_of_an_unexpected_shape_never_kill_the_reader_and_a_closed_transport_is_offline(fake_cli, tmp_path, root):
    p = provider(fake_cli, tmp_path, ledger_root=root)

    class Proc:
        def __init__(self, lines):
            self.stdout, self.stdin = iter(lines), self

        def poll(self):
            return None

        def write(self, s):
            pass

        def flush(self):
            pass
    odd = [json.dumps(x) + "\n" for x in ([], "x", None, 7, {"type": ["result"]}, {"type": "result", "permission_denials": 5}, {"type": "result", "permission_denials": [1, "x", None]},
                                          {"type": "system", "subtype": "init", "session_id": ["x"]}, {"type": "result", "is_error": True, "subtype": ["x"]})]
    proc = Proc(odd + [json.dumps({"type": "system", "subtype": "init", "session_id": SID}) + "\n"])
    p._proc = proc
    p._read(proc)                                                                                         # nothing raised; the valid init at the END was still read
    assert p._ref == SID
    assert p.probe().state == "offline" and p.probe().attention == "transport_closed"                    # the stream ended with the process alive: not a healthy idle
    assert p.deliver("x", "y").reason == "not_running"                                                    # nothing is written to a closed transport


def test_a_closed_claude_transport_is_replaced_by_a_clean_restart(fake_cli, tmp_path, root):
    p = provider(fake_cli, tmp_path, ledger_root=root)
    p.start()
    first = p._proc
    p._closed = True                                                                                      # as the reader would have marked it
    p.start()                                                                                             # the supervisor restarts: the old process is ended first
    assert p._proc is not first and first.poll() is not None and p.probe().state == "idle" and p._closed is False
    p.close()
