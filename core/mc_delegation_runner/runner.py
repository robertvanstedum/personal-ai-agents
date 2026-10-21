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
    """Resend pending results, heartbeat, then at most one claimed task."""
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
