"""Real budget binding for delegate CLIs (the runner refuses any actor without one).

Claude Code: ``--max-budget-usd`` (claude 2.1.x, print mode) is enforced by the CLI itself.
Codex: codex-cli 0.145 has no spend cap flag, so there is no adapter; Codex delegations are refused
as cost_limit_unenforceable until Robert decides how subscription billing counts.
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


ENFORCERS = {"claude-code": ClaudeBudget()}
