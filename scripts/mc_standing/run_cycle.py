#!/usr/bin/env python3
"""Run one MC standing morning cycle (standing spec v0.3.1; plan v0.3 P0).

Scheduled by the platform at 05:40 America/Chicago (Codex's scheduler registration). Reads an
approved watchlist, sanitized sources under --source-root (read-only), the owner control file and
the emergency disable file; writes only its own spool; delivers through the ingress listener.

A watchlist whose approval_ref still says "candidate" is refused unless --dev-fixture is given
(dev fixture runs only). The producer token is read from --token-file (0600), never argv.
Exit codes: 0 delivered / already final / paused / skipped; 2 configuration error; 3 the cycle
ended failed, held or with delivery still pending/unknown (the next run reconciles).
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.mc_standing import registry, runner  # noqa: E402
from core.mc_standing.control import Control  # noqa: E402
from core.mc_standing.ingress_client import IngressClient  # noqa: E402
from core.mc_standing.spool import Spool  # noqa: E402


def _token(path: Path) -> str:
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("token file must not be readable by group or others")
    value = path.read_text().strip()
    if not 32 <= len(value) <= 256:
        raise SystemExit("token file content invalid")
    return value


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--watchlist", required=True, type=Path)
    p.add_argument("--state-root", required=True, type=Path)
    p.add_argument("--source-root", required=True, type=Path)
    p.add_argument("--environment", required=True, choices=("dev", "prod"))
    p.add_argument("--policy-id", required=True)
    p.add_argument("--destination", required=True)
    p.add_argument("--ingress-url", required=True)
    p.add_argument("--token-file", required=True, type=Path)
    p.add_argument("--control-file", required=True, type=Path)
    p.add_argument("--disable-file", required=True, type=Path)
    p.add_argument("--details-link")
    p.add_argument("--dev-fixture", action="store_true")
    args = p.parse_args(argv)
    try:
        wl = registry.load(args.watchlist)
    except (OSError, registry.RegistryError) as exc:
        print(json.dumps({"state": "config_error", "reason": str(exc)[:120]}))
        return 2
    if any(w.approval_ref.startswith("candidate") for w in wl.watches) and not args.dev_fixture:
        print(json.dumps({"state": "config_error", "reason": "watchlist_not_approved"}))
        return 2
    if args.dev_fixture and args.environment != "dev":
        print(json.dumps({"state": "config_error", "reason": "dev_fixture_only_in_dev"}))
        return 2
    token = _token(args.token_file)
    spool = Spool(args.state_root)
    control = Control(args.control_file, args.disable_file, spool)
    deps = {}
    dsn_file = os.environ.get("MC_STANDING_GUEST_DSN_FILE")
    if dsn_file:
        from minimoi_portal import guest_requests
        dsn = Path(dsn_file).read_text().strip()
        deps["guest_summary_fn"] = lambda: guest_requests.pending_summary(guest_requests.PostgresStore(dsn))
    result = runner.run_cycle(
        spool=spool, watchlist=wl, policy_id=args.policy_id, environment=args.environment,
        now=datetime.now(timezone.utc), source_root=args.source_root,
        ingress=IngressClient(args.ingress_url, lambda: token), destination_binding=args.destination,
        kill=control.killed, paused=control.paused, details_link=args.details_link, deps=deps,
        clock=lambda: datetime.now(timezone.utc))
    print(json.dumps({k: result.get(k) for k in ("cycle_key", "state", "reason")}))
    return 0 if result.get("state") in ("delivered", "paused", "skipped") or result.get("reason") == "already_final" \
        else 3


if __name__ == "__main__":
    raise SystemExit(main())
