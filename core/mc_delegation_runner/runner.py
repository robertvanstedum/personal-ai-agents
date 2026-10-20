"""The Mac delegation runner: claim one task, run it in a fresh workspace, report back.

Harness v0.3.1 §6.2. The dispatcher (Codex's) owns work state, grants and the return path into
the MiniMoi conversation; this runner only executes what it claimed:

  resend pending results → heartbeat → claim (lease, generation) → validate envelope → fresh
  workspace → run the agent CLI → renew lease / poll cancel / enforce deadline and output bound →
  collect evidence → persist the terminal result → post it (at least once, across restarts)

* Heartbeat is not pickup: ``online``/``busy`` is reported separately from claims.
* The child process group is owned for its whole lifetime: whatever happens (dispatcher errors,
  exceptions, lost lease), it is stopped and reaped before run_once returns.
* A lost lease, or losing contact with the dispatcher's control calls, stops the work and posts
  no completion: a stale worker never completes someone else's claim.
* The terminal result (status, artifacts, stable event id) is written durably before it is sent,
  and resent on later runs until acknowledged, without rerunning the work. Workspaces, logs and
  artifacts are named per request *and generation*.
* Bounds actually enforced: deadline, cancel, output size (checked while running). A cost cap is
  only accepted for actors whose CLI enforces one (``cost_enforced_actors``); otherwise the task
  is refused as unenforceable rather than run under a cap nobody checks.
* Artifacts carry author, sha256, size and ``review_status: unreviewed``.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import commands, envelope as env_mod, workspace


@dataclass
class RunnerConfig:
    runner_id: str
    actors: tuple                       # which CLIs this runner can run
    scopes: tuple
    repositories: dict                  # name → local mirror path
    workspace_root: Path
    artifact_root: Path
    max_cost_usd: float
    cost_enforced_actors: tuple = ()    # actors whose CLI enforces the envelope's cost cap
    lease_renew_s: float = 40.0
    tick_s: float = 2.0
    kill_grace_s: float = 5.0
    max_output_bytes: int = 2 * 1024 * 1024
    capabilities: dict = field(default_factory=dict)

    def caps(self) -> dict:
        return {"actors": list(self.actors), "scopes": list(self.scopes),
                "repositories": sorted(self.repositories),
                "cost_enforced_actors": list(self.cost_enforced_actors), **self.capabilities}


def _now():
    return datetime.now(timezone.utc)


def _event_id(request_id: str, generation: int, status: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-delegation:{request_id}:{generation}:{status}"))


def _task_name(request_id: str, generation: int) -> str:
    return f"{request_id}-g{generation}"


def _kill(proc, grace: float):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _write_atomic(path: Path, data: bytes):
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


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


def _persist_result(cfg, request_id, generation, status, artifacts, detail) -> dict:
    record = {"request_id": request_id, "generation": generation, "status": status,
              "event_id": _event_id(request_id, generation, status), "artifacts": artifacts,
              "detail": detail, "acknowledged": False}
    path = _result_path(cfg, request_id, generation)
    if path.exists():                     # never replace a recorded outcome
        return json.loads(path.read_text())
    _write_atomic(path, json.dumps(record, sort_keys=True).encode())
    return record


def _send(dispatcher, cfg, record) -> bool:
    try:
        dispatcher.result(record["request_id"], record["generation"], event_id=record["event_id"],
                          status=record["status"], artifacts=record["artifacts"], detail=record["detail"])
    except Exception:
        return False
    record["acknowledged"] = True
    _write_atomic(_result_path(cfg, record["request_id"], record["generation"]),
                  json.dumps(record, sort_keys=True).encode())
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


def _bounded_tail(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        fh.seek(max(0, size - limit))
        return fh.read(limit)


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

    if env.actor not in cfg.actors or env.scope not in cfg.scopes:
        return _finish(dispatcher, cfg, env, "refused", [], {"code": "runner_not_capable"})
    if env.actor not in cfg.cost_enforced_actors:
        return _finish(dispatcher, cfg, env, "refused", [], {"code": "cost_limit_unenforceable"})

    dispatcher.heartbeat(cfg.runner_id, state="busy", capabilities=cfg.caps())
    task = _task_name(env.request_id, env.generation)
    try:
        task_dir = workspace.prepare(cfg.workspace_root, Path(cfg.repositories[env.repository]), task, env.ref)
    except workspace.WorkspaceError as exc:
        return _finish(dispatcher, cfg, env, "failed", [], {"code": str(exc)})

    log_path = cfg.artifact_root / f"{task}.log"
    proc = None
    status, outcome_local = None, None
    try:
        with open(log_path, "xb") as out:
            proc = subprocess.Popen(commands.argv(env.actor, env.scope, env.brief, task_dir), cwd=task_dir,
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                    env=workspace.child_env(), start_new_session=True)
            dispatcher.progress(env.request_id, env.generation, phase="executing")
            last_renew = time.monotonic()
            while proc.poll() is None:
                if now() >= env.deadline:
                    status = "timed_out"
                    break
                if log_path.stat().st_size > cfg.max_output_bytes:
                    status = "output_limit"
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
            _kill(proc, cfg.kill_grace_s)

    if outcome_local:
        try:
            dispatcher.heartbeat(cfg.runner_id, state="online", capabilities=cfg.caps())
        except Exception:
            pass
        return {"state": outcome_local, "request_id": env.request_id, "delivered": False}
    if status is None:
        status = "completed" if proc.returncode == 0 else "failed"

    artifacts = [_artifact(cfg, task, "output", _bounded_tail(log_path, cfg.max_output_bytes), env.actor)]
    if env.scope == "build":
        patch, _ = workspace.collect_diff(task_dir, env.ref)
        artifacts.append(_artifact(cfg, task, "patch", patch, env.actor))
    return _finish(dispatcher, cfg, env, status, artifacts, {"exit_code": proc.returncode, "base_ref": env.ref},
                   workspace_dir=task_dir)


def _finish(dispatcher, cfg, env, status, artifacts, detail, workspace_dir=None) -> dict:
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
    return out
