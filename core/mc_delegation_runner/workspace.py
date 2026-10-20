"""A fresh, isolated workspace per delegated task.

* Cloned from the runner's own local mirror (never the shared project checkout), then checked
  out at the envelope's exact commit, detached.
* The clone has **no remote**: there is nothing to push to, and the git config also refuses
  pushes. Push/merge/deploy authority is never implied by a build scope.
* The child process environment is rebuilt from an allowlist; tokens for GitHub, AWS, MiniMoi
  services and model providers are not passed through.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

_TASK = re.compile(r"^[0-9a-f-]{36}-g[1-9][0-9]{0,8}$")    # <request uuid>-g<generation>
ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL")
# Credential names that must never reach a delegate, even if present in the runner's environment.
ENV_DENY_PATTERNS = (r"^GH_", r"^GITHUB_", r"^AWS_", r"^MINIMOI_", r"_API_KEY$", r"_TOKEN$",
                     r"^DATABASE_URL$", r"^SSH_AUTH_SOCK$")


class WorkspaceError(RuntimeError):
    pass


def _git(args, cwd=None, timeout=120):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                            env=child_env())
    if result.returncode != 0:
        raise WorkspaceError(f"git_{args[0]}_failed")
    return result.stdout


def child_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOW}
    env["GIT_TERMINAL_PROMPT"] = "0"
    for k, v in (extra or {}).items():
        if any(re.search(p, k) for p in ENV_DENY_PATTERNS):
            raise WorkspaceError("credential_variable_refused")
        env[k] = v
    return env


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


def remove(task_dir: Path, root: Path):
    task_dir, root = Path(task_dir).resolve(), Path(root).resolve()
    if root not in task_dir.parents or not _TASK.match(task_dir.name):
        raise WorkspaceError("refuse_remove_outside_root")
    shutil.rmtree(task_dir)
