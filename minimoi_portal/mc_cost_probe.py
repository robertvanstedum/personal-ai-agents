"""Entry point for the operator-only MC cost probe, inside the staging portal
container: loads the running portal's own services (the same app the
container serves) and hands them to guild_ui/mc/cost_probe.py.

    docker exec -i minimoi-portal python -m minimoi_portal.mc_cost_probe --yes-spend
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    from minimoi_portal import app as portal_app
    from minimoi_portal.guild_ui.mc import cost_probe
    services = portal_app.app.extensions["guild_ui_next"]["services"]
    return cost_probe.main(argv, services=services)


if __name__ == "__main__":
    sys.exit(main())
