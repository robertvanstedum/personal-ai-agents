"""The fresh-versus-long context cost probe for Master Craftsman (slice 2).

Operator only: it runs INSIDE the staging portal container, reached with
`docker exec` on the Mac (scripts/staging/mc_cost_probe.sh). It never uses a
browser session or Robert's login. It keeps real owner notes (marked
"[cost probe]") and sends them through the same server-side path as the
/mc/turns route (api.run_mc_turn: one reply per note, one turn in flight, the
scrub, only answered turns kept), after the same checks that matter: MC's turns
must be on and its backend must be the real one.

The turns (each a paid model call):
  1. the Shop floor thread: a question that builds context;
  2. the thread: a follow-up (the long-context turn);
  3. a new conversation: the same follow-up (the fresh turn);
  4. (with --turns 4) the thread: one more follow-up (the context growing).

Per turn it prints the conversation, input and output tokens and the cost,
read from the environment's usage store (the gateway's records for actor mc,
inside the turn's window). It refuses without --yes-spend, and stops before
the next turn once the spend passes the cap ($1 by default, never more).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

PROMPTS = [
    ("thread", "Summarise the in-build Build Queue items and what blocks each, in 5 bullets."),
    ("thread", "Which one should I do first, and why? One paragraph."),
    ("new", "Which one should I do first, and why? One paragraph."),
    ("thread", "And the second one?"),
]
HARD_CAP = 1.0
MARK = "[cost probe] "


def _parse(value):
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def usage_in_window(folder: str | None, start: datetime, end: datetime) -> list[dict]:
    """MC's gateway usage records between start (minus 1 s) and end (plus 5 s)."""
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(n for n in os.listdir(folder) if n.startswith("usage-") and n.endswith(".jsonl"))[-2:]:
        try:
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    at = _parse(rec.get("occurred_at")) if isinstance(rec, dict) else None
                    if (at and rec.get("actor") == "mc" and rec.get("emitter") == "gateway"
                            and start - timedelta(seconds=1) <= at <= end + timedelta(seconds=5)):
                        out.append(rec)
        except OSError:
            continue
    return out


def _sum(recs, key):
    vals = [r.get(key) for r in recs if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
    return sum(vals) if vals else None


def run(services, conversations, *, principal: str, label: str, turns: int = 3, cap: float = HARD_CAP,
        usage_dir: str | None = None, wait_s: float = 30.0, out=print, sleep=time.sleep) -> dict:
    from ..api import run_mc_turn
    from ..conversations import LEGACY_ID
    from ..stores import Author

    cap = min(float(cap), HARD_CAP)
    run_id = secrets.token_hex(4)
    author = Author(principal, "owner", label)
    thread = conversations.get(LEGACY_ID, principal)
    fresh, _ = conversations.create(principal, key=f"costprobe-{run_id}")
    conversations.rename(fresh["id"], principal, f"Cost probe {run_id} (fresh)")
    rows, spent = [], 0.0
    counted: set[str] = set()      # usage records already counted for an earlier turn
    for i, (where, prompt) in enumerate(PROMPTS[:turns], start=1):
        if spent > cap:
            out(f"STOP: spent ${spent:.4f}, over the ${cap:.2f} cap; turn {i} not sent.")
            break
        conv = thread if where == "thread" else fresh
        floor = services.floor if conv.get("notes_floor") in (None, services.floor.floor) else \
            services.floor.for_floor(conv["notes_floor"])
        note_id = f"costprobe{run_id}t{i}"
        kept = floor.add_note(note_id, MARK + prompt, author, area="Build", page="cost-probe")
        if kept.outcome != "kept":
            out(f"turn {i}: the note was not kept ({kept.outcome}); stopping.")
            break
        start = datetime.now(timezone.utc)
        answer, status = run_mc_turn(services, conversations=conversations, floor=floor, conv=conv,
                                     principal=principal, note=kept.value, note_id=note_id)
        end = datetime.now(timezone.utc)
        recs, waited = [], 0.0
        while answer.get("status") == "answered" and not recs and waited < wait_s:
            recs = [r for r in usage_in_window(usage_dir, start, end) if r.get("record_id") not in counted]
            if not recs:
                sleep(1.0)
                waited += 1.0
        counted.update(r.get("record_id") for r in recs)
        cost = _sum(recs, "cost_usd") or 0.0
        spent += cost
        row = {"turn": i, "conversation": conv["id"], "where": where, "status": answer.get("status", status),
               "calls": len(recs), "input_tokens": _sum(recs, "input_tokens"),
               "output_tokens": _sum(recs, "output_tokens"), "cost_usd": round(cost, 6)}
        rows.append(row)
        out(f"turn {i} · {where:6} · {conv['id']:18} · {row['status']:10} · calls {row['calls']} · "
            f"in {row['input_tokens']} · out {row['output_tokens']} · ${row['cost_usd']:.6f}")
        if answer.get("status") != "answered":
            out(f"turn {i} did not answer ({answer.get('failure_class') or answer.get('error')}); stopping.")
            break
    by_turn = {r["turn"]: r for r in rows}
    if 2 in by_turn and 3 in by_turn and by_turn[2]["input_tokens"] is not None and by_turn[3]["input_tokens"] is not None:
        out(f"long (turn 2) minus fresh (turn 3) input tokens: {by_turn[2]['input_tokens'] - by_turn[3]['input_tokens']}")
    out(f"total spent: ${spent:.6f} (cap ${cap:.2f}) · fresh conversation {fresh['id']}")
    return {"rows": rows, "spent": spent, "fresh": fresh["id"], "run_id": run_id}


def main(argv=None, *, services=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes-spend", action="store_true", help="really send the turns (each is a paid model call)")
    ap.add_argument("--principal", default="robert", help="the owner's username (conversations are per owner)")
    ap.add_argument("--label", default="Robert")
    ap.add_argument("--turns", type=int, choices=(3, 4), default=3)
    ap.add_argument("--cap", type=float, default=HARD_CAP, help=f"stop once spend passes this (at most ${HARD_CAP:.2f})")
    a = ap.parse_args(argv)
    if not a.yes_spend:
        print(f"Refused: this sends {a.turns} paid Master Craftsman turns. Add --yes-spend to run it "
              f"(stops past ${min(a.cap, HARD_CAP):.2f}).")
        return 2
    if services is None:
        print("Refused: run it through scripts/staging/mc_cost_probe.sh (python -m minimoi_portal.mc_cost_probe).")
        return 4
    if not getattr(services, "mc_turns", False) or getattr(services.mc, "kind", None) != "openclaw":
        print("Refused: Master Craftsman's turns are off, or its backend is not the real one; nothing was sent.")
        return 3
    from ..conversations import conversations_of
    run(services, conversations_of(services), principal=a.principal, label=a.label, turns=a.turns, cap=a.cap,
        usage_dir=os.environ.get("MINIMOI_USAGE_DIR"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
