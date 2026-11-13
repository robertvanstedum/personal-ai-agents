#!/usr/bin/env python3
"""The deploy's summary: every stack this release refreshed, and what it left for a person (#550).

deploy.yml's deploy job runs this last (always) and appends the output to the run's summary page. Inputs are the job's own
step results, from the environment: SERVICES, MAIN (the main deploy step's outcome), GUILDHALL_NAMED (did the classifier
name the Guildhall stack), GUILDHALL (guildhall_prod.sh refresh's result line), GUILDHALL_STEP (that step's outcome),
WORKER_IMAGE, MC_LIVE, MC_NEEDED, MC_CHANGES. CI only: never part of a service image. Standard library only.
"""
from __future__ import annotations

import os


def _main(env: dict) -> str:
    services = env.get("SERVICES", "").split()
    outcome = env.get("MAIN", "")
    if not services:
        return "not part of this release (no main service changed)"
    if outcome == "success":
        return "deployed: " + ", ".join(services)
    if outcome in ("", "skipped"):
        return "NOT DEPLOYED: an earlier step stopped the job (" + ", ".join(services) + " still on the previous release)"
    return f"**FAILED** ({outcome}): " + ", ".join(services)


GUILDHALL_WORDS = {
    "refreshed": "refreshed and verified",
    "current": "already on this release's images; verified",
    "skipped": "skipped: Guildhall is not switched on on this host (start is by hand)",
    "refused": "**REFUSED**: a gate refused; Guildhall unchanged on its previous images",
    "rolled_back": "**FAILED, put back**: Guildhall runs its previous images and verifies; this release's Guildhall part is not live",
    "rollback_failed": "**FAILED, NOT VERIFIED**: act now (guildhall_prod.sh status on the host)",
}


def _guildhall(env: dict) -> str:
    if env.get("GUILDHALL_NAMED") != "true":
        return "not part of this release (neither its images nor its files changed)"
    result = env.get("GUILDHALL", "")
    if not result:
        step = env.get("GUILDHALL_STEP", "")
        return "NOT RUN: an earlier step stopped the job" if step in ("", "skipped") else f"**UNCONFIRMED** ({step})"
    words = GUILDHALL_WORDS.get(result, f"**UNCONFIRMED** ({result})")
    if env.get("WORKER_IMAGE"):
        words += "; workers built from this commit"
    return words


def _mc(env: dict) -> str:
    live, needed, changes = env.get("MC_LIVE", ""), env.get("MC_NEEDED", ""), env.get("MC_CHANGES", "")
    if needed == "yes":
        return (f"**MC agent release needed**: {changes} file(s) MC's image holds changed since `{live}`. "
                "Tag an `mc-agent-v*` release (deploy-mc-agent.yml).")
    if needed == "no":
        return f"current: production runs `{live}`, and nothing it holds changed since"
    return "**unknown**: could not read the tag production runs; check by hand"


def summary(env: dict) -> str:
    rows = [("Main", _main(env)), ("Guildhall", _guildhall(env)), ("MC agent", _mc(env))]
    lines = ["## Stacks in this release", "", "| Stack | This release |", "|---|---|"]
    lines += [f"| {name} | {text} |" for name, text in rows]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(summary(dict(os.environ)), end="")
