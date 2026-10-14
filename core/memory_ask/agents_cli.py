"""``moi ask-agents``: the owner's commands for who may use the Ask service. Terminal only.

    moi ask-agents list                                         names and grants (never a token or a hash)
    moi ask-agents add NAME [--raw] [--scope robert] [--max-k N] [--rate N] [--entry-point tool]
    moi ask-agents rotate NAME                                  a new token; the old one stops at once
    moi ask-agents revoke NAME                                  the token stops at once; the entry stays

``add`` and ``rotate`` print the new token **once, at your terminal**, and store only its hash; hand it to the agent runtime as a
secret (not into a prompt, a file the model can read, or this chat). All four refuse under a pipe, a script or an agent's shell
(``not_interactive``), the same rule as ``moi approve``: an agent cannot grant itself or another agent access. The raw tier
(``--raw``) is off unless you say so; scopes default to ``robert``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

from core.memory_ask import identity

OK, PROBLEM, USAGE, REFUSED = 0, 1, 2, 3


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} Type 'yes' to confirm: ").strip().lower() == "yes"


def main(argv: Sequence[str] | None = None, *, out=None, is_tty: Callable[[], bool] = _tty,
         confirm: Callable[[str], bool] = _confirm) -> int:
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="moi ask-agents")
    p.add_argument("--shelf-root")
    p.add_argument("--registry")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--raw", action="store_true", help="also allow the raw tier (default: curated only)")
    add.add_argument("--scope", action="append")
    add.add_argument("--max-k", type=int, default=identity.DEFAULT_MAX_K)
    add.add_argument("--rate", type=int, default=identity.DEFAULT_RATE_PER_MIN)
    add.add_argument("--entry-point", default="tool")
    for name in ("rotate", "revoke"):
        sub.add_parser(name).add_argument("name")
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return OK if exc.code == 0 else USAGE
    if args.registry:
        path = Path(args.registry).expanduser()
    else:
        from core.memory_shelf import config
        path = identity.registry_path(config.load().shelf_root)
    if not is_tty():
        print("refused: not_interactive (agent access is granted only at your own terminal)", file=out)
        return REFUSED
    try:
        if args.cmd == "list":
            print(json.dumps(identity.public_view(path), indent=1, sort_keys=True), file=out)
            return OK
        what = {"add": f"grant Ask access to agent '{args.name}'" + (" including the RAW record" if getattr(args, "raw", False) else ""),
                "rotate": f"issue a new Ask token for '{args.name}' (the old one stops)",
                "revoke": f"revoke Ask access for '{args.name}'"}[args.cmd]
        if not confirm(f"Owner action: {what}."):
            print("refused: declined", file=out)
            return REFUSED
        if args.cmd == "add":
            token = identity.add_agent(path, args.name, tiers=("curated", "all") if args.raw else ("curated",),
                                       scopes=tuple(args.scope or ("robert",)), entry_point=args.entry_point, max_k=args.max_k,
                                       rate_per_min=args.rate)
            print(f"added {args.name}\ntoken (shown once; give it to the agent runtime as a secret):\n{token}", file=out)
        elif args.cmd == "rotate":
            token = identity.rotate_agent(path, args.name)
            print(f"rotated {args.name}\ntoken (shown once):\n{token}", file=out)
        else:
            identity.revoke_agent(path, args.name)
            print(f"revoked {args.name}", file=out)
    except identity.RegistryError as exc:
        if exc.code == "registry_durability_unconfirmed":
            advice = ("The revoke may not survive a crash. Re-run revoke after storage recovers (safe to repeat); "
                      "do not assume access is durably revoked until it succeeds." if args.cmd == "revoke" else
                      "The grant or token may have changed. Check the registry after storage recovers; "
                      "rotate a token if add/rotate did not return it.")
            print("uncertain: registry_durability_unconfirmed (the registry was published but its directory sync failed; "
                  + advice + ")", file=out)
            return PROBLEM
        print(f"refused: {exc.code}", file=out)
        return PROBLEM
    return OK


if __name__ == "__main__":
    sys.exit(main())
