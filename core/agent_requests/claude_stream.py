"""Claude Code stream-json provider for the connection manager (SCAFFOLDING: written from the installed CLI's own help, run only against a test double so far).

It owns ONE ``claude -p --input-format stream-json --output-format stream-json`` process with a FIXED session id (``--session-id`` the first time, ``--resume`` after), an
explicit allowed-tools list, a permission mode and a spend cap. That is a real, full Claude Code session with its own saved history, started and supervised by the operator,
NOT the desktop deep session and NOT a stand-in under its name. ``probe`` never calls the model (it looks at the process and the events already read). ``deliver`` writes one
user message line and says only that the transport took it. The process is started only by ``start()``, which only an operator action calls.

Unverified live (to be proved by the first real round trip): the exact stream-json event shapes for init/result, and whether a message written while a turn runs is queued.
The reader therefore trusts only ``type`` fields it recognises, and ``probe`` calls a live process with no events yet ``idle`` (ready for stdin), never ``busy``."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone

from core.agent_requests.connection import Delivery, Probe, Provider, _UUID
from core.mc_delegation_runner import launch, launch_env

ROUTE = "claude-code.subscription"                              # the plan sign-in; no key can reach the session (R6.7-e)
LaunchRefused = launch.LaunchRefused                            # a start the launch contract refuses, before any spawn (names only)

PERMISSION_MODES = ("default", "acceptEdits", "plan")          # bypassPermissions is not offered
_TOOL = re.compile(r"^[A-Za-z0-9_:*.\-/ ()]{1,400}$")
BUDGET_MAX_USD = 50.0


def helper_selector(python: str, helper: str, root: str) -> str:
    """The ONE Bash permission a persistent session gets: the ledger helper, with this interpreter and THIS ledger, as a prefix (``--root <root>`` is part of the prefix, so only
    ``helper --root <root> <anything>`` matches). Never a general ``python`` or a bare ``Bash``."""
    return f"Bash({python} {helper} --root {root}:*)"


class ClaudeStreamProvider(Provider):
    name = "claude-stream"
    dedupes_by_correlation = False

    def __init__(self, *, claude_bin: str, session_id: str, cwd: str, allowed_tools: list[str], ledger_root: str, permission_mode: str = "default",
                 max_budget_usd=5.0, resume: bool = False, display_name: str = "", helper_bash: str | None = None, popen=subprocess.Popen, recycle_fraction: float = 0.6,
                 binary_sha256: str | None = None):
        if not os.path.isabs(ledger_root):
            raise ValueError("ledger_root must be an absolute path (there is no default ledger for a managed session)")
        self.ledger_root = ledger_root
        if not os.path.isabs(claude_bin) or not os.access(claude_bin, os.X_OK):
            raise ValueError("claude_bin must be an absolute path to an executable")
        if not _UUID.fullmatch(session_id or ""):
            raise ValueError("session_id must be a UUID")
        if not os.path.isabs(cwd) or not os.path.isdir(cwd):
            raise ValueError("cwd must be an existing absolute directory")
        if permission_mode not in PERMISSION_MODES:
            raise ValueError(f"permission_mode must be one of {PERMISSION_MODES}")
        if not allowed_tools or any(not isinstance(t, str) or not _TOOL.fullmatch(t) for t in allowed_tools):
            raise ValueError("allowed_tools must be a non-empty list of plain tool names")
        for t in allowed_tools:                                         # a Bash permission may only be the exact ledger-helper selector, never general
            if (t == "Bash" or t.startswith("Bash(") or t.startswith("Bash ")) and t != helper_bash:
                raise ValueError("a Bash permission must be exactly the ledger-helper selector, not a general one")
        # The cap may be a number or a function that reads the limits registry at each process start (#296): a changed cap applies at the next start, never mid-process.
        self._cap_source = max_budget_usd if callable(max_budget_usd) else (lambda: max_budget_usd)
        first = self._cap_now()
        if not (0 < first <= BUDGET_MAX_USD):
            raise ValueError(f"max_budget_usd must be above 0 and at most {BUDGET_MAX_USD}")
        if not (0 < recycle_fraction < 1):
            raise ValueError("recycle_fraction must be between 0 and 1")
        self.recycle_fraction = float(recycle_fraction)
        self.claude_bin, self.session_id, self.cwd = claude_bin, session_id, cwd
        self.binary_sha256 = binary_sha256                 # the pin (--claude-bin-sha256); a start without one is refused binary_unpinned
        self.allowed_tools, self.permission_mode, self.resume = list(allowed_tools), permission_mode, resume
        self.max_budget_usd = first                      # kept for callers that read it; the effective cap of a live process is _proc_cap
        self._proc_cap = None                            # the cap THIS process was started with
        self._total = 0.0                                # what THIS process has added to the session's cost (its allowance is spent against this)
        self._child = ""                                 # an id for THIS process (a label on its records)
        self.session_total_hint = None                   # a function returning the last session total on record (the manager sets it); a resumed process continues from it
        self._origin = 0.0                               # the session total when this process started (None: not known yet)
        self._last_cum = 0.0                             # the last session total seen (Claude's total_cost_usd is for the whole SESSION, so a resumed process reports the old history too)
        self._seq = 0
        self.usage_sink = None                           # called with each cost record (the manager appends it to the day's usage file); a record without a cost says so
        self._unknown = False                            # a result arrived without a readable cost: usage may be undercounted until the process is replaced
        self._turns = 0
        self._recycles = 0
        self.display_name = re.sub(r"[^\w .\-]", "", display_name)[:60]
        self._popen = popen
        self._proc = None
        self._busy = False
        self._ref = ""
        self._attention = ""
        self._closed = False
        self._lock = threading.Lock()

    def _cap_now(self) -> float:
        try:
            v = float(self._cap_source())
        except (TypeError, ValueError):
            v = 5.0
        return v if v == v else 5.0

    def argv(self, cap: float | None = None, floor: float = 0.0) -> list[str]:
        """The exact command line, an allowlisted array (never a shell string). ``floor`` is the session's cost so far: Claude's budget flag is compared with the SESSION total, so a
        process's allowance (``cap``) is added on top of it."""
        cap = self._cap_now() if cap is None else cap
        out = [self.claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--resume" if self.resume else "--session-id", self.session_id,
               "--permission-mode", self.permission_mode, "--allowedTools", ",".join(self.allowed_tools), "--max-budget-usd", f"{cap + max(0.0, floor):g}"]
        if self.display_name:
            out += ["--name", self.display_name]
        return out

    def child_env(self) -> dict:
        """The session's environment, from the one launch policy (``launch_env``), never the whole parent environment: a provider key or token in this
        process's environment would make Claude Code bill the API instead of the plan sign-in. Extras by exact name only (``launch_env.EXTRA_ALLOW``): the
        child's own ledger (never the global default), ``DISABLE_AUTOUPDATER`` so the pinned CLI never replaces itself, and a UTF-8 ``LANG``."""
        return launch_env.child_env({"MINIMOI_AGENT_REQUESTS_DIR": self.ledger_root, "DISABLE_AUTOUPDATER": "1", "LANG": "en_US.UTF-8"})

    def verify_pin(self) -> str:
        """The binary's digest when it matches its pin (launch contract step 1), else ``LaunchRefused('binary_unpinned')``."""
        return launch.verify_binary(self.claude_bin, self.binary_sha256)

    def launch_record(self, env: dict) -> dict:
        """The launch contract around the environment (``launch.connection_start``): the pinned binary, the sign-in and the key-setting checks
        (names only), and the launch record. Raises ``launch.LaunchRefused`` before any spawn."""
        return launch.connection_start(tool="claude-code", route_name=ROUTE, binary=self.claude_bin, pinned=self.binary_sha256, env=env, parent=os.environ,
                                       project=self.cwd, argv=(), at=datetime.now(timezone.utc).isoformat(), session_ref=self.session_id)

    # ── operator action ──
    def start(self) -> None:
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None and not self._closed
        if alive:
            return
        self.close()                                                                # a previous process that lives but has a closed transport is ended first
        env = self.child_env()
        self._launched(self.launch_record(env))                                     # refused (or unrecorded) means never started
        with self._lock:
            self._closed = False
            self._ref, self._attention = "", ""
            cap = self._cap_now()
            if not (0 < cap <= BUDGET_MAX_USD):
                cap = min(max(cap, 0.01), BUDGET_MAX_USD)
            self._proc_cap, self._total, self._seq, self._unknown = cap, 0.0, 0, False
            self._child = uuid.uuid4().hex[:12]
            origin = 0.0                                                            # a brand-new session starts at zero; a resumed one continues from the last total on record
            if self.resume:
                try:
                    hint = self.session_total_hint() if self.session_total_hint else None
                except Exception:
                    hint = None
                origin = float(hint) if isinstance(hint, (int, float)) and not isinstance(hint, bool) and hint == hint and hint >= 0 else None
            self._origin = self._last_cum = origin
            self._proc = self._popen(self.argv(cap, origin or 0.0), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=self.cwd, text=True, bufsize=1,
                                     env=env)
            self._busy = False
            self.resume = True                                  # any later start continues the same saved session
            threading.Thread(target=self._read, args=(self._proc,), daemon=True).start()

    def _read(self, proc) -> None:
        """Reads ONE process's events. Every state change is conditional on this still being the current process, so late events from an old process (after a restart) can never
        change the replacement's busy state or identity. Valid JSON of an unexpected shape is ignored; if the stream ends or the reader fails while the process still lives, the
        transport is marked closed (offline), so the supervisor restarts it."""
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                try:
                    self._handle(proc, ev)
                except Exception:
                    with self._lock:
                        if self._proc is proc and not self._attention:
                            self._attention = "malformed_event"
        except Exception:
            pass
        with self._lock:
            if self._proc is proc:
                self._busy = False
                if proc.poll() is None:
                    self._closed, self._attention = True, "transport_closed"

    def _handle(self, proc, ev: dict) -> None:
        kind = ev.get("type")
        with self._lock:
            if self._proc is not proc:
                return
            if kind == "system" and ev.get("subtype") == "init" and isinstance(ev.get("session_id"), str):
                self._ref = ev["session_id"][:80]                           # the manager compares this with the configured id and fails closed on a mismatch
            elif kind == "result":
                self._busy = False                                          # the turn ended: ready for the next notice
                cost = ev.get("total_cost_usd")                             # cumulative for the SESSION: a resumed process reports the old history too (seen live 11 Oct: 11.89, 12.26, 12.55, ... across restarts)
                self._seq += 1
                if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost == cost and cost >= 0:
                    cost = float(cost)
                    baseline_unknown = self._last_cum is None
                    if baseline_unknown:                                    # no total on record to measure from: this first result sets the baseline, and the record SAYS its own cost is not separable
                        delta, self._origin = 0.0, cost
                    elif cost >= self._last_cum:
                        delta = cost - self._last_cum
                    else:
                        delta = cost                                        # the total went DOWN: a different (new) session, so everything in it is new
                        self._origin = 0.0
                    rec = {"child": self._child, "seq": self._seq, "cumulative_usd": cost, "delta_usd": round(delta, 6), "unknown": False,
                           "origin_usd": self._origin, **({"baseline_unknown": True} if baseline_unknown else {})}                      # the session total when this process started, so a day's first record is measured from it
                    self._last_cum = cost
                    self._total = max(0.0, cost - (self._origin or 0.0))
                else:
                    rec = {"child": self._child, "seq": self._seq, "cumulative_usd": None, "delta_usd": 0.0, "unknown": True}
                    self._unknown = True                                    # never counted as zero: the manager refuses new work until the process is replaced
                self._turns += 1
                if self.usage_sink is not None:
                    try:
                        self.usage_sink(rec)
                    except Exception:
                        self._unknown = True                                # the record could not be kept: usage is unknown, not zero
                raw = ev.get("permission_denials")
                denied = [d.get("tool_name", "?") for d in raw if isinstance(d, dict)] if isinstance(raw, list) else []
                if denied:
                    self._attention = "permission_denied:" + ",".join(sorted(set(str(x) for x in denied)))[:100]     # the normal approval policy blocked a tool: shown, never auto-approved
                elif ev.get("is_error") or (isinstance(ev.get("subtype"), str) and ev["subtype"] != "success"):
                    self._attention = f"turn_failed:{str(ev.get('subtype') or 'error')[:40]}"

    # ── cost and the restart before the cap (#296) ──
    def usage_unknown(self) -> bool:
        with self._lock:
            return self._unknown

    def usage(self) -> dict:
        with self._lock:
            return {"process_spent_usd": round(self._total, 4), "process_cap_usd": self._proc_cap, "turns": self._turns, "recycles": self._recycles, "child": self._child}

    def recycle_due(self) -> bool:
        """True when this idle process has used enough of its cap that one more turn could trip it: it is ended and a fresh one resumes the SAME session with a fresh cap."""
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None and not self._closed
            return bool(alive and not self._busy and self._proc_cap and self._total >= self.recycle_fraction * self._proc_cap)

    def recycle(self) -> None:
        self.close()
        self.start()
        with self._lock:
            self._recycles += 1

    # ── Provider ──
    def probe(self) -> Probe:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or self._closed:
                return Probe("offline", self._ref, self._attention if self._closed else "")
            return Probe("busy" if self._busy else "idle", self._ref or self.session_id, self._attention)

    def deliver(self, notice: str, correlation: str) -> Delivery:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or self._closed:
                return Delivery("failed", reason="not_running")          # nothing was written: definitely not taken
            if self._ref and self._ref != self.session_id:
                return Delivery("refused", reason="identity_mismatch")   # this process is a different session than the configured one: nothing was written
            line = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": notice}]}}) + "\n"
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (OSError, ValueError):
                return Delivery("unknown", reason="write_error")         # part of it may have gone
            self._busy = True
            self._attention = ""
            return Delivery("accepted", native_ref=self._ref or self.session_id)

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
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
