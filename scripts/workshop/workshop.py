#!/usr/bin/env python3
"""workshop.py — the local Workshop's file-first record (4a: observe, record, sync; never launches).

Runs on the workshop host (the Mac), from the repository, with no model call:

  workshop.py event --actor claude-code --item pr:265 --kind needs_you --stage review --text "..." [--next-actor robert] [--ref pr=265]
  workshop.py observe [--force]      one host observation; a health event only when something changed
                                     (or every 9 minutes as a heartbeat, under the page's 15-minute
                                     stale limit). Run it with sync every 5 minutes.
  workshop.py state                  print the derived state (in progress, next, needs you, host)
  workshop.py sync [--to DIR]        copy events.jsonl and state.json to the staging data folder
                                     (default $STAGING_ROOT/data/workshops, else ~/minimoi-staging/...); code and secrets never go

Files: $MINIMOI_WORKSHOP_HOME (default ~/minimoi-workshops)/<id>/events.jsonl and state.json.
The workshop id is $MINIMOI_WORKSHOP_ID (default "mac").
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.workshop_journal import strictjson  # noqa: E402
from core.workshop_journal.journal import Journal, Result  # noqa: E402
from minimoi_portal.workshop.observer import changed, health_event, observe  # noqa: E402
from minimoi_portal.workshop.record import Workshop, now, parse  # noqa: E402

HOME = os.environ.get("MINIMOI_WORKSHOP_HOME") or os.path.expanduser("~/minimoi-workshops")
WORKSHOP_ID = os.environ.get("MINIMOI_WORKSHOP_ID") or "mac"
HEARTBEAT = timedelta(minutes=9)     # under the page's 15-minute stale limit, even with a 5-minute run landing late
SYNC_TO = os.path.join(os.path.expanduser(os.environ.get("STAGING_ROOT") or "~/minimoi-staging"), "data", "workshops")


def _last_health(ws: Workshop) -> dict | None:
    health = [e for e in ws.events() if e.get("kind") == "health"]
    return health[-1] if health else None


def cmd_event(ws: Workshop, a) -> int:
    refs = dict(r.split("=", 1) for r in (a.ref or []))
    ev = ws.append({"actor": a.actor, "kind": a.kind, "item": a.item, "stage": a.stage, "text": a.text,
                    "next_actor": a.next_actor, "room": a.room, "refs": refs or None})
    print(f"recorded {ev['kind']} on {ev['item']} ({ev['event_id'][:8]})")
    return 0


def cmd_observe(ws: Workshop, a) -> int:
    obs = observe()
    ev = health_event(obs, ws.id, hostname=WORKSHOP_ID)
    last = _last_health(ws)
    due = last is None or (parse(last.get("at")) or now()) + HEARTBEAT <= now()
    if a.force or due or changed((last or {}).get("health"), ev):
        ws.append(ev)
        print(ev["text"])
    else:
        print(f"unchanged: {ev['text']}")
    return 0


def cmd_state(ws: Workshop, a) -> int:
    print(json.dumps(ws.write_state(), indent=1))
    return 0


def _atomic_copy(src: Path, dest: Path):
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".sync-")
    with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
        out.write(f.read())
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)


def cmd_sync(ws: Workshop, a) -> int:
    """Only events.jsonl and state.json, one way. The events are appended by
    byte offset when the copy is a prefix (a partial copy never tears a line);
    otherwise replaced atomically."""
    dest_dir = Path(a.to) / ws.id
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    src_ev, dst_ev = Path(ws.events_path), dest_dir / "events.jsonl"
    if src_ev.exists():
        data = src_ev.read_bytes()
        old = dst_ev.read_bytes() if dst_ev.exists() else b""
        if old and data.startswith(old):
            new = data[len(old):]
            new = new[: new.rfind(b"\n") + 1]                  # whole lines only
            if new:
                with open(dst_ev, "ab") as f:
                    f.write(new)
        else:
            _atomic_copy(src_ev, dst_ev)
    ws.write_state()
    _atomic_copy(Path(ws.state_path), dest_dir / "state.json")
    print(f"synced {ws.id} to {dest_dir}")
    return 0


ENVELOPE_MAX = 64 * 1024


def _emit(result: Result) -> int:
    """One JSON object on stdout, nothing else; the exit code carries the class of outcome (v0.6 section 13)."""
    print(json.dumps(result.to_json(), sort_keys=True))
    return result.exit_code


def _read_envelope(path: str) -> dict | Result:
    try:
        raw = sys.stdin.buffer.read(ENVELOPE_MAX + 1) if path == "-" else Path(path).read_bytes()
    except OSError:
        return Result(False, "invalid_input", None, None, False, False, "file_unreadable", {}, 2)
    if len(raw) > ENVELOPE_MAX:
        return Result(False, "invalid_input", None, None, False, False, "envelope_too_large", {}, 2)
    try:
        doc = strictjson.loads(raw)
    except strictjson.StrictJSONError as exc:
        return Result(False, "invalid_input", None, None, False, False, exc.reason, {}, 2)
    if not isinstance(doc, dict):
        return Result(False, "invalid_input", None, None, False, False, "not_an_object", {}, 2)
    return doc


def cmd_append(journal: Journal, a) -> int:
    envelope = _read_envelope(a.file)
    return _emit(envelope) if isinstance(envelope, Result) else _emit(
        journal.append(envelope, adapter="cli", session_ref=a.session_ref))


def cmd_prepare(journal: Journal, a) -> int:
    envelope = _read_envelope(a.file)
    return _emit(envelope) if isinstance(envelope, Result) else _emit(journal.prepare(envelope))


def cmd_get(journal: Journal, a) -> int:
    return _emit(journal.get(a.event_id))


EXIT_BY_STATUS = {"ok": 0, "tail_in_progress": 0, "torn_tail": 5, "corrupt": 5, "missing": 8, "unsupported_writer": 8,
                  "unsafe_root": 2}


def cmd_verify(journal: Journal, a) -> int:
    report = journal.verify()
    print(json.dumps(report, sort_keys=True))
    return EXIT_BY_STATUS.get(report["status"], 5) if report["status"] != "ok" or report["ok"] else 5


def cmd_repair(journal: Journal, a) -> int:
    report = journal.repair(apply=a.apply)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("nothing_to_repair", "would_repair", "repaired") else 5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=HOME)
    ap.add_argument("--workshop", "--id", dest="workshop", default=WORKSHOP_ID,
                    help="the workshop ID (v0.6 writes it --id; --workshop is the older spelling)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("event")
    e.add_argument("--actor", required=True)
    e.add_argument("--kind", required=True)
    e.add_argument("--item", required=True)
    e.add_argument("--text", required=True)
    e.add_argument("--stage")
    e.add_argument("--next-actor")
    e.add_argument("--room")
    e.add_argument("--ref", action="append")
    o = sub.add_parser("observe")
    o.add_argument("--force", action="store_true")
    sub.add_parser("state")
    ap_ = sub.add_parser("append", help="append a v2 envelope (JSON file or - for stdin); one JSON object out")
    ap_.add_argument("--file", required=True)
    ap_.add_argument("--session-ref")
    pr = sub.add_parser("prepare", help="fix the event ID and time now, without touching the journal; prints the envelope to resubmit")
    pr.add_argument("--file", required=True)
    g = sub.add_parser("get", help="one event by ID")
    g.add_argument("--event-id", required=True)
    g.add_argument("--json", action="store_true")
    v = sub.add_parser("verify", help="read-only consistency report")
    v.add_argument("--json", action="store_true")
    rp = sub.add_parser("repair", help="dry run unless --apply: preserve, then complete or cut an unterminated final record")
    rp.add_argument("--apply", action="store_true")
    s = sub.add_parser("sync")
    s.add_argument("--to", default=SYNC_TO)
    a = ap.parse_args(argv)
    if a.cmd in ("append", "prepare", "get", "verify", "repair"):
        try:
            journal = Journal(a.home, a.workshop)
        except Exception as exc:                                   # a refused workshop ID or root; fixed text only
            print(json.dumps({"ok": False, "status": getattr(exc, "status", "invalid_input"), "reason": getattr(exc, "reason", "bad_arguments")}))
            return 2
        return {"append": cmd_append, "prepare": cmd_prepare, "get": cmd_get, "verify": cmd_verify, "repair": cmd_repair}[a.cmd](journal, a)
    ws = Workshop(a.home, a.workshop)
    try:
        return {"event": cmd_event, "observe": cmd_observe, "state": cmd_state, "sync": cmd_sync}[a.cmd](ws, a)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
