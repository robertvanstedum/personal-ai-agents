#!/usr/bin/env python3
"""Offline evaluation of the voice understanding call (confirm by voice, v3; core/realtime_voice/intent_gateway.py) against
the real gateway alias minimoi-voice-intent, over the table tests/realtime_voice/data/intent_cases.json (its "model"
cases: the ones the grammar fast path does not hear). Never part of any test suite; the build thread runs it in the live
check, under the live-testing cap.

It reads the host's capped key from the environment only (MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC by default; never the
Keychain, never a file) and the gateway's address from MINIMOI_MODEL_GATEWAY_URL (default the containers' own,
http://model-gateway:4000/v1). It prints each case's expected and heard word and its time, then the agreement per
language; the words said are the table's synthetic ones, never Robert's. One call per case, a few tokens each.

    MINIMOI_MODEL_GATEWAY_URL=http://localhost:4000/v1 MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC=… \\
        venv/bin/python scripts/dev/eval_voice_intent.py [--host mc|cos] [--all]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.realtime_voice.intent_gateway import GatewayIntent  # noqa: E402
from core.realtime_voice.summary_gateway import key_env  # noqa: E402

TABLE = ROOT / "tests" / "realtime_voice" / "data" / "intent_cases.json"
PEOPLE = {"mc": {"name": "Master Craftsman", "short": "MC"}, "cos": {"name": "Chief of Staff", "short": "CoS"}}
OWNER = "Robert"                                # both hosts' owner_name (review v3 N5: the call names the host's owner)
BOARD = {"mc": True, "cos": False}              # MC has the Board; CoS has none, so she is offered no postit (M1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", choices=sorted(PEOPLE), default="mc", help="whose key and person (default mc)")
    parser.add_argument("--all", action="store_true", help="also run the cases the grammar hears (as a model check)")
    args = parser.parse_args(argv)
    key_name = f"voice-summary-{args.host}"
    if not os.environ.get(key_env(key_name)):
        print(f"{key_env(key_name)} is not set: nothing to run (the call would count every case as chat).", file=sys.stderr)
        return 2
    table = json.loads(TABLE.read_text())
    person = PEOPLE[args.host]
    client = GatewayIntent(key_name, person, owner=OWNER, board=BOARD[args.host])
    cases = [c for c in table["cases"] if args.all or c["path"] == "model"]
    if args.host != "mc":                       # the table's person is MC: cases that name a person are MC's own
        cases = [c for c in cases if not any(name in c["text"] for name in ("MC", "CoS"))]
    tally, misses = Counter(), []
    for case in cases:
        expected = case["label"] if BOARD[args.host] or case["label"] != "postit" else "consult"   # the server's map
        began = time.monotonic()
        heard = client(case["text"], case["waiting"])
        took = time.monotonic() - began
        ok = heard == expected or (expected == "end" and heard == "chat")
        tally[(case["lang"], ok)] += 1
        if not ok:
            misses.append(case)
        print(f"{'ok ' if ok else 'MISS'} {took:4.2f}s {case['lang']} waiting={case['waiting'] or '-':7} "
              f"expected={expected:11} heard={heard:11} {case['text']}")
    print()
    for lang in sorted({c["lang"] for c in cases}):
        good, bad = tally[(lang, True)], tally[(lang, False)]
        print(f"{lang}: {good} of {good + bad} agree")
    print(f"all: {len(cases) - len(misses)} of {len(cases)} agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
