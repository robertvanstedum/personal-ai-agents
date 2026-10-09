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
The workshop id is $MINIMOI_WORKSHOP_ID (default "mac" for the old commands: event, observe, state, sync). The newer commands
(append, brief, history, ...) have no default: name the workshop with --id, so a forgotten flag never touches the wrong one.
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


def cmd_inbox(journal: Journal, a) -> int:
    from core.workshop_journal.inbox import Inbox
    outcomes = Inbox(journal, settle=a.settle).scan(apply=a.apply)
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    print(json.dumps({"ok": True, "apply": a.apply, "counts": counts, "files": [o.to_json() for o in outcomes]}, sort_keys=True))
    return 0 if not any(o.status in ("held", "unstable") for o in outcomes) else 8


def cmd_artifact(journal: Journal, a) -> int:
    from core.workshop_journal.errors import JournalError
    if a.action == "report":
        report = journal.artifact_report()
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "ok" else 5
    try:
        data = journal.open_artifact(a.sha256)
    except JournalError as exc:
        print(json.dumps({"ok": False, "status": exc.status, "reason": exc.reason}))
        return exc.exit_code
    print(json.dumps({"ok": True, "sha256": a.sha256, "size": len(data), "text": data.decode("utf-8")}, sort_keys=True))
    return 0


def _find_request(journal: Journal, request_id: str):
    read = journal.read(deep=False)
    for ev in read.events:
        if ev.get("event_id") == request_id and ev.get("kind") == "request":
            return ev, read
    return None, read


def _send(journal: Journal, a, envelope: dict) -> int:
    """Append one shorthand event, or only check it with --dry-run. Same one-object output and exit codes as ``append``."""
    if getattr(a, "event_id", None):
        envelope["event_id"] = a.event_id
    result = journal.preflight(envelope) if a.dry_run else journal.append(envelope, adapter="cli")
    return _emit(result)


def _shorthand_base(journal: Journal, a, request_id: str, actor: str):
    request, _ = _find_request(journal, request_id)
    if request is None:
        print(json.dumps({"ok": False, "status": "policy_refused", "reason": "unknown_request", "committed": False}))
        return None
    return {"actor": actor, "item": request["item"], "topic": request.get("topic"), "recipients": []}


def cmd_pending(journal: Journal, a) -> int:
    from core.workshop_journal import workflow
    read = journal.read(deep=False)
    if read.status in ("missing", "unsafe_root", "unsupported_writer", "corrupt"):
        print(json.dumps({"ok": False, "status": "refused" if read.status == "corrupt" else read.status, "for": a.for_actor, "requests": []}))
        return EXIT_BY_STATUS.get(read.status, 5)
    flow = workflow.derive(read.events)
    rows = []
    for rid, req in flow.requests.items():
        if a.for_actor in req["recipients"] and workflow.recipient_status(req, a.for_actor) != "returned":
            ev = req["event"]
            rows.append({"request_id": rid, "seq": ev["seq"], "from": ev["actor"], "action": ev["payload"]["action"], "text": ev["text"],
                         "status": workflow.recipient_status(req, a.for_actor), "due_at": ev["payload"].get("due_at"), "topic": ev.get("topic")})
    incomplete = read.status in INCOMPLETE
    print(json.dumps({"ok": True, "complete": not incomplete, "status": read.status, "for": a.for_actor, "requests": rows,
                      "as_of_seq": read.scan.last_seq}, sort_keys=True))
    return INCOMPLETE_EXIT if incomplete else 0


def cmd_receipt(journal: Journal, a) -> int:
    base = _shorthand_base(journal, a, a.request, a.actor)
    if base is None:
        return 2
    payload = {"request_id": a.request, "recipient": a.actor}
    if a.native_correlation:
        payload["native_correlation"] = a.native_correlation
    return _send(journal, a, {**base, "kind": "receipt", "text": f"{a.actor} picked up the request.", "payload": payload})


def cmd_claim(journal: Journal, a) -> int:
    base = _shorthand_base(journal, a, a.request, a.actor)
    if base is None:
        return 2
    return _send(journal, a, {**base, "kind": "claim", "text": f"{a.actor} claims {a.resource}.",
                              "payload": {"request_id": a.request, "resource": a.resource, "generation": a.expected_generation,
                                          "claimant": a.actor}})


def cmd_release(journal: Journal, a) -> int:
    read = journal.read(deep=False)
    claim = next((e for e in read.events if e.get("event_id") == a.claim and e.get("kind") == "claim"), None)
    if claim is None:
        print(json.dumps({"ok": False, "status": "policy_refused", "reason": "unknown_claim", "committed": False}))
        return 2
    return _send(journal, a, {"actor": a.actor, "kind": "release", "item": claim["item"], "topic": claim.get("topic"), "recipients": [],
                              "text": f"{a.actor} releases the claim.",
                              "payload": {"claim_id": a.claim, "generation": a.generation, "stopped": a.stopped, "reason": a.reason}})


def cmd_result(journal: Journal, a) -> int:
    doc = _read_envelope(a.file)
    if isinstance(doc, Result):
        return _emit(doc)
    actor = doc.pop("actor", None) or a.actor
    base = _shorthand_base(journal, a, a.request, actor)
    if base is None:
        return 2
    payload = {"request_id": a.request, "recipient": actor, "outcome": doc.pop("outcome", None), "limitations": doc.pop("limitations", [])}
    for key in ("claim_id", "evidence_refs", "test_summary"):
        if key in doc:
            payload[key] = doc.pop(key)
    envelope = {**base, "kind": "result", "text": doc.pop("text", "Result."), "payload": payload}
    if "refs" in doc:
        envelope["refs"] = doc.pop("refs")
    if doc:
        return _emit(Result(False, "invalid_input", None, None, False, False, "unknown_result_fields", {}, 2))
    return _send(journal, a, envelope)


def cmd_notify(journal: Journal, a) -> int:
    """Print the exact, frozen notification for one recipient of one request. Sends nothing, ever."""
    from core.workshop_journal import notify
    request, _ = _find_request(journal, a.request)
    if request is None:
        print(json.dumps({"ok": False, "status": "policy_refused", "reason": "unknown_request"}))
        return 2
    try:
        sys.stdout.write(notify.build(request, a.to, journal.id, os.path.abspath(journal.root)).decode("utf-8"))
    except notify.NotNotifiable as exc:
        print(json.dumps({"ok": False, "status": "policy_refused", "reason": str(exc)}))
        return 2
    return 0


def cmd_routes(journal: Journal, a) -> int:
    from core.workshop_journal import routes
    try:
        table = routes.load(journal.dir)
    except routes.BadRoutes as exc:
        print(json.dumps({"ok": False, "status": "invalid_input", "reason": str(exc)}))
        return 2
    rows = [routes.describe(table, who) for who in ([a.actor] if a.actor else sorted(table))]
    print(json.dumps({"ok": True, "routes": rows}, sort_keys=True))
    return 0


def cmd_checkin(journal: Journal, a) -> int:
    """Where are they: the brief in plain words, no model. --ask-refresh also posts an ordinary refresh request per teammate."""
    from core.workshop_journal import checkin
    doc = journal.brief(topic=a.topic, now=_parse_now(a.now), teammates=tuple(a.teammate or ()))
    sys.stdout.write(checkin.report(doc))
    code = 0
    for who in a.ask_refresh or ():
        env = checkin.refresh_request([who])
        result = journal.preflight(env) if a.dry_run else journal.append(env, adapter="cli")
        print(json.dumps(result.to_json(), sort_keys=True))
        code = code or result.exit_code
    if "status" in doc:
        return {"missing": 8, "refused": 5}.get(doc["status"], 2)
    return code or (INCOMPLETE_EXIT if doc["journal_status"] in INCOMPLETE else 0)


def _parse_now(text: str | None):
    from datetime import datetime, timezone
    if not text:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


INCOMPLETE = ("torn_tail", "tail_in_progress")
INCOMPLETE_EXIT = 8


def _notice(status: str) -> str:
    return (f"NOTICE: incomplete. The journal read is {status}: the newest bytes are not shown, so this is not the whole record.\n")


def cmd_brief(journal: Journal, a) -> int:
    from core.workshop_journal import brief as brief_view
    doc = journal.brief(topic=a.topic, now=_parse_now(a.now), teammates=tuple(a.teammate or ()), since_seq=a.since_seq)
    if "status" in doc:                                                  # refused, missing or unsafe: never an empty brief
        print(json.dumps({"ok": False, **doc}, sort_keys=True))
        return {"missing": 8, "refused": 5, "unsafe_root": 2}.get(doc["status"], 5)
    incomplete = doc["journal_status"] in INCOMPLETE
    if a.format == "md":
        print((_notice(doc["journal_status"]) if incomplete else "") + brief_view.render_markdown(doc), end="")
    else:
        print(json.dumps({"ok": True, "complete": not incomplete, **doc}, sort_keys=True))
    return INCOMPLETE_EXIT if incomplete else 0


def cmd_history(journal: Journal, a) -> int:
    from core.workshop_journal import brief as brief_view
    doc = journal.history(topic=a.topic, through_seq=a.through_seq)
    if doc["status"] in ("missing", "unsafe_root", "unsupported_writer", "refused"):
        print(json.dumps({"ok": False, **doc}, sort_keys=True))
        return {"missing": 8, "refused": 5}.get(doc["status"], 2)
    incomplete = doc["status"] in INCOMPLETE
    if a.format == "md":
        print((_notice(doc["status"]) if incomplete else "") + brief_view.render_history_markdown(doc["events"]), end="")
    else:
        print(json.dumps({"ok": True, "complete": not incomplete, **doc}, sort_keys=True))
    return INCOMPLETE_EXIT if incomplete else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=HOME)
    ap.add_argument("--workshop", "--id", dest="workshop", default=None,
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
    ib = sub.add_parser("inbox", help="scan the landing folder; a dry run unless --apply")
    ib.add_argument("--apply", action="store_true")
    ib.add_argument("--settle", type=float, default=2.0, help="seconds a file must stay unchanged before it is read")
    af = sub.add_parser("artifact", help="retained documents: report orphans, or open the exact bytes for a hash")
    af.add_argument("action", choices=["report", "open"])
    af.add_argument("--sha256")
    pe = sub.add_parser("pending", help="open requests addressed to one actor (read only)")
    pe.add_argument("--for", dest="for_actor", required=True)
    pe.add_argument("--json", action="store_true")
    for name, helptext in (("receipt", "pick up a request"), ("claim", "claim a resource for a request"),
                           ("release", "release a claim"), ("result", "return a result for a request")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--dry-run", action="store_true", help="check only; write nothing")
        sp.add_argument("--event-id", help="a stable event ID, so a retry is a duplicate and never a second event")
        if name != "release":
            sp.add_argument("--request", required=True)
        sp.add_argument("--actor", required=name != "result")
        if name == "receipt":
            sp.add_argument("--native-correlation")
        if name == "claim":
            sp.add_argument("--resource", required=True)
            sp.add_argument("--expected-generation", type=int, required=True)
        if name == "release":
            sp.add_argument("--claim", required=True)
            sp.add_argument("--generation", type=int, required=True)
            sp.add_argument("--stopped", action=argparse.BooleanOptionalAction, required=True)
            sp.add_argument("--reason", required=True)
        if name == "result":
            sp.add_argument("--file", required=True)
    nt = sub.add_parser("notify", help="print the frozen notification for one recipient of a request (sends nothing)")
    nt.add_argument("--request", required=True)
    nt.add_argument("--to", required=True)
    ro = sub.add_parser("routes", help="how each teammate is reached, stated honestly")
    ro.add_argument("--actor")
    ck = sub.add_parser("checkin", help="where are they: the brief in plain words; no model; optionally ask teammates to refresh")
    ck.add_argument("--topic")
    ck.add_argument("--now")
    ck.add_argument("--teammate", action="append")
    ck.add_argument("--ask-refresh", action="append", help="post a refresh request to this teammate (repeatable)")
    ck.add_argument("--dry-run", action="store_true")
    br = sub.add_parser("brief", help="the deterministic return brief (read only, no model)")
    br.add_argument("--topic")
    br.add_argument("--format", choices=["json", "md"], default="json")
    br.add_argument("--now", help="ISO time for overdue checks (default: the clock)")
    br.add_argument("--since-seq", type=int, help="also list what changed after this journal entry (what you have already seen)")
    br.add_argument("--teammate", action="append", help="name a teammate to list even if silent (repeatable)")
    hi = sub.add_parser("history", help="every entry in a topic up to a sequence number (read only)")
    hi.add_argument("--topic")
    hi.add_argument("--through-seq", type=int)
    hi.add_argument("--format", choices=["json", "md"], default="json")
    s = sub.add_parser("sync")
    s.add_argument("--to", default=SYNC_TO)
    a = ap.parse_args(argv)
    if a.cmd in ("append", "prepare", "get", "verify", "repair", "inbox", "artifact", "pending", "receipt", "claim", "release", "result",
                 "brief", "history", "notify", "routes", "checkin"):
        if not a.workshop and not os.environ.get("MINIMOI_WORKSHOP_ID"):
            print(json.dumps({"ok": False, "status": "refused", "reason": "workshop_id_required",
                              "hint": "pass --id <workshop> (or set MINIMOI_WORKSHOP_ID); there is no default for these commands"}))
            return 2
        a.workshop = a.workshop or os.environ["MINIMOI_WORKSHOP_ID"]
        try:
            journal = Journal(a.home, a.workshop)
        except Exception as exc:                                   # a refused workshop ID or root; fixed text only
            print(json.dumps({"ok": False, "status": getattr(exc, "status", "invalid_input"), "reason": getattr(exc, "reason", "bad_arguments")}))
            return 2
        return {"append": cmd_append, "prepare": cmd_prepare, "get": cmd_get, "verify": cmd_verify, "repair": cmd_repair,
                "inbox": cmd_inbox, "artifact": cmd_artifact, "pending": cmd_pending, "receipt": cmd_receipt, "claim": cmd_claim,
                "release": cmd_release, "result": cmd_result, "brief": cmd_brief, "history": cmd_history, "notify": cmd_notify,
                "routes": cmd_routes, "checkin": cmd_checkin}[a.cmd](journal, a)
    ws = Workshop(a.home, a.workshop or WORKSHOP_ID)
    try:
        return {"event": cmd_event, "observe": cmd_observe, "state": cmd_state, "sync": cmd_sync}[a.cmd](ws, a)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
