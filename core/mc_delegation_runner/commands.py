"""How a delegated task is started for each agent CLI. Argument vectors only, never a shell.

These match the installed CLIs on the Mac runner (claude 2.1.x, codex-cli 0.145.x) as of 20 Oct
2026, and are an activation gate, not a claim: authentication, terms, sandbox behaviour,
cancellation and one real round trip must be verified before the runner is enabled.

research  read-only: Read/Grep/Glob only, answered as text; no edits, shell or network.
build     workspace-write: edits only inside the fresh checkout; no network for Codex's sandbox;
          git push and GitHub CLI calls are disallowed for Claude Code.
"""
from __future__ import annotations

import sys
from pathlib import Path

# fakework/1 (spec v0.5 §9.3), the test worker: run by this runtime's own interpreter, isolated, from the bundle.
FAKEWORK = str(Path(__file__).resolve().with_name("fakework.py"))

PREAMBLE = (
    "You are working on a delegated MiniMoi task in an isolated checkout at an exact commit. "
    "Do not push, merge, deploy, change credentials or contact external services. "
    "Your result is reviewed independently before anything is used.\n\n"
)


def argv(actor: str, scope: str, brief: str, task_dir: Path) -> list[str]:
    prompt = PREAMBLE + brief
    if actor == "codex":
        sandbox = "read-only" if scope == "research" else "workspace-write"
        # --ignore-user-config: none of the Mac user's Codex settings, MCP servers or model choice (his configured
        # model needs a newer CLI, 26 Oct); --json: events, from which the answer is read (dispatcher_adapter.readable).
        return ["codex", "exec", "--sandbox", sandbox, "--cd", str(task_dir), "--skip-git-repo-check", "--ephemeral",
                "--ignore-user-config", "--json", "--color", "never", prompt]
    if actor == "claude-code":
        if scope == "research":
            # Read-only tools only, and nothing to approve: plan mode would end in a plan-approval call that
            # print mode denies, returning an empty answer (seen on dev, 21 Oct).
            return ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk",
                    "--tools", "Read,Grep,Glob", "--allowedTools", "Read", "Grep", "Glob"]
        return ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "acceptEdits",
                "--disallowedTools", "Bash(git push:*)", "Bash(gh:*)", "WebFetch"]
    if actor == "fakework":
        # Its program is the brief itself (no preamble: it must start with "fakework/1"). It has no tools, no network
        # and no provider client; the runtime adds its channel, sink and cap (runner._argv_v2, cost.UNIT_ENFORCERS).
        return [sys.executable, "-I", "-B", FAKEWORK, "--program", brief]
    raise ValueError("actor_unsupported")
