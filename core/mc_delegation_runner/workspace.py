"""A fresh, isolated workspace per delegated task.

* Cloned from the runner's own local mirror (never the shared project checkout), then checked
  out at the envelope's exact commit, detached. A build (#693) then gets a local branch ``mc/<job>`` at
  that commit, so the runner can commit the result itself after the agent exits; research stays detached.
* The clone has **no remote**: there is nothing to push to, and the git config also refuses
  pushes. Push/merge/deploy authority is never implied by a build scope.
* The child process environment is rebuilt from an allowlist; tokens for GitHub, AWS, MiniMoi
  services and model providers are not passed through. The policy is defined once, in the launch
  policy file that ``launch_env`` reads (shared with the front-door connections and the probe); this
  module adds only ``GIT_TERMINAL_PROMPT``.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

from core.mc_delegation_runner import launch_env

_TASK = re.compile(r"^[0-9a-f-]{36}-g[1-9][0-9]{0,8}$")    # <request uuid>-g<generation>
# A build's branch (#693): mc/<first 8 hex of the request id>-g<generation>. publish.py pushes nothing else.
BRANCH = re.compile(r"^mc/[0-9a-f]{8}-g[1-9][0-9]{0,8}$")
COMMIT_NAME, COMMIT_EMAIL = "MiniMoi delegated build", "noreply@minimoi.ai"
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


def branch_name(request_id: str, generation: int) -> str:
    """``mc/<first 8 hex of the request id>-g<generation>``, or refused."""
    name = f"mc/{str(request_id).replace('-', '')[:8]}-g{generation}"
    if not BRANCH.fullmatch(name):
        raise WorkspaceError("branch_invalid")
    return name


def prepare(root: Path, mirror: Path, task_name: str, ref: str, branch: str | None = None) -> Path:
    """``task_name`` is ``<request id>-g<generation>``: a reclaimed task under a new generation
    never shares a workspace with an earlier attempt. ``branch`` (a build only) is created at the exact
    commit and checked out; the clone still has no remote and still refuses pushes."""
    if not _TASK.match(task_name):
        raise WorkspaceError("task_id_invalid")
    if branch is not None and not BRANCH.fullmatch(branch):
        raise WorkspaceError("branch_invalid")
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
    if branch is not None:
        _git(["checkout", "--quiet", "-b", branch], cwd=task_dir)
        if _git(["rev-parse", "HEAD"], cwd=task_dir).strip() != ref:
            raise WorkspaceError("checkout_mismatch")
    return task_dir


def _own_git_env() -> dict:
    """For git commands the runner itself runs after the agent exited: the scrubbed child environment, and none of
    the Mac user's git configuration (signing, hooks path, templates)."""
    return {**child_env(), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def commit_all(task_dir: Path, ref: str, message: str) -> dict:
    """The runner's own commit of whatever the agent left (#693): everything is staged and committed on the current
    branch under a fixed identity, with no hook and no signing. ``{"commit": sha or None, "changed": bool}``, where
    ``changed`` is whether the branch's tree differs from the exact base commit. Running it again changes nothing."""
    env = _own_git_env()

    def git(args, ok=(0,), code=None):
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=task_dir, capture_output=True,
                           text=True, timeout=120, env=env)
        if r.returncode not in ok:
            raise WorkspaceError(code or f"git_{args[0]}_failed")
        return r

    git(["add", "--all"])
    if git(["diff", "--cached", "--quiet", "HEAD"], ok=(0, 1)).returncode == 1:
        git(["-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}", "-c", "commit.gpgsign=false",
             "commit", "--quiet", "--no-verify", "-m", message], code="git_commit_failed")
    head = git(["rev-parse", "HEAD"]).stdout.strip()
    changed = git(["diff", "--quiet", ref, "HEAD"], ok=(0, 1)).returncode == 1
    return {"commit": head if changed else None, "changed": changed}


def changed_paths(task_dir: Path, ref: str) -> list:
    """Repository-relative paths that differ between the exact base commit and the branch head."""
    out = subprocess.run(["git", "diff", "--name-only", "-z", ref, "HEAD"], cwd=task_dir, capture_output=True,
                         timeout=120, env=_own_git_env())
    if out.returncode != 0:
        raise WorkspaceError("git_diff_failed")
    return [p for p in out.stdout.decode("utf-8", errors="replace").split("\0") if p]


def collect_diff(task_dir: Path, ref: str, committed: bool = False) -> tuple[bytes, str]:
    """Everything the delegate changed relative to the exact base commit, as a patch (tracked and
    new files), with its sha256. This is evidence for independent review, not an applied change.
    ``committed``: the runner already committed the work (a completed build), so the patch is the base
    commit against the branch head, and nothing the runner's own test run left behind is in it."""
    if committed:
        patch = subprocess.run(["git", "diff", "--binary", ref, "HEAD"], cwd=task_dir, capture_output=True,
                               timeout=120, env=_own_git_env()).stdout
        return patch, hashlib.sha256(patch).hexdigest()
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
