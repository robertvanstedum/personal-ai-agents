"""Entry point for the operator-only MC cost probe, inside the staging portal
container: loads the running portal's own services (the same app the
container serves) and hands them to guild_ui/mc/cost_probe.py.

    docker exec -i minimoi-portal python -m minimoi_portal.mc_cost_probe --yes-spend
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    from minimoi_portal import app as portal_app
    from minimoi_portal.guild_ui.mc import cost_probe, ledger_gate
    services = portal_app.app.extensions["guild_ui_next"]["services"]
    mount = portal_app.app.extensions["guild_ui_next"]
    # The mount's own admitted settings (never the process environment): with the turn ledger on,
    # the probe would bypass it, so it refuses before creating any conversation or note.
    if ledger_gate.enabled(mount.get("feature_environ") or {}):
        print("Refused: the turn ledger is on for this portal; probes do not run through it. Nothing was sent.")
        return 5
    return cost_probe.main(argv, services=services)


if __name__ == "__main__":
    sys.exit(main())
