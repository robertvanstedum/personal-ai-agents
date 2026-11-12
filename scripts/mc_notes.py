#!/usr/bin/env python3
"""Leave MC a short note, or read the register (core/mc_notes/register.py).

  scripts/mc_notes.py add --by claude-code --topic "rooms archive" --text "Robert: the Mac job is retired; prod needs it (#523)."
  scripts/mc_notes.py add --by codex --for remind --replaces n-20261112T150000Z-1a2b3c --text "Robert changed his mind: ..."
  scripts/mc_notes.py list                  current notes (not replaced), oldest first
  scripts/mc_notes.py list --all --since 2026-11-12
  scripts/mc_notes.py show n-20261112T150000Z-1a2b3c
  scripts/mc_notes.py verify                check every month's chain (exit 1 if a line was edited or removed)

--by      who writes the note: claude-code | codex | grok | mc | robert
--said-by who said it (default robert): the note records Robert's words or decision as the writer heard them
--for     remember (default) | remind (MC should remind people) | act (put it on MC's to-do list)
--root    the register folder (else $MINIMOI_MC_NOTES_DIR, else ~/minimoi-staging/data/guild/mc-notes)
--host    the writing host (else $MINIMOI_MC_NOTES_HOST, else mac)
--json    print JSON instead of text
Exit codes: 0 ok, 1 verify found a problem, 2 refused (reason on stderr).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.mc_notes import register as R  # noqa: E402


def _line(n: dict) -> str:
    head = f"{n['id']}  {n['at']}  {n['by']}"
    if n.get("said_by") and n["said_by"] != n["by"]:
        head += f" (said by {n['said_by']})"
    head += f"  [{n['for']}]"
    if n.get("topic"):
        head += f"  {n['topic']}"
    tail = f"\n    replaces: {', '.join(n['replaces'])}" if n.get("replaces") else ""
    ref = f"\n    ref: {n['ref']}" if n.get("ref") else ""
    body = "\n".join("    " + line for line in n["text"].splitlines())
    return f"{head}\n{body}{tail}{ref}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root")
    p.add_argument("--json", action="store_true")
    p.add_argument("--host")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--by", required=True, choices=R.WRITERS)
    a.add_argument("--said-by", default="robert", choices=R.SPEAKERS)
    a.add_argument("--for", dest="what_for", default="remember", choices=R.FOR)
    a.add_argument("--topic", default="")
    a.add_argument("--text", required=True, help="the note; use - to read it from stdin")
    a.add_argument("--replaces", action="append", default=[])
    a.add_argument("--ref", default="", help="optional pointer: an issue, PR, commit or file")
    ls = sub.add_parser("list")
    ls.add_argument("--all", action="store_true", help="include replaced notes")
    ls.add_argument("--since", default="", help="YYYY-MM-DD (UTC)")
    sh = sub.add_parser("show")
    sh.add_argument("note_id")
    sub.add_parser("verify")
    args = p.parse_args(argv)

    try:
        if args.cmd == "add":
            text = sys.stdin.read() if args.text == "-" else args.text
            note = R.add(args.root, by=args.by, said_by=args.said_by, what_for=args.what_for, topic=args.topic, text=text,
                         replaces=args.replaces, ref=args.ref, host=args.host)
            print(json.dumps(note, ensure_ascii=False) if args.json else note["id"])
            return 0
        if args.cmd == "verify":
            rep = R.verify(args.root)
            if args.json:
                print(json.dumps(rep, indent=1))
            else:
                for name, f in rep["files"].items():
                    state = "sealed" if f["sealed"] else "open"
                    print(f"{name}: {f['lines']} notes, {state}" + ("" if not f["problems"] else "; " + "; ".join(f["problems"])))
                print("ok" if rep["ok"] else "PROBLEMS FOUND")
            return 0 if rep["ok"] else 1
        if args.cmd == "show":
            note = R.get(args.root, args.note_id)
            if note is None:
                raise R.Refused("not_found", "No such note.")
            print(json.dumps(note, ensure_ascii=False, indent=1) if args.json else _line(note))
            return 0
        notes = R.all_notes(args.root)
        if not args.all:
            notes = R.current(notes)
        if args.since:
            notes = [n for n in notes if n["at"][:10] >= args.since]
        if args.json:
            print(json.dumps(notes, ensure_ascii=False, indent=1))
        else:
            print("\n\n".join(_line(n) for n in notes) if notes else "(no notes)")
        return 0
    except R.Refused as e:
        print(f"refused: {e.code}: {e.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
