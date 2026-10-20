"""The Mac delegation runner: claim one task, run it in a fresh workspace, report back.

Harness v0.3.1 §6.2. The dispatcher (Codex's) owns work state, grants and the return path into
the MiniMoi conversation; this runner only executes what it claimed:

  heartbeat → claim (lease, generation) → validate envelope → fresh workspace → run the agent
  CLI → renew lease / poll cancel / enforce deadline → collect evidence → post result

* Heartbeat is not pickup: the runner reports ``online``/``busy`` separately from claims.
* A lost lease (renew refused = another generation owns the task) kills the process and posts
  nothing: a stale worker must never complete someone else's claim.
* Cancel and deadline kill the whole process group; a completed effect is not "undone" by cancel.
* Results are delivered at least once with a stable event id the dispatcher deduplicates.
* Artifacts carry author, sha256, size and ``review_status: unreviewed``; a delegate's summary is
  never acceptance.
"""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import commands, envelope as env_mod, workspace

RESULT_RETRIES = 3


@dataclass
class RunnerConfig:
    runner_id: str
    actors: tuple                       # which CLIs this runner can run
    scopes: tuple
    repositories: dict                  # name → local mirror path
    workspace_root: Path
    artifact_root: Path
    max_cost_usd: float
    lease_renew_s: float = 40.0
    tick_s: float = 2.0
    kill_grace_s: float = 5.0
    max_output_bytes: int = 2 * 1024 * 1024
    capabilities: dict = field(default_factory=dict)

    def caps(self) -> dict:
        return {"actors": list(self.actors), "scopes": list(self.scopes),
                "repositories": sorted(self.repositories), **self.capabilities}


def _now():
    return datetime.now(timezone.utc)


def _event_id(request_id: str, generation: int, status: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-delegation:{request_id}:{generation}:{status}"))


def _kill(proc, grace: float):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def _artifact(cfg: RunnerConfig, request_id: str, kind: str, data: bytes, author: str) -> dict:
    cfg.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = cfg.artifact_root / f"{request_id}.{kind}"
    with open(path, "xb") as fh:                       # never overwrite an earlier artifact
        fh.write(data)
    os.chmod(path, 0o600)
    return {"kind": kind, "ref": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "author": author, "review_status": "unreviewed"}


def _post_result(dispatcher, env, status: str, artifacts: list, detail: dict) -> dict | None:
    event_id = _event_id(env.request_id, env.generation, status)
    for attempt in range(RESULT_RETRIES):
        try:
            return dispatcher.result(env.request_id, env.generation, event_id=event_id, status=status,
                                     artifacts=artifacts, detail=detail)
        except Exception:
            if attempt == RESULT_RETRIES - 1:
                return None
            time.sleep(0.01 * (attempt + 1))
    return None


def run_once(dispatcher, cfg: RunnerConfig, *, now=_now, sleep=time.sleep) -> dict:
    """One heartbeat + at most one claimed task. Returns a small local outcome record."""
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
        if isinstance(request_id, str) and isinstance(generation, int):
            dispatcher.result(request_id, generation, event_id=_event_id(request_id, generation, "refused"),
                              status="refused", artifacts=[], detail={"code": str(exc)})
        return {"state": "refused", "code": str(exc)}
    if env.actor not in cfg.actors or env.scope not in cfg.scopes:
        _post_result(dispatcher, env, "refused", [], {"code": "runner_not_capable"})
        return {"state": "refused", "code": "runner_not_capable"}

    dispatcher.heartbeat(cfg.runner_id, state="busy", capabilities=cfg.caps())
    try:
        task_dir = workspace.prepare(cfg.workspace_root, Path(cfg.repositories[env.repository]),
                                     env.request_id, env.ref)
    except workspace.WorkspaceError as exc:
        _post_result(dispatcher, env, "failed", [], {"code": str(exc)})
        return {"state": "failed", "code": str(exc)}

    log_path = cfg.artifact_root / f"{env.request_id}.log"
    cfg.artifact_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(log_path, "xb") as out:
        proc = subprocess.Popen(commands.argv(env.actor, env.scope, env.brief, task_dir), cwd=task_dir,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env=workspace.child_env(), start_new_session=True)
        dispatcher.progress(env.request_id, env.generation, phase="executing")
        status, last_renew = None, time.monotonic()
        while proc.poll() is None:
            if now() >= env.deadline:
                _kill(proc, cfg.kill_grace_s)
                status = "timed_out"
                break
            if dispatcher.cancel_requested(env.request_id, env.generation):
                _kill(proc, cfg.kill_grace_s)
                status = "cancelled"
                break
            if time.monotonic() - last_renew >= cfg.lease_renew_s:
                if not dispatcher.renew(env.request_id, env.generation):
                    _kill(proc, cfg.kill_grace_s)
                    dispatcher.heartbeat(cfg.runner_id, state="online", capabilities=cfg.caps())
                    return {"state": "lease_lost", "request_id": env.request_id}
                last_renew = time.monotonic()
            sleep(cfg.tick_s)
        if status is None:
            status = "completed" if proc.returncode == 0 else "failed"

    output = log_path.read_bytes()[-cfg.max_output_bytes:]
    artifacts = [_artifact(cfg, env.request_id, "output", output, env.actor)]
    if env.scope == "build":
        patch, _ = workspace.collect_diff(task_dir, env.ref)
        artifacts.append(_artifact(cfg, env.request_id, "patch", patch, env.actor))
    detail = {"exit_code": proc.returncode, "base_ref": env.ref}
    ack = _post_result(dispatcher, env, status, artifacts, detail)
    dispatcher.heartbeat(cfg.runner_id, state="online", capabilities=cfg.caps())
    return {"state": status, "request_id": env.request_id, "delivered": ack is not None,
            "artifacts": artifacts, "workspace": str(task_dir)}
