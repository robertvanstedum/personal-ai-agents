"""The Mac delegation runner: claim one task, run it in a fresh workspace, report back.

Harness v0.3.1 §6.2. The dispatcher (Codex's) owns work state, grants and the return path into
the MiniMoi conversation; this runner only executes what it claimed:

  resend pending results → heartbeat → claim (lease, generation) → validate envelope → fresh
  workspace → run the agent CLI → renew lease / poll cancel / enforce deadline and output bound →
  collect evidence → persist the terminal result → post it (at least once, across restarts)

* Heartbeat is not pickup: ``online``/``busy`` is reported separately from claims.
* The child's whole process group is owned for its lifetime: whatever happens (dispatcher errors,
  exceptions, lost lease, the parent exiting early while descendants linger), the group is
  stopped before run_once returns.
* A lost lease, or losing contact with the dispatcher's control calls, stops the work and posts
  no completion: a stale worker never completes someone else's claim.
* Durable stages per request and generation (``<task>.stage.json``): ``started`` (before the
  child is spawned, with its process group), ``exited`` (status and exit code), ``artifacts``.
  A reclaim after a crash resumes from the last stage: an exited child's real outcome is kept, and
  a child whose completion is unknown is reported explicitly as ``interrupted``, never re-run and
  never mistaken for a workspace conflict.
* The terminal result (status, artifacts, stable event id) is written durably before it is sent,
  and resent on later runs until the dispatcher returns a matching ``accepted``/``already_applied``
  acknowledgement; anything else leaves it pending.
* Output is captured through a pipe with a hard byte limit: the log never exceeds the bound, even
  for a child that writes a burst and exits immediately; exceeding it is ``output_limit``.
* A cost cap is only accepted for actors with a real budget-binding adapter
  (``cost_enforcers[actor].apply(argv, env, cap_usd)``); otherwise the task is refused as
  unenforceable rather than run under a cap nobody checks.
* Artifacts carry author, sha256, size and ``review_status: unreviewed``.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import commands, envelope as env_mod, workspace

ACK_STATES = ("accepted", "already_applied")


@dataclass
class RunnerConfig:
    runner_id: str
    actors: tuple                       # which CLIs this runner can run
    scopes: tuple
    repositories: dict                  # name → local mirror path
    workspace_root: Path
    artifact_root: Path
    max_cost_usd: float
    cost_enforcers: dict = field(default_factory=dict)   # actor → adapter binding the envelope's cap
    lease_renew_s: float = 40.0
    tick_s: float = 2.0
    kill_grace_s: float = 5.0
    max_output_bytes: int = 2 * 1024 * 1024
    capabilities: dict = field(default_factory=dict)
    runtime: object = None              # version 2 (spec v0.5): the HostRuntime whose pass this is; None is version 1

    def caps(self) -> dict:
        return {"actors": list(self.actors), "scopes": list(self.scopes),
                "repositories": sorted(self.repositories),
                "cost_enforced_actors": sorted(a for a, e in self.cost_enforcers.items() if _is_enforcer(e)),
                **self.capabilities}


def _is_enforcer(adapter) -> bool:
    return callable(getattr(adapter, "apply", None))


def _now():
    return datetime.now(timezone.utc)


def _event_id(request_id: str, generation: int, status: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-delegation:{request_id}:{generation}:{status}"))


def _task_name(request_id: str, generation: int) -> str:
    return f"{request_id}-g{generation}"


# The child starts behind a barrier (R-F1): it waits for one "G" byte on stdin, sent only after
# its process group and identity are durably recorded. If the runner dies first, the child reads
# EOF and exits without starting the agent. After "G" it swaps stdin for /dev/null and execs.
_BARRIER = ("import os,sys\n"
            "if os.read(0, 1) != b'G':\n"
            "    os._exit(97)\n"
            "fd = os.open(os.devnull, os.O_RDONLY); os.dup2(fd, 0); os.close(fd)\n"
            "os.execvp(sys.argv[1], sys.argv[1:])\n")


def _identity(pid: int) -> str | None:
    """Process group and start time of ``pid`` (R-F2), or None if it is gone."""
    try:
        out = subprocess.run(["ps", "-o", "pgid=,lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5, env={"PATH": "/bin:/usr/bin", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = " ".join(out.stdout.split())
    return text or None


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_group(pgid: int, grace: float, proc=None):
    """Stop the whole process group, including descendants that outlive the parent."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + grace
    if proc is not None:
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc is not None and proc.poll() is None:
        proc.wait()


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)                    # the rename itself is durable
    finally:
        os.close(dfd)


def _write_json(path: Path, doc: dict):
    _write_atomic(path, json.dumps(doc, sort_keys=True).encode())


def _artifact(cfg: RunnerConfig, task: str, kind: str, data: bytes, author: str) -> dict:
    path = cfg.artifact_root / f"{task}.{kind}"
    if path.exists():
        existing = path.read_bytes()
        if existing != data:
            raise RuntimeError("artifact_conflict")
    else:
        _write_atomic(path, data)
    return {"kind": kind, "ref": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "author": author, "review_status": "unreviewed"}


def _result_path(cfg: RunnerConfig, request_id: str, generation: int) -> Path:
    return cfg.artifact_root / f"{_task_name(request_id, generation)}.result.json"


def _stage_path(cfg: RunnerConfig, task: str) -> Path:
    return cfg.artifact_root / f"{task}.stage.json"


def _read_stage(cfg, task) -> dict | None:
    path = _stage_path(cfg, task)
    return json.loads(path.read_text()) if path.exists() else None


def _persist_result(cfg, request_id, generation, status, artifacts, detail) -> dict:
    record = {"request_id": request_id, "generation": generation, "status": status,
              "event_id": _event_id(request_id, generation, status), "artifacts": artifacts,
              "detail": detail, "acknowledged": False}
    path = _result_path(cfg, request_id, generation)
    if path.exists():                     # never replace a recorded outcome
        return json.loads(path.read_text())
    _write_json(path, record)
    return record


def _send(dispatcher, cfg, record) -> bool:
    try:
        ack = dispatcher.result(record["request_id"], record["generation"], event_id=record["event_id"],
                                status=record["status"], artifacts=record["artifacts"], detail=record["detail"])
    except Exception:
        return False
    if not (isinstance(ack, dict) and ack.get("state") in ACK_STATES and ack.get("event_id") == record["event_id"]):
        return False                      # no matching acknowledgement: stays pending, resent later
    record["acknowledged"] = True
    _write_json(_result_path(cfg, record["request_id"], record["generation"]), record)
    return True


def resend_pending(dispatcher, cfg: RunnerConfig) -> int:
    """Deliver any recorded but unacknowledged results (after a crash or dispatcher outage)."""
    if not cfg.artifact_root.exists():
        return 0
    sent = 0
    for path in sorted(cfg.artifact_root.glob("*.result.json")):
        record = json.loads(path.read_text())
        if not record.get("acknowledged") and _send(dispatcher, cfg, record):
            sent += 1
    return sent


class _BoundedCapture:
    """Drain the child's stdout/stderr pipe into the log, never writing more than ``limit`` bytes.
    Beyond the limit the rest is discarded (so the child never blocks) and ``exceeded`` is set."""

    def __init__(self, pipe, log_path: Path, limit: int):
        self.pipe, self.limit, self.written, self.exceeded = pipe, limit, 0, threading.Event()
        self.error: str | None = None
        self.completed = False
        self.log = os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb")   # private: it holds the full output
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        fd = self.pipe.fileno()
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                room = self.limit - self.written
                if room > 0:
                    self.log.write(chunk[:room])
                    self.written += min(room, len(chunk))
                if len(chunk) > room:
                    self.exceeded.set()
        except OSError as exc:
            self.error = f"capture_read_{type(exc).__name__}"
        finally:
            try:
                self.log.flush()
                os.fsync(self.log.fileno())
                self.log.close()
                self.completed = self.error is None
            except OSError as exc:
                self.error = self.error or f"capture_write_{type(exc).__name__}"

    @property
    def ok(self) -> bool:
        return self.completed and not self.thread.is_alive()

    def finish(self, timeout: float):
        self.thread.join(timeout)
        try:
            self.pipe.close()
        except OSError:
            pass


def _collect(cfg, env, task, task_dir, status, exit_code) -> list:
    log_path = cfg.artifact_root / f"{task}.log"
    data = log_path.read_bytes()[:cfg.max_output_bytes] if log_path.exists() else b""
    artifacts = [_artifact(cfg, task, "output", data, env.actor)]
    if env.scope == "build" and task_dir.exists():     # evidence even when completion is unknown
        patch, _ = workspace.collect_diff(task_dir, env.ref)
        artifacts.append(_artifact(cfg, task, "patch", patch, env.actor))
    _write_json(_stage_path(cfg, task), {"stage": "artifacts", "status": status, "exit_code": exit_code,
                                         "artifacts": artifacts})
    return artifacts


def _resume(dispatcher, cfg, env, task, stage) -> dict:
    """A reclaim after a crash: finish from the last durable stage, never rerun the work."""
    task_dir = Path(cfg.workspace_root) / task
    if stage["stage"] == "preparing":
        # The child was never let past the barrier (no "started" stage): nothing ran. Rebuild.
        if task_dir.exists():
            workspace.remove(task_dir, Path(cfg.workspace_root))
        _stage_path(cfg, task).unlink()
        return None
    if stage["stage"] == "started":
        pgid, identity = stage.get("pgid"), stage.get("identity")
        if isinstance(pgid, int) and pgid > 1 and _group_alive(pgid):
            if identity and _identity(pgid) == identity:
                _kill_group(pgid, cfg.kill_grace_s)      # verified: our orphan from the crashed runner
            else:                                        # PID reuse or a lost leader: never signal blindly
                return _finish(dispatcher, cfg, env, "interrupted", [],
                               {"code": "process_identity_unverified", "base_ref": env.ref}, resumed=True)
        artifacts = _collect(cfg, env, task, task_dir, "interrupted", None)
        return _finish(dispatcher, cfg, env, "interrupted", artifacts,
                       {"code": "child_outcome_unknown", "base_ref": env.ref}, resumed=True)
    if stage["stage"] == "exited":
        artifacts = _collect(cfg, env, task, task_dir, stage["status"], stage.get("exit_code"))
    else:
        artifacts = stage["artifacts"]
    return _finish(dispatcher, cfg, env, stage["status"], artifacts,
                   {"exit_code": stage.get("exit_code"), "base_ref": env.ref}, resumed=True)


def run_once(dispatcher, cfg: RunnerConfig, *, now=_now, sleep=time.sleep) -> dict:
    """Resend pending results, heartbeat, then at most one claimed task. Configured for version 2 (a host runtime,
    spec v0.5 §3.3: one binary, two configurations), the same pass runs §5.5 first and speaks version 2."""
    if cfg.runtime is not None:
        return cfg.runtime.pass_once()
    cfg.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    resend_pending(dispatcher, cfg)
    dispatcher.heartbeat(cfg.runner_id, state="online", capabilities=cfg.caps())
    claimed = dispatcher.claim(cfg.runner_id, capabilities=cfg.caps())
    if claimed is None:
        return {"state": "idle"}
    request_id = claimed.get("request_id") if isinstance(claimed, dict) else None
    generation = claimed.get("generation") if isinstance(claimed, dict) else None
    try:
        env = env_mod.parse(claimed, allowed_repositories=cfg.repositories, max_cost_usd=cfg.max_cost_usd,
                            now=now())
    except env_mod.EnvelopeRefused as exc:
        if isinstance(request_id, str) and isinstance(generation, int) and not isinstance(generation, bool):
            try:
                uuid.UUID(request_id)
                _send(dispatcher, cfg, _persist_result(cfg, request_id, generation, "refused", [],
                                                       {"code": str(exc)}))
            except ValueError:
                pass
        return {"state": "refused", "code": str(exc)}

    existing = _result_path(cfg, env.request_id, env.generation)
    if existing.exists():               # reclaimed after a crash: report, never rerun the work
        record = json.loads(existing.read_text())
        delivered = record.get("acknowledged") or _send(dispatcher, cfg, record)
        return {"state": record["status"], "request_id": env.request_id, "delivered": bool(delivered),
                "resumed": True}
    task = _task_name(env.request_id, env.generation)
    stage = _read_stage(cfg, task)
    if stage is not None:
        resumed = _resume(dispatcher, cfg, env, task, stage)
        if resumed is not None:
            return resumed
    elif (Path(cfg.workspace_root) / task).exists():
        # A workspace with no stage at all was not made by this runner's protocol: never assume
        # nothing ran, never delete it. Report and keep it for inspection (R-F3).
        return _finish(dispatcher, cfg, env, "interrupted", [],
                       {"code": "workspace_without_stage", "base_ref": env.ref}, resumed=True)

    if env.actor not in cfg.actors or env.scope not in cfg.scopes:
        return _finish(dispatcher, cfg, env, "refused", [], {"code": "runner_not_capable"})
    enforcer = cfg.cost_enforcers.get(env.actor)
    if not _is_enforcer(enforcer):
        return _finish(dispatcher, cfg, env, "refused", [], {"code": "cost_limit_unenforceable"})

    dispatcher.heartbeat(cfg.runner_id, state="busy", capabilities=cfg.caps())
    _write_json(_stage_path(cfg, task), {"stage": "preparing"})      # durable before the workspace exists
    try:
        task_dir = workspace.prepare(cfg.workspace_root, Path(cfg.repositories[env.repository]), task, env.ref)
    except workspace.WorkspaceError as exc:
        return _finish(dispatcher, cfg, env, "failed", [], {"code": str(exc)})
    try:
        argv, child_env = enforcer.apply(commands.argv(env.actor, env.scope, env.brief, task_dir),
                                         workspace.child_env(), env.cost_cap_usd)
    except Exception:
        return _finish(dispatcher, cfg, env, "refused", [], {"code": "cost_limit_unenforceable"})

    log_path = cfg.artifact_root / f"{task}.log"
    if log_path.exists():
        log_path.unlink()               # a partial log from a crash before the child was spawned
    proc, capture = None, None
    status, outcome_local = None, None
    try:
        proc = subprocess.Popen([sys.executable, "-c", _BARRIER, *argv], cwd=task_dir, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=child_env,
                                start_new_session=True)
        capture = _BoundedCapture(proc.stdout, log_path, cfg.max_output_bytes)
        identity = _identity(proc.pid)
        if identity is None:
            raise RuntimeError("child_identity_unavailable")
        _write_json(_stage_path(cfg, task), {"stage": "started", "pgid": proc.pid, "identity": identity})
        proc.stdin.write(b"G")                           # only now may the agent start
        proc.stdin.close()
        dispatcher.progress(env.request_id, env.generation, phase="executing")
        last_renew = time.monotonic()
        while proc.poll() is None:
            if capture.exceeded.is_set():
                status = "output_limit"
                break
            if now() >= env.deadline:
                status = "timed_out"
                break
            if dispatcher.cancel_requested(env.request_id, env.generation):
                status = "cancelled"
                break
            if time.monotonic() - last_renew >= cfg.lease_renew_s:
                if not dispatcher.renew(env.request_id, env.generation):
                    outcome_local = "lease_lost"
                    break
                last_renew = time.monotonic()
            sleep(cfg.tick_s)
    except Exception:
        # Lost contact with the dispatcher's control calls (or any local fault): stop the work
        # conservatively; no completion is claimed for it.
        outcome_local = outcome_local or "control_lost"
    finally:
        if proc is not None:
            _kill_group(proc.pid, cfg.kill_grace_s, proc)
        if capture is not None:
            capture.finish(cfg.kill_grace_s)

    if outcome_local:
        try:
            dispatcher.heartbeat(cfg.runner_id, state="online", capabilities=cfg.caps())
        except Exception:
            pass
        return {"state": outcome_local, "request_id": env.request_id, "delivered": False}
    if capture.exceeded.is_set():
        status = "output_limit"         # a burst written just before exiting still counts
    if not capture.ok:
        # Missing or unfinished evidence: an exit code of 0 cannot stand in for it (R-F4).
        _write_json(_stage_path(cfg, task), {"stage": "exited", "status": "failed", "exit_code": proc.returncode})
        return _finish(dispatcher, cfg, env, "failed", [],
                       {"code": capture.error or "capture_incomplete", "exit_code": proc.returncode,
                        "base_ref": env.ref}, workspace_dir=task_dir)
    if status is None:
        status = "completed" if proc.returncode == 0 else "failed"
    _write_json(_stage_path(cfg, task), {"stage": "exited", "status": status, "exit_code": proc.returncode})
    artifacts = _collect(cfg, env, task, task_dir, status, proc.returncode)
    return _finish(dispatcher, cfg, env, status, artifacts, {"exit_code": proc.returncode, "base_ref": env.ref},
                   workspace_dir=task_dir)


def _finish(dispatcher, cfg, env, status, artifacts, detail, workspace_dir=None, resumed=False) -> dict:
    record = _persist_result(cfg, env.request_id, env.generation, status, artifacts, detail)
    delivered = _send(dispatcher, cfg, record)
    try:
        dispatcher.heartbeat(cfg.runner_id, state="online", capabilities=cfg.caps())
    except Exception:
        pass
    out = {"state": status, "request_id": env.request_id, "delivered": delivered, "artifacts": artifacts}
    if status == "refused":
        out["code"] = detail.get("code")
    if workspace_dir is not None:
        out["workspace"] = str(workspace_dir)
        # The result and its artifacts are persisted: a finished checkout (~0.5 GB) goes, except an interrupted one,
        # which is kept for inspection.
        if status != "interrupted" and artifacts:          # no evidence collected (e.g. capture failed): keep it
            try:
                workspace.remove(workspace_dir, Path(cfg.workspace_root))
                out["workspace_removed"] = True
            except Exception:
                out["workspace_removed"] = False
    if resumed:
        out["resumed"] = True
    return out


# ══ Version 2: the host runtime (host runtime spec v0.5 §2.2 G1-G6, §3.2, R3.2-c, R4.5-e/f, §5.5, R6.3, R6.4, R7.6-a) ══
#
# The same pass, configured as a host: run_once hands it to HostRuntime.pass_once. It reuses this module's child path
# (the barrier, the recorded process group and identity, the stage files, the bounded capture, the verified kill) and
# changes what happens around it:
#   * contact runs on its own timer, whatever the job is doing, and carries the host's facts (R3.2-a);
#   * a wake (a tick overrun past gap_threshold_s), a reconnect, a restart or an authority refusal runs §5.5 in order:
#     freeze verified groups, replay pending records, read back open effects and reconcile each owned item, then the
#     workspace; only then a new claim;
#   * every owner write goes through the task's pending records first (journal.py), and is replayed byte for byte;
#   * no effect while the authority is unreachable; offline, the child is frozen (scope none) and stopped at its
#     checkpoint after offline_hold_s, or keeps editing its workspace (workspace_only);
#   * on SIGTERM: announce stopping, stop the child at a checkpoint, keep the pending records, announce stopped (G4);
#   * the do-not-resume ids are refused before any spawn, whatever the adapter (R6.4).
# A group whose identity cannot be verified is never signalled: its item goes to the owner (decide).

class _Offline(Exception):
    """An authority call got no answer: the host is disconnected until a call succeeds again."""


class _Busy(Exception):
    """A step that cannot finish yet (a receipt's own lease still runs): recovery tries again on a later pass."""


class _AuthorityError(Exception):
    """A call the authority answered with a deterministic error: a refused shape, an integrity or audit failure at the
    authority, a body over the door's bound. It is an answer, never "offline", so it is never retried as a lost
    connection: an owner write is marked final with ``authority_error:<code>`` and its item reconciled, a reconcile
    blocks its task with that name until an operator restarts the runtime (review N2)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _transport(exc) -> bool:
    """True only for a lost connection (sockets, timeouts, an HTTP 5xx or a reset mid-answer, the database
    unreachable): the host is offline until a call gets through. Everything else is an answer."""
    import http.client
    if isinstance(exc, ValueError):
        return False
    code = getattr(exc, "code", None)
    if type(code) is int and 400 <= code < 500:              # the door answered with a refusal
        return False
    if isinstance(exc, (OSError, http.client.HTTPException)):
        return True
    return any(c.__module__.startswith("psycopg2") and c.__name__ in ("OperationalError", "InterfaceError")
               for c in type(exc).__mro__)


def _error_code(exc) -> str:
    """A fixed code for an error: its message when that is already one (the store's and the door's), else its type."""
    text = str(exc)
    return text if 0 < len(text) <= 64 and text.replace("_", "").isalnum() and text == text.lower() \
        else type(exc).__name__


class GapDetector:
    """§5.5 step 1. A wake or suspension is a tick overrun: more than ``threshold_s`` of wall-clock time between two runs
    of the runtime's own loops (the contact timer ticks about once a second, the child's supervision every tick). That
    holds whether or not monotonic time ran during the suspension, because the loops simply did not run. Wall minus
    monotonic time and the platform adapter's hint are recorded as supporting signals only. Switched off
    (``gap_detection: off``), it reports nothing, and the authority's refusals still catch the wake (T6c)."""

    def __init__(self, threshold_s, enabled=True, *, wall=time.time, mono=time.monotonic, hint=None):
        self.threshold_s, self.enabled, self._wall, self._mono, self._hint = threshold_s, enabled, wall, mono, hint
        self._lock = threading.Lock()
        self._last = None

    def tick(self):
        w, m = self._wall(), self._mono()
        with self._lock:
            last, self._last = self._last, (w, m)
        if last is None or not self.enabled or w - last[0] <= self.threshold_s:
            return None
        gap = w - last[0]
        return {"gap_s": round(gap, 3), "wall_minus_mono_s": round(gap - (m - last[1]), 3),
                "hint": None if self._hint is None else self._hint()}


def _claim_path(cfg, task):
    return cfg.artifact_root / f"{task}.claim.json"


def _closed_path(cfg, task):
    return cfg.artifact_root / f"{task}.closed.json"


def _cp_path(cfg, request_id, digest):
    return cfg.artifact_root / f"{request_id}.cp.{digest.split(':', 1)[1]}.patch"


def _decimal(value):
    from decimal import Decimal
    return Decimal(str(value))


class _Child:
    """One supervised child and the means to signal it. Every signal checks the recorded identity first (R-F2); a
    group that cannot be verified is never signalled."""

    def __init__(self, rt, claim, env, task_dir):
        self.rt, self.claim, self.env, self.task_dir = rt, claim, env, task_dir
        self.task, self.request_id, self.generation = claim["task"], claim["request_id"], claim["generation"]
        self.journal = rt.journal(self.task)
        from . import fakework
        self.channel = fakework.Channel(rt.cfg.artifact_root / f"{self.task}.chan")
        self.proc = self.capture = self.pgid = self.identity = None
        self.frozen = False
        self.signalled = False             # this runtime stopped it while it ran: its exit is ours, never its own (B1)
        self.lost = False                  # reaped by anyone else (ECHILD): its group id is no longer pinned (N-D1)
        self.lock = threading.RLock()

    def reaped(self) -> bool:
        return self.proc is None or self.proc.returncode is not None or self.lost

    def exited(self) -> bool:
        """Whether the leader has exited, read without reaping it: until this runtime reaps it, its process group id
        cannot be anyone else's, so a signal to the group reaches only this child's group."""
        if self.reaped():
            return True
        if hasattr(os, "waitid"):
            try:
                got = os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                self.lost = True                     # reaped elsewhere: never signalled again
                return True
            # macOS also reports a stopped (frozen) child here, whatever the options: only an exit counts.
            return got is not None and got.si_code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(self.proc.pid)], capture_output=True, text=True,
                             timeout=5, env={"PATH": "/bin:/usr/bin", "LC_ALL": "C"}).stdout.strip()
        return not out or out.startswith("Z")

    def alive(self) -> bool:
        return not self.exited()

    def verified(self) -> bool:
        """Checked right before every signal (review N-D1): this runtime's own child, its identity recorded (by ps)
        before it was let start, and its leader still unreaped, asked of the kernel now. The leader is a session
        leader (it can change neither its group nor its session), and no other process can take its pid as a group
        id while it is unreaped, a zombie included, so a signal to that group reaches this child's group and no one
        else's (review N1). If anything else reaped it (ECHILD), it is lost and never signalled. Without waitid
        (Python 3.9) the identity is checked by ps, as for orphans of an earlier process (_freeze_task)."""
        if self.proc is None or self.proc.returncode is not None or self.lost or self.identity is None:
            return False
        if hasattr(os, "waitid"):
            try:
                os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                self.lost = True
                return False
            return True
        return _identity(self.pgid) == self.identity

    def end_group(self, *, term=True) -> str:
        """Stop the whole group and reap the leader: SIGTERM, up to kill_grace_s, then SIGKILL for whatever remains
        (descendants that outlived the leader included). Every signal is sent before the leader is reaped, after the
        identity check; an unverified group is never signalled. 'stopped' or 'unverified'."""
        if self.lost:
            return "unverified"
        if self.reaped():
            return "stopped"
        if not self.verified():
            return "unverified"
        live = not self.exited()
        if live:
            self.signalled = True
        try:
            if term and live:
                os.killpg(self.pgid, signal.SIGTERM)
                deadline = time.monotonic() + self.rt.cfg.kill_grace_s
                while not self.exited() and time.monotonic() < deadline:
                    time.sleep(0.02)
            if not self.verified():
                return "unverified"                  # right before the SIGKILL too: never an unpinned group
            os.killpg(self.pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait()
        return "stopped"

    def freeze(self, reason) -> str:
        """§5.5 step 2: SIGSTOP the verified group; 'frozen', 'stopped' (none runs) or 'unverified' (not signalled)."""
        with self.lock:
            if self.frozen:
                state = "frozen"
            elif self.proc is None or self.proc.returncode is not None:
                state = "stopped"
            elif not self.verified():
                state = "unverified"                 # reaped elsewhere, or not this child: never signalled
            elif self.exited():
                state = "stopped"
            else:
                os.killpg(self.pgid, signal.SIGSTOP)
                self.frozen, state = True, "frozen"
            self.journal.note("freeze", self.rt.at(), request_id=self.request_id, generation=self.generation,
                              child=state, reason=reason)
            return state

    def thaw(self):
        with self.lock:
            if self.frozen and self.verified():
                os.killpg(self.pgid, signal.SIGCONT)
                self.journal.note("thaw", self.rt.at(), request_id=self.request_id, generation=self.generation)
                self.frozen = False
            elif self.reaped():
                self.frozen = False                  # nothing left to thaw

    def kill(self, reason) -> str:
        """Stop the group for good: 'stopped', or 'unverified' when its identity no longer matches (never signalled)."""
        with self.lock:
            state = self.end_group(term=not self.frozen)    # a frozen group runs nothing more before it dies
            self.frozen = False
            if self.capture is not None:
                self.capture.finish(self.rt.cfg.kill_grace_s)
            if state == "stopped":
                _write_json(_stage_path(self.rt.cfg, self.task), {"stage": "stopped", "reason": reason})
            return state


class HostRuntime:
    """One host's runtime for one environment (G6): its contact timer, its pending records and its child, around
    run_once's pass. Built by the CLI from host.yaml (host.HostConfig) and the authority's values (Authority.hosts)."""

    def __init__(self, dispatcher, cfg: RunnerConfig, host, values, gap_threshold_s, *, bundle_digest, sink=None,
                 now=_now, wall=time.time, mono=time.monotonic, hint=None):
        self.dispatcher, self.cfg, self.host, self.values = dispatcher, cfg, host, values
        self.bundle_digest, self.sink, self._now, self._mono = bundle_digest, sink, now, mono
        self.detector = GapDetector(gap_threshold_s, host.gap_detection, wall=wall, mono=mono, hint=hint)
        self.runner_id = host.runner_id
        self.child = None
        self.announced = "online"
        self.last_gap_s = 0
        self.offline_since = None          # monotonic time of the first failed call; None while in contact
        self.failures = 0
        self.last_contact_ok = None
        self.reasons = {"restart"}         # §5.5 runs before the first claim: a restart has no state in memory
        self._left_frozen = False          # §5.5 step 2 for children of an earlier process, done before any contact
        self.blocked = {}                  # task -> authority_error:<code>: reconcile answered with an error (N2)
        self.damaged = {}                  # task -> code: pending records that cannot be read (fail closed)
        self.held = {}                     # operation id -> receipt lease, in memory only (its token never on disk)
        self.stop_requested = threading.Event()
        self._lock = threading.RLock()
        self._contact_lock = threading.Lock()
        self._contact_stop = threading.Event()
        self._contact_thread = None
        self._journals = {}
        self._state_written = 0.0
        cfg.runtime = self

    # ── time ──
    def at(self) -> str:
        return self._now().isoformat()

    # ── the contact timer (R3.2-a: heartbeat on its own timer) ──
    def facts(self) -> dict:
        return {"host_id": self.host.host_id, "bundle_digest": self.bundle_digest, "roles": list(self.host.roles),
                "availability": self.host.expected_availability, "grace": self.host.unreachable_grace_s,
                "last_gap_s": self.last_gap_s, "announced": self.announced}

    def contact(self, announced=None) -> bool:
        """One heartbeat with the host's facts. S1-F's census batches ride in this body (store._heartbeat_v2's seam);
        none is sent yet."""
        with self._contact_lock:
            if announced is not None:
                self.announced = announced
            state = "busy" if self.child is not None else "online"
            try:
                self.dispatcher.heartbeat(self.runner_id, state, list(self.host.capabilities), self.facts())
            except Exception as exc:
                if not _transport(exc):
                    self._ok()
                    raise _AuthorityError(_error_code(exc)) from None
                self._failed()
                return False
            self._ok()
            return True

    def start_contact(self):
        self._contact_thread = threading.Thread(target=self._contact_loop, name="mc-runtime-contact", daemon=True)
        self._contact_thread.start()

    def stop_contact(self):
        self._contact_stop.set()
        if self._contact_thread is not None:
            self._contact_thread.join(timeout=5)

    def _contact_loop(self):
        interval = self.values.contact_interval_s
        step, due = min(1.0, interval), 0.0
        while not self._contact_stop.is_set():
            gap = self.detector.tick()
            if gap:
                self.on_wake(gap)
                due = 0.0
            if self._mono() >= due and self.announced == "online":
                try:
                    self.contact()
                except Exception:
                    pass                       # a malformed answer is logged by the pass, never kills the timer
                due = self._mono() + interval
            self._contact_stop.wait(step)

    def _ok(self):
        with self._lock:
            reconnected = self.offline_since is not None
            self.offline_since, self.failures, self.last_contact_ok = None, 0, self.at()
            if reconnected:
                self.reasons.add("reconnect")          # §5.5 step 1: the first successful call after failures

    def _failed(self):
        with self._lock:
            first = self.offline_since is None
            if first:
                self.offline_since = self._mono()
            self.failures += 1
            child = self.child
        if first and child is not None and self.host.offline_scope_default == "none":
            child.freeze("authority_unreachable")    # R4.5-f: frozen at the first failed control call

    def offline(self) -> bool:
        return self.offline_since is not None

    def on_wake(self, gap):
        with self._lock:
            self.last_gap_s = int(round(gap["gap_s"]))
            self.reasons.add("wake")
            child = self.child
        if child is not None:
            child.journal.note("wake", self.at(), **gap)
            child.freeze("wake")                      # as soon as detected; the rest of §5.5 runs in the pass

    def call(self, name, *args, **kwargs):
        try:
            out = getattr(self.dispatcher, name)(*args, **kwargs)
        except Exception as exc:
            if not _transport(exc):
                self._ok()                              # it answered: in contact, whatever it said
                raise _AuthorityError(_error_code(exc)) from None
            self._failed()
            raise _Offline(type(exc).__name__) from None
        self._ok()
        return out

    # ── files ──
    def journal(self, task):
        from .journal import Journal
        with self._lock:
            if task not in self._journals:
                self._journals[task] = Journal(self.cfg.artifact_root / f"{task}.pending.jsonl")
            return self._journals[task]

    def owned(self) -> list:
        """The open tasks this host owns: a claim on file without a closing record."""
        out = []
        for path in sorted(self.cfg.artifact_root.glob("*.claim.json")):
            task = path.name[:-len(".claim.json")]
            if not _closed_path(self.cfg, task).exists():
                out.append(task)
        return out

    def _claim(self, task) -> dict:
        return json.loads(_claim_path(self.cfg, task).read_text())

    def _item_tasks(self, request_id) -> list:
        tasks = []
        for path in self.cfg.artifact_root.glob(f"{request_id}-g*.claim.json"):
            doc = json.loads(path.read_text())
            tasks.append((doc["generation"], doc["task"]))
        return [t for _, t in sorted(tasks)]

    def _close(self, task, reason, answer=None):
        _write_json(_closed_path(self.cfg, task), {"reason": reason, "answer": answer, "at": self.at()})

    # ── spend across resumes (§8.1 record 2): one reservation, a launch gets only what remains ──
    def _launch_usage(self, task):
        notes = self.journal(task).notes("usage")
        return _decimal(0) if not notes else (None if notes[-1]["fields"]["amount"] is None
                                              else _decimal(notes[-1]["fields"]["amount"]))

    def _settled(self, task) -> bool:
        return any(c["kind"] == "result" and c["mark"] == "applied"
                   and (c["answer"] or {}).get("code") in (None, "retry", "effects_open", "grant_revoked")
                   for c in self.journal(task).calls())

    def _unsettled(self, request_id, through_generation):
        """The usage this host recorded for the item's launches no accepted result has settled, up to a generation:
        None when any of it is unknown (unknown is never read as zero)."""
        total = _decimal(0)
        for task in self._item_tasks(request_id):
            if self._claim(task)["generation"] > through_generation or self._settled(task):
                continue
            used = self._launch_usage(task)
            if used is None:
                return None
            total += used
        return total

    # ── host-local state (R7.6-a: host-state.json, source host-local, with its age) ──
    def write_state(self, force=False):
        if not force and time.monotonic() - self._state_written < 1.0:
            return
        self._state_written = time.monotonic()
        items = []
        for task in self.owned():
            claim = self._claim(task)
            stage = _read_stage(self.cfg, task) or {}
            try:
                pending = len(self.journal(task).pending())
            except Exception:
                pending = None
            items.append({"item_id": claim["request_id"], "generation": claim["generation"], "task": task,
                          "route": claim.get("route"), "stage": stage.get("stage"),
                          "child": None if self.child is None or self.child.task != task else
                          ("frozen" if self.child.frozen else "running" if self.child.alive() else "stopped"),
                          "checkpoint": self._acked_checkpoint(claim["request_id"]), "pending_records": pending,
                          "journal": self.damaged.get(task, "readable" if pending is not None else "damaged")})
        doc = {"schema": "minimoi.runtime.state.v1", "environment": self.host.environment, "as_of": self.at(),
               "source": "host-local",
               "hosts": [{"host_id": self.host.host_id, "runner_id": self.runner_id, "roles": list(self.host.roles),
                          "availability": self.host.expected_availability, "grace_s": self.host.unreachable_grace_s,
                          "bundle_digest": self.bundle_digest, "announced": self.announced,
                          "last_gap_s": self.last_gap_s, "last_contact_at": self.last_contact_ok,
                          "authority_reachable": not self.offline(), "contact_failures": self.failures}],
               "items": items,
               "unknowns": ["authority_snapshot: this is the host's own view; the authority's is S1-G's"]}
        _write_json(self.cfg.artifact_root / "host-state.json", doc)

    # ── the pass ──
    def freeze_left_children(self) -> dict:
        """§5.5 step 2 after a restart, before the authority is contacted (X3): every child an earlier process of this
        runtime left running is frozen, verified by its stage file's identity, and stopped; an unverified group is
        never signalled. Its pending records stay on disk. Nothing here needs the authority. {task: child state}."""
        self.cfg.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = {}
        if not self._left_frozen:
            for task in self.owned():
                if task not in self.damaged:
                    out[task] = self._freeze_task(task, ["restart"])
            self._left_frozen = True
        return out

    def pass_once(self) -> dict:
        self.cfg.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.stop_requested.is_set():
            return {"state": "stopping"}
        self.freeze_left_children()
        gap = self.detector.tick()
        if gap:
            self.on_wake(gap)
        try:
            if self.offline():
                return {"state": "offline"}            # nothing new is admitted while disconnected (R4.5-f)
            if self.reasons or self.owned():
                launch = self.recover()
                if launch is not None:
                    return self._run(launch)
                if self.owned() or self.damaged or self.blocked:
                    stuck = {**self.damaged, **self.blocked}
                    return {"state": "blocked" if stuck else "recovering",
                            **({"damaged": sorted(self.damaged)} if self.damaged else {}),
                            **({"blocked": dict(self.blocked)} if self.blocked else {})}
            claimed = self.call("claim", self.runner_id, list(self.host.capabilities))
        except _Offline:
            return {"state": "offline"}
        except _Busy:
            return {"state": "recovering"}
        except _AuthorityError as exc:
            return {"state": "authority_error", "code": exc.code}       # logged each pass; nothing is offline
        finally:
            self.write_state()
        if claimed is None:
            return {"state": "idle"}
        claim = self._record_claim(claimed)
        return self._run(claim)

    def _record_claim(self, claimed) -> dict:
        env = claimed["envelope"]
        task = _task_name(env["request_id"], env["generation"])
        claim = {"request_id": env["request_id"], "generation": env["generation"], "task": task,
                 "route": claimed["route"], "unit": claimed["unit"], "reserved": claimed["reserved"],
                 "lease_until": claimed["lease_until"], "card_id": claimed.get("card_id"), "step": claimed.get("step"),
                 "attempt": claimed.get("attempt"), "resume_from": claimed.get("resume_from"), "envelope": env,
                 "claimed_at": self.at()}
        _write_json(_claim_path(self.cfg, task), claim)           # ownership is on disk before anything starts
        return claim

    def _run(self, claim) -> dict:
        out = {"state": "idle"}
        while claim is not None:
            out = self._launch(claim)
            claim = out.pop("next_claim", None)          # a resume restarts the child from its checkpoint at once
        return out

    # ── §5.5 ──
    def recover(self):
        """§5.5 for every open task this host owns, in order: freeze (step 2), replay (3), read back and reconcile
        (4), workspace (5). Returns a claim to launch (a resume), or None. Raises _Offline or _Busy to try again later;
        the host claims nothing until every owned task has been through it (step 6)."""
        with self._lock:
            reasons = sorted(self.reasons)
        tasks = [t for t in self.owned() if t not in self.damaged and t not in self.blocked]
        stages = {}
        for task in tasks:                                                    # step 2: freeze
            stages[task] = self._freeze_task(task, reasons)
            self._result_from_stage(task)
        readable = []
        for task in tasks:                                                    # step 3: replay
            try:
                self.replay(task)
                readable.append(task)
            except _Offline:
                raise
            except Exception as exc:
                from .journal import JournalDamaged
                if not isinstance(exc, JournalDamaged):
                    raise
                self.damaged[task] = str(exc)                                 # fails closed: nothing more for it
        readable = [t for t in readable if t not in self.blocked]
        for task in readable:
            # An effect's outcome on file but not yet acknowledged (its finish waits on a receipt lease still running):
            # the item is not reconciled until it is, so it never goes unknown with its outcome known (S2).
            if self._finish_waiting(self._claim(task)["request_id"]):
                raise _Busy()
        launch = None
        for task in readable:                                                 # steps 4 and 5
            if _closed_path(self.cfg, task).exists():
                continue                                                      # its replayed result closed it
            try:
                answer = self._reconcile(task, stages[task], reasons)
            except _AuthorityError as exc:
                self.blocked[task] = f"authority_error:{exc.code}"            # a named reason, not retried forever
                continue
            nxt = self._workspace(task, answer)
            launch = launch or nxt
        with self._lock:                  # every owned task has its answer: step 6 may follow (a damaged one blocks it)
            self.reasons.difference_update(reasons)
        return launch

    def _finish_waiting(self, request_id) -> bool:
        """An effect of the item whose outcome is on file here and not yet acknowledged by the authority."""
        from .journal import JournalDamaged
        for t in self._item_tasks(request_id):
            try:
                if any(c["kind"] == "effect_finish" and c["mark"] is None for c in self.journal(t).calls()):
                    return True
            except JournalDamaged:
                continue                    # its own task is blocked as damaged; this one need not wait on it
        return False

    def _freeze_task(self, task, reasons) -> str:
        child = self.child
        if child is not None and child.task == task:
            return child.freeze("+".join(reasons))
        claim, stage = self._claim(task), _read_stage(self.cfg, task) or {}
        state = "stopped"
        if stage.get("stage") == "started":
            # A child left by an earlier process of this runtime: it cannot be supervised again (its output pipe
            # died with that process). Verified, it is frozen at once and then stopped; otherwise never signalled.
            pgid, identity = stage.get("pgid"), stage.get("identity")
            if isinstance(pgid, int) and pgid > 1 and _group_alive(pgid):
                if identity and _identity(pgid) == identity:
                    os.killpg(pgid, signal.SIGSTOP)
                    os.killpg(pgid, signal.SIGKILL)
                    _write_json(_stage_path(self.cfg, task), {"stage": "stopped", "reason": "orphan_after_restart"})
                else:
                    state = "unverified"
        self.journal(task).note("freeze", self.at(), request_id=claim["request_id"], generation=claim["generation"],
                                child=state, reason="+".join(reasons))
        return state

    def _result_from_stage(self, task):
        """A child that exited before this runtime recorded its result (a crash in between, or a graceful stop that
        found it finished): its known exit is kept and reported, never rerun (E4, as version 1's stages)."""
        stage = _read_stage(self.cfg, task) or {}
        if stage.get("stage") not in ("exited", "artifacts") or self.child is not None and self.child.task == task:
            return
        try:
            if any(c["kind"] == "result" for c in self.journal(task).calls()):
                return
        except Exception:
            return                                                     # damaged: replay reports it and fails closed
        claim = self._claim(task)
        e = claim["envelope"]
        env = type("Env", (), {"actor": e["actor"], "scope": e["scope"], "ref": e["ref"]})
        task_dir = Path(self.cfg.workspace_root) / task
        artifacts = stage["artifacts"] if stage["stage"] == "artifacts" else \
            _collect(self.cfg, env, task, task_dir, stage["status"], stage.get("exit_code"))
        self._record_result(claim, stage["status"], artifacts, {"exit_code": stage.get("exit_code"),
                                                                "base_ref": e["ref"]})

    def replay(self, task, *, again=False) -> dict:
        """§5.5 step 3 (R3.2-c): send the task's pending records through the same calls, each exactly as first sent;
        with ``again``, every record, acknowledged or not (DUP-5: ``journal replay --again`` applies 0)."""
        from .journal import classify
        j = self.journal(task)
        records = j.calls() if again else j.pending()
        counts = {"sent": 0, "applied": 0, "already_applied": 0, "final": 0, "pending": 0}
        for rec in records:
            try:
                answer = self._send_record(rec)
            except _Busy:
                counts["pending"] += 1
                continue
            except _AuthorityError as exc:
                if rec["kind"] in KNOWN_OUTCOMES:          # kept pending, its task blocked (NS1); the rest waits too
                    self.blocked[task] = f"authority_error:{exc.code}"
                    counts["pending"] += 1
                    break
                answer = {"state": "refused", "code": f"authority_error:{exc.code}"}
            counts["sent"] += 1
            kind = classify(rec["kind"], answer)
            if kind == "applied":
                counts["already_applied" if answer.get("state") == "already_applied" else "applied"] += 1
            else:
                counts[kind] += 1
            if rec["mark"] is None and kind != "pending":
                j.mark(rec["event_id"], kind, _summary(answer))
                self._after_answer(task, rec, answer, kind)
        return counts

    def _send_record(self, rec):
        a = rec["args"]
        if rec["kind"] == "progress":
            return self.call("progress", rec["request_id"], rec["generation"], runner_id=a["runner_id"],
                             event_id=rec["event_id"], at=rec["at"], phase=a["phase"], checkpoint=a["checkpoint"])
        if rec["kind"] == "result":
            return self.call("result", rec["request_id"], rec["generation"], runner_id=a["runner_id"],
                             event_id=rec["event_id"], at=rec["at"], status=a["status"], artifacts=a["artifacts"],
                             detail=a["detail"], output_text=a["output_text"], route=a["route"], usage=a["usage"])
        return self._send_finish(rec)

    def _send_finish(self, rec):
        """An effect's finish, whose outcome is on file here (S1-B: a finish and a readback are always taken). Under
        the receipt lease it was made under while this process holds that lease and it still runs (the token lives
        in memory only). Otherwise, whether the token died with an earlier process or the lease ran out while the
        finish could not reach the authority (S2), the same outcome is recorded under a reconcile lease, as a
        readback; a lease refusal is never this record's final answer."""
        a, eff = rec["args"], dict(rec["args"]["effect"])
        call = (a["runner_id"], rec["request_id"], rec["generation"])
        lease = self.held.get(a["operation_id"])
        if lease is not None and lease["receipt_generation"] == a["receipt_generation"]:
            eff["lease"] = {k: lease[k] for k in ("receipt_generation", "token", "mode")}
            got = self.call("effect", *call, eff)
            if not (got.get("state") == "refused" and got.get("code") == "receipt_lease_stale"):
                return got
        base = {k: eff[k] for k in ("key", "grant_id", "request")}
        got = self.call("effect", *call, {"op": "reconcile", **base})
        if got.get("state") != "ok":
            if got.get("code") == "receipt_busy":
                raise _Busy()                       # the old lease still runs; its token died with that process
            if got.get("code") == "receipt_terminal":
                # An outcome is already recorded. Whether it is this one is the authority's to say: the finish
                # replayed with its token gone (a placeholder no receipt lease can match) is answered already_applied
                # when the receipt holds this same outcome. A contradicting one is refused receipt_outcome_conflict
                # and becomes one decision on the item at the authority (review N9, N-D2); any other answer is
                # passed on as it is.
                eff["lease"] = {"receipt_generation": a["receipt_generation"], "token": "0" * 43, "mode": "execute"}
                return self.call("effect", *call, eff)
            return got
        eff["lease"] = {k: got["lease"][k] for k in ("receipt_generation", "token", "mode")}
        return self.call("effect", *call, eff)

    def _after_answer(self, task, rec, answer, kind):
        """What a replayed or first-sent answer means for the task. A result acknowledged closes it; a final refusal
        of a result leaves it open for reconcile (S1-B N6), which learns the item's fate."""
        if rec["kind"] == "result" and kind == "applied":
            self._close(task, "result", _summary(answer))
        elif rec["kind"] == "effect_finish" and kind != "pending":
            self.held.pop(rec["args"]["operation_id"], None)
        elif kind == "final" and rec["kind"] in ("progress", "result"):
            with self._lock:
                self.reasons.add("refusal")

    def _acked_checkpoint(self, request_id):
        """The newest checkpoint of the item the authority acknowledged and this host can rebuild (its patch on file,
        digest checked), as R6.3's checkpoint condition compares it; None when there is none."""
        best = None
        for task in self._item_tasks(request_id):
            try:
                calls = self.journal(task).calls()
            except Exception:
                continue
            for c in calls:
                cp = c["args"].get("checkpoint") if c["kind"] == "progress" else None
                when = _parse_iso(c["at"]) if cp else None
                if cp and c["mark"] == "applied" and (best is None or when > best[0]):
                    best = (when, {"name": cp["name"], "digest": cp["digest"]})
        if best is None:
            return None
        path = _cp_path(self.cfg, request_id, best[1]["digest"])
        if not path.exists() or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != best[1]["digest"]:
            return None
        return best[1]

    def _open_operations(self, request_id) -> list:
        opened, closed = {}, set()
        for task in self._item_tasks(request_id):
            j = self.journal(task)
            for n in j.notes("effect_open"):
                opened[n["fields"]["operation_id"]] = {**n["fields"], "task": task}
            for n in j.notes("effect_closed"):
                closed.add(n["fields"]["operation_id"])
            for c in j.calls():
                if c["kind"] == "effect_finish" and c["mark"] == "applied":     # acknowledged, not merely sent (S2)
                    closed.add(c["args"]["operation_id"])
        return [v for k, v in opened.items() if k not in closed]

    def _readback(self, task, claim) -> list:
        """Each open effect resolved by asking its target first (X3; S1-B's contract with decide): committed is
        verified, absent failed, both under a reconcile lease with the target's answer as evidence. A target that
        cannot be asked leaves it open, so the authority decides. Returns the operation ids still open."""
        from . import fakework
        still = []
        for op in self._open_operations(claim["request_id"]):
            if self.sink is None or not self.sink.lookup or op["request"]["operation"] != fakework.OPERATION:
                still.append(op["operation_id"])
                continue
            base = {"key": op["key"], "grant_id": op["grant_id"], "request": op["request"]}
            gen = self._claim(op["task"])["generation"]
            got = self.call("effect", self.runner_id, claim["request_id"], gen, {"op": "reconcile", **base})
            if got.get("state") != "ok":
                if got.get("code") == "receipt_busy":
                    raise _Busy()
                if got.get("code") in ("receipt_terminal", "receipt_not_dispatched"):
                    self.journal(task).note("effect_closed", self.at(), operation_id=op["operation_id"],
                                            code=got.get("code"))
                    continue
                still.append(op["operation_id"])
                continue
            seen = self.sink.get(op["operation_id"])
            if seen not in ("committed", "absent"):
                still.append(op["operation_id"])
                continue
            lease = {"receipt_generation": got["lease"]["receipt_generation"], "token": got["lease"]["token"],
                     "mode": got["lease"]["mode"], "request_id": claim["request_id"], "generation": gen}
            self.held[op["operation_id"]] = lease
            eff = {"op": "finish", **base, "state": "verified" if seen == "committed" else "failed",
                   "evidence": {"kind": "readback", "outcome": seen}}
            answer = self._owner_write(task, "effect_finish", claim["request_id"], gen,
                                       {"runner_id": self.runner_id, "effect": eff,
                                        "operation_id": op["operation_id"],
                                        "receipt_generation": lease["receipt_generation"]})
            if answer is None or answer.get("state") not in ("ok", "already_applied"):
                still.append(op["operation_id"])
        return still

    def _reconcile(self, task, child_state, reasons) -> dict:
        claim = self._claim(task)
        still = self._readback(task, claim)
        stage = {"child": child_state, "checkpoint": self._acked_checkpoint(claim["request_id"])}
        answer = self.call("reconcile", self.runner_id, claim["request_id"], claim["generation"], stage, still)
        self.journal(task).note("reconcile", self.at(), request_id=claim["request_id"],
                                generation=claim["generation"], stage=stage, open_operations=still,
                                reasons=reasons, answer=answer)
        return answer

    def _workspace(self, task, answer):
        """§5.5 step 5. continue: thaw and keep. resume: stop the old child, keep its files, and hand back the next
        generation's claim, rebuilt at the checkpoint the authority holds. stop and decide: the known outcome is
        recorded and the workspace kept (and on stop/superseded for this host's own queued retry, its checkpoint)."""
        child = self.child if self.child is not None and self.child.task == task else None
        kind = answer.get("answer")
        if kind == "continue" and child is not None and child.frozen:
            child.thaw()
            return None
        if child is not None:
            child.kill(f"reconcile_{kind}")
        if kind == "resume":
            old = self._claim(task)
            g = answer["generation"]
            nxt = {**old, "generation": g, "task": _task_name(old["request_id"], g), "lease_until": answer["lease_until"],
                   "reserved": answer.get("reserved"), "unit": answer.get("unit") or old["unit"],
                   "resume_from": answer.get("checkpoint"), "envelope": {**old["envelope"], "generation": g},
                   "resumed_from_generation": old["generation"], "claimed_at": self.at()}
            _write_json(_claim_path(self.cfg, nxt["task"]), nxt)        # the next generation is owned first
            self._close(task, "resumed", answer)
            return nxt
        if kind == "continue":                                          # nothing left to thaw: ask again, stopped
            return self._workspace(task, self._reconcile(task, "stopped", ["continue_without_child"]))
        self._close(task, kind, answer)
        return None

    # ── one launch ──
    def _owner_write(self, task, kind, request_id, generation, args):
        """Write-ahead (R3.2-c): the record first, fsynced, then the call. None while it waits (offline)."""
        from .journal import classify
        j = self.journal(task)
        rec = j.call(kind, str(uuid.uuid4()), request_id, generation, self.at(), args)
        rec = {**rec, "mark": None, "answer": None}
        try:
            answer = self._send_record(rec)
        except (_Offline, _Busy):
            return None
        except _AuthorityError as exc:
            if kind in KNOWN_OUTCOMES:
                # A result or an effect's outcome is known here: never dropped and never rerun (review NS1). Its record
                # stays pending and its task is blocked under the error's name, as a reconcile error is, until a
                # restart replays it.
                self.blocked[task] = f"authority_error:{exc.code}"
                return None
            answer = {"state": "refused", "code": f"authority_error:{exc.code}"}
        mark = classify(kind, answer)
        if mark != "pending":
            j.mark(rec["event_id"], mark, _summary(answer))
            self._after_answer(task, rec, answer, mark)
        return answer

    def _refuse(self, claim, code, detail=None) -> dict:
        """A refusal before any spawn: reported as the item's result, with no usage."""
        return self._result(claim, "refused", [], {"code": code, **(detail or {})})

    def _record_result(self, claim, status, artifacts, detail, *, send=False):
        """The item's result as a pending record (R3.2-c): what this host reports, its usage being every launch of the
        item no accepted result has settled (unknown stays unknown), in the reserved route and unit."""
        task, rid, g = claim["task"], claim["request_id"], claim["generation"]
        used = self._unsettled(rid, g)
        usage = {"amount": None if used is None else float(used), "unit": claim["unit"], "children_stopped": True}
        text, _ = _output_of(self.cfg.artifact_root, artifacts)
        args = {"runner_id": self.runner_id, "status": status, "artifacts": artifacts, "detail": detail,
                "output_text": text, "route": claim["route"], "usage": usage}
        if send:
            return self._owner_write(task, "result", rid, g, args)
        self.journal(task).call("result", str(uuid.uuid4()), rid, g, self.at(), args)
        return None

    def _result(self, claim, status, artifacts, detail, workspace_dir=None) -> dict:
        rid, g = claim["request_id"], claim["generation"]
        answer = self._record_result(claim, status, artifacts, detail, send=True)
        out = {"state": status, "request_id": rid, "generation": g, "delivered": answer is not None
               and answer.get("state") in ("accepted", "already_applied"), "artifacts": artifacts}
        if status == "refused":
            out["code"] = detail.get("code")
        if answer is not None:
            out["answer"] = _summary(answer)
        code = None if answer is None else answer.get("code")
        if (workspace_dir is not None and out["delivered"] and code is None and status != "interrupted"
                and artifacts):
            try:                     # a finished checkout goes, as in version 1; retry, effects_open and refusals keep it
                workspace.remove(workspace_dir, Path(self.cfg.workspace_root))
                out["workspace_removed"] = True
            except Exception:
                out["workspace_removed"] = False
        return out

    def _do_not_resume(self, *texts) -> str | None:
        """R6.4: any listed session id, anywhere in what this launch would run, refuses it before any spawn, whatever
        the adapter."""
        blob = " ".join(str(t) for t in texts).lower()
        return next((s for s in self.host.do_not_resume if s in blob), None)

    def _launch(self, claim) -> dict:
        from . import cost, fakework
        cfg, task, rid = self.cfg, claim["task"], claim["request_id"]
        try:
            env = env_mod.parse(claim["envelope"], allowed_repositories=cfg.repositories, max_cost_usd=cfg.max_cost_usd,
                                now=self._now(), actors=env_mod.V2_ACTORS)
        except env_mod.EnvelopeRefused as exc:
            return self._refuse(claim, str(exc))
        if self._do_not_resume(env.brief, env.work_id):
            return self._refuse(claim, "do_not_resume")
        if env.actor not in self.host.actors() or env.scope not in self.host.scopes():
            return self._refuse(claim, "runner_not_capable")
        enforcer = cost.UNIT_ENFORCERS.get(env.actor)
        if enforcer is None:
            return self._refuse(claim, "cost_limit_unenforceable")
        if env.actor == "fakework":
            try:
                steps = fakework.parse_program(env.brief)
            except fakework.ProgramRefused as exc:
                return self._refuse(claim, str(exc))
            if self.sink is None and any(k in ("effect", "stale_effect_on_wake") for k, _ in steps):
                return self._refuse(claim, "fakework_sink_missing")
        earlier = self._unsettled(rid, claim["generation"] - 1)
        if earlier is None or claim.get("reserved") is None:
            return self._refuse(claim, "usage_unknown")         # what remains can't be known, so nothing is assumed
        cap = _decimal(claim["reserved"]) - earlier
        if cap <= 0:
            return self._refuse(claim, "reservation_exhausted")
        resume = claim.get("resume_from")
        if resume is not None and not _cp_path(cfg, rid, resume["digest"]).exists():
            return self._refuse(claim, "checkpoint_unavailable")
        _write_json(_stage_path(cfg, task), {"stage": "preparing"})
        try:
            task_dir = workspace.prepare(cfg.workspace_root, Path(cfg.repositories[env.repository]), task, env.ref)
            if resume is not None:          # §5.5 step 5: rebuilt at the checkpoint; later writes never come back
                workspace.apply_patch(task_dir, _cp_path(cfg, rid, resume["digest"]), resume["digest"].split(":", 1)[1])
        except workspace.WorkspaceError as exc:
            return self._result(claim, "failed", [], {"code": str(exc), "base_ref": env.ref})
        child = _Child(self, claim, env, task_dir)
        child.channel.open()
        argv = commands.argv(env.actor, env.scope, env.brief, task_dir)
        if env.actor == "fakework":
            argv += ["--channel", str(child.channel.path)]
            if self.sink is not None:
                argv += ["--sink", str(self.sink.path)] + ([] if self.sink.lookup else ["--no-lookup"])
            if resume is not None:
                argv += ["--from", resume["name"]]
        try:
            argv, child_env = enforcer.apply(argv, workspace.child_env(), float(cap), claim["unit"])
        except Exception:
            return self._refuse(claim, "cost_limit_unenforceable")
        if self._do_not_resume(*argv):
            return self._refuse(claim, "do_not_resume")
        return self._supervise(child, argv, child_env)

    def _supervise(self, child, argv, child_env) -> dict:
        cfg, env, task = self.cfg, child.env, child.task
        log_path = cfg.artifact_root / f"{task}.log"
        if log_path.exists():
            log_path.unlink()
        status = None
        with self._lock:
            self.child = child
        try:
            child.proc = subprocess.Popen([sys.executable, "-c", _BARRIER, *argv], cwd=child.task_dir,
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          env=child_env, start_new_session=True)
            child.capture = _BoundedCapture(child.proc.stdout, log_path, cfg.max_output_bytes)
            child.pgid, child.identity = child.proc.pid, _identity(child.proc.pid)
            if child.identity is None:
                raise RuntimeError("child_identity_unavailable")
            _write_json(_stage_path(cfg, task), {"stage": "started", "pgid": child.pgid, "identity": child.identity})
            child.proc.stdin.write(b"G")                          # only now may the work start
            child.proc.stdin.close()
            self._owner_write(task, "progress", child.request_id, child.generation,
                              {"runner_id": self.runner_id, "phase": "executing", "checkpoint": None})
            status = self._watch(child)
        except BaseException:
            child.kill("runner_fault")
            with self._lock:
                self.child = None
            raise
        if isinstance(status, dict):                     # stopped, resumed or ended by reconcile: no result here
            with self._lock:
                self.child = None
            return status
        child.end_group(term=child.alive())             # it (on Stop, deadline, bound) and descendants, then reap
        child.capture.finish(cfg.kill_grace_s)
        with self._lock:
            self.child = None
        if child.lost:                                   # reaped elsewhere: its exit can't be known (N-D1)
            return self._result(child.claim, "interrupted", [], {"code": "process_identity_unverified",
                                                                 "base_ref": env.ref}, workspace_dir=child.task_dir)
        rc = child.proc.returncode
        if rc == 75 and status is None:                  # stopped at our request; G4 or the hold already said so
            return {"state": "stopped", "request_id": child.request_id}
        if child.capture.exceeded.is_set():
            status = "output_limit"
        if not child.capture.ok:
            _write_json(_stage_path(cfg, task), {"stage": "exited", "status": "failed", "exit_code": rc})
            return self._result(child.claim, "failed", [], {"code": child.capture.error or "capture_incomplete",
                                                            "exit_code": rc, "base_ref": env.ref},
                                workspace_dir=child.task_dir)
        if status is None:
            status = "completed" if rc == 0 else "failed"
        _write_json(_stage_path(cfg, task), {"stage": "exited", "status": status, "exit_code": rc})
        artifacts = _collect(cfg, env, task, child.task_dir, status, rc)
        findings = cfg.artifact_root / f"{task}.findings"
        if findings.exists():
            artifacts.append(_artifact(cfg, task, "findings", findings.read_bytes(), env.actor))
        return self._result(child.claim, status, artifacts, {"exit_code": rc, "base_ref": env.ref},
                            workspace_dir=child.task_dir)

    def _watch(self, child):
        """The child's supervision ticks. Returns a terminal status for a result (None: by its exit code), or a dict
        when the pass ends without one (stopped, offline hold, reconcile's stop or decide, or a resume's next claim)."""
        cfg, env = self.cfg, child.env
        last_renew = last_cancel = self._mono()
        while True:
            gap = self.detector.tick()
            if gap:
                self.on_wake(gap)
            if self.stop_requested.is_set():
                return self.graceful_stop()
            self.write_state()
            if child.task in self.blocked:                 # an authority error on a known outcome (NS1)
                state = child.kill("blocked")
                child.journal.note("stop", self.at(), request_id=child.request_id, generation=child.generation,
                                   reason=self.blocked[child.task], child=state,
                                   checkpoint=self._acked_checkpoint(child.request_id))
                return {"state": "blocked", "request_id": child.request_id, "code": self.blocked[child.task]}
            if self.offline():
                if self.host.offline_scope_default == "none" and child.alive() and not child.frozen:
                    child.freeze("authority_unreachable")              # offline before it started: frozen now
                if child.alive() and self.host.offline_scope_default == "workspace_only" and not child.frozen:
                    # It keeps editing, and nothing reaches a target; its deadline and output bound still hold (S6).
                    if child.capture.exceeded.is_set():
                        return "output_limit"
                    if self._now() >= env.deadline:
                        return "timed_out"
                    self._serve(child, offline=True)
                elif self._mono() - self.offline_since >= self.values.offline_hold_s and child.alive():
                    state = child.kill("offline_hold")                    # stopped at its last checkpoint
                    child.journal.note("stop", self.at(), request_id=child.request_id, generation=child.generation,
                                       reason="offline_hold", child=state,
                                       checkpoint=self._acked_checkpoint(child.request_id))
                    return {"state": "offline", "request_id": child.request_id, "stopped": "offline_hold"}
                if not child.alive() and self.host.offline_scope_default == "workspace_only":
                    return None                                          # its result waits, pending, for reconcile
                time.sleep(cfg.tick_s)
                continue
            if self.reasons - {"restart"}:
                try:
                    launch = self.recover()
                except (_Offline, _Busy):
                    time.sleep(cfg.tick_s)
                    continue
                if launch is not None:
                    return {"state": "resumed", "request_id": child.request_id, "next_claim": launch}
                if _closed_path(cfg, child.task).exists():
                    return {"state": "reconciled", "request_id": child.request_id}
                last_renew = self._mono()
                continue
            if child.frozen:
                time.sleep(cfg.tick_s)
                continue
            try:
                self._serve(child)
                if child.exited():
                    self._serve(child)                    # anything it asked just before it exited
                    return None
                if child.capture.exceeded.is_set():
                    return "output_limit"
                if self._now() >= env.deadline:
                    return "timed_out"
                if self._mono() - last_cancel >= self.values.contact_interval_s:
                    last_cancel = self._mono()
                    if self.call("cancel_requested", child.request_id, child.generation):
                        return "cancelled"
                if self._mono() - last_renew >= self.values.renew_s:
                    if not self.call("renew", child.request_id, child.generation, runner_id=self.runner_id):
                        with self._lock:
                            self.reasons.add("refusal")          # the authority's refusal triggers §5.5 (T6c)
                        child.freeze("renew_refused")
                        continue
                    last_renew = self._mono()
            except _Offline:
                continue
            except _AuthorityError as exc:                 # an answer, not a lost connection (N2): reconcile
                with self._lock:
                    self.reasons.add("refusal")
                child.freeze(f"authority_error:{exc.code}")
                continue
            time.sleep(cfg.tick_s)

    # ── the local channel (fakework's requests) ──
    def _serve(self, child, offline=False):
        for seq, doc in child.channel.requests():
            if self.reasons - {"restart"}:
                return                      # §5.5 first: the rest waits for its answer (or the child is stopped)
            kind = doc.get("kind") if isinstance(doc, dict) else None
            if kind in ("effect_begin", "effect_hold", "effect_dispatch_held", "effect_end") and offline:
                continue                    # R4.5-e: no effect while the authority is unreachable; it waits, unanswered
            try:
                answer = self._request(child, kind, doc)
            except (_Offline, _Busy):
                return                      # answered after the next successful call (or the child is stopped)
            except _AuthorityError as exc:
                answer = {"state": "refused", "code": f"authority_error:{exc.code}"}
            if (kind or "").startswith("effect") and answer.get("state") == "refused" and answer.get("code") in FENCES:
                with self._lock:
                    self.reasons.add("refusal")     # the authority fenced the item itself: reconcile before anything
            child.channel.answer(seq, answer)

    def _request(self, child, kind, doc) -> dict:
        from . import fakework
        rid, g, task = child.request_id, child.generation, child.task
        if kind == "checkpoint" and _small_ok(doc, {"kind", "name", "paths"}):
            patch, sha = workspace.collect_diff(child.task_dir, child.env.ref)
            digest = "sha256:" + sha
            path = _cp_path(self.cfg, rid, digest)
            if not path.exists():
                _write_atomic(path, patch)
            child.journal.note("checkpoint", self.at(), request_id=rid, generation=g, name=doc["name"],
                               digest=digest)
            answer = self._owner_write(task, "progress", rid, g, {
                "runner_id": self.runner_id, "phase": "checkpoint",
                "checkpoint": {"name": doc["name"], "digest": digest, "paths": list(doc["paths"])}})
            if answer is not None and answer.get("state") == "refused":
                with self._lock:
                    self.reasons.add("refusal")
            return {"state": "pending" if answer is None else answer.get("state"), "digest": digest}
        if kind == "usage" and _small_ok(doc, {"kind", "amount"}):
            amount = doc["amount"]
            if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0):
                return {"state": "refused", "code": "channel_request_invalid"}
            child.journal.note("usage", self.at(), request_id=rid, generation=g,
                               amount=None if amount is None else float(_decimal(amount).quantize(_decimal("0.0001"))))
            return {"state": "recorded"}
        if kind == "findings" and _small_ok(doc, {"kind", "text"}) and isinstance(doc["text"], str):
            art = _artifact(self.cfg, task, "findings", doc["text"].encode()[:4096], child.env.actor)
            return {"state": "recorded", "sha256": art["sha256"]}
        if kind in ("effect_begin", "effect_hold") and _small_ok(doc, {"kind", "key", "grant"}):
            return self._effect_begin(child, doc["key"], doc["grant"], dispatch=kind == "effect_begin")
        if kind == "effect_dispatch_held" and _small_ok(doc, {"kind", "key"}):
            return self._effect_dispatch(child, doc["key"])
        if kind == "effect_end" and _small_ok(doc, {"kind", "key", "answer"}):
            return self._effect_end(child, doc["key"], doc["answer"])
        _ = fakework
        return {"state": "refused", "code": "channel_request_invalid"}

    def _effect(self, child, op, key, grant, **extra) -> dict:
        from . import fakework
        eff = {"op": op, "key": key, "grant_id": grant, "request": fakework.effect_request(key), **extra}
        return self.call("effect", self.runner_id, child.request_id, child.generation, eff)

    def _effect_begin(self, child, key, grant, *, dispatch) -> dict:
        """prepare → claim → (dispatch): each one authority transaction (R4.5-a). An effect already done is never
        sent again; one whose outcome is unknown is never retried."""
        prep = self._effect(child, "prepare", key, grant)
        if prep.get("state") != "ok":
            return {"state": "refused", "code": prep.get("code")}
        op_id, rstate = prep["operation_id"], prep.get("receipt_state")
        if rstate == "verified":
            return {"state": "done", "receipt_state": rstate, "operation_id": op_id}
        if rstate == "failed":
            # A definite "not done" (a send the target refused, or a readback that found nothing): never reported as
            # success, and never sent again under the same operation (S1).
            return {"state": "refused", "code": "effect_failed", "operation_id": op_id}
        if rstate != "prepared":
            return {"state": "refused", "code": f"receipt_{rstate}"}
        lease = self._lease(child, op_id=op_id)
        if lease is None or lease.get("lease_until") is None or _parse_iso(lease["lease_until"]) <= self._now():
            got = self._effect(child, "claim", key, grant)
            if got.get("state") != "ok":
                if got.get("code") == "receipt_busy":
                    raise _Busy()
                return {"state": "refused", "code": got.get("code")}
            lease = {**got["lease"], "key": key, "grant": grant, "request_id": child.request_id,
                     "generation": child.generation}
            self.held[op_id] = lease
        if not dispatch:
            return {"state": "held", "operation_id": op_id}
        return self._effect_dispatch(child, key, op_id=op_id)

    def _lease(self, child, *, op_id=None, key=None):
        """The receipt lease this child's own generation claimed (S3(a): a new generation never holds or uses an
        earlier one's; it claims afresh), by operation or by the child's key; None otherwise."""
        for k, v in self.held.items():
            if ((op_id is not None and k == op_id) or (op_id is None and v.get("key") == key)) \
                    and v.get("request_id") == child.request_id and v.get("generation") == child.generation:
                return v
        return None

    def _effect_dispatch(self, child, key, op_id=None) -> dict:
        from . import fakework
        lease = self._lease(child, op_id=op_id, key=key)
        if lease is None:
            return {"state": "refused", "code": "receipt_lease_missing"}
        op_id = op_id or next(k for k, v in self.held.items() if v is lease)
        child.journal.note("effect_open", self.at(), request_id=child.request_id, generation=child.generation,
                           operation_id=op_id, key=key, grant_id=lease["grant"], request=fakework.effect_request(key))
        sent = self._effect(child, "dispatch", key, lease["grant"],
                            lease={k: lease[k] for k in ("receipt_generation", "token", "mode")})
        if sent.get("state") != "ok":            # never dispatched: nothing of it is open (no readback needed)
            child.journal.note("effect_closed", self.at(), operation_id=op_id, code=sent.get("code"))
            return {"state": "refused", "code": sent.get("code"), "operation_id": op_id}
        return {"state": "ok", "operation_id": op_id, "item_id": child.request_id, "generation": child.generation,
                "lease_until": sent["lease_until"]}

    def _effect_end(self, child, key, sink_answer) -> dict:
        lease = self._lease(child, key=key)
        if lease is None or sink_answer not in ("committed", "duplicate", "lease_expired", "operation_reused"):
            return {"state": "refused", "code": "channel_request_invalid"}
        op_id = next(k for k, v in self.held.items() if v is lease)
        from . import fakework
        eff = {"op": "finish", "key": key, "grant_id": lease["grant"], "request": fakework.effect_request(key),
               "state": "verified" if sink_answer in ("committed", "duplicate") else "failed",
               "evidence": {"kind": "sink", "outcome": sink_answer}}
        answer = self._owner_write(child.task, "effect_finish", child.request_id, child.generation,
                                   {"runner_id": self.runner_id, "effect": eff, "operation_id": op_id,
                                    "receipt_generation": lease["receipt_generation"]})
        return {"state": "pending" if answer is None else answer.get("state")}

    # ── G4: a graceful stop (SIGTERM) ──
    def request_stop(self, *_):
        self.stop_requested.set()

    def graceful_stop(self) -> dict:
        """G4 (E2, E9, E14): announce stopping, stop the child at a checkpoint (asked first; stopped by signal if it
        does not answer in half the timeout), keep its pending records and send what can be sent, announce stopped
        (the authority pauses the item), all within stop_timeout_s."""
        start = self._mono()
        budget = self.host.stop_timeout_s
        self.stop_contact()
        self._announce("stopping")
        child = self.child
        out = {"state": "stopped"}
        if child is not None:
            with child.lock:
                if child.frozen:
                    child.kill("graceful")            # frozen: stopped at its last checkpoint, by this runtime
            if child.alive():
                child.channel.request_stop()
            while child.alive() and self._mono() - start < budget / 2:
                time.sleep(0.05)
            state = child.kill("graceful") if child.alive() else child.end_group(term=False)
            if child.capture is not None:
                child.capture.finish(self.cfg.kill_grace_s)
            rc = None if child.proc is None else child.proc.returncode
            # Its own exit only if this runtime never stopped it while it ran (B1): a frozen child killed here, or one
            # stopped by signal, is paused at its checkpoint and resumed at the next start, never reported failed.
            if (not child.signalled and rc is not None and rc != 75 and child.capture is not None
                    and child.capture.ok):
                # It finished before it saw the request: its real exit is kept for the next start to report.
                _write_json(_stage_path(self.cfg, child.task),
                            {"stage": "exited", "status": "completed" if rc == 0 else "failed", "exit_code": rc})
            else:
                _write_json(_stage_path(self.cfg, child.task), {"stage": "stopped", "reason": "graceful"})
            child.journal.note("stop", self.at(), request_id=child.request_id, generation=child.generation,
                               reason="graceful", child=state, checkpoint=self._acked_checkpoint(child.request_id))
            out.update(request_id=child.request_id, child=state)
            with self._lock:
                self.child = None
        for task in self.owned():                     # what can be sent now is; the rest stays pending, on disk
            if self._mono() - start >= budget * 0.8 or self.offline():
                break
            try:
                self.replay(task)
            except Exception:
                break
        self._announce("stopped")
        self.write_state(force=True)
        return out

    def _announce(self, state):
        try:
            self.contact(announced=state)
        except _AuthorityError:
            pass                                  # said or not, the stop goes on; the item lapses if it was not


# Owner writes whose outcome is known on this host: an authority error never drops them (NS1).
KNOWN_OUTCOMES = ("result", "effect_finish")
# An effect refusal that fences the item itself, not the effect: §5.5 runs before anything else is served.
FENCES = ("lease_lapsed", "stale_generation", "not_owner", "not_owned", "item_ended", "grace_ended", "cancel_requested",
          "grant_revoked", "grant_expired")


def _parse_iso(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _small_ok(doc, keys) -> bool:
    if not isinstance(doc, dict) or set(doc) != keys:
        return False
    try:
        return len(json.dumps(doc)) <= 8192
    except (TypeError, ValueError):
        return False


def _summary(answer) -> dict:
    """What a pending record keeps of an answer: never a lease token."""
    if not isinstance(answer, dict):
        return {"state": None}
    return {k: v for k, v in answer.items() if k in ("state", "code", "event_id", "decision_id", "attempt",
                                                      "generation", "operation_id", "receipt_state", "recorded_as")}


def _output_of(artifact_root, artifacts):
    from .dispatcher_adapter import output_of
    return output_of(artifact_root, artifacts)
