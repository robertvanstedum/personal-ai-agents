"""Codex app-server provider for the connection manager (SCAFFOLDING until a real idle-to-MC round trip proves it).

Protocol (proved live by Codex on 11 Oct 2026 against the installed binary; JSON lines over stdio, JSON-RPC-like):
  initialize {clientInfo} -> result, then the notification ``initialized``;
  thread/start {cwd, approvalPolicy, sandbox} -> result.thread.id, or thread/resume {threadId} after a restart (the answered id must equal the configured one);
  turn/start {threadId, input:[{type:text,text}]} -> result.turn.id with status inProgress = the transport accepted the turn (a JSON-RPC error is a refusal; EOF or a timeout
  after the write is UNKNOWN); turn/started = busy; turn/completed = idle (a status other than completed, or an error, is attention); item/completed (agentMessage) is the reply.
A server request (an id and a method) is an approval the session wants. This provider NEVER approves: it answers with an error, records the attention, and the owner sees it.
The provider owns ONE explicitly identified thread. It never sends while busy (the manager checks, and so does ``deliver``), and it starts nothing by itself: ``start()`` is an
operator action. Approval policy ``never`` and the ``danger-full-access`` sandbox are not offered."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from datetime import datetime, timezone

from core.agent_requests.connection import Delivery, Probe, Provider
from core.mc_delegation_runner import launch, launch_env

ROUTE = "codex.subscription"                                   # the ChatGPT plan sign-in; no key can reach the app-server (R6.7-e)

APPROVAL_POLICIES = ("on-request", "untrusted")           # the installed protocol also has "never": not offered
SANDBOXES = ("read-only", "workspace-write")           # "danger-full-access" is not offered
_POLICY_TYPE = {"read-only": "readOnly", "workspace-write": "workspaceWrite"}
_THREAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,99}$")


class CodexAppServerProvider(Provider):
    name = "codex-app-server"
    dedupes_by_correlation = False

    def __init__(self, *, codex_bin: str, cwd: str, ledger_root: str, thread_id: str | None = None, approval_policy: str = "on-request", sandbox: str = "workspace-write",
                 writable_roots: list[str] | None = None, request_timeout: float = 30.0, popen=subprocess.Popen, binary_sha256: str | None = None):
        if not os.path.isabs(codex_bin) or not os.access(codex_bin, os.X_OK):
            raise ValueError("codex_bin must be an absolute path to an executable")
        if not os.path.isabs(cwd) or not os.path.isdir(cwd):
            raise ValueError("cwd must be an existing absolute directory")
        if not os.path.isabs(ledger_root):
            raise ValueError("ledger_root must be an absolute path (there is no default ledger for a managed session)")
        if approval_policy not in APPROVAL_POLICIES:
            raise ValueError(f"approval_policy must be one of {APPROVAL_POLICIES}")
        if sandbox not in SANDBOXES:
            raise ValueError(f"sandbox must be one of {SANDBOXES}")
        if thread_id is not None and not _THREAD.fullmatch(thread_id):
            raise ValueError("thread_id must be a plain id")
        if not isinstance(request_timeout, (int, float)) or not (0 < request_timeout <= 300):
            raise ValueError("request_timeout must be between 0 and 300 seconds")
        roots = [ledger_root, *(writable_roots or [])]
        home = os.path.expanduser("~")
        for r in roots:
            if not isinstance(r, str) or not os.path.isabs(r) or os.path.realpath(r) in ("/", home, os.path.realpath(home)) or ".." in r.split(os.sep):
                raise ValueError("writable roots must be specific absolute folders (never / or the home folder)")
        self.writable_roots = list(dict.fromkeys(roots))                    # the ledger is always writable so the session can record its receipt and result; nothing else by default
        self.codex_bin, self.cwd, self.ledger_root = codex_bin, cwd, ledger_root
        self.binary_sha256 = binary_sha256                 # the pin (--codex-bin-sha256); a start without one is refused binary_unpinned
        self.thread_id, self.approval_policy, self.sandbox, self.timeout, self._popen = thread_id, approval_policy, sandbox, float(request_timeout), popen
        self._proc = None
        self._ready = False
        self._busy = False
        self._attention = ""
        self._next_id = 10
        self._waiting: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()

    def argv(self) -> list[str]:
        return [self.codex_bin, "app-server", "--listen", "stdio://"]

    def child_env(self) -> dict:
        """The app-server's environment, from the one launch policy (``launch_env``), never the whole parent environment: a provider key or token in this
        process's environment could make Codex bill the API instead of the ChatGPT plan. Extras by exact name only (``launch_env.EXTRA_ALLOW``): the
        child's own ledger and a UTF-8 ``LANG`` for the commands it runs."""
        return launch_env.child_env({"MINIMOI_AGENT_REQUESTS_DIR": self.ledger_root, "LANG": "en_US.UTF-8"})

    def verify_pin(self) -> str:
        """The binary's digest when it matches its pin (launch contract step 1), else ``LaunchRefused('binary_unpinned')``."""
        return launch.verify_binary(self.codex_bin, self.binary_sha256)

    def launch_record(self, env: dict) -> dict:
        """The launch contract around the environment (``launch.connection_start``): the pinned binary, the ChatGPT sign-in, and the key-setting
        checks (names only; the app-server reads the user configuration, so it is checked), then the launch record. Raises ``launch.LaunchRefused``
        before any spawn."""
        return launch.connection_start(tool="codex", route_name=ROUTE, binary=self.codex_bin, pinned=self.binary_sha256, env=env, parent=os.environ,
                                       project=self.cwd, argv=self.argv(), at=datetime.now(timezone.utc).isoformat(), session_ref=self.thread_id)

    # ── operator action ──
    def start(self, *, create: bool = False) -> str:
        """Start the app-server and bind it to the configured thread: ``thread/resume`` of the stored id, or (only when ``create`` and no id is configured) ``thread/start``.
        Returns the thread id. A mismatch between the configured and the answered id fails closed and closes the process."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None and self._ready:
                return self.thread_id or ""
        self.close()                                                                    # a previous process that is alive but unusable (closed transport) is ended first
        env = self.child_env()
        self._launched(self.launch_record(env))                                         # refused (or unrecorded) means never started
        with self._lock:
            self._ready, self._busy, self._attention, self._waiting = False, False, "", {}
            self._proc = self._popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=self.cwd, text=True, bufsize=1,
                                     env=env)
            proc = self._proc
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        try:
            self._call("initialize", {"clientInfo": {"name": "minimoi_connection", "title": "MiniMoi connection manager", "version": "0.1"}})
            self._notify("initialized", {})
            if self.thread_id:
                got = self._call("thread/resume", {"threadId": self.thread_id, "cwd": self.cwd, "approvalPolicy": self.approval_policy, "sandbox": self.sandbox})
                thread = got.get("thread") if isinstance(got.get("thread"), dict) else {}
                answered = thread.get("id")
                if answered != self.thread_id:
                    self.close()
                    raise RuntimeError(f"identity_mismatch: configured thread {self.thread_id}, answered {answered}")
                wrong = [k for k, want, have in (("cwd", self.cwd, got.get("cwd")), ("approvalPolicy", self.approval_policy, got.get("approvalPolicy")),
                                                  ("sandbox", _POLICY_TYPE[self.sandbox], (got.get("sandbox") if isinstance(got.get("sandbox"), dict) else {}).get("type")))
                         if have is not None and have != want]            # the resumed session must run under the configured working directory, approval policy and sandbox
                if wrong:
                    self.close()
                    raise RuntimeError("policy_mismatch: " + ",".join(wrong))
            elif create:
                got = self._call("thread/start", {"cwd": self.cwd, "approvalPolicy": self.approval_policy, "sandbox": self.sandbox})
                tid = (got.get("thread") if isinstance(got.get("thread"), dict) else {}).get("id")
                if not isinstance(tid, str) or not _THREAD.fullmatch(tid):
                    self.close()
                    raise RuntimeError("thread/start did not answer a thread id")
                self.thread_id = tid
            else:
                self.close()
                raise RuntimeError("no thread is configured; creating the first one needs an explicit create")
        except Exception:
            self.close()
            raise
        with self._lock:
            if self._proc is proc:
                self._ready = True
        return self.thread_id or ""

    # ── JSON lines ──
    def _write(self, msg: dict) -> None:
        proc = self._proc
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._write({"method": method, "params": params})

    def _call(self, method: str, params: dict) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            box: queue.Queue = queue.Queue(maxsize=1)
            self._waiting[rid] = box
        try:
            self._write({"method": method, "params": params, "id": rid})
            got = box.get(timeout=self.timeout)
        except queue.Empty:
            raise TimeoutError(f"{method} timed out") from None
        finally:
            with self._lock:
                self._waiting.pop(rid, None)
        if got is None:
            raise EOFError(f"{method}: the app-server closed")
        if "error" in got:
            raise _RpcError(got["error"])
        result = got.get("result")
        return result if isinstance(result, dict) else {}

    def _read(self, proc) -> None:
        """Reads ONE process's lines; every state change is conditional on this still being the current process. Any valid-JSON line of an unexpected shape is ignored (or noted as
        attention), never fatal; if the stream ends or the reader itself fails while the process is still alive, the transport is marked closed, which reads as offline, so the supervisor
        restarts it instead of leaving a dead reader behind a healthy-looking idle."""
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                try:
                    self._handle(proc, msg)
                except Exception:                                                       # a surprising shape in one message must not kill the reader
                    with self._lock:
                        if self._proc is proc and not self._attention:
                            self._attention = "malformed_event"
        except Exception:
            pass
        with self._lock:
            if self._proc is proc:
                self._busy = False
                if proc.poll() is None:                                                 # the stream closed but the process lives: the transport is gone
                    self._ready, self._attention = False, "transport_closed"
                for box in list(self._waiting.values()):
                    try:
                        box.put_nowait(None)                                            # the app-server closed: wake anyone waiting
                    except queue.Full:
                        pass

    def _handle(self, proc, msg: dict) -> None:
        with self._lock:
            if self._proc is not proc:
                return
            method, mid = msg.get("method"), msg.get("id")
            if mid is not None and not isinstance(mid, (int, str)):
                return                                                                  # an unhashable or odd id: ignored
            if method is None and mid is not None:                                      # a response to one of our requests
                box = self._waiting.get(mid)
                if box is not None:
                    try:
                        box.put_nowait(msg)                                             # a duplicate response finds the slot taken and is ignored: never blocks the reader
                    except queue.Full:
                        pass
                return
            if not isinstance(method, str):
                return
            params = msg.get("params")
            params = params if isinstance(params, dict) else {}
            if mid is not None:                                                         # a server request: an approval or similar. Never approved here.
                self._attention = f"approval_blocked:{method[:80]}"
                try:
                    self._write({"id": mid, "error": {"code": -32601, "message": "Not approved: the connection manager never approves actions; the owner decides."}})
                except (OSError, ValueError, AttributeError):
                    pass
                return
            if method == "turn/started":
                self._busy = True
            elif method == "turn/completed":
                self._busy = False
                turn = params.get("turn")
                if not isinstance(turn, dict):
                    self._attention = "malformed_event:turn/completed"                   # the turn ended but the answer is unreadable: the owner is told
                    return
                status = turn.get("status")
                if turn.get("error") or (status and status != "completed"):
                    self._attention = f"turn_failed:{str(status or 'error')[:40]}"

    # ── Provider ──
    def probe(self) -> Probe:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or not self._ready:
                return Probe("offline", self.thread_id or "")
            return Probe("busy" if self._busy else "idle", self.thread_id or "", self._attention)

    def _turn_policy(self) -> dict:
        if self.sandbox == "read-only":
            return {"type": "readOnly", "networkAccess": False}
        return {"type": "workspaceWrite", "writableRoots": list(self.writable_roots), "networkAccess": False, "excludeSlashTmp": True, "excludeTmpdirEnvVar": True}

    def deliver(self, notice: str, correlation: str) -> Delivery:
        with self._lock:
            proc, ready, busy = self._proc, self._ready, self._busy
            if proc is None or proc.poll() is not None or not ready:
                return Delivery("failed", reason="not_running")                         # nothing was written
            if busy:
                return Delivery("refused", reason="busy")                                # never send while busy, whatever the backend would do
            self._busy, self._attention = True, ""                                       # RESERVED before the write: a fast completion or an approval read next is kept, not overwritten
        try:
            got = self._call("turn/start", {"threadId": self.thread_id, "approvalPolicy": self.approval_policy, "sandboxPolicy": self._turn_policy(),
                                            "input": [{"type": "text", "text": notice}]})
        except _RpcError as exc:
            with self._lock:
                self._busy = False                                                       # an error answered: the turn did not start
            return Delivery("refused", reason=f"rpc_error:{exc.code}")
        except (TimeoutError, EOFError, OSError, ValueError):
            with self._lock:
                self._attention = self._attention or "turn_start_unconfirmed"            # it may have started: stays busy until the session says otherwise, and is shown
            return Delivery("unknown", reason="no_answer_after_write")
        turn = got.get("turn") if isinstance(got.get("turn"), dict) else {}
        if not isinstance(turn.get("id"), str) or not turn["id"] or turn.get("status") not in ("inProgress", "completed"):
            with self._lock:
                self._attention = self._attention or "turn_start_unconfirmed"
            return Delivery("unknown", reason="unconfirmed_turn")                        # an empty or odd answer is not an acceptance
        return Delivery("accepted", native_ref=turn["id"][:100])

    def close(self) -> None:
        with self._lock:
            proc, self._proc, self._ready = self._proc, None, False
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


class _RpcError(Exception):
    def __init__(self, err):
        self.code = (err or {}).get("code") if isinstance(err, dict) else "error"
        super().__init__(str(err)[:200])
