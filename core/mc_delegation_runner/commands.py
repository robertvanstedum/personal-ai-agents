"""How a delegated task is started for each agent CLI. Argument vectors only, never a shell.

These match the installed CLIs on the Mac runner (claude 2.1.x, codex-cli 0.145.x) as of 20 Oct
2026, and are an activation gate, not a claim: authentication, terms, sandbox behaviour,
cancellation and one real round trip must be verified before the runner is enabled.

research  read-only: Read/Grep/Glob only, answered as text; no edits, shell or network.
build     workspace-write: edits only inside the fresh checkout; no network for Codex's sandbox;
          git push and GitHub CLI calls are disallowed for Claude Code.
"""
from __future__ import annotations

from pathlib import Path

PREAMBLE = (
    "You are working on a delegated MiniMoi task in an isolated checkout at an exact commit. "
    "Do not push, merge, deploy, change credentials or contact external services. "
    "Your result is reviewed independently before anything is used.\n\n"
)


def argv(actor: str, scope: str, brief: str, task_dir: Path) -> list[str]:
    prompt = PREAMBLE + brief
    if actor == "codex":
        sandbox = "read-only" if scope == "research" else "workspace-write"
        return ["codex", "exec", "--sandbox", sandbox, "--cd", str(task_dir), "--skip-git-repo-check", prompt]
    if actor == "claude-code":
        if scope == "research":
            # Read-only tools only, and nothing to approve: plan mode would end in a plan-approval call that
            # print mode denies, returning an empty answer (seen on dev, 21 Oct).
            return ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk",
                    "--tools", "Read,Grep,Glob", "--allowedTools", "Read", "Grep", "Glob"]
        return ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits",
                "--disallowedTools", "Bash(git push:*)", "Bash(gh:*)", "WebFetch"]
    raise ValueError("actor_unsupported")
