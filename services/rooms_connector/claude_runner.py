"""The Claude Code runner (ROOMS_R2.md §3.3, §3.5, §3.7).

It plays the MC relay's part for the R1 worker: ready(), stream(), stop().
One CLI process per turn, in its own process group, in a fresh empty working
directory, with a stripped environment and NO tools, NO MCP servers and NO
setting sources. The CLI's own `system/init` event is checked inside the turn
before any answer is accepted; a missing, malformed or non-empty one fails
closed. Startup inputs that the flags do not cover (instruction files, rules,
project memory, managed settings) are checked before every turn and must be
absent; anything unreadable fails closed.

Outcome rules: a check that fails before any process starts is "refused"
(definitive, no model request). Once a process has started, anything but a
clean result is "error" (the worker records it as uncertain), or "stopped".

Nothing here prints prompt text, replies or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

OUTPUT_CAP = 1_000_000
SETTINGS_KEYS_ALLOWED = {"theme", "agentPushNotifEnabled", "inputNeededNotifEnabled"}
MANAGED_DIR = Path("/Library/Application Support/ClaudeCode")
FLAGS = ["-p", "--output-format", "stream-json", "--verbose", "--tools", "",
         "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
         "--disable-slash-commands", "--no-session-persistence"]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def project_key(path):
    """Claude Code's per-project folder name: the absolute path with every
    non-alphanumeric character as a dash."""
    import re
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


class StartupInputs(Exception):
    pass


def startup_problems(home, cwd):
    """Return a list of present startup inputs (empty = allowed). Raises
    StartupInputs if something cannot be read (fail closed)."""
    home, cwd = Path(home), Path(cwd).resolve()
    present = []

    def exists(p):
        try:
            return p.exists() or p.is_symlink()
        except OSError as error:
            raise StartupInputs(f"unreadable: {p.name}") from error
    for p in (home / ".claude" / "CLAUDE.md", home / ".claude" / "rules", MANAGED_DIR):
        if exists(p):
            present.append(str(p))
    d = cwd
    while True:
        for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", ".claude/rules"):
            if exists(d / name):
                present.append(str(d / name))
        if d.parent == d:
            break
        d = d.parent
    projects = home / ".claude" / "projects"
    if exists(projects / project_key(cwd)):
        present.append("project memory folder for the working directory")
    elif exists(projects):
        # Fail toward "present" if any project folder mentions this working directory's own name.
        try:
            leaf = project_key(cwd.name)
            if any(leaf in entry.name for entry in projects.iterdir()):
                present.append("a project folder naming the working directory")
        except OSError as error:
            raise StartupInputs("unreadable: projects") from error
    settings = home / ".claude" / "settings.json"
    if exists(settings):
        try:
            keys = set(json.loads(settings.read_text()).keys())
        except (OSError, ValueError) as error:
            raise StartupInputs("unreadable: settings.json") from error
        extra = keys - SETTINGS_KEYS_ALLOWED
        if extra:
            present.append("settings.json keys: " + ",".join(sorted(extra)))
    return present


class ClaudeRunner:
    def __init__(self, cli="/opt/homebrew/bin/claude", home=None, state_dir=None, max_turn_s=180,
                 turns_root="/private/tmp/rooms-claude-turns", output_cap=OUTPUT_CAP, popen=subprocess.Popen,
                 run=subprocess.run, environ=None):
        self.cli = cli
        self.home = home or os.path.expanduser("~")
        self.state_dir = Path(state_dir or os.path.expanduser("~/minimoi-staging/data/rooms-connector"))
        self.max_turn_s = max_turn_s
        self.turns_root = Path(turns_root)
        self.output_cap = output_cap
        self.popen, self.run = popen, run
        self.base_env = environ or os.environ
        self.unready_reason = None
        self.version = None
        self._procs = {}
        self._stopped = set()
        self._lock = threading.Lock()

    # ── no-inference checks ─────────────────────────────────────────────────
    def env(self):
        keep = {k: self.base_env[k] for k in ("USER", "LOGNAME", "TMPDIR") if k in self.base_env}
        return {**keep, "HOME": self.home, "PATH": "/usr/bin:/bin:/opt/homebrew/bin", "LANG": "en_US.UTF-8"}

    def fingerprint(self):
        """Binary hash, version and flag profile: what a proof is bound to."""
        out = self.run([self.cli, "--version"], capture_output=True, text=True, timeout=20, env=self.env())
        self.version = (out.stdout or "").strip().split(" ")[0] or "unknown"
        profile = hashlib.sha256(json.dumps(FLAGS).encode()).hexdigest()
        return {"binary_sha256": sha256_file(os.path.realpath(self.cli)), "version": self.version, "profile_sha256": profile}

    def signed_in(self):
        out = self.run([self.cli, "auth", "status"], capture_output=True, text=True, timeout=30, env=self.env())
        try:
            status = json.loads(out.stdout)
        except ValueError:
            return False
        return status.get("loggedIn") is True and status.get("authMethod") == "claude.ai"

    def proof_path(self):
        return self.state_dir / "proof-claude-code.json"

    def proof_matches(self, fp):
        try:
            proof = json.loads(self.proof_path().read_text())
        except (OSError, ValueError):
            return None
        return all(proof.get(k) == fp[k] for k in ("binary_sha256", "version", "profile_sha256"))

    def check(self, allow_unproven=False):
        """(ok, reason) with no model request."""
        if not os.path.isfile(self.cli) or not os.access(self.cli, os.X_OK):
            return False, "runner_unavailable"
        try:
            fp = self.fingerprint()
            if not self.signed_in():
                return False, "signed_out"
            probe = Path(tempfile.mkdtemp(prefix="probe-", dir=self._turn_root()))
            try:
                if startup_problems(self.home, probe):
                    return False, "startup_inputs"
            finally:
                probe.rmdir()
        except (StartupInputs, OSError, subprocess.SubprocessError):
            return False, "startup_inputs"
        match = self.proof_matches(fp)
        if not allow_unproven:
            if match is None:
                return False, "not_proven_with_this_runner"
            if match is False:
                return False, "runner_changed_since_proof"
        return True, None

    def ready(self):
        ok, reason = self.check()
        self.unready_reason = reason
        return ok

    def _turn_root(self):
        self.turns_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.turns_root, 0o700)
        return self.turns_root

    # ── one turn ────────────────────────────────────────────────────────────
    def stream(self, messages, user, correlation, on_open=None, turn=None, **_):
        proof = bool(turn and (turn.get("brief") or {}).get("kind") == "proof")
        ok, reason = self.check(allow_unproven=proof)
        if not ok:
            return {"outcome": "refused", "text": "", "usage": None, "detail": reason, "reason": reason}
        cwd = Path(tempfile.mkdtemp(prefix="turn-", dir=self._turn_root()))
        try:
            if startup_problems(self.home, cwd):
                return {"outcome": "refused", "text": "", "usage": None, "detail": "startup_inputs", "reason": "startup_inputs"}
        except StartupInputs:
            return {"outcome": "refused", "text": "", "usage": None, "detail": "startup_inputs", "reason": "startup_inputs"}
        system, transcript, trigger = messages[0]["content"], messages[1]["content"], messages[2]["content"]
        with self._lock:
            if correlation in self._stopped:
                return {"outcome": "stopped", "text": "", "usage": None, "detail": "stopped_before_start"}
        try:
            proc = self.popen([self.cli, *FLAGS, "--system-prompt", system], cwd=str(cwd), env=self.env(),
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              start_new_session=True, text=True)
        except OSError:
            return {"outcome": "refused", "text": "", "usage": None, "detail": "spawn_failed", "reason": "runner_unavailable"}
        with self._lock:
            self._procs[correlation] = proc
            stop_now = correlation in self._stopped
        if stop_now:
            self._kill(proc)
        result = self._read(proc, transcript + "\n\n" + trigger, correlation)
        with self._lock:
            self._procs.pop(correlation, None)
            self._stopped.discard(correlation)
        if result["outcome"] == "done" and proof:
            self._pending_proof = self.fingerprint()
        return result

    def _read(self, proc, prompt, correlation):
        timer = threading.Timer(self.max_turn_s, lambda: self._kill(proc, reason="timeout"))
        timer.daemon = True
        timer.start()
        text, usage, init_ok, result_seen, size, failure = [], None, False, False, 0, None
        try:
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except OSError:
                pass
            for line in proc.stdout:
                size += len(line)
                if size > self.output_cap:
                    failure = "output_cap"
                    self._kill(proc)
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not init_ok:
                    if (event.get("type") == "system" and event.get("subtype") == "init"
                            and event.get("tools") == [] and event.get("mcp_servers") == []
                            and not event.get("plugins")):
                        init_ok = True
                        continue
                    failure = "runner_boundary"            # missing, malformed or non-empty init
                    self._kill(proc)
                    break
                if event.get("type") == "result":
                    result_seen = True
                    if event.get("is_error") is False and isinstance(event.get("result"), str):
                        text = [event["result"]]
                        u = event.get("usage") or {}
                        usage = {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens")}
                    else:
                        failure = "result_error"
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            failure = failure or "no_exit"
        finally:
            timer.cancel()
        killed_for = getattr(proc, "_rooms_kill_reason", None)
        with self._lock:
            stopped = correlation in self._stopped
        if stopped:
            return {"outcome": "stopped", "text": "".join(text), "usage": usage, "detail": "stopped"}
        if failure or killed_for or not result_seen or not text or not "".join(text).strip():
            return {"outcome": "error", "text": "", "usage": None,
                    "detail": failure or killed_for or ("no_result" if not result_seen else "empty")}
        return {"outcome": "done", "text": "".join(text), "usage": usage, "detail": "done"}

    def _kill(self, proc, reason=None):
        if reason and not getattr(proc, "_rooms_kill_reason", None):
            proc._rooms_kill_reason = reason
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return

        def hard():
            time.sleep(5)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        threading.Thread(target=hard, daemon=True).start()

    def stop(self, correlation):
        with self._lock:
            self._stopped.add(correlation)
            proc = self._procs.get(correlation)
        if proc:
            self._kill(proc)
        return 200

    # ── proof evidence, written only after Records accepted the proof reply ─
    def record_proof(self, turn):
        if (turn.get("brief") or {}).get("kind") != "proof":
            return
        fp = getattr(self, "_pending_proof", None) or self.fingerprint()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.proof_path().with_suffix(".tmp")
        tmp.write_text(json.dumps({**fp, "proof_turn": turn["id"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
        os.chmod(tmp, 0o600)
        tmp.replace(self.proof_path())
        self._pending_proof = None
