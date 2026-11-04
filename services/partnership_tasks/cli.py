"""Read-only command line for the task service (spec v1.1 Q07: a CLI before any dashboard).

    python -m services.partnership_tasks.cli --root DIR --environment synthetic --bindings FILE --token-file FILE \\
        reconstruct --task TK-...  [--at 2026-11-04T16:00:00+00:00]
        reconstruct --request RQ-...
        open
        history WORDS...

The token is read from a file (or PARTNERSHIP_TOKEN), never from the command line, so it does not land in shell
history or process listings; a token file readable by group or others is refused. Nothing here creates a record;
like every read of the store it first finishes an intent a dead writer left (E01). Output is JSON on stdout.

Exit codes: 0 ok · 2 not found or not yours (indistinguishable by design) · 3 store held or missing ·
4 no usable token · 64 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from services.partnership_tasks.auth import Bindings
from services.partnership_tasks.reconstruct import Reconstructor, history, open_work
from services.partnership_tasks.service import StoreUnavailable, TaskService


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="partnership_tasks")
    ap.add_argument("--root", required=True)
    ap.add_argument("--environment", required=True, choices=["synthetic", "dev", "staging", "prod"])
    ap.add_argument("--bindings", required=True)
    ap.add_argument("--token-file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("reconstruct")
    which = rc.add_mutually_exclusive_group(required=True)
    which.add_argument("--task")
    which.add_argument("--request")
    rc.add_argument("--at")
    sub.add_parser("open")
    hs = sub.add_parser("history")
    hs.add_argument("words", nargs="+")
    args = ap.parse_args(argv)

    try:
        if args.token_file:
            mode = os.stat(args.token_file).st_mode
            if mode & 0o077:
                print(json.dumps({"error": "token_file_not_private"}))
                return 4
            token = Path(args.token_file).read_text("utf-8").strip()
        else:
            token = os.environ.get("PARTNERSHIP_TOKEN", "").strip()
    except OSError:
        print(json.dumps({"error": "token_unreadable"}))
        return 4
    if not token:
        print(json.dumps({"error": "no_token"}))
        return 4
    if not (Path(args.root) / "journal" / "journal.jsonl").is_file():
        print(json.dumps({"error": "no_store_at_root"}))                 # never create a store by reading
        return 3
    svc = TaskService(args.root, args.environment, Bindings.load(args.bindings))
    if svc.bindings.resolve(token, svc._clock()) is None:
        print(json.dumps({"error": "not_authenticated"}))
        return 4
    try:
        if args.cmd == "reconstruct":
            kind, id_ = ("task", args.task) if args.task else ("request", args.request)
            out = Reconstructor(svc).reconstruct(kind, id_, token, at=args.at)
            if out is None:
                print(json.dumps({"found": False}))
                return 2
            print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True))
            return 3 if out["integrity"] == "damaged" else 0
        if args.cmd == "open":
            print(json.dumps(open_work(svc, token), indent=1))
            return 0
        print(json.dumps(history(svc, token, " ".join(args.words)), indent=1, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 64
    except StoreUnavailable:
        print(json.dumps({"error": "store_unavailable"}))
        return 3


if __name__ == "__main__":
    sys.exit(main())
