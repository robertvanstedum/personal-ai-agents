"""The streaming mode of the operator-only cost probe (streaming S1): the two
real turns that prove streaming, usage and Stop on staging, without a browser
or anyone's login.

    scripts/staging/mc_cost_probe.sh --stream --yes-spend [--stop-after-first-text]

It runs INSIDE the staging portal container (docker exec), through the same
server path as POST /mc/turns/stream: ``api.open_mc_stream`` (one reply per
note, the dispatched-set, one turn in flight, the scrub, the worker, the
turn log, the runtime-stream usage record), and reads the same NDJSON the
browser reads. Each turn keeps a real owner note marked "[cost probe]" in a
new conversation of its own.

* **Turn A** (streaming and usage): the time to the first delta and to the
  finish; the event counts (ack, delta, render as the browser sees them;
  finish and usage from the backend); the turn's ``mc_turns.jsonl`` line; its
  ``runtime-stream`` usage line; the gateway's line(s) in the turn's window,
  with the output tokens compared.
* **Turn B** (Stop): the owner's Stop after the first text arrives, as the
  Stop route does it. Its stopped status, its ``runtime-stream`` error line,
  and whether the gateway wrote a record for the aborted call, and with what
  tokens.

``--stream`` runs Turn A, then Turn B only when Turn A's cost was read and
is under the cap; ``--stream --stop-after-first-text`` runs Turn B alone. The
spend rules are the cost probe's: never above $1, and a turn whose cost
cannot be read stops the probe (unknown, never $0).
"""
from __future__ import annotations

import json
import os
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

PROMPT_A = "In 5 short bullets, what are the in-build Build Queue items and what blocks each?"
PROMPT_B = "Write a detailed 600-word explanation of how Build Queue items move from spec to done."
MARK = "[cost probe] "


def _parse(value):
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _records(folder: str | None, keep) -> list[dict]:
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
                    if isinstance(rec, dict) and keep(rec):
                        out.append(rec)
        except OSError:
            continue
    return out


def _turn_line(services, turn_id: str) -> dict | None:
    from .turn_log import turn_log_of
    path = turn_log_of(services).path
    try:
        with open(path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if turn_id in line]
    except (OSError, TypeError, ValueError):
        return None
    return lines[-1] if lines else None


def _count(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def one_turn(services, conversations, *, principal: str, label: str, stop_after_first_text: bool,
             usage_dir: str | None, wait_s: float = 30.0, out=print, sleep=time.sleep, clock=time.monotonic,
             counted: set | None = None) -> dict:
    """One streamed probe turn; returns its figures (and prints them). Gateway
    records already counted for an earlier turn (``counted``) are skipped."""
    counted = counted if counted is not None else set()
    from ..api import open_mc_stream
    from ..stores import Author

    which = "B" if stop_after_first_text else "A"
    run_id = secrets.token_hex(4)
    conv, _ = conversations.create(principal, key=f"streamprobe-{run_id}-{which.lower()}")
    conversations.rename(conv["id"], principal, f"Stream probe {run_id} (turn {which})")
    floor = services.floor if conv.get("notes_floor") in (None, services.floor.floor) else \
        services.floor.for_floor(conv["notes_floor"])
    note_id = f"streamprobe{run_id}{which.lower()}"
    kept = floor.add_note(note_id, MARK + (PROMPT_B if stop_after_first_text else PROMPT_A),
                          Author(principal, "owner", label), area="Build", page="cost-probe")
    if kept.outcome != "kept":
        out(f"turn {which}: the note was not kept ({kept.outcome}); nothing was sent.")
        return {"turn": which, "status": "not_sent"}
    backend_events: Counter = Counter()
    browser_events: Counter = Counter()
    t: dict = {}
    t0 = clock()
    ms = lambda: int((clock() - t0) * 1000)          # noqa: E731

    def observe(events):
        for event in events:
            name = type(event).__name__.lower()
            backend_events[name] += 1
            if name == "finish":
                t.setdefault("finish_ms", ms())
            yield event

    started = datetime.now(timezone.utc)
    opened = open_mc_stream(services, conversations=conversations, floor=floor, conv=conv, principal=principal,
                            note=kept.value, note_id=note_id, request_id=f"streamprobe-{run_id}-{which.lower()}",
                            observe=observe)
    if opened[0] == "refused":
        _, body, status = opened
        out(f"turn {which}: refused before sending ({status} {body.get('error')}): {body.get('message')}")
        return {"turn": which, "status": "refused", "error": body.get("error")}
    _, run, events = opened
    end = {}
    stopped = None
    for line in events:
        event = json.loads(line)
        browser_events[event["t"]] += 1
        if event["t"] == "delta" and "first_delta_ms" not in t:
            t["first_delta_ms"] = ms()
            if stop_after_first_text:
                stopped = run.stop()                  # exactly what POST /mc/turns/<id>/stop does
        if event["t"] in ("done", "error"):
            end = event
    run.join(150)
    t["end_ms"] = ms()
    ended = datetime.now(timezone.utc)
    turn_id = run.ctx.turn_id
    from .stream_usage import flush
    flush()
    line = _turn_line(services, turn_id)
    from .stream_usage import portal_folder
    own = portal_folder() or os.path.join(usage_dir or "", "portal")
    stream_rec = next(iter(_records(own, lambda r: r.get("emitter") == "runtime-stream"
                                    and r.get("correlation_id") == turn_id)), None)

    def gateway():
        return _records(usage_dir, lambda r: r.get("emitter") == "gateway" and r.get("actor") == "mc"
                        and r.get("record_id") not in counted
                        and (lambda at: at is not None and started - timedelta(seconds=1) <= at
                             <= ended + timedelta(seconds=5))(_parse(r.get("occurred_at"))))
    gw, waited = gateway(), 0.0
    while not gw and waited < wait_s:
        sleep(1.0)
        waited += 1.0
        gw = gateway()
    counted.update(r.get("record_id") for r in gw)
    gw_out = [_count(r.get("output_tokens")) for r in gw]
    gw_cost = [_count(r.get("cost_usd")) for r in gw]
    gw_out_sum = sum(gw_out) if gw and all(v is not None for v in gw_out) else None
    cost = sum(gw_cost) if gw and all(v is not None for v in gw_cost) else None
    stream_out = _count((stream_rec or {}).get("output_tokens"))

    out(f"── turn {which} ({'Stop after the first text' if stop_after_first_text else 'streaming and usage'}) · "
        f"turn {turn_id} · conversation {conv['id']}")
    out(f"  end: {end.get('t')} · status {end.get('status')} · class {end.get('failure_class')} · "
        f"kept reply: {bool(end.get('reply_note'))}" + (f" · Stop reached the relay: {stopped}" if stopped is not None else ""))
    out(f"  timing: first delta {t.get('first_delta_ms')} ms · finish {t.get('finish_ms')} ms · end {t['end_ms']} ms")
    out(f"  events (browser): ack {browser_events['ack']} · delta {browser_events['delta']} · "
        f"render {browser_events['render']} · ping {browser_events['ping']} · done {browser_events['done']} · "
        f"error {browser_events['error']}")
    out(f"  events (backend): delta {backend_events['delta']} · finish {backend_events['finish']} · "
        f"usage {backend_events['usage']} · failure {backend_events['failure']}")
    out("  mc_turns.jsonl: " + (json.dumps({k: line.get(k) for k in ("mode", "status", "failure_class", "duration_ms")})
                                if line else "no line found"))
    out("  runtime-stream: " + (json.dumps({k: stream_rec.get(k) for k in ("status", "error_class", "cost_source",
                                                                            "input_tokens", "output_tokens", "latency_ms")})
                                if stream_rec else "no record found"))
    if gw:
        out(f"  gateway: {len(gw)} record(s) in the turn's window · output tokens {gw_out} · cost {gw_cost} "
            f"· statuses {[r.get('status') for r in gw]}")
    else:
        out(f"  gateway: no record in the turn's window within {wait_s:.0f} s"
            + (" (the aborted call was not recorded, or not yet)" if stop_after_first_text else ""))
    if gw_out_sum is not None and stream_out is not None:
        diff = abs(gw_out_sum - stream_out) / max(gw_out_sum, 1) * 100
        out(f"  output tokens: stream {stream_out} vs gateway {gw_out_sum} ({diff:.0f}% apart)")
    out(f"  cost: " + (f"${cost:.6f}" if cost is not None else "unknown (no gateway record with a cost)"))
    return {"turn": which, "turn_id": turn_id, "status": end.get("status"), "failure_class": end.get("failure_class"),
            "timing": t, "browser_events": dict(browser_events), "backend_events": dict(backend_events),
            "turn_line": line, "runtime_stream": stream_rec, "gateway": gw, "cost_usd": cost,
            "stream_output_tokens": stream_out, "gateway_output_tokens": gw_out_sum, "stopped": stopped}


def run(services, conversations, *, principal: str, label: str, stop_only: bool, cap: float, usage_dir: str | None,
        wait_s: float = 30.0, out=print, sleep=time.sleep) -> dict:
    """Turn A, then Turn B (or Turn B alone), within the cap (see the module doc)."""
    from .cost_probe import HARD_CAP
    cap = min(float(cap), HARD_CAP)
    if not usage_dir or not os.path.isdir(usage_dir):
        out("Refused: no usage store to read (MINIMOI_USAGE_DIR); each turn's cost would be unknown. Nothing was sent.")
        return {"turns": [], "spent": 0.0, "stopped": "no_usage_store"}
    turns, spent, counted = [], 0.0, set()
    plan = [True] if stop_only else [False, True]
    for stop in plan:
        if spent > cap:
            out(f"STOP: spent ${spent:.4f}, over the ${cap:.2f} cap; turn {'B' if stop else 'A'} not sent.")
            return {"turns": turns, "spent": spent, "stopped": "cap"}
        result = one_turn(services, conversations, principal=principal, label=label, stop_after_first_text=stop,
                          usage_dir=usage_dir, wait_s=wait_s, out=out, sleep=sleep, counted=counted)
        turns.append(result)
        if result.get("status") in ("refused", "not_sent"):
            return {"turns": turns, "spent": spent, "stopped": "refused"}
        if result.get("cost_usd") is None:
            if stop is False:
                out(f"STOP: turn A's cost could not be read; it is unknown, so turn B is not sent. "
                    f"Spent at least ${spent:.4f}.")
            else:
                out(f"total spent: at least ${spent:.6f} (turn B's cost is unknown) · cap ${cap:.2f}")
            return {"turns": turns, "spent": spent, "stopped": "usage_unknown"}
        spent += result["cost_usd"]
    out(f"total spent: ${spent:.6f} (cap ${cap:.2f})")
    return {"turns": turns, "spent": spent, "stopped": None}


__all__ = ["run", "one_turn", "PROMPT_A", "PROMPT_B"]
