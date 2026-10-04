"""``moi``: the owner's command line for the memory shelf (amendment R2, R4).

    moi review                       what waits for Robert (metadata only)
    moi approve <id> [--under <m>]   the ONLY path to approval events
    moi approve-source <source>      switch a source on after its dry run
    moi designate <id> [<id> ...]    designate records as curated (one typed "yes"); short or full ids
    moi list [<source>] [--canary]   your records for one source (claude-ai | grok | claude-code | codex | paste); titles shown,
                                     so it runs only at a real terminal
    moi dry-run <source>             write the dry-run listing (claude-code | codex | inbox | backfill-guild | backfill-curator)
    moi watch <source>               one watcher pass (dry run only until approved)
    moi inbox                        one inbox pass (nothing but the canary moves until `approve-source inbox`)
    moi drain                        apply the outbox to the shelf
    moi canary emit | check          write the canary into the inbox | audit it (read-only)
    moi ledger [--canary]            F1: captured vs expected exclusions vs missing
    moi fidelity [--sample N] [--canary]   F2 sample (read-only)
    moi migrate-preview [source]     read-only, counts only: what the current parser rules would change in each provider's records
    moi reprocess inbox              re-read claude.ai / Grok records from their kept export files with the current rules (one typed "yes");
                                     a new edition where the turns differ, old editions untouched
    moi doctor                       read-only: does each record's main file agree with its own editions? counts and short ids
    moi repair                       rebuild the body of records `doctor` finds unreadable, from their current edition (one typed "yes")

**Titles stay in the owner's terminal.** ``moi list`` prints each record's stored title (a slug) and so needs a real
TTY on stdin and stdout; under a pipe, a script or an agent's shell it refuses with ``not_interactive`` and prints nothing.

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

from core.memory_shelf import approvals, backfill, canary, config, editions, fidelity, fsio, inbox, ledger, migration, repair, review, ulid, watchers, weight
from core.memory_shelf.events import OwnerAuthority
from core.memory_shelf.shelf import Shelf

BACKFILL = tuple(backfill.SOURCES)
OK, USAGE, REFUSED = 0, 2, 3


def stdio_is_tty() -> bool:
    """Both ends at a terminal: the owner is reading this. False under a pipe, a script or an agent's shell."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def tty_confirm(prompt: str) -> bool:
    """Interactive owner confirmation. No TTY means no approval (fail closed)."""
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} Type 'yes' to confirm: ").strip().lower() == "yes"


def _shelf(cfg: config.Config, now=None) -> Shelf:
    return Shelf(cfg.shelf_root, min_free_bytes=cfg.min_free_bytes, min_free_fraction=cfg.min_free_fraction,
                 clock=(lambda: now) if now else None)


def _fingerprint(cfg: config.Config, name: str) -> str | None:
    if name == inbox.SOURCE:
        return inbox.fingerprint(cfg.inbox_root, cfg.never_copy)
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
    names = [*cfg.sources, *BACKFILL, inbox.SOURCE]
    for name in names:
        listing = fsio.read_json(approvals.dry_run_path(shelf, name))
        if not isinstance(listing, dict):
            continue
        status = approvals.source_status(shelf, name, _fingerprint(cfg, name))
        counts = listing.get("counts", {})
        print(f"dry-run\t{name}\t{status}\t{json.dumps(counts, sort_keys=True)}\t{listing.get('generated_at')}\t"
              f"sha256:{str(listing.get('listing_sha256'))[:12]}", file=out)
    from core.memory_shelf.shelf import prune_flags
    closed = prune_flags(shelf)
    if closed:
        print(f"# closed {closed} possible-same flags between exported records (paste-only rule)", file=out)
    print("# designation candidates", file=out)
    for item in review.items(shelf):
        if item["type"] == "designation-candidate":
            print(f"designation-candidate\t{item['detail']['record']}\tturn {item['detail']['ordinal']}", file=out)
    print("# possible same conversation", file=out)
    for item in review.items(shelf):
        if item["type"] == "possible-same-conversation":
            print(f"possible-same\t{item['detail']['records'][0]}\t{item['detail']['records'][1]}\t"
                  f"{','.join(item['detail']['reasons'])}", file=out)
    print("# coverage flags (a file shows more than was taken, or a format the reader has not seen)", file=out)
    for item in review.items(shelf):
        if item["type"] == "coverage-flag":
            d = item["detail"]
            print(f"coverage-flag\t{d.get('source')}\t{d.get('date')}\t{d.get('bytes')} bytes\t"
                  f"{','.join(d.get('flags') or [])}\tgap={json.dumps(d.get('gap') or {}, sort_keys=True)}\t"
                  f"unknown={','.join(d.get('unknown') or []) or '-'}", file=out)
    print("# refused inbox files", file=out)
    for row in inbox.refused_listing(cfg.inbox_root):
        print(f"refused\t{row['name']}\t{row['reason']}\t{row['bytes']}\tsha256:{row['sha256'][:12]}", file=out)
    return OK


LIST_SOURCES = ("claude-ai", "grok", "claude-code", "codex", "paste")


def cmd_list(shelf, out, source: str | None, canary_flag: bool) -> int:
    from core.memory_shelf.shelf import read_front_matter
    print("# id\ttitle\tcreated\tmessages\ttier\tstate\teditions", file=out)
    rows = []
    for entry in shelf.list_records(canary=canary_flag):
        if source and entry.get("provider") != source:
            continue
        main = shelf.main_path(entry)
        try:
            meta = read_front_matter(main)
        except OSError:
            continue
        events = meta.get("events") or []
        designated = any(e.get("kind") == "designated-curated" for e in events)
        state = ",".join(filter(None, ["designated" if designated else "", weight.derive(meta)["weight"]]))
        rows.append((str(meta.get("created", ""))[:10], meta["id"],
                     f"{ulid.short(meta['id'])}\t{entry.get('title') or '-'}\t{str(meta.get('created', ''))[:10]}\t"
                     f"{(meta.get('normalized') or {}).get('turns', 0)}\t{meta.get('tier')}\t{state}\t"
                     f"{len(editions.list_editions(main.parent))}"))
    for _, _, line in sorted(rows):
        print(line, file=out)
    print(f"# {len(rows)} records", file=out)
    return OK


def cmd_designate(shelf, tokens: list[str], confirm, out, now) -> int:
    resolved, skipped = [], []
    for token in dict.fromkeys(tokens):                       # the same id twice is one record
        rid, why = shelf.resolve_id(token)
        if rid is None:
            skipped.append((token, why))
        elif rid in resolved:
            skipped.append((token, "duplicate_id"))
        else:
            meta = record_front(shelf, rid)
            if any(e.get("kind") == "designated-curated" for e in (meta.get("events") or [])):
                skipped.append((token, "already_designated"))
            else:
                resolved.append(rid)
    for token, why in skipped:
        print(f"skipped\t{token}\t{why}", file=out)
    if not resolved:
        print("refused: no record to designate", file=out)
        return REFUSED
    shorts = ", ".join(ulid.short(r) for r in resolved)
    auth = _authority(confirm, f"designate {len(resolved)} record{'s' if len(resolved) != 1 else ''} as curated ({shorts})", out)
    if not auth:
        return REFUSED
    for rid in resolved:
        try:
            event = approvals.designate_record(shelf, rid, auth, now=now)
        except approvals.ApprovalRefused as exc:
            print(f"skipped\t{ulid.short(rid)}\t{exc}", file=out)
            skipped.append((rid, "refused"))
            continue
        print(f"designated\t{ulid.short(rid)}\t{event['at']}", file=out)
    return OK if not skipped else REFUSED


def record_front(shelf, record_id: str) -> dict:
    from core.memory_shelf.shelf import read_front_matter
    path = shelf.find_record(record_id)
    try:
        return read_front_matter(path) if path else {}
    except OSError:
        return {}


def cmd_ledger(shelf, out, canary_flag: bool) -> int:
    rep = ledger.report(shelf, canary=canary_flag)
    print(f"# ledger{' (canary)' if canary_flag else ''}", file=out)
    for name, row in rep["sources"].items():
        print(f"{name}\texpected={row['expected']}\tcaptured={row['captured']}\t"
              f"excluded={json.dumps(row['excluded'], sort_keys=True)}\trefused={json.dumps(row['refused'], sort_keys=True)}\t"
              f"held={json.dumps(row['held'], sort_keys=True)}\tmissing={json.dumps(row['missing'], sort_keys=True)}", file=out)
    print(f"expected-exclusions\t{json.dumps(rep['expected_exclusions'], sort_keys=True)}", file=out)
    cov = rep["coverage"]
    print("coverage\t" + (json.dumps(cov, sort_keys=True) if cov else "clear (every capture took all the messages its file shows)"), file=out)
    print("OK" if rep["ok"] else f"MISSING {rep['missing']}", file=out)
    return OK if rep["ok"] else REFUSED


def main(argv: Sequence[str] | None = None, *, confirm: Callable[[str], bool] = tty_confirm, out=None,
         now: datetime | None = None, is_tty: Callable[[], bool] = stdio_is_tty) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="moi")
    parser.add_argument("--config", default=os.environ.get("MOI_CONFIG"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("review")
    p = sub.add_parser("approve"); p.add_argument("id"); p.add_argument("--under")
    p = sub.add_parser("approve-source"); p.add_argument("source")
    p = sub.add_parser("designate"); p.add_argument("ids", nargs="+")
    p = sub.add_parser("list"); p.add_argument("source", nargs="?"); p.add_argument("--canary", action="store_true")
    p = sub.add_parser("dry-run"); p.add_argument("source")
    p = sub.add_parser("watch"); p.add_argument("source")
    sub.add_parser("inbox")
    sub.add_parser("drain")
    p = sub.add_parser("canary"); p.add_argument("action", choices=["emit", "check"])
    p = sub.add_parser("ledger"); p.add_argument("--canary", action="store_true")
    p = sub.add_parser("migrate-preview"); p.add_argument("source", nargs="?")
    p = sub.add_parser("reprocess"); p.add_argument("source", choices=["inbox"])
    sub.add_parser("doctor")
    sub.add_parser("repair")
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
    if args.cmd == "migrate-preview":
        res = migration.preview(shelf, cfg, only=args.source)
        for provider, row in res["providers"].items():
            flat = {k: v for k, v in row.items()}
            print(f"migrate-preview\t{provider}\t{json.dumps(flat, sort_keys=True)}", file=out)
        print("migrate-preview\tread_only", file=out)
        return OK
    if args.cmd == "reprocess":
        auth = _authority(confirm, "re-read the inbox records from their kept export files with the current rules "
                                   "(new editions only; nothing is deleted or rewritten)", out)
        if not auth:
            return REFUSED
        print(f"reprocess\tinbox\t{json.dumps(migration.reprocess_inbox(shelf, cfg, now=clock), sort_keys=True)}", file=out)
        return OK
    if args.cmd == "doctor":
        res = repair.inspect(shelf)
        print(f"doctor\trecords={res['records']}\tstates={json.dumps(res['states'], sort_keys=True)}", file=out)
        for state, short in res["ids"].items():
            print(f"{state}\t{','.join(short)}", file=out)
        return OK if set(res["states"]) <= {repair.OK, repair.MAIN_BEHIND} else REFUSED
    if args.cmd == "repair":
        res = repair.inspect(shelf)
        todo = [i for state in repair.RECOVERABLE for i in res["ids"].get(state, [])]
        if not todo:
            print("repair\tnothing to repair", file=out)
            return OK
        auth = _authority(confirm, f"rebuild the body of {len(todo)} record{'s' if len(todo) != 1 else ''} from "
                                   f"their editions (the old main file is kept under repairs/)", out)
        if not auth:
            return REFUSED
        outcome: dict[str, int] = {}
        for short in todo:
            rid, why = shelf.resolve_id(short)
            result = repair.recover(shelf, rid, now=now) if rid else why
            outcome[result] = outcome.get(result, 0) + 1
        print(f"repair\t{json.dumps(outcome, sort_keys=True)}", file=out)
        return OK if set(outcome) <= {"repaired"} else REFUSED
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
        return cmd_designate(shelf, args.ids, confirm, out, now)
    if args.cmd == "list":
        if not is_tty():
            print("refused: not_interactive (titles are shown only at your own terminal)", file=out)
            return REFUSED
        if args.source and args.source not in (*LIST_SOURCES, "canary"):
            print("unknown source", file=out)
            return USAGE
        return cmd_list(shelf, out, args.source, args.canary)
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
        if args.source == inbox.SOURCE:
            listing = inbox.dry_run(shelf, cfg.inbox_root, cfg.never_copy, clock)
        elif args.source in cfg.sources:
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
        res = inbox.run(shelf, cfg.inbox_root, now=clock, never_copy=cfg.never_copy)
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
