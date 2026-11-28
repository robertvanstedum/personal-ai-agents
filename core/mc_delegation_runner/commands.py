"""How a delegated task is started for each agent CLI. Argument vectors only, never a shell.

These match the installed CLIs on the Mac runner (claude 2.1.x, codex-cli 0.145.x) as of 20 Oct
2026, and are an activation gate, not a claim: authentication, terms, sandbox behaviour,
cancellation and one real round trip must be verified before the runner is enabled.

research  read-only: Read/Grep/Glob only, answered as text; no edits, shell or network.
build     workspace-write: edits only inside the fresh checkout; no network for Codex's sandbox.
          Claude Code (#693, Robert's decision 3 of 27 Nov 2026) may also run a fixed list of commands and nothing
          else: the test runner and local git reads and ``git add`` (BUILD_ALLOWED). git push, the GitHub CLI, curl,
          wget and the web tools are denied by name (BUILD_DISALLOWED); every other shell command needs an approval
          that print mode cannot give, so it is refused. It does not commit: the runner does, after it exits.
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

# Claude Code, build only. The runner commits, runs the tests itself and reports them (runner._build_steps).
BUILD_PREAMBLE = (
    "This is a build: you are on a branch the runner made for this job. Edit files in this checkout only. "
    "Run the tests that cover what you changed with pytest (python -m pytest <paths>); git status, git diff, "
    "git log and git add are also available, and no other shell command is. Do not commit: the runner commits your "
    "changes after you finish, runs the tests itself and reports its own result. End your answer with a short "
    "summary: what you changed, and which tests you ran with their real results. If you ran none, or any failed, "
    "say so plainly.\n\n"
)
BUILD_ALLOWED = ("Bash(python -m pytest:*)", "Bash(python3 -m pytest:*)", "Bash(pytest:*)", "Bash(git status:*)",
                 "Bash(git diff:*)", "Bash(git log:*)", "Bash(git add:*)", "Bash(node --check:*)")
BUILD_DISALLOWED = ("Bash(git push:*)", "Bash(gh:*)", "WebFetch", "WebSearch", "Bash(curl:*)", "Bash(wget:*)")


def argv(actor: str, scope: str, brief: str, task_dir: Path, binary: str | None = None) -> list[str]:
    """The argument vector for one task. ``binary`` is the pinned path a version 2 host starts (launch contract, R6.7-d
    step 1; host.yaml ``tools``); without it, version 1's bare names, unchanged for production's runner."""
    prompt = PREAMBLE + brief
    if actor == "codex":
        sandbox = "read-only" if scope == "research" else "workspace-write"
        # --ignore-user-config: none of the Mac user's Codex settings, MCP servers or model choice (his configured
        # model needs a newer CLI, 26 Oct); --json: events, from which the answer is read (dispatcher_adapter.readable).
        return [binary or "codex", "exec", "--sandbox", sandbox, "--cd", str(task_dir), "--skip-git-repo-check", "--ephemeral",
                "--ignore-user-config", "--json", "--color", "never", prompt]
    if actor == "claude-code":
        if scope == "research":
            # Read-only tools only, and nothing to approve: plan mode would end in a plan-approval call that
            # print mode denies, returning an empty answer (seen on dev, 21 Oct).
            return [binary or "claude", "-p", prompt, "--output-format", "json", "--permission-mode", "dontAsk",
                    "--tools", "Read,Grep,Glob", "--allowedTools", "Read", "Grep", "Glob"]
        # claude 2.1.x: --allowedTools and --disallowedTools each take a list of rules (checked with --help, 27 Nov).
        return [binary or "claude", "-p", PREAMBLE + BUILD_PREAMBLE + brief, "--output-format", "json",
                "--permission-mode", "acceptEdits", "--allowedTools", *BUILD_ALLOWED,
                "--disallowedTools", *BUILD_DISALLOWED]
    if actor == "fakework":
        # Its program is the brief itself (no preamble: it must start with "fakework/1"). It has no tools, no network
        # and no provider client; the runtime adds its channel, sink and cap (runner._launch, cost.UNIT_ENFORCERS). A
        # pinned build (fakecli, §9.3) is started by its own path.
        if binary:
            return [binary, "--program", brief]
        return [sys.executable, "-I", "-B", FAKEWORK, "--program", brief]
    raise ValueError("actor_unsupported")
