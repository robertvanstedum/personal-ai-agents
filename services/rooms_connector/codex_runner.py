"""Rooms R2 v0.4 Codex runner (ROOMS_R2.md §3.6): one `codex app-server`
child per turn over stdio, inside the rooms-codex container.

The runner shares the Claude Code runner's proof lifecycle, stop and kill
handling; only the command, the readiness checks and the event contract are
Codex's own. Every item that is not a message, reasoning or plan, and every
server request, fails the turn closed (§3.6.4).
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time

from services.rooms_connector.claude_runner import OUTPUT_CAP, ClaudeRunner, sha256_file

CATALOG = Path(__file__).with_name("codex_catalog.json")
MODEL = "gpt-5.5"
DISABLED = ["shell_tool", "unified_exec", "apps", "plugins", "browser_use", "browser_use_external", "computer_use",
            "in_app_browser", "image_generation", "multi_agent", "hooks", "skill_search",
            "skill_mcp_dependency_install", "tool_suggest", "goals", "code_mode_host", "workspace_dependencies",
            "shell_snapshot", "remote_plugin"]
EXPECTED_TOOLS = {"update_plan", "request_user_input", "view_image"}
ALLOWED_ITEMS = {"userMessage", "agentMessage", "reasoning", "plan"}
# Streamed model output: any of these with text is evidence the model worked (C2).
OUTPUT_DELTAS = {"item/agentMessage/delta", "item/reasoning/textDelta", "item/reasoning/summaryTextDelta",
                 "item/plan/delta"}
INTERRUPT_GRACE_S = 3                    # after turn/interrupt, before TERM (then KILL after 5 s)
# Codex's own runtime files (observed in a fresh CODEX_HOME, EVIDENCE.md) plus the sign-in.
HOME_ALLOWED = {"auth.json", "installation_id", "tmp", ".sandbox_migration", ".personality_migration", "version.json",
                "models_cache.json", "log", "logs", "sessions", "cache", "shell_snapshots"}
HOME_DATABASE = re.compile(r"^[a-z]+_\d+\.sqlite(-shm|-wal)?$")
HOME_FORBIDDEN = {"config.toml", "AGENTS.md", "AGENTS.override.md", "rules", "skills", "plugins", "hooks",
                  "hooks.json", "prompts", "memories"}


def flags(catalog=CATALOG, provider=None):
    """The exact argument list after the binary (the flag profile, §3.6.3)."""
    args = ["app-server", "--listen", "stdio://"]
    for name in DISABLED:
        args += ["--disable", name]
    args += ["-c", 'web_search="disabled"', "-c", "skills.bundled.enabled=false",
             "-c", "skills.include_instructions=false", "-c", f'model_catalog_json="{catalog}"',
             "-c", f'model="{MODEL}"', "-c", 'approval_policy="never"', "-c", 'sandbox_mode="read-only"',
             "-c", 'forced_login_method="chatgpt"', "-c", 'cli_auth_credentials_store="file"']
    if provider:
        args += ["-c", 'model_provider="rooms_probe"', "-c",
                 'model_providers.rooms_probe={name="rooms_probe",base_url="%s",wire_api="responses",'
                 'env_key="ROOMS_PROBE_KEY",request_max_retries=0,stream_max_retries=0}' % provider]
    return args


class CodexRunner(ClaudeRunner):
    def __init__(self, cli="/usr/local/bin/codex", home="/codex-home", state_dir="/state", max_turn_s=180,
                 turns_root="/turns", output_cap=OUTPUT_CAP, popen=subprocess.Popen, run=subprocess.run,
                 environ=None, catalog=CATALOG):
        super().__init__(cli=cli, home=home, state_dir=state_dir, max_turn_s=max_turn_s, turns_root=turns_root,
                         output_cap=output_cap, popen=popen, run=run, environ=environ)
        self.catalog = Path(catalog)
        self.probe_ok = None                 # None: not yet probed; False: failed (unready)
        self._sessions = {}                  # correlation -> active _Session, so Stop can interrupt (C3)

    # ── no-inference checks ─────────────────────────────────────────────────
    def env(self, home=None):
        home = str(home or self.home)
        return {"HOME": home, "CODEX_HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}

    def fingerprint(self):
        out = self.run([self.cli, "--version"], capture_output=True, text=True, timeout=20, env=self.env())
        self.version = ((out.stdout or "").strip().split() or ["unknown"])[-1]
        profile = hashlib.sha256(json.dumps([flags(), sha256_file(self.catalog)]).encode()).hexdigest()
        return {"binary_sha256": sha256_file(os.path.realpath(self.cli)), "version": self.version,
                "profile_sha256": profile}

    def signed_in(self):
        out = self.run([self.cli, "login", "status"], capture_output=True, text=True, timeout=30, env=self.env())
        text = (out.stdout or "") + (out.stderr or "")
        return out.returncode == 0 and "Logged in using ChatGPT" in text            # no API key, no token login

    def home_problems(self):
        """Forbidden or unknown entries in CODEX_HOME (empty = allowed); raises OSError if unreadable."""
        present = []
        for entry in os.listdir(self.home):
            if entry in HOME_FORBIDDEN or not (entry in HOME_ALLOWED or HOME_DATABASE.match(entry)):
                present.append(entry)
        return present

    def proof_path(self):
        return self.state_dir / "proof-codex.json"

    def _check(self, allow_unproven=False):
        if not os.path.isfile(self.cli) or not os.access(self.cli, os.X_OK):
            return False, "runner_unavailable", None
        try:
            fp = self.fingerprint()
            if self.home_problems():
                return False, "startup_inputs", fp
            if not self.signed_in():
                return False, "signed_out", fp
        except (OSError, subprocess.SubprocessError):
            return False, "startup_inputs", None
        if self.probe_ok is None:
            self.probe_ok = self.boundary_probe()
        if not self.probe_ok:
            return False, "runner_boundary", fp
        match = self.proof_matches(fp)
        if not allow_unproven:
            if match is None:
                return False, "not_proven_with_this_runner", fp
            if match is False:
                return False, "runner_changed_since_proof", fp
        return True, None, fp

    # ── the offline boundary probe (§3.6.5) ─────────────────────────────────
    def boundary_probe(self, timeout=60, correlation=None):
        """Run the profile against a loopback fake endpoint: the captured request
        must offer exactly EXPECTED_TOOLS, carry no skills instructions, and a
        scripted view_image call must be refused. No model call."""
        captured, calls = [], [0]

        def sse(event):
            return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

        class Fake(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                captured.append(body)
                calls[0] += 1
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                rid = f"resp_{calls[0]}"
                self.wfile.write(sse({"type": "response.created", "response": {"id": rid}}))
                if calls[0] == 1:
                    item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "view_image",
                            "arguments": json.dumps({"path": "/etc/hostname"})}
                else:
                    item = {"type": "message", "id": "msg_1", "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok"}]}
                self.wfile.write(sse({"type": "response.output_item.done", "item": item}))
                self.wfile.write(sse({"type": "response.completed", "response": {"id": rid, "usage": {
                    "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}}))

            def log_message(self, *_):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        scratch = Path(tempfile.mkdtemp(prefix="probe-", dir=self._turn_root()))
        home, cwd = scratch / "home", scratch / "cwd"
        home.mkdir(mode=0o700)
        cwd.mkdir(mode=0o700)
        try:
            env = {**self.env(home), "ROOMS_PROBE_KEY": "probe"}
            argv = [self.cli, *flags(self.catalog, provider=f"http://127.0.0.1:{server.server_port}/v1")]
            proc = self.popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, start_new_session=True)
            session = _Session(proc, timeout)                    # every read bounded by this deadline (C1)
            if correlation:                                      # a Prove's Stop releases its probe (C1)
                with self._lock:
                    self._procs[correlation], self._sessions[correlation] = proc, session
                    stopped = correlation in self._stopped
                if stopped:
                    self._kill(proc)
            try:
                session.handshake(str(cwd), "Rooms boundary probe.")
                session.request("turn/start", {"threadId": session.thread,
                                               "input": [{"type": "text", "text": "probe"}]})
                items, _ = session.until_completed()
            finally:
                if correlation:
                    with self._lock:
                        self._procs.pop(correlation, None)
                        self._sessions.pop(correlation, None)
                self._kill(proc)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            if len(captured) < 2 or any(i.get("type") not in ALLOWED_ITEMS for i in items):
                return False
            tools = {t.get("name") or t.get("type") for t in captured[0].get("tools", [])}
            first_input = json.dumps(captured[0].get("input", []))
            refused = any(i.get("type") == "function_call_output" and "not allowed" in json.dumps(i)
                          for i in captured[1].get("input", []))
            return tools == EXPECTED_TOOLS and "skills_instructions" not in first_input and refused
        except Exception:
            return False
        finally:
            server.shutdown()
            shutil.rmtree(scratch, ignore_errors=True)

    # ── one turn ────────────────────────────────────────────────────────────
    def stream(self, messages, user, correlation, on_open=None, turn=None, admit=None, **_):
        proof = bool(turn and (turn.get("brief") or {}).get("kind") == "proof")
        if proof:
            self.probe_ok = self.boundary_probe(correlation=correlation)    # fresh before a Prove (§3.6.5)
            with self._lock:
                if correlation in self._stopped:
                    self._stopped.discard(correlation)
                    return {"outcome": "stopped", "text": "", "usage": None, "detail": "stopped_before_start"}
        ok, reason, fp = self._check(allow_unproven=proof)
        if not ok:
            return {"outcome": "refused", "text": "", "usage": None, "detail": reason, "reason": reason}
        cwd = Path(tempfile.mkdtemp(prefix="turn-", dir=self._turn_root()))
        system, transcript, trigger = messages[0]["content"], messages[1]["content"], messages[2]["content"]
        argv = [self.cli, *flags(self.catalog)]
        if proof and turn:
            self._write_pending_proof(turn, fp)
        with self._lock:
            if correlation in self._stopped:
                return {"outcome": "stopped", "text": "", "usage": None, "detail": "stopped_before_start"}
        if admit is not None:
            granted, state = admit()
            if not granted:
                return {"outcome": "not_admitted", "state": state, "text": "", "usage": None,
                        "detail": state or "not_admitted"}
        try:
            proc = self.popen(argv, cwd=str(cwd), env=self.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            return {"outcome": "refused", "text": "", "usage": None, "detail": "spawn_failed",
                    "reason": "runner_unavailable"}
        with self._lock:
            self._procs[correlation] = proc
            stop_now = correlation in self._stopped
        try:
            if stop_now:
                self._kill(proc)
            return self._turn(proc, str(cwd), system, transcript + "\n\n" + trigger, correlation)
        finally:
            if proc.poll() is None:
                self._kill(proc)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            with self._lock:
                self._procs.pop(correlation, None)
                self._stopped.discard(correlation)
            shutil.rmtree(cwd, ignore_errors=True)

    def _turn(self, proc, cwd, system, prompt, correlation):
        timer = threading.Timer(self.max_turn_s, lambda: self._kill(proc, reason="timeout"))
        timer.daemon = True
        timer.start()
        session = _Session(proc, self.max_turn_s + 5, output_cap=self.output_cap)
        with self._lock:
            self._sessions[correlation] = session
        failure, items, final = None, [], None
        try:
            session.handshake(cwd, system)
            session.request("turn/start", {"threadId": session.thread, "input": [{"type": "text", "text": prompt}]})
            items, final = session.until_completed()
        except _Boundary as error:
            failure = "runner_boundary"
            session.interrupt()
            self._kill(proc)
            _ = error
        except _Broken as error:
            failure = str(error) or "malformed"
            self._kill(proc)
        finally:
            timer.cancel()
            with self._lock:
                self._sessions.pop(correlation, None)
        killed_for = getattr(proc, "_rooms_kill_reason", None)
        with self._lock:
            stopped = correlation in self._stopped
        texts = [i.get("text") or "" for i in items if i.get("type") == "agentMessage"]
        text = texts[-1] if texts else ""
        usage = session.usage
        generated = bool(any(t.strip() for t in texts) or session.generated)     # kept on every exit path (C2)
        if stopped:
            return {"outcome": "stopped", "text": text, "usage": usage, "detail": "stopped"}
        if killed_for == "timeout":
            return {"outcome": "error", "text": "", "usage": None, "detail": "timeout"}
        if failure:
            return {"outcome": "error", "text": "", "usage": usage, "detail": failure,
                    **({"reason": failure} if failure == "runner_boundary" else {})}
        status = (final or {}).get("status")
        if status == "completed":
            if text.strip():
                return {"outcome": "done", "text": text, "usage": usage, "detail": "done"}
            return {"outcome": "done", "text": "", "usage": usage, "detail": "empty"}
        info = ((final or {}).get("error") or {}).get("codexErrorInfo")
        unauthorized = info == "unauthorized" or (
            isinstance(info, dict) and (info.get("httpConnectionFailed") or {}).get("httpStatusCode") == 401)
        if unauthorized:
            if generated:
                return {"outcome": "error", "text": "", "usage": usage, "detail": "signed_out_after_start",
                        "reason": "signed_out_after_start"}
            return {"outcome": "refused", "text": "", "usage": None, "detail": "signed_out", "reason": "signed_out"}
        if info == "usageLimitExceeded" and not generated:
            return {"outcome": "refused", "text": "", "usage": None, "detail": "usage_limit", "reason": "usage_limit"}
        return {"outcome": "error", "text": "", "usage": usage, "detail": f"turn_{status or 'incomplete'}"}

    def stop(self, correlation):
        """C3: the app-server's own interruption first when the turn's ids are
        known, then bounded TERM/KILL. Whether the provider stopped stays
        unknown; Records' late-post fence still discards anything after."""
        with self._lock:
            self._stopped.add(correlation)
            proc, session = self._procs.get(correlation), self._sessions.get(correlation)
        if not proc:
            return 200
        if session and session.thread and session.turn:
            session.interrupt()

            def bounded():
                deadline = time.monotonic() + INTERRUPT_GRACE_S
                while time.monotonic() < deadline and proc.poll() is None:
                    time.sleep(0.05)
                if proc.poll() is None:
                    self._kill(proc)
            threading.Thread(target=bounded, daemon=True).start()
        else:
            self._kill(proc)                                  # handshake not done: nothing to interrupt
        return 200


class _Boundary(Exception):
    pass


class _Broken(Exception):
    pass


class _Session:
    """A minimal JSON-RPC client over the child's stdio with bounded reads."""

    def __init__(self, proc, timeout, output_cap=OUTPUT_CAP):
        self.proc, self.timeout, self.cap = proc, timeout, output_cap
        self.deadline = time.monotonic() + timeout
        self.next_id, self.total, self.buf = 0, 0, b""
        self.thread = self.turn = None
        self.usage, self.generated = None, False
        self._send_lock = threading.Lock()                # Stop may interrupt from another thread

    def send(self, message):
        try:
            with self._send_lock:
                self.proc.stdin.write((json.dumps(message) + "\n").encode())
                self.proc.stdin.flush()
        except (OSError, ValueError) as error:
            raise _Broken("exit_before_completion") from error

    def request(self, method, params):
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        return self.next_id

    def interrupt(self):
        if self.thread and self.turn:
            try:
                self.request("turn/interrupt", {"threadId": self.thread, "turnId": self.turn})
            except _Broken:
                pass

    def read(self):
        fd = self.proc.stdout.fileno()
        while b"\n" not in self.buf:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise _Broken("timeout")
            ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
            if not ready:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise _Broken("exit_before_completion")
            self.total += len(chunk)
            if self.total > self.cap:
                raise _Broken("output_cap")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        try:
            message = json.loads(line.decode("utf-8", "replace"))
        except ValueError as error:
            raise _Broken("malformed") from error
        if not isinstance(message, dict):
            raise _Broken("malformed")
        return message

    def response(self, request_id):
        while True:
            message = self.read()
            self.observe(message)
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise _Broken("request_refused")
                return message.get("result") or {}

    def observe(self, message):
        method = message.get("method")
        if method and "id" in message:                      # a server request: decline, then fail closed
            self.send({"id": message["id"], "error": {"code": -32000, "message": "declined by Rooms"}})
            raise _Boundary(method)
        params = message.get("params") or {}
        if method in OUTPUT_DELTAS and str(params.get("delta") or "").strip():
            self.generated = True
        if method == "item/completed" and str((params.get("item") or {}).get("text") or "").strip():
            self.generated = True
        if method in ("item/started", "item/completed"):
            if (params.get("item") or {}).get("type") not in ALLOWED_ITEMS:
                raise _Boundary((params.get("item") or {}).get("type"))
        elif method == "thread/tokenUsage/updated":
            last = ((params.get("tokenUsage") or {}).get("last") or (params.get("tokenUsage") or {}).get("total") or {})
            prompt, completion = last.get("inputTokens"), last.get("outputTokens")
            if (prompt or 0) > 0 or (completion or 0) > 0:
                self.generated = True
            self.usage = {"prompt_tokens": prompt, "completion_tokens": completion}

    def handshake(self, cwd, instructions):
        self.response(self.request("initialize", {"clientInfo": {"name": "minimoi-rooms", "version": "r2"}}))
        self.send({"method": "initialized"})
        result = self.response(self.request("thread/start", {
            "cwd": cwd, "ephemeral": True, "approvalPolicy": "never", "sandbox": "read-only",
            "developerInstructions": instructions}))
        self.thread = (result.get("thread") or {}).get("id")
        if not self.thread:
            raise _Broken("malformed")

    def until_completed(self):
        items = []
        while True:
            message = self.read()
            self.observe(message)
            method, params = message.get("method"), message.get("params") or {}
            if method == "turn/started":
                self.turn = (params.get("turn") or {}).get("id")
            elif method == "item/completed":
                items.append(params.get("item") or {})
            elif method == "turn/completed":
                return items, params.get("turn") or {}
