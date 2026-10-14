"""``moi ask``: Robert's own entry point to the one Ask procedure (v0.5 D1a).

    moi ask "question"  [--raw | --tier curated|raw|all] [--after YYYY-MM-DD] [--before YYYY-MM-DD] [-k N] [--json]
    moi ask --opened QUERY_ID CHUNK_ID      note that you opened a hit (one line in the log)
    moi ask --mark QUERY_ID useful|not      the one-keystroke usefulness mark
    moi ask --log-summary                   counts only: queries by caller, opened, marks
    moi ask --canary-check [DAY]            F3: is the day's canary findable by Ask? (exit 1 unless found; not logged)

The caller is always ``robert/cli``: the owner at a terminal. There is no way to pass another identity (an agent's identity comes from its
token at the Ask service, ``service.py``). The default tier is curated (spec E4); ``--raw`` searches the raw record, which is where most of the
shelf is until records are designated. Options for all: ``--shelf-root``, ``--index``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from core.memory_ask import ask as ask_mod
from core.memory_ask import canary_check, querylog
from core.memory_index import indexer, policy, search, store
from core.memory_index.cli import _embedder, _paths

EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2


def _render(result: dict, out) -> None:
    status = result["status"]
    print(f"query {result['query_id']}  {result['caller']['agent']}/{result['caller']['entry_point']}  "
          f"tier={result['tier']}  meaning={status['meaning']}  log={status['log']}  {status['ms']} ms", file=out)
    for label in ("keyword", "meaning"):
        print(f"\n== {label} ({len(result[label])}) ==", file=out)
        for i, hit in enumerate(result[label], 1):
            print(f"\n[{i}] {hit['chunk_id']}\n    {hit['file']}:{hit['lines'][0]}-{hit['lines'][1]}  {hit['date']}  "
                  f"{hit['speaker']}  score={hit['score']:.4f}\n    {hit['provenance']}", file=out)
            print("    " + hit["passage"].replace("\n", "\n    "), file=out)
    slot = result["adjacent"]
    print("\n== Adjacent ==", file=out)
    if "chunk_id" in slot:
        print(f"{slot['chunk_id']}  ({slot['basis']}, distance {slot['distance']:.3f})\n    {slot['file']}:{slot['lines'][0]}-"
              f"{slot['lines'][1]}  {slot['date']}  {slot['speaker']}\n    {slot['provenance']}", file=out)
        print("    " + slot["passage"].replace("\n", "\n    "), file=out)
    else:
        print(f"none ({slot['none']})", file=out)
    if result["unresolved"]:
        print(f"\n== unresolved ({len(result['unresolved'])}) ==", file=out)
        for u in result["unresolved"]:
            print(f"  {u['chunk_id']}  {u['list']}  {u['reason']}", file=out)
    if result["tier"] == "curated" and not (result["keyword"] or result["meaning"]):
        print("\nNothing in the curated record. Add --raw to search the raw record too.", file=out)


def main(argv: Sequence[str] | None = None, *, embedder=None, out=None, clock=None) -> int:
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="moi ask")
    p.add_argument("query", nargs="?")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--tier", choices=sorted(policy.TIERS))
    p.add_argument("--after")
    p.add_argument("--before")
    p.add_argument("-k", type=int, default=search.DEFAULT_K)
    p.add_argument("--json", action="store_true")
    p.add_argument("--opened", nargs=2, metavar=("QUERY_ID", "CHUNK_ID"))
    p.add_argument("--mark", nargs=2, metavar=("QUERY_ID", "useful|not"))
    p.add_argument("--log-summary", action="store_true")
    p.add_argument("--canary-check", nargs="?", const="today", metavar="YYYY-MM-DD")
    p.add_argument("--shelf-root")
    p.add_argument("--index")
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    root, index_path = _paths(args)
    log = querylog.path_for(index_path)
    try:
        if args.log_summary:
            print(json.dumps(querylog.summary(log), sort_keys=True, indent=1), file=out)
            return EXIT_OK
        if args.canary_check:
            from datetime import datetime, timezone
            day = (clock() if clock else datetime.now(timezone.utc)).strftime("%Y-%m-%d") if args.canary_check == "today" \
                else args.canary_check
            verdict = canary_check.check(day, shelf_root=root, embedder=embedder or _embedder(), index_path=index_path)
            print(json.dumps(verdict, sort_keys=True), file=out)
            return EXIT_OK if verdict["state"] == "found" else EXIT_PROBLEM
        if args.opened:
            ask_mod.opened(*args.opened, index_path=index_path, clock=clock)
            print("noted", file=out)
            return EXIT_OK
        if args.mark:
            ask_mod.mark(*args.mark, index_path=index_path, clock=clock)
            print("noted", file=out)
            return EXIT_OK
        if not args.query:
            print("usage: moi ask \"question\"", file=out)
            return EXIT_USAGE
        tier = args.tier or ("all" if args.raw else "curated")
        caller = policy.Caller(tier=tier, agent="robert", entry_point="cli")     # the owner at a terminal; agents use the service
        result = ask_mod.ask(args.query, shelf_root=root, embedder=embedder or _embedder(), caller=caller, k=args.k,
                             index_path=index_path, created_after=args.after, created_before=args.before, clock=clock)
    except querylog.LogRefused as exc:
        print(f"refused: {exc.code}", file=out)
        return EXIT_PROBLEM
    except ask_mod.BadQuery as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_USAGE
    except ValueError:
        print("refused: bad_caller", file=out)
        return EXIT_USAGE
    except store.IndexMissing:
        print("no index: run moi rebuild", file=out)
        return EXIT_PROBLEM
    except store.IndexIncompatible:
        print("index made by another version: run moi rebuild", file=out)
        return EXIT_PROBLEM
    except search.QueryRefused as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_PROBLEM
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=1), file=out)
    else:
        _render(result, out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
