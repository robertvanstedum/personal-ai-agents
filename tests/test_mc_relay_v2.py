"""docker/mc-agent/relay_v2.mjs: what v2 adds to v1 (images, the agent allow-list, jobs). The real relay runs under node against
a fake OpenClaw gateway and a fake OpenClaw run record (sqlite). Synthetic data only; no network beyond 127.0.0.1, no model."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import select
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RELAY = REPO / "docker" / "mc-agent" / "relay_v2.mjs"
CALLER = "relay-caller-" + "a" * 32
MC_TOKEN = "mc-openclaw-" + "b" * 32
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452") + b"x" * 40
JPEG = bytes.fromhex("ffd8ffe000104a464946") + b"x" * 40
GIF = b"GIF89a" + b"x" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"x" * 40

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


class Gateway:
    """A fake OpenClaw: answers chat completions; a 'hold' answer waits until released and notices when the caller hangs up."""

    def __init__(self):
        self.seen, self.mode, self.text = [], "ok", "the job's answer"
        self.release = threading.Event()
        self.disconnected = threading.Event()
        self.on_disconnect = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ready":true}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                outer.seen.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
                mode = outer.mode
                if mode == "hold":
                    while not outer.release.is_set():
                        ready, _, _ = select.select([self.connection], [], [], 0.05)
                        if ready:
                            try:
                                if self.connection.recv(1, socket.MSG_PEEK) == b"":
                                    outer.disconnected.set()
                                    if outer.on_disconnect:
                                        outer.on_disconnect()
                                    return
                            except OSError:
                                outer.disconnected.set()
                                return
                if mode == "status500":
                    return self._send(500, {"error": {"message": "boom"}})
                if mode == "badjson":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"not json")
                    return
                text = "No response from OpenClaw." if mode == "placeholder" else outer.text
                self._send(200, {"id": "chatcmpl_x", "choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class RunRecord:
    """A fake of OpenClaw's run record (the sqlite the relay reads for the audit), with just the tables and columns it needs."""

    def __init__(self, path):
        self.path = str(path)
        db = sqlite3.connect(self.path)
        db.executescript("""CREATE TABLE session_windows (session_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, status TEXT, created_at INTEGER NOT NULL);
          CREATE TABLE trajectory_runtime_events (session_id TEXT NOT NULL, seq INTEGER NOT NULL, run_id TEXT, event_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (session_id, seq));""")
        db.commit()
        db.close()
        self.seq = 0

    def session(self, job_id, status="running", sid=None):
        sid = sid or f"s-{job_id}"
        db = sqlite3.connect(self.path)
        db.execute("INSERT OR REPLACE INTO session_windows VALUES (?,?,?,?)", (sid, f"agent:mc-agent:openai-user:guild-mc:job:{job_id}", status, int(time.time())))
        db.commit()
        db.close()
        return sid

    def status(self, job_id, status):
        db = sqlite3.connect(self.path)
        db.execute("UPDATE session_windows SET status=? WHERE session_key=?", (status, f"agent:mc-agent:openai-user:guild-mc:job:{job_id}"))
        db.commit()
        db.close()

    def call(self, job_id, name, args, ok=True, workspace="/ws"):
        sid = f"s-{job_id}"
        db = sqlite3.connect(self.path)
        self.seq += 1
        tid = f"call-{self.seq}"
        for kind, data in (("tool.call", {"toolCallId": tid, "name": name, "args": args}), ("tool.result", {"toolCallId": tid, "name": name, "success": ok, "result": {}})):
            self.seq += 1
            db.execute("INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)", (sid, self.seq, "run", json.dumps({"type": kind, "data": data, "workspaceDir": workspace}), int(time.time())))
        db.commit()
        db.close()


class Env:
    def __init__(self, tmp_path, *, jobs=True, record=True, extra=None):
        self.tmp = tmp_path
        self.gateway = Gateway()
        self.state, self.inbox_root = tmp_path / "state", tmp_path / "inbox"
        self.project, self.workspace, self.handoff = tmp_path / "project", tmp_path / "workspace", tmp_path / "project" / "_working" / "mc-handoff"
        for d in (self.inbox_root, self.project, self.workspace, self.handoff):
            d.mkdir(parents=True, exist_ok=True)
        self.record = RunRecord(tmp_path / "agent.sqlite") if record else None
        self.env = {"PATH": os.environ["PATH"], "MC_RELAY_TOKEN": CALLER, "MC_OPENCLAW_GATEWAY_TOKEN": MC_TOKEN, "MC_RELAY_TARGET": self.gateway.url,
                    "MC_RELAY_STOP_CONFIRM_MS": "1500", "MC_RELAY_JOB_GRACE_MS": "300", "MC_RELAY_JOB_DEADLINE_MAX_S": "30"}
        if jobs:
            self.env.update({"MC_RELAY_STATE_DIR": str(self.state), "MC_RELAY_INBOX_ROOT": str(self.inbox_root), "MC_RELAY_PROJECT_ROOT": str(self.project),
                             "MC_RELAY_WORKSPACE": str(self.workspace), "MC_RELAY_HANDOFF": str(self.handoff)})
            if record:
                self.env["MC_RELAY_AGENT_DB"] = self.record.path
        self.env.update(extra or {})
        self.proc = None
        self.start()

    def start(self):
        for _ in range(6):
            self.port = _port()
            env = {**self.env, "MC_RELAY_PORT": str(self.port)}
            self.proc = subprocess.Popen(["node", str(RELAY)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.base = f"http://127.0.0.1:{self.port}"
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and self.proc.poll() is None:
                try:
                    urllib.request.urlopen(self.base + "/healthz", timeout=1)
                    return
                except Exception:
                    time.sleep(0.1)
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        raise AssertionError("the relay did not start: " + (self.proc.stderr.read() if self.proc and self.proc.stderr else ""))

    def stop(self, kill=False):
        if self.proc and self.proc.poll() is None:
            (self.proc.kill if kill else self.proc.terminate)()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def call(self, method, path, body=None, *, auth=True, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"content-type": "application/json", **({"authorization": f"Bearer {CALLER}"} if auth else {})})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raw_body = e.read()
            try:
                return e.code, json.loads(raw_body or b"null")
            except ValueError:
                return e.code, raw_body.decode()

    def wait_state(self, job_id, want, seconds=8):
        end = time.monotonic() + seconds
        last = None
        while time.monotonic() < end:
            code, body = self.call("GET", f"/v1/jobs/{job_id}")
            last = body
            if code == 200 and body["state"] in (want if isinstance(want, tuple) else (want,)):
                return body
            time.sleep(0.1)
        raise AssertionError(f"job never reached {want}: {last}")

    def log_lines(self):
        self.stop()
        return (self.proc.stdout.read() or "") + (self.proc.stderr.read() or "")


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.gateway.release.set()
    e.stop()


JOB = "j-0123456789abcdef"
JOB2 = "j-1111111111111111"
JOB3 = "j-2222222222222222"


def spec(job_id=JOB, task="Compare the two resumes in the inbox.", **extra):
    return {"job_id": job_id, "agent": "mc-agent", "task": task, "inbox": None, "deadline_s": 30, **extra}


def chat(model="openclaw/mc-agent", content="hello", user="guild-mc:test", **extra):
    return {"model": model, "user": user, "messages": [{"role": "user", "content": content}], **extra}


# ── what v2 adds to the chat lane ────────────────────────────────────────────────

def test_the_chat_lane_accepts_the_lean_agent_and_still_refuses_every_other_model(env):
    code, _ = env.call("POST", "/v1/chat/completions", chat("openclaw/mc-chat"))
    assert code == 200 and env.gateway.seen[-1]["body"]["model"] == "openclaw/mc-chat"          # forwarded as asked
    for model in ("openclaw/default", "openclaw/main", "gpt-5", "mc-agent", "openclaw/mc-chat2"):
        assert env.call("POST", "/v1/chat/completions", chat(model))[0] == 403, model


def test_an_image_travels_with_its_text_and_nothing_else_of_the_part_does(env):
    content = [{"type": "text", "text": "What is in this picture?"}, {"type": "image_url", "image_url": {"url": data_url(PNG)}}]
    code, _ = env.call("POST", "/v1/chat/completions", chat(content=content))
    assert code == 200
    assert env.gateway.seen[-1]["body"]["messages"] == [{"role": "user", "content": content}]
    for mime, raw in (("image/jpeg", JPEG), ("image/gif", GIF), ("image/webp", WEBP)):
        assert env.call("POST", "/v1/chat/completions", chat(content=[{"type": "image_url", "image_url": {"url": data_url(raw, mime)}}]))[0] == 200


@pytest.mark.parametrize("content", [
    [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],                                    # not a data URL
    [{"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64,PHN2Zz4="}}],                           # a type outside the list
    [{"type": "image_url", "image_url": {"url": data_url(JPEG, "image/png")}}],                                    # bytes are not the label
    [{"type": "image_url", "image_url": {"url": "data:image/png;base64,@@@@"}}],                                   # not base64
    [{"type": "image_url", "image_url": {"url": data_url(PNG), "detail": "high"}}],                                # an extra key
    [{"type": "image_url", "image_url": {"url": data_url(PNG)}, "extra": 1}],
    [{"type": "text", "text": "x", "cache_control": {}}],
    [{"type": "input_file", "file": "x"}], [{"type": "image_url"}], [], "", [{"type": "image_url", "image_url": {"url": "data:image/png;base64,"}}],
])
def test_a_content_part_that_is_not_exactly_an_allowed_one_is_refused(env, content):
    code, body = env.call("POST", "/v1/chat/completions", chat(content=content))
    assert code == 403 or (content == "" and code == 200), (content, code, body)
    assert not env.gateway.seen or content == ""


def test_image_limits_count_size_role_and_the_text_cap(env):
    img = {"type": "image_url", "image_url": {"url": data_url(PNG)}}
    assert env.call("POST", "/v1/chat/completions", chat(content=[img] * 4))[0] == 200
    assert env.call("POST", "/v1/chat/completions", chat(content=[img] * 5))[0] == 403                          # at most four
    big = {"type": "image_url", "image_url": {"url": data_url(PNG + b"z" * (5 * 1024 * 1024))}}
    assert env.call("POST", "/v1/chat/completions", chat(content=[big]))[0] == 403                              # over 5 MB
    n = len(env.gateway.seen)
    body = {"model": "openclaw/mc-agent", "user": "guild-mc:t", "messages": [{"role": "assistant", "content": [img]}]}
    assert env.call("POST", "/v1/chat/completions", body)[0] == 403                                             # only the user's message
    long_text = {"type": "text", "text": "x" * (257 * 1024)}
    assert env.call("POST", "/v1/chat/completions", chat(content=[long_text, img]))[0] == 403                   # the text cap holds with images
    assert len(env.gateway.seen) == n


def test_a_text_only_request_over_256_kb_is_still_413(env):
    assert env.call("POST", "/v1/chat/completions", chat(content="x" * (257 * 1024)))[0] == 413


def test_capabilities_and_health_say_what_this_relay_is_and_need_the_token(env):
    assert env.call("GET", "/v1/capabilities", auth=False)[0] == 401
    code, caps = env.call("GET", "/v1/capabilities")
    assert code == 200 and caps["version"] == "v2" and caps["jobs"]["enabled"] is True and caps["jobs"]["agent"] == "openclaw/mc-agent"
    assert caps["chat"]["images"]["max"] == 4 and "openclaw/mc-chat" in caps["chat"]["models"] and caps["jobs"]["audit"] is True
    health = urllib.request.urlopen(env.base + "/healthz").read()
    assert json.loads(health) == {"ok": True, "version": "v2"}


# ── jobs: the door ───────────────────────────────────────────────────────────────

def test_job_routes_need_the_caller_token(env):
    for method, path, body in (("POST", "/v1/jobs", spec()), ("GET", f"/v1/jobs/{JOB}", None), ("POST", f"/v1/jobs/{JOB}/stop", {})):
        assert env.call(method, path, body, auth=False)[0] == 401, path
    assert env.gateway.seen == []


def test_an_unconfigured_relay_starts_no_jobs_and_says_so(tmp_path):
    e = Env(tmp_path, jobs=False)
    try:
        code, body = e.call("POST", "/v1/jobs", spec())
        assert code == 503 and body["error"]["type"] == "jobs_not_configured" and e.gateway.seen == []
        jobs = e.call("GET", "/v1/capabilities")[1]["jobs"]
        assert jobs["enabled"] is False and jobs["audit"] is False                                  # no run record configured: it does not claim an audit
    finally:
        e.stop()


@pytest.mark.parametrize("mutation", [
    {"agent": "main"}, {"agent": "mc-chat"}, {"agent": None}, {"job_id": "j-123"}, {"job_id": "../x"}, {"job_id": None}, {"task": ""}, {"task": "   "},
    {"task": "x" * 8001}, {"task": 5}, {"deadline_s": "soon"}, {"model": "x"}, {"user": "guild-mc:x"}, {"stream": True}, {"inbox": "x"}, {"inbox": []},
])
def test_a_job_request_is_exactly_the_documented_shape(env, mutation):
    code, _ = env.call("POST", "/v1/jobs", {**spec(), **mutation})
    assert code in (403, 422) and env.gateway.seen == [], mutation


def test_a_job_request_is_small_and_strict(env):
    assert env.call("POST", "/v1/jobs", spec(task="x" * 20000))[0] == 413
    assert env.call("POST", "/v1/jobs", raw=b'{"job_id":"j-0123456789abcdef","job_id":"j-1111111111111111","agent":"mc-agent","task":"t"}')[0] == 400
    assert env.call("POST", "/v1/jobs", raw=b"not json")[0] == 400
    assert env.call("POST", "/v1/jobs", raw=b"[]")[0] == 403
    assert env.call("GET", "/v1/jobs")[0] == 403 and env.call("GET", "/v1/jobs/../x")[0] in (403, 404) and env.call("GET", "/v1/jobs/j-12")[0] == 403
    assert env.gateway.seen == []


# ── jobs: the lifecycle ──────────────────────────────────────────────────────────

def test_a_job_runs_as_the_job_agent_with_the_owners_words_and_the_rules_and_ends_with_its_answer(env):
    env.gateway.mode = "hold"
    code, body = env.call("POST", "/v1/jobs", spec())
    assert code == 200 and body == {"accepted": True, "job_id": JOB, "state": "running"}
    for _ in range(50):
        if env.gateway.seen:
            break
        time.sleep(0.05)
    [seen] = env.gateway.seen
    sent = seen["body"]
    assert sent["model"] == "openclaw/mc-agent" and sent["user"] == f"guild-mc:job:{JOB}" and "stream" not in sent
    [message] = sent["messages"]
    assert message["role"] == "user" and "Compare the two resumes in the inbox." in message["content"]
    assert str(env.project) in message["content"] and str(env.handoff) in message["content"] and str(env.workspace) in message["content"]
    assert "A refusal is a valid answer" in message["content"] and "not enforced by software" in message["content"]
    assert seen["headers"]["authorization"] == f"Bearer {MC_TOKEN}" and CALLER not in json.dumps(seen["headers"])
    running = env.call("GET", f"/v1/jobs/{JOB}")[1]
    assert running["state"] == "running" and running["result"] is None and isinstance(running["heartbeat_age_s"], int)
    env.gateway.release.set()
    done = env.wait_state(JOB, "completed")
    assert done["result"] == {"text": "the job's answer", "truncated": False} and done["heartbeat_age_s"] is None and done["error_class"] is None


def test_the_same_job_id_twice_is_one_run(env):
    env.gateway.mode = "hold"
    assert env.call("POST", "/v1/jobs", spec())[1]["accepted"] is True
    code, body = env.call("POST", "/v1/jobs", spec())
    assert code == 200 and body == {"accepted": False, "duplicate": True, "state": "running"}
    time.sleep(0.3)
    assert len(env.gateway.seen) == 1
    env.gateway.release.set()
    env.wait_state(JOB, "completed")
    assert env.call("POST", "/v1/jobs", spec())[1]["duplicate"] is True and len(env.gateway.seen) == 1       # even when it is over


def test_at_most_two_jobs_run_at_once_and_a_chat_turn_is_never_blocked_by_them(env):
    env.gateway.mode = "hold"
    assert env.call("POST", "/v1/jobs", spec(JOB))[0] == 200 and env.call("POST", "/v1/jobs", spec(JOB2))[0] == 200
    code, body = env.call("POST", "/v1/jobs", spec(JOB3))
    assert code == 429 and body == {"error": "busy"} and JOB3 not in {s["body"]["user"] for s in env.gateway.seen}
    env.gateway.mode = "ok"
    assert env.call("POST", "/v1/chat/completions", chat())[0] == 200                                         # chat goes through meanwhile
    env.gateway.release.set()
    env.gateway.mode = "ok"
    env.wait_state(JOB, ("completed", "failed"))


@pytest.mark.parametrize("mode,error", [("status500", "upstream_http_500"), ("placeholder", "no_answer"), ("badjson", "upstream_unreadable")])
def test_a_gateway_that_fails_or_says_nothing_ends_the_job_as_failed_never_as_an_answer(env, mode, error):
    env.gateway.mode = mode
    env.call("POST", "/v1/jobs", spec())
    done = env.wait_state(JOB, "failed")
    assert done["error_class"] == error and done["result"] is None


def test_a_result_over_64_kb_is_cut_and_says_so(env):
    env.gateway.text = "word " * 30000
    env.call("POST", "/v1/jobs", spec())
    done = env.wait_state(JOB, "completed")
    assert done["result"]["truncated"] is True and len(done["result"]["text"].encode()) <= 64 * 1024


def test_a_gateway_that_is_down_fails_the_job_with_its_reason(tmp_path):
    e = Env(tmp_path, extra={"MC_RELAY_TARGET": "http://127.0.0.1:9"})
    try:
        assert e.call("POST", "/v1/jobs", spec())[0] == 200
        assert e.wait_state(JOB, "failed")["error_class"] == "upstream_unreachable"
    finally:
        e.stop()


def test_a_job_that_passes_its_deadline_is_ended_as_failed_deadline_and_its_run_is_hung_up_on(tmp_path):
    e = Env(tmp_path, extra={"MC_RELAY_JOB_DEADLINE_MAX_S": "5", "MC_RELAY_JOB_GRACE_MS": "200"})
    try:
        e.gateway.mode = "hold"
        e.call("POST", "/v1/jobs", spec(deadline_s=5))
        done = e.wait_state(JOB, "failed", 15)
        assert done["error_class"] == "deadline" and e.gateway.disconnected.wait(3)
    finally:
        e.gateway.release.set()
        e.stop()


# ── stop, tombstones, restarts ───────────────────────────────────────────────────

def test_a_stop_hangs_up_on_the_run_and_is_confirmed_only_when_the_run_record_says_it_ended(env):
    env.gateway.mode = "hold"
    env.record.session(JOB, "running")
    env.gateway.on_disconnect = lambda: env.record.status(JOB, "killed")
    env.call("POST", "/v1/jobs", spec())
    time.sleep(0.3)
    code, body = env.call("POST", f"/v1/jobs/{JOB}/stop", {})
    assert code == 200 and body == {"stop": "confirmed"} and env.gateway.disconnected.is_set()
    assert env.call("GET", f"/v1/jobs/{JOB}")[1]["state"] == "stopped"
    assert env.call("POST", f"/v1/jobs/{JOB}/stop", {})[1] == {"stop": "not_running"}


def test_a_stop_the_run_record_does_not_confirm_is_only_requested_and_settles_when_it_does(env):
    env.gateway.mode = "hold"
    env.record.session(JOB, "running")                                       # the run record keeps saying "running"
    env.call("POST", "/v1/jobs", spec())
    time.sleep(0.3)
    code, body = env.call("POST", f"/v1/jobs/{JOB}/stop", {})
    assert code == 200 and body == {"stop": "requested"}
    assert env.call("GET", f"/v1/jobs/{JOB}")[1]["state"] == "running"        # never "stopped" without evidence
    env.record.status(JOB, "killed")
    assert env.wait_state(JOB, "stopped", 8)["state"] == "stopped"


def test_without_a_run_record_a_stop_can_never_be_confirmed(tmp_path):
    e = Env(tmp_path, record=False)
    try:
        e.gateway.mode = "hold"
        e.call("POST", "/v1/jobs", spec())
        time.sleep(0.3)
        assert e.call("POST", f"/v1/jobs/{JOB}/stop", {})[1] == {"stop": "requested"}
        assert e.call("GET", f"/v1/jobs/{JOB}")[1]["audit"]["available"] is False
    finally:
        e.gateway.release.set()
        e.stop()


def test_a_stop_for_a_job_the_relay_never_heard_of_is_remembered_so_a_late_start_is_refused_even_after_a_restart(env):
    code, body = env.call("POST", f"/v1/jobs/{JOB}/stop", {})
    assert code == 404 and body == {"error": "not_found"}
    assert env.call("POST", "/v1/jobs", spec()) == (409, {"error": "stopped_before_start"})
    env.stop()
    env.start()
    assert env.call("POST", "/v1/jobs", spec()) == (409, {"error": "stopped_before_start"}) and env.gateway.seen == []


def test_a_relay_restart_ends_a_running_job_as_unknown_with_its_reason_and_keeps_finished_ones(env):
    env.call("POST", "/v1/jobs", spec(JOB2))
    env.wait_state(JOB2, "completed")
    env.gateway.mode = "hold"
    env.call("POST", "/v1/jobs", spec(JOB))
    time.sleep(0.3)
    env.stop(kill=True)
    env.start()
    lost = env.call("GET", f"/v1/jobs/{JOB}")[1]
    assert lost["state"] == "unknown" and lost["error_class"] == "relay_restarted" and lost["result"] is None
    kept = env.call("GET", f"/v1/jobs/{JOB2}")[1]
    assert kept["state"] == "completed" and kept["result"]["text"] == "the job's answer"
    assert env.call("POST", "/v1/jobs", spec(JOB))[1]["duplicate"] is True                      # and it is not run again


def test_an_unknown_job_is_404_and_the_state_files_are_private(env):
    assert env.call("GET", f"/v1/jobs/{JOB}")[0] == 404
    env.call("POST", "/v1/jobs", spec())
    env.wait_state(JOB, "completed")
    record = env.state / "jobs" / f"{JOB}.json"
    assert oct(record.stat().st_mode & 0o777) == "0o600" and oct((env.state / "jobs").stat().st_mode & 0o777) == "0o700"
    assert "reply text" not in record.read_text() and CALLER not in record.read_text() and MC_TOKEN not in record.read_text()


# ── the files a job is given ─────────────────────────────────────────────────────

def make_inbox(env, job=JOB, files=(("01-a.txt", b"resume a"), ("02-b.txt", b"resume b")), manifest=True):
    d = env.inbox_root / job
    d.mkdir(parents=True, exist_ok=True)
    listed = []
    for name, raw in files:
        (d / name).write_bytes(raw)
        listed.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "name": name, "bytes": len(raw)})
    if manifest:
        (d / "MANIFEST.json").write_text(json.dumps({"job_id": job, "files": listed}))
    return d, [{"name": f["path"], "sha256": f["sha256"]} for f in listed]


def test_a_job_is_given_the_paths_of_its_own_verified_files_and_only_those(env):
    d, files = make_inbox(env)
    code, _ = env.call("POST", "/v1/jobs", spec(inbox={"job_id": JOB, "files": files}))
    assert code == 200
    env.wait_state(JOB, "completed")
    content = env.gateway.seen[0]["body"]["messages"][0]["content"]
    assert str(Path(os.path.realpath(d / "01-a.txt"))) in content and str(Path(os.path.realpath(d / "02-b.txt"))) in content
    a, b = os.path.realpath(d / "01-a.txt"), os.path.realpath(d / "02-b.txt")
    ha, hb = env.handoff / "a.txt", env.handoff / "b.txt"                           # the owner's own names, chosen by the relay
    assert f"cp -n -- '{a}' '{ha}' && chmod u+w '{ha}' && cmp '{a}' '{ha}'" in content   # no-clobber, writable, bytes checked
    assert f"cp -n -- '{b}' '{hb}' && chmod u+w '{hb}' && cmp '{b}' '{hb}'" in content
    assert "do not use mv" in content and "do not add -f" in content


def hand_back_lines(env, files, before=()):
    for name, raw in before:
        (env.handoff / name).write_bytes(raw)
    d, listed = make_inbox(env, files=files)
    code, _ = env.call("POST", "/v1/jobs", spec(inbox={"job_id": JOB, "files": listed}))
    assert code == 200
    env.wait_state(JOB, "completed")
    content = env.gateway.seen[0]["body"]["messages"][0]["content"]
    return [ln.strip() for ln in content.splitlines() if ln.strip().startswith("cp -n -- ")]


def test_two_files_with_the_same_owner_name_get_two_different_hand_back_names(env):
    lines = hand_back_lines(env, (("01-report.docx", b"one"), ("02-report.docx", b"two")))
    assert len(lines) == 2
    assert f"'{env.handoff / 'report.docx'}'" in lines[0] and f"'{env.handoff / 'report (2).docx'}'" in lines[1]


def test_a_hand_back_never_takes_the_name_of_a_file_already_in_the_hand_off_folder(env):
    lines = hand_back_lines(env, (("01-report.docx", b"new"),), before=(("report.docx", b"someone else's"), ("report (2).docx", b"also there")))
    assert f"'{env.handoff / 'report (3).docx'}'" in lines[0]
    assert (env.handoff / "report.docx").read_bytes() == b"someone else's"          # nothing was touched



@pytest.mark.parametrize("case", ["hash", "missing_file", "symlink_file", "symlink_dir", "no_manifest", "wrong_job", "traversal", "too_many", "bad_name", "not_listed", "outside"])
def test_an_inbox_that_is_not_exactly_what_the_portal_staged_starts_nothing(env, tmp_path, case):
    d, files = make_inbox(env)
    inbox = {"job_id": JOB, "files": list(files)}
    if case == "hash":
        (d / "01-a.txt").write_bytes(b"swapped")
    elif case == "missing_file":
        (d / "01-a.txt").unlink()
    elif case == "symlink_file":
        outside = tmp_path / "secret.txt"
        outside.write_bytes(b"resume a")
        (d / "01-a.txt").unlink()
        os.symlink(outside, d / "01-a.txt")
    elif case == "symlink_dir":
        real = tmp_path / "elsewhere"
        shutil.copytree(d, real)
        shutil.rmtree(d)
        os.symlink(real, d)
    elif case == "no_manifest":
        (d / "MANIFEST.json").unlink()
    elif case == "wrong_job":
        inbox["job_id"] = JOB2
    elif case == "traversal":
        inbox["files"] = [{"name": "../01-a.txt", "sha256": files[0]["sha256"]}]
    elif case == "too_many":
        inbox["files"] = files * 3
    elif case == "bad_name":
        inbox["files"] = [{"name": ".hidden", "sha256": files[0]["sha256"]}]
    elif case == "not_listed":
        (d / "03-c.txt").write_bytes(b"extra")
        inbox["files"] = [{"name": "03-c.txt", "sha256": hashlib.sha256(b"extra").hexdigest()}]
    elif case == "outside":
        other = env.tmp / "other"
        other.mkdir()
        (other / "01-a.txt").write_bytes(b"resume a")
        inbox["files"] = [{"name": "01-a.txt", "sha256": files[0]["sha256"]}]
        shutil.rmtree(d)
        os.symlink(other, d)
    code, body = env.call("POST", "/v1/jobs", spec(inbox=inbox))
    assert code in (403, 422), (case, code, body)
    assert env.gateway.seen == []
    assert env.call("GET", f"/v1/jobs/{JOB}")[0] == 404                                          # nothing was recorded either


def test_a_file_over_5_mb_is_refused(env):
    d, files = make_inbox(env, files=(("01-big.bin", b"x" * (5 * 1024 * 1024 + 1)),))
    assert env.call("POST", "/v1/jobs", spec(inbox={"job_id": JOB, "files": files}))[0] == 422 and env.gateway.seen == []


# ── the audit and the writes ─────────────────────────────────────────────────────

def job_with_audit(env, calls, finish=True):
    env.gateway.mode = "hold"
    env.record.session(JOB, "running")
    env.call("POST", "/v1/jobs", spec())
    time.sleep(0.2)
    for name, args, *ok in calls:
        env.record.call(JOB, name, args, ok=ok[0] if ok else True, workspace=str(env.workspace))
    if finish:
        env.record.status(JOB, "done")
        env.gateway.release.set()
        return env.wait_state(JOB, "completed")
    return env.call("GET", f"/v1/jobs/{JOB}")[1]


def test_the_audit_lists_the_runs_own_tool_calls_with_their_targets_and_outcomes(env):
    (env.project / "docs").mkdir()
    done = job_with_audit(env, [("read", {"path": str(env.project / "docs" / "a.md")}), ("exec", {"command": "ls -la"}, False), ("dir_list", {"path": "."})])
    tools = done["audit"]["tools"]
    assert [t["name"] for t in tools] == ["read", "exec", "dir_list"] and tools[1]["ok"] is False and tools[0]["ok"] is True
    assert tools[0]["target"].endswith("docs/a.md") and tools[1]["target"] == "ls -la" and "flag" not in tools[0]
    assert done["audit"]["available"] is True and done["audit"]["truncated"] is False


def test_a_call_into_a_forbidden_area_or_a_write_outside_the_allowed_folders_is_flagged_not_hidden(env):
    outside = env.tmp / "elsewhere.txt"
    done = job_with_audit(env, [
        ("read", {"path": "~/.openclaw/openclaw.json"}), ("read", {"path": str(env.project / ".env")}), ("write", {"path": str(outside), "content": "x"}),
        ("write", {"path": str(env.handoff / "ok.md"), "content": "x"}), ("write", {"path": str(env.workspace / "note.md"), "content": "x"}),
        ("exec", {"command": "cat ~/.ssh/id_rsa"}), ("read", {"path": str(env.project / "_working" / "RVS-Career-Aug26" / "cv.md")})])
    flags = [t.get("flag") for t in done["audit"]["tools"]]
    assert flags == ["touches_restricted_area", "touches_restricted_area", "write_outside_allowed_area", None, None, "touches_restricted_area", "touches_restricted_area"]


def test_the_audit_never_carries_a_secret_a_control_character_or_an_unbounded_string(env):
    secret = "Bearer " + "A" * 40
    done = job_with_audit(env, [("exec", {"command": f"curl -H 'Authorization: {secret}' https://x\x07\x1b[31m " + "y" * 500}), ("exec", {"command": "export API_KEY=abcdef123456"}),
                                ("exec", {"command": "echo " + "a1" * 30})])
    for t in done["audit"]["tools"]:
        assert "A" * 20 not in t["target"] and "\x07" not in t["target"] and "\x1b" not in t["target"] and len(t["target"]) <= 200, t
    assert "[redacted]" in done["audit"]["tools"][0]["target"] and "abcdef123456" not in done["audit"]["tools"][1]["target"]
    assert "[redacted]" in done["audit"]["tools"][2]["target"]                                   # a long hex-looking string


def test_more_than_200_tool_calls_are_cut_and_the_audit_says_so(env):
    done = job_with_audit(env, [("read", {"path": f"/p/{i}"}) for i in range(230)])
    assert len(done["audit"]["tools"]) == 200 and done["audit"]["truncated"] is True


def test_the_audit_is_live_while_the_job_runs(env):
    running = job_with_audit(env, [("read", {"path": "/p/live.md"})], finish=False)
    assert running["state"] == "running" and [t["name"] for t in running["audit"]["tools"]] == ["read"]


def test_what_a_job_says_it_wrote_is_checked_on_the_host(env):
    real = env.handoff / "report.md"
    real.write_bytes(b"the report")
    link = env.handoff / "link.md"
    os.symlink(real, link)
    done = job_with_audit(env, [("write", {"path": str(real), "content": "ignored"}), ("write", {"path": str(env.handoff / "never-written.md"), "content": "x"}),
                                ("write", {"path": str(link), "content": "x"}), ("write", {"path": str(env.handoff / "failed.md"), "content": "x"}, False)])
    writes = {Path(w["path"]).name: w for w in done["writes"]}
    assert writes["report.md"] == {"path": str(real), "exists": True, "bytes": 10, "sha256": hashlib.sha256(b"the report").hexdigest(), "verified": True}
    assert writes["never-written.md"]["exists"] is False and writes["never-written.md"]["verified"] is False and writes["never-written.md"]["sha256"] == ""
    assert writes["link.md"]["verified"] is False and writes["link.md"]["exists"] is False                            # a link is never "verified"
    assert "failed.md" not in writes                                                                               # a failed call claims nothing


def test_the_run_record_missing_or_corrupt_means_the_audit_is_unavailable_not_empty(tmp_path):
    e = Env(tmp_path)
    try:
        Path(e.record.path).write_bytes(b"this is not a database")
        e.call("POST", "/v1/jobs", spec())
        done = e.wait_state(JOB, "completed")
        assert done["audit"]["available"] is False and done["audit"]["tools"] == []
    finally:
        e.stop()


# ── what the relay logs ──────────────────────────────────────────────────────────

def test_the_logs_carry_job_ids_and_outcomes_but_never_a_token_the_task_or_the_answer(env):
    env.call("POST", "/v1/jobs", spec(task="A PRIVATE SENTENCE the log must not repeat"))
    env.wait_state(JOB, "completed")
    logs = env.log_lines()
    assert JOB in logs and "job_start" in logs and "job_end" in logs
    for banned in (CALLER, MC_TOKEN, "A PRIVATE SENTENCE", "the job's answer"):
        assert banned not in logs, banned


def test_files_a_plain_shell_command_writes_are_checked_too_and_anything_else_is_not_claimed(env):
    (env.handoff / "copied.txt").write_bytes(b"copied bytes")
    (env.handoff / "redirected.md").write_bytes(b"redirected")
    src = env.tmp / "source" / "copied.txt"
    done = job_with_audit(env, [
        ("exec", {"command": f"cp '{src}' {env.handoff}/"}), ("exec", {"command": f"echo hi > {env.handoff}/redirected.md"}),
        ("exec", {"command": "python3 -c \"open('x','w').write('y')\""}), ("exec", {"command": f"cat notes | tee {env.workspace}/t.txt"})])
    claimed = {Path(w["path"]).name: w for w in done["writes"]}
    assert claimed["copied.txt"]["verified"] is True and claimed["copied.txt"]["sha256"] == hashlib.sha256(b"copied bytes").hexdigest()
    assert claimed["redirected.md"]["verified"] is True and claimed["t.txt"]["exists"] is False                    # claimed, but it is not there
    assert set(claimed) == {"copied.txt", "redirected.md", "t.txt"}                                              # the python one claims nothing
    assert [t.get("flag") for t in done["audit"]["tools"]] == [None, None, None, None]


def test_a_shell_command_that_plainly_writes_outside_the_allowed_folders_is_flagged(env):
    done = job_with_audit(env, [("exec", {"command": f"cp a.txt {env.tmp}/elsewhere/"}), ("exec", {"command": f"echo x > {env.project}/src.py"}),
                                ("exec", {"command": f"echo x >> {env.handoff}/ok.md"}), ("exec", {"command": "ls"})])
    assert [t.get("flag") for t in done["audit"]["tools"]] == ["write_outside_allowed_area", "write_outside_allowed_area", None, None]
