"""``moi``: the owner's command line for the memory shelf (amendment R2, R4).

    moi review                       what waits for Robert (metadata only)
    moi approve <id> [--under <m>]   the ONLY path to approval events
    moi approve-source <source>      switch a source on after its dry run
    moi designate <id>               confirm a pasted "file this" candidate
    moi dry-run <source>             write the dry-run listing (claude-code | codex | backfill-guild | backfill-curator)
    moi watch <source>               one watcher pass (dry run only until approved)
    moi inbox                        one inbox pass       moi drain   apply the outbox to the shelf
    moi canary emit | check          write the canary into the inbox | audit it (read-only)
    moi ledger [--canary]            F1: captured vs expected exclusions vs missing
    moi fidelity [--sample N] [--canary]   F2 sample (read-only)

**Approval authority.** ``approve``, ``approve-source`` and ``designate`` need an
interactive confirmation typed at a terminal; there is no ``--yes``. Under a pipe,
a script or an agent's shell (no TTY) they refuse with ``not_interactive``. The
``OwnerAuthority`` token is built here, after the confirmation, and nowhere else.
Output is metadata only: ids, counts, codes, hashes. Nothing is installed or scheduled.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from core.memory_shelf import approvals, backfill, canary, config, fidelity, fsio, inbox, ledger, review, watchers
from core.memory_shelf.events import OwnerAuthority
from core.memory_shelf.shelf import Shelf

BACKFILL = tuple(backfill.SOURCES)
OK, USAGE, REFUSED = 0, 2, 3


def tty_confirm(prompt: str) -> bool:
    """Interactive owner confirmation. No TTY means no approval (fail closed)."""
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} Type 'yes' to confirm: ").strip().lower() == "yes"


def _shelf(cfg: config.Config, now=None) -> Shelf:
    return Shelf(cfg.shelf_root, min_free_bytes=cfg.min_free_bytes, min_free_fraction=cfg.min_free_fraction,
                 clock=(lambda: now) if now else None)


def _fingerprint(cfg: config.Config, name: str) -> str | None:
    if name in cfg.sources:
        return cfg.sources[name].fingerprint()
    if name in backfill.SOURCES:
        return backfill.fingerprint(name, cfg.repo_root, cfg.never_copy)
    return None


def _authority(confirm: Callable[[str], bool], what: str, out) -> OwnerAuthority | None:
    if not confirm(f"Owner action: {what}."):
        print("refused: not_interactive_or_declined (approvals need Robert's own confirmation at a terminal)", file=out)
        return None
    return OwnerAuthority("moi-approve")


def cmd_review(cfg, shelf, out) -> int:
    print("# dry runs", file=out)
    names = [*cfg.sources, *BACKFILL]
    for name in names:
        listing = fsio.read_json(approvals.dry_run_path(shelf, name))
        if not isinstance(listing, dict):
            continue
        status = approvals.source_status(shelf, name, _fingerprint(cfg, name))
        counts = listing.get("counts", {})
        print(f"dry-run\t{name}\t{status}\t{json.dumps(counts, sort_keys=True)}\t{listing.get('generated_at')}\t"
              f"sha256:{str(listing.get('listing_sha256'))[:12]}", file=out)
    print("# designation candidates", file=out)
    for item in review.items(shelf):
        if item["type"] == "designation-candidate":
            print(f"designation-candidate\t{item['detail']['record']}\tturn {item['detail']['ordinal']}", file=out)
    print("# possible same conversation", file=out)
    for item in review.items(shelf):
        if item["type"] == "possible-same-conversation":
            print(f"possible-same\t{item['detail']['records'][0]}\t{item['detail']['records'][1]}\t"
                  f"{','.join(item['detail']['reasons'])}", file=out)
    print("# refused inbox files", file=out)
    for row in inbox.refused_listing(cfg.inbox_root):
        print(f"refused\t{row['name']}\t{row['reason']}\t{row['bytes']}\tsha256:{row['sha256'][:12]}", file=out)
    return OK


def cmd_ledger(shelf, out, canary_flag: bool) -> int:
    rep = ledger.report(shelf, canary=canary_flag)
    print(f"# ledger{' (canary)' if canary_flag else ''}", file=out)
    for name, row in rep["sources"].items():
        print(f"{name}\texpected={row['expected']}\tcaptured={row['captured']}\t"
              f"excluded={json.dumps(row['excluded'], sort_keys=True)}\trefused={json.dumps(row['refused'], sort_keys=True)}\t"
              f"missing={json.dumps(row['missing'], sort_keys=True)}", file=out)
    print(f"expected-exclusions\t{json.dumps(rep['expected_exclusions'], sort_keys=True)}", file=out)
    print("OK" if rep["ok"] else f"MISSING {rep['missing']}", file=out)
    return OK if rep["ok"] else REFUSED


def main(argv: Sequence[str] | None = None, *, confirm: Callable[[str], bool] = tty_confirm, out=None,
         now: datetime | None = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="moi")
    parser.add_argument("--config", default=os.environ.get("MOI_CONFIG"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("review")
    p = sub.add_parser("approve"); p.add_argument("id"); p.add_argument("--under")
    p = sub.add_parser("approve-source"); p.add_argument("source")
    p = sub.add_parser("designate"); p.add_argument("id")
    p = sub.add_parser("dry-run"); p.add_argument("source")
    p = sub.add_parser("watch"); p.add_argument("source")
    sub.add_parser("inbox")
    sub.add_parser("drain")
    p = sub.add_parser("canary"); p.add_argument("action", choices=["emit", "check"])
    p = sub.add_parser("ledger"); p.add_argument("--canary", action="store_true")
    p = sub.add_parser("fidelity"); p.add_argument("--sample", type=int, default=5); p.add_argument("--canary", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except config.ConfigError:
        print("config problem", file=out)
        return USAGE
    shelf = _shelf(cfg, now)
    clock = now or datetime.now(timezone.utc)

    if args.cmd == "review":
        return cmd_review(cfg, shelf, out)
    if args.cmd == "ledger":
        return cmd_ledger(shelf, out, args.canary)
    if args.cmd == "approve":
        auth = _authority(confirm, f"approve record {args.id}" + (f" under mandate {args.under}" if args.under else ""), out)
        if not auth:
            return REFUSED
        try:
            event = approvals.approve_record(shelf, args.id, auth, under=args.under, now=now)
        except approvals.ApprovalRefused as exc:
            print(f"refused: {exc}", file=out)
            return REFUSED
        print(f"approved\t{args.id}\t{event['kind']}\t{event['at']}", file=out)
        return OK
    if args.cmd == "designate":
        auth = _authority(confirm, f"designate record {args.id} as curated", out)
        if not auth:
            return REFUSED
        try:
            event = approvals.designate_record(shelf, args.id, auth, now=now)
        except approvals.ApprovalRefused as exc:
            print(f"refused: {exc}", file=out)
            return REFUSED
        print(f"designated\t{args.id}\t{event['at']}", file=out)
        return OK
    if args.cmd == "approve-source":
        fp = _fingerprint(cfg, args.source)
        if fp is None:
            print("unknown source", file=out)
            return USAGE
        auth = _authority(confirm, f"approve source {args.source} after its dry run", out)
        if not auth:
            return REFUSED
        try:
            doc = approvals.approve_source(shelf, args.source, fp, auth, now=now)
        except approvals.ApprovalRefused as exc:
            print(f"refused: {exc}", file=out)
            return REFUSED
        print(f"source-approved\t{args.source}\t{doc['approved_at']}", file=out)
        return OK
    if args.cmd == "dry-run":
        if args.source in cfg.sources:
            listing = watchers.dry_run(shelf, cfg.sources[args.source], clock)
        elif args.source in BACKFILL:
            listing = backfill.dry_run(shelf, args.source, cfg.repo_root, cfg.never_copy, clock)
        else:
            print("unknown source", file=out)
            return USAGE
        print(f"dry-run\t{args.source}\t{json.dumps(listing['counts'], sort_keys=True)}\tsha256:{listing['listing_sha256'][:12]}", file=out)
        return OK
    if args.cmd == "watch":
        if args.source not in cfg.sources:
            print("unknown source", file=out)
            return USAGE
        res = watchers.run_source(shelf, cfg.sources[args.source], now=clock)
        print(f"watch\t{args.source}\t{res['status']}\t{json.dumps(res['counts'], sort_keys=True)}", file=out)
        return OK
    if args.cmd == "inbox":
        res = inbox.process(shelf, cfg.inbox_root, now=clock, never_copy=cfg.never_copy)
        print(f"inbox\t{res['status']}\t{json.dumps(res['counts'], sort_keys=True)}", file=out)
        return OK
    if args.cmd == "drain":
        results = shelf.drain()
        print(f"drain\t{json.dumps({o: sum(r.outcome == o for r in results) for o in sorted({r.outcome for r in results})}, sort_keys=True)}", file=out)
        return OK
    if args.cmd == "canary":
        if args.action == "emit":
            path = canary.emit(cfg.inbox_root, clock)
            print(f"canary\temitted\t{path.name}", file=out)
            return OK
        res = fidelity.sample(shelf, cfg, n=5, canary=True)
        print(f"canary\tchecked={res['checked']}\tok={res['ok']}\tfailed={res['failed']}\tskipped={json.dumps(res['skipped'], sort_keys=True)}", file=out)
        return OK if res["checked"] and not res["failed"] else REFUSED
    if args.cmd == "fidelity":
        res = fidelity.sample(shelf, cfg, n=args.sample, canary=args.canary)
        print(f"fidelity\tchecked={res['checked']}\tok={res['ok']}\tfailed={res['failed']}\tskipped={json.dumps(res['skipped'], sort_keys=True)}", file=out)
        for r in res["results"]:
            for f in r.get("findings", []):
                print(f"finding\t{r['record']}\t{f['kind']}\tposition={f['position']}\texpected={f['expected']}\tactual={f['actual']}", file=out)
        return OK if not res["failed"] else REFUSED
    return USAGE


if __name__ == "__main__":
    sys.exit(main())
