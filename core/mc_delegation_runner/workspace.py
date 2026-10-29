"""A fresh, isolated workspace per delegated task.

* Cloned from the runner's own local mirror (never the shared project checkout), then checked
  out at the envelope's exact commit, detached.
* The clone has **no remote**: there is nothing to push to, and the git config also refuses
  pushes. Push/merge/deploy authority is never implied by a build scope.
* The child process environment is rebuilt from an allowlist; tokens for GitHub, AWS, MiniMoi
  services and model providers are not passed through. The policy is defined once, in
  ``launch_env`` (shared with the front-door connections); this module adds only ``GIT_TERMINAL_PROMPT``.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from core.mc_delegation_runner import launch_env

_TASK = re.compile(r"^[0-9a-f-]{36}-g[1-9][0-9]{0,8}$")    # <request uuid>-g<generation>
ENV_ALLOW = launch_env.BASE_ALLOW
# Credential names that must never reach a delegate, even if present in the runner's environment.
ENV_DENY_PATTERNS = launch_env.DENY_PATTERNS


class WorkspaceError(RuntimeError):
    pass


def _git(args, cwd=None, timeout=120):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                            env=child_env())
    if result.returncode != 0:
        raise WorkspaceError(f"git_{args[0]}_failed")
    return result.stdout


def child_env(extra: dict | None = None) -> dict:
    try:
        return launch_env.child_env({"GIT_TERMINAL_PROMPT": "0", **(extra or {})})
    except launch_env.EnvRefused:
        raise WorkspaceError("credential_variable_refused") from None


def prepare(root: Path, mirror: Path, task_name: str, ref: str) -> Path:
    """``task_name`` is ``<request id>-g<generation>``: a reclaimed task under a new generation
    never shares a workspace with an earlier attempt."""
    if not _TASK.match(task_name):
        raise WorkspaceError("task_id_invalid")
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    task_dir = root / task_name
    if task_dir.exists():
        raise WorkspaceError("workspace_exists")      # never reuse a workspace
    _git(["clone", "--quiet", "--no-checkout", "--no-hardlinks", str(mirror), str(task_dir)])
    _git(["remote", "remove", "origin"], cwd=task_dir)
    _git(["config", "remote.pushDefault", "nowhere"], cwd=task_dir)
    _git(["config", "push.default", "nothing"], cwd=task_dir)
    _git(["checkout", "--quiet", "--detach", ref], cwd=task_dir)
    if _git(["rev-parse", "HEAD"], cwd=task_dir).strip() != ref:
        raise WorkspaceError("checkout_mismatch")
    return task_dir


def collect_diff(task_dir: Path, ref: str) -> tuple[bytes, str]:
    """Everything the delegate changed relative to the exact base commit, as a patch (tracked and
    new files), with its sha256. This is evidence for independent review, not an applied change."""
    _git(["add", "--all", "--intent-to-add"], cwd=task_dir)
    patch = subprocess.run(["git", "diff", "--binary", ref], cwd=task_dir, capture_output=True,
                           timeout=120, env=child_env()).stdout
    return patch, hashlib.sha256(patch).hexdigest()


def apply_patch(task_dir: Path, patch_path: Path, sha256: str):
    """Rebuild a workspace at a checkpoint (spec v0.5 §5.5 step 5): apply the patch recorded for it, after checking
    its digest, onto the fresh checkout at the exact base commit. Anything written after the checkpoint never reaches
    the new workspace."""
    data = Path(patch_path).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise WorkspaceError("checkpoint_patch_mismatch")
    if data:
        result = subprocess.run(["git", "apply", "--binary", "--whitespace=nowarn", str(patch_path)], cwd=task_dir,
                                capture_output=True, timeout=120, env=child_env())
        if result.returncode != 0:
            raise WorkspaceError("checkpoint_patch_failed")


def remove(task_dir: Path, root: Path):
    task_dir, root = Path(task_dir).resolve(), Path(root).resolve()
    if root not in task_dir.parents or not _TASK.match(task_dir.name):
        raise WorkspaceError("refuse_remove_outside_root")
    shutil.rmtree(task_dir)
