#!/usr/bin/env python3
"""Agent-neutral command line for the topic workshop's inbox (WORKSHOP_BUILD_SPEC.md section 4). Standard library only.

  topic_inbox.py put    --topic t-0123456789ab --agent codex --kind finding --text "..." [--ref file:path] [--model gpt-5.5] [--session ...]
  topic_inbox.py waiting --topic t-0123456789ab
  topic_inbox.py import  --topic t-0123456789ab          (the owner's import, bounded; run it yourself or press Import in the Workshop)
  topic_inbox.py verify                                    (a read-only consistency report; changes nothing)

`put` writes ONE small JSON file into the topic's inbox folder and nothing else. It is a contribution from an UNTRUSTED local tool:
the agent name is a claim, nothing is verified, an agent's "decision" is stored as a proposal, and a contribution can only add to the
topic's record (it cannot change a card, an order, a stage, a disposition or an approval). Data folder: --data, else
$MINIMOI_GUILD_DATA, else ~/minimoi-staging/data/guild.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys


def _load_store():
    """The store module, loaded from the repository without importing the portal (and so without Flask): a stand-in package whose
    folder is guild_ui lets topics.py find its sibling payment_scrub.py, which is also standard library only."""
    import types
    here = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.normpath(os.path.join(here, "..", "..", "minimoi_portal", "guild_ui"))
    pkg = types.ModuleType("minimoi_topics_pkg")
    pkg.__path__ = [folder]
    sys.modules["minimoi_topics_pkg"] = pkg
    return importlib.import_module("minimoi_topics_pkg.topics")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.environ.get("MINIMOI_GUILD_DATA") or os.path.expanduser("~/minimoi-staging/data/guild"))
    ap.add_argument("--owner", default=os.environ.get("MINIMOI_OWNER", "robert"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("put"); p.add_argument("--topic", required=True); p.add_argument("--agent", required=True); p.add_argument("--kind", required=True)
    p.add_argument("--text", required=True); p.add_argument("--ref", action="append", default=[], help="type:ref, e.g. file:path/to/it or commit:abc123")
    p.add_argument("--model", default=""); p.add_argument("--session", default="")
    for name in ("waiting", "import"):
        q = sub.add_parser(name); q.add_argument("--topic", required=True)
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    T = _load_store()
    store = T.TopicStore(a.data)
    try:
        if a.cmd == "put":
            refs = []
            for r in a.ref:
                t, _, v = r.partition(":")
                refs.append({"type": t, "ref": v})
            name = store.put_inbox(a.topic, a.agent, kind=a.kind, text=a.text, refs=refs, model=a.model, session_ref=a.session)
            print(f"left in the inbox of {a.topic} as {a.agent}: {name}\n(waiting for the owner to import it; nothing else was changed)")
        elif a.cmd == "waiting":
            print(json.dumps(store.inbox_waiting(a.topic, a.owner), indent=2))
        elif a.cmd == "import":
            print(json.dumps(store.import_inbox(a.topic, a.owner), indent=2))
        else:
            r = store.verify(a.owner)
            print(json.dumps(r, indent=2))
            return 0 if r["ok"] else 1
    except (T.Refused, T.TopicNotFound, T.TopicStoreUnavailable, OSError) as exc:
        print(f"not done: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
