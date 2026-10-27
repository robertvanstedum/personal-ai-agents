"""Real budget binding for delegate CLIs (the runner refuses any actor without one).

Claude Code: ``--max-budget-usd`` (claude 2.1.x, print mode) is enforced by the CLI itself.
Codex: codex-cli 0.145 has no spend cap flag. It runs on Robert's ChatGPT plan, so he set its limit (26 Oct 2026:
"that's fine 30 minutes and we adjust if needed to be higher"): research only, read-only sandbox, and the job's own
deadline (30 min, set by the dispatcher relay and enforced by the runner), one job at a time (the runner is serial).
"""
from __future__ import annotations

import math


class ClaudeBudget:
    def apply(self, argv, env, cap_usd):
        if not argv or argv[0] != "claude" or "-p" not in argv:
            raise ValueError("not_a_claude_print_invocation")
        if isinstance(cap_usd, bool) or not isinstance(cap_usd, (int, float)) or not math.isfinite(cap_usd) or cap_usd <= 0:
            raise ValueError("cap_invalid")
        return [*argv, "--max-budget-usd", f"{cap_usd:.2f}"], dict(env)


class CodexPlanLimit:
    """No dollars to cap on a plan: admit only ``codex exec --sandbox read-only`` with no bypass flag. The time cap is
    the job deadline, which the runner enforces for every actor."""
    BYPASS = ("--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "--add-dir", "--oss")

    def apply(self, argv, env, cap_usd):
        if argv[:2] != ["codex", "exec"] or any(a in self.BYPASS for a in argv):
            raise ValueError("not_a_read_only_codex_exec")
        i = argv.index("--sandbox") if "--sandbox" in argv else -1
        if i < 0 or argv[i + 1:i + 2] != ["read-only"] or argv.count("--sandbox") != 1:
            raise ValueError("codex_sandbox_not_read_only")
        return list(argv), dict(env)


ENFORCERS = {"claude-code": ClaudeBudget(), "codex": CodexPlanLimit()}
