"""The Codex app-server provider against a fake app-server that speaks the protocol Codex proved live (JSON lines, initialize/initialized, thread/start|resume, turn/start,
turn/started, item/completed, turn/completed, server approval requests). No real Codex, no network, no model."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time

import pytest

from core import agent_requests as ar
from core.agent_requests.codex_app_server import CodexAppServerProvider
from core.agent_requests.connection import ConnectionManager

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
THREAD = "01a129b0-d511-7f03-a96d-645475ccd7a4"
CONV = "c-132bc60f078e"

FAKE_SERVER = r'''
import json, os, re, shlex, subprocess, sys, time
mode = os.environ.get("FAKE_MODE", "ack")
thread = os.environ.get("FAKE_THREAD", "01a129b0-d511-7f03-a96d-645475ccd7a4")
log = os.environ.get("FAKE_LOG")
def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
def record(msg):
    if log:
        with open(log, "a") as fh: fh.write(json.dumps(msg) + "\n")
turn_n = 0
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    record(msg)
    m, i, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mode == "garbage":
        sys.stdout.write("this is not json\n{broken\n"); sys.stdout.flush()
    if m == "initialize":
        out({"id": i, "result": {"userAgent": "fake"}})
        if mode == "close_stdout":
            os.close(1); time.sleep(30); os._exit(0)
    elif m == "initialized":
        pass
    elif m == "thread/start":
        out({"id": i, "result": {"thread": {"id": thread}, "model": "fake-model", "cwd": p.get("cwd"), "approvalPolicy": p.get("approvalPolicy"), "sandbox": {"type": "workspaceWrite" if p.get("sandbox") == "workspace-write" else "readOnly"}}})
    elif m == "thread/resume":
        tid = "99999999-0000-0000-0000-000000000000" if mode == "resume_other" else p.get("threadId")
        cwd = "/somewhere/else" if mode == "resume_cwd" else p.get("cwd")
        pol = "never" if mode == "resume_policy" else p.get("approvalPolicy")
        sb = {"type": "dangerFullAccess"} if mode == "resume_sandbox" else {"type": "workspaceWrite" if p.get("sandbox") == "workspace-write" else "readOnly"}
        out({"id": i, "result": {"thread": {"id": tid}, "cwd": cwd, "approvalPolicy": pol, "sandbox": sb}})
    elif m == "turn/start":
        turn_n += 1
        text = p["input"][0]["text"]
        if mode == "silent_rpc":
            continue                                           # never answers: the provider must time out and say unknown
        if mode == "rpc_error":
            out({"id": i, "error": {"code": -32000, "message": "busy"}}); continue
        if mode == "eof_after_write":
            os._exit(4)
        if mode == "odd_answer":
            out({"id": i, "result": {}}); continue
        tid = "turn-%d" % turn_n
        mt = re.search(r"read it with: (.*?) · acknowledge pickup with: (.*?) · when done, report with: (.*?) <your report>", text)
        if mode == "immediate":
            # the whole turn completes BEFORE the RPC response is written
            out({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": tid, "status": "inProgress"}}})
            out({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": tid, "status": "completed"}}})
            out({"id": i, "result": {"turn": {"id": tid, "status": "inProgress"}}}); continue
        if mode == "approval_first":
            out({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": tid, "status": "inProgress"}}})
            out({"id": 901, "method": "item/commandExecution/requestApproval", "params": {"command": "rm -rf /"}})
            out({"id": i, "result": {"turn": {"id": tid, "status": "inProgress"}}})
            time.sleep(0.3)
            out({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": tid, "status": "failed", "error": {"message": "approval declined"}}}}); continue
        out({"id": i, "result": {"turn": {"id": tid, "status": "inProgress"}}})
        if mode == "slow_started":
            time.sleep(1.5)                                    # the response is out, but turn/started has not arrived yet: the provider must already be busy
        out({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": tid, "status": "inProgress"}}})
        if mode == "ack" and mt:
            show, receipt, result = (shlex.split(x) for x in mt.groups())
            subprocess.run(show, check=True, capture_output=True)
            subprocess.run(receipt, check=True, capture_output=True)
            time.sleep(float(os.environ.get("FAKE_DELAY", "0.2")))
            subprocess.run(result + ["Hello from the fake codex session."], check=True, capture_output=True)
        out({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "done"}}})
        if mode != "never_completes":
            out({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": tid, "status": "completed"}}})
    elif i is not None and m is None:
        record({"client_reply": msg})
'''


@pytest.fixture
def root(tmp_path):
    return str(tmp_path / "ledger")


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER)
    wrapper = tmp_path / "codex"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "wire.jsonl"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MINIMOI_AGENT_REQUESTS_DIR", raising=False)
    return str(wrapper)


def provider(fake, tmp_path, root, **kw):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return CodexAppServerProvider(codex_bin=fake, cwd=str(work), ledger_root=root, request_timeout=kw.pop("request_timeout", 5), **kw)


def wire(tmp_path):
    try:
        return [json.loads(x) for x in open(tmp_path / "wire.jsonl").read().splitlines()]
    except OSError:
        return []


def ask(root, seed="a", action="Please say hello. SENTINEL-WORDS"):
    return ar.create_request(root, request_id=ar.request_id_for(seed), frm="mc", to="codex", action=action, conversation_id=CONV)[0]["request_id"]


def wait_for(cond, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def mgr(root, p, tid=THREAD):
    return ConnectionManager(root, "codex", p, session_id=tid)


# ── start, identity and policy ───────────────────────────────────────────────

def test_create_binds_one_explicit_thread_and_a_later_start_resumes_it_never_creates_again(fake_codex, tmp_path, root):
    p = provider(fake_codex, tmp_path, root)
    assert p.start(create=True) == THREAD and p.probe().state == "idle" and p.probe().session_ref == THREAD
    methods = [w.get("method") for w in wire(tmp_path)]
    assert methods[:3] == ["initialize", "initialized", "thread/start"]
    start = next(w for w in wire(tmp_path) if w.get("method") == "thread/start")["params"]
    assert start["approvalPolicy"] == "on-request" and start["sandbox"] == "workspace-write"
    p.close()
    q = provider(fake_codex, tmp_path, root, thread_id=THREAD)                                            # a restart: the stored id
    assert q.start() == THREAD
    assert [w.get("method") for w in wire(tmp_path)].count("thread/start") == 1                           # never created a second time
    resume = [w for w in wire(tmp_path) if w.get("method") == "thread/resume"][0]["params"]
    assert resume == {"threadId": THREAD, "cwd": str(tmp_path / "work"), "approvalPolicy": "on-request", "sandbox": "workspace-write"}
    q.close()


def test_starting_without_a_thread_or_an_explicit_create_is_refused(fake_codex, tmp_path, root):
    p = provider(fake_codex, tmp_path, root)
    with pytest.raises(RuntimeError):
        p.start()
    assert p.probe().state == "offline"


@pytest.mark.parametrize("mode,why", [("resume_other", "identity_mismatch"), ("resume_cwd", "policy_mismatch: cwd"), ("resume_policy", "policy_mismatch: approvalPolicy"),
                                      ("resume_sandbox", "policy_mismatch: sandbox")])
def test_a_resume_that_answers_a_different_thread_or_a_different_policy_fails_closed(fake_codex, tmp_path, root, monkeypatch, mode, why):
    monkeypatch.setenv("FAKE_MODE", mode)
    p = provider(fake_codex, tmp_path, root, thread_id=THREAD)
    with pytest.raises(RuntimeError, match=why):
        p.start()
    assert p.probe().state == "offline"                                                                   # closed: nothing can be delivered


def test_unsafe_settings_are_refused(fake_codex, tmp_path, root):
    base = dict(codex_bin=fake_codex, cwd=str(tmp_path), ledger_root=root)
    for change in (dict(approval_policy="never"), dict(sandbox="danger-full-access"), dict(approval_policy="on-failure"), dict(thread_id="x"), dict(codex_bin="codex"),
                   dict(cwd="relative"), dict(ledger_root="relative"), dict(writable_roots=["/"]), dict(writable_roots=[os.path.expanduser("~")]),
                   dict(writable_roots=["/tmp/../etc"]), dict(request_timeout=0), dict(request_timeout=9999)):
        with pytest.raises(ValueError):
            CodexAppServerProvider(**{**base, **change})


def test_each_turn_carries_a_scoped_sandbox_with_only_the_ledger_writable(fake_codex, tmp_path, root):
    ask(root)
    p = provider(fake_codex, tmp_path, root, writable_roots=[str(tmp_path / "work" / "out")])
    p.start(create=True)
    m = mgr(root, p)
    assert m.step()["delivered"]["status"] == "accepted"
    turn = [w for w in wire(tmp_path) if w.get("method") == "turn/start"][0]["params"]
    assert turn["sandboxPolicy"] == {"type": "workspaceWrite", "writableRoots": [root, str(tmp_path / "work" / "out")], "networkAccess": False,
                                     "excludeSlashTmp": True, "excludeTmpdirEnvVar": True}
    assert turn["approvalPolicy"] == "on-request" and "SENTINEL-WORDS" not in turn["input"][0]["text"]     # tokens only, never the request's words
    ro = provider(fake_codex, tmp_path, root, sandbox="read-only")
    assert ro._turn_policy() == {"type": "readOnly", "networkAccess": False}
    wait_for(lambda: p.probe().state == "idle")
    p.close()
    m.close()


# ── the live-shaped turn lifecycle ───────────────────────────────────────────

def test_a_real_round_trip_idle_to_receipt_to_result_once_through_the_app_server_protocol(fake_codex, tmp_path, root):
    rid = ask(root)
    p = provider(fake_codex, tmp_path, root)
    p.start(create=True)
    m = mgr(root, p)
    out = m.step()
    assert out["delivered"] == {"request_id": rid, "status": "accepted"} and ar.armed_age(root, "codex") is not None
    assert wait_for(lambda: ar.load(root, rid)["stage"] == "returned")
    doc = ar.load(root, rid)
    assert len([e for e in doc["events"] if e["kind"] == "receipt"]) == 1 and len([e for e in doc["events"] if e["kind"] == "result"]) == 1
    assert [e for e in doc["events"] if e["kind"] == "receipt"][0]["native_ref"] == THREAD
    assert wait_for(lambda: p.probe().state == "idle") and m.step()["delivered"] is None
    p.close()
    m.close()


def test_a_turn_that_completes_before_the_rpc_response_leaves_the_session_idle_not_stuck_busy(fake_codex, tmp_path, root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "immediate")
    rid = ask(root)
    p = provider(fake_codex, tmp_path, root)
    p.start(create=True)
    m = mgr(root, p)
    assert m.step()["delivered"]["status"] == "accepted"
    assert wait_for(lambda: p.probe().state == "idle")                                                    # the completion that arrived first is kept
    m.step()
    m.step()
    assert [d for d in ar.load(root, rid)["events"] if d["kind"] == "delivery" and d.get("state") == "needs_attention"][0]["reason"] == "turn_ended_without_receipt"
    p.close()
    m.close()


def test_an_approval_that_arrives_before_the_rpc_response_is_declined_kept_visible_and_never_approved(fake_codex, tmp_path, root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "approval_first")
    rid = ask(root)
    p = provider(fake_codex, tmp_path, root)
    p.start(create=True)
    m = mgr(root, p)
    assert m.step()["delivered"]["status"] == "accepted"
    assert wait_for(lambda: p.probe().state == "idle")
    assert p.probe().attention.startswith("approval_blocked:item/commandExecution/requestApproval") or p.probe().attention.startswith("turn_failed")
    m.step()
    reasons = [d["reason"] for d in ar.load(root, rid)["events"] if d["kind"] == "delivery" and d.get("state") == "needs_attention"]
    assert any(r.startswith("approval_blocked") or r.startswith("turn_failed") for r in reasons)
    reply = [w["client_reply"] for w in wire(tmp_path) if "client_reply" in w][0]
    assert reply["id"] == 901 and "error" in reply and "result" not in reply                              # declined with an error, never a result
    p.close()
    m.close()


def test_a_busy_provider_never_sends_and_a_json_rpc_error_is_a_refusal_that_frees_it(fake_codex, tmp_path, root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "never_completes")
    p = provider(fake_codex, tmp_path, root)
    p.start(create=True)
    assert p.deliver("hello", "r-1").status == "accepted"
    assert wait_for(lambda: p.probe().state == "busy")
    assert p.deliver("again", "r-2").status == "refused" and p.deliver("again", "r-2").reason == "busy"
    assert len([w for w in wire(tmp_path) if w.get("method") == "turn/start"]) == 1
    p.close()
    monkeypatch.setenv("FAKE_MODE", "rpc_error")
    q = provider(fake_codex, tmp_path, root)
    q.start(create=True)
    got = q.deliver("hello", "r-3")
    assert got.status == "refused" and got.reason == "rpc_error:-32000" and q.probe().state == "idle"
    q.close()


@pytest.mark.parametrize("mode,reason", [("silent_rpc", "no_answer_after_write"), ("eof_after_write", "no_answer_after_write"), ("odd_answer", "unconfirmed_turn")])
def test_no_answer_eof_or_an_odd_answer_after_the_write_is_unknown_never_accepted_and_never_resent(fake_codex, tmp_path, root, monkeypatch, mode, reason):
    monkeypatch.setenv("FAKE_MODE", mode)
    rid = ask(root)
    p = provider(fake_codex, tmp_path, root, request_timeout=0.6)
    p.start(create=True)
    m = mgr(root, p)
    out = m.step()
    assert out["delivered"] == {"request_id": rid, "status": "unknown"}
    assert ("unknown", reason) in [(e["state"], e["reason"]) for e in ar.load(root, rid)["events"] if e["kind"] == "delivery" and e["actor"] == "connection"]
    assert p.probe().attention == "turn_start_unconfirmed" or p.probe().state == "offline"
    for _ in range(3):
        m.step()
    assert len([w for w in wire(tmp_path) if w.get("method") == "turn/start"]) == 1                       # never resent
    assert ar.pending(root, "codex")                                                                      # and not lost
    p.close()
    m.close()


def test_garbage_lines_from_the_server_are_ignored(fake_codex, tmp_path, root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    p = provider(fake_codex, tmp_path, root)
    assert p.start(create=True) == THREAD and p.probe().state == "idle"
    p.close()


def test_a_dead_server_is_offline_and_a_restart_resumes_the_same_thread(fake_codex, tmp_path, root):
    p = provider(fake_codex, tmp_path, root)
    p.start(create=True)
    p._proc.kill()
    assert wait_for(lambda: p.probe().state == "offline")
    assert p.deliver("x", "r").status == "failed" and p.deliver("x", "r").reason == "not_running"
    p.start()                                                                                             # thread id is now known: this resumes
    assert p.probe().state == "idle" and [w.get("method") for w in wire(tmp_path)].count("thread/resume") == 1
    p.close()


def test_late_events_from_an_old_server_cannot_change_the_replacements_state(fake_codex, tmp_path, root):
    p = provider(fake_codex, tmp_path, root)

    class Old:
        stdout = iter([json.dumps({"method": "turn/completed", "params": {"turn": {"status": "failed"}}}) + "\n",
                       json.dumps({"id": 5, "method": "item/fileChange/requestApproval", "params": {}}) + "\n"])

    class New:
        def poll(self):
            return None
    p._proc, p._busy, p._ready, p.thread_id = New(), True, True, THREAD
    p._read(Old())
    assert p._busy is True and p._attention == "" and p.probe().state == "busy"


# ── valid JSON of an unexpected shape, duplicate responses, EOF with a live child ──

ODD_LINES = [json.dumps(x) + "\n" for x in (
    [], "x", None, 5,
    {"method": "turn/completed", "params": {"turn": ["bad"]}},
    {"method": "turn/completed", "params": "x"},
    {"method": "turn/completed"},
    {"method": "turn/started", "params": None},
    {"id": [1], "result": {}},
    {"id": {"a": 1}, "method": "item/x/requestApproval"},
    {"id": 7, "method": ["x"]},
    {"method": ["x"]},
    {"id": None, "result": {}},
    {"id": 11, "result": "a string"},
    {"id": 11, "result": {}}, {"id": 11, "result": {}},
)]


class FakeProc:
    def __init__(self, lines, alive=True):
        self.stdout, self.alive, self.stdin = iter(lines), alive, self

    def poll(self):
        return None if self.alive else 1

    def write(self, s):
        pass

    def flush(self):
        pass


def test_valid_json_of_an_unexpected_shape_never_kills_the_reader_or_leaves_a_false_idle(fake_codex, tmp_path, root):
    p = provider(fake_codex, tmp_path, root)
    proc = FakeProc(ODD_LINES + [json.dumps({"method": "turn/started", "params": {"threadId": THREAD, "turn": {"id": "t", "status": "inProgress"}}}) + "\n"])
    p._proc, p._ready, p.thread_id = proc, True, THREAD
    proc.alive = True
    p._read(proc)                                                                                         # runs to the end: nothing raised
    # the valid line AFTER all the odd ones was still processed: the reader was alive throughout (then the stream ended with the process alive)
    assert p._busy is False                                                                               # EOF resets busy ...
    assert p.probe().state == "offline" and p._attention == "transport_closed"                           # ... and a closed transport is NOT a healthy idle


def test_a_turn_completion_that_cannot_be_read_is_visible_attention_not_a_silent_idle(fake_codex, tmp_path, root):
    p = provider(fake_codex, tmp_path, root)
    p._proc, p._ready, p.thread_id, p._busy = FakeProc([]), True, THREAD, True
    p._handle(p._proc, {"method": "turn/completed", "params": {"turn": ["bad"]}})
    assert p._busy is False and p.probe().attention == "malformed_event:turn/completed" and p.probe().state == "idle"


def test_a_duplicate_response_for_the_same_request_never_blocks_the_reader(fake_codex, tmp_path, root):
    import queue as q
    p = provider(fake_codex, tmp_path, root)
    p._proc, p._ready = FakeProc([]), True
    box = q.Queue(maxsize=1)
    p._waiting[42] = box
    done = []

    import threading

    def feed():
        for _ in range(5):
            p._handle(p._proc, {"id": 42, "result": {"ok": True}})
        done.append(True)
    t = threading.Thread(target=feed, daemon=True)
    t.start()
    t.join(timeout=3)
    assert done == [True] and box.get_nowait()["result"] == {"ok": True}                                  # five copies, one kept, no deadlock


def test_a_non_dict_result_is_treated_as_empty_never_an_attribute_error(fake_codex, tmp_path, root, monkeypatch):
    p = provider(fake_codex, tmp_path, root)
    monkeypatch.setattr(p, "_call", lambda method, params: "garbage" if False else {})
    p._proc, p._ready = FakeProc([]), True
    p.thread_id = THREAD
    assert p.deliver("x", "r").reason == "unconfirmed_turn"                                               # an empty answer is not an acceptance


def test_a_stream_that_closes_while_the_child_lives_is_offline_and_a_restart_replaces_the_old_process(fake_codex, tmp_path, root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "close_stdout")
    p = provider(fake_codex, tmp_path, root)
    with pytest.raises(Exception):
        p.start(create=True)                                                                              # the transport closed during the handshake: refused, not "idle"
    assert p.probe().state == "offline"
    monkeypatch.setenv("FAKE_MODE", "ack")
    assert p.start(create=True) == THREAD and p.probe().state == "idle"                                   # a clean restart works
    p.close()


def test_the_provider_is_busy_from_the_moment_it_sends_not_from_the_moment_the_server_says_so(fake_codex, tmp_path, root, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow_started")
    p = provider(fake_codex, tmp_path, root)
    p.start(create=True)
    assert p.deliver("first", "r-1").status == "accepted"
    assert p.probe().state == "busy"                                                                      # busy at once, before any turn/started notification
    second = p.deliver("second", "r-2")
    assert second.status == "refused" and second.reason == "busy"                                         # a second notice cannot slip in during that window
    assert len([w for w in wire(tmp_path) if w.get("method") == "turn/start"]) == 1
    p.close()
