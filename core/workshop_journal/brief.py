"""The return brief and the history reader (v0.6 section 14; v0.7 Unit 3). Deterministic, read only, no model.

The same events and the same ``now`` always give the same brief. Nothing is guessed: a missing entry is "nothing reported since
<time>", never "idle" or "done"; a worker's ``completed`` is a worker report; test numbers are listed per reporter and pin and
never added together; an owner question closes only through the injected owner resolver; and every gap is named. The optional
model summary of the product spec is not here: the base brief must work without one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from core.workshop_journal import reducer, workflow

BRIEF_VERSION = 1
NOT_OBSERVED = "not_observed"


def human_time(iso: str | None, zone: str = "America/Chicago") -> str:
    """A time a person can read ("Oct 8, 6:49 pm CDT"). The machine fields keep the exact UTC text; only the words change."""
    moment = _parse(iso)
    if moment is None:
        return "an unknown time"
    local = moment.astimezone(ZoneInfo(zone))
    return f"{local.strftime('%b')} {local.day}, {local.strftime('%I:%M %p').lstrip('0').lower()} {local.strftime('%Z')}"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _line(ev: dict) -> dict:
    return {"seq": ev.get("seq"), "event_id": ev.get("event_id"), "at": ev.get("at"), "actor": ev.get("actor"), "kind": ev.get("kind"),
            "item": ev.get("item"), "text": ev.get("text")}


def _refs(ev: dict) -> list[dict]:
    return [{k: r.get(k) for k in ("type", "id", "sha256", "availability", "locator") if r.get(k) is not None}
            for r in ev.get("refs") or []]


def _snippet(text: str | None, limit: int = 90) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _evidence(payload: dict) -> list[dict]:
    return [{k: r.get(k) for k in ("type", "id", "sha256", "availability", "locator") if r.get(k) is not None}
            for r in payload.get("evidence_refs") or []]


def _counts_text(summary: dict) -> str:
    names = (("reported", "reported"), ("run", "run"), ("passed", "passed"), ("failed", "failed"), ("not_run", "not run"))
    return ", ".join(f"{label} {summary[k]}" for k, label in names if summary.get(k) is not None) or "no counts given"


def _in_topic(events: list[dict], flow: workflow.Flow, topic: str | None) -> list[dict]:
    """The events of a topic and everything linked to them: a request's receipts, results and claims, a decision that resolves
    one of its questions, and replies and supersessions. Linked events are kept even if they omit the topic, so a question the
    owner already answered never reappears in a topic view. Unrelated events are not admitted."""
    v2 = [e for e in events if e.get("v") == 2]
    if topic is None:
        return v2
    requests = {rid for rid, r in flow.requests.items() if r["event"].get("topic") == topic}
    claims = {cid for cid, c in flow.claims.items() if c.request_id in requests}
    ids = {e["event_id"] for e in v2 if e.get("topic") == topic} | requests | claims
    keep: dict[str, dict] = {}
    changed = True
    while changed:
        changed = False
        for ev in v2:
            if ev["event_id"] in keep:
                continue
            p = ev.get("payload") or {}
            linked = (ev.get("topic") == topic or ev["event_id"] in ids or p.get("request_id") in requests or p.get("claim_id") in claims
                      or bool(set(p.get("resolves") or ()) & ids) or ev.get("supersedes") in ids or ev.get("in_reply_to") in ids
                      or p.get("incident_id") in ids and ev["kind"] != "needs_you")
            if linked:
                keep[ev["event_id"]] = ev
                ids.add(ev["event_id"])
                if ev["kind"] == "needs_you" and p.get("incident_id"):
                    ids.add(p["incident_id"])
                changed = True
    return [e for e in v2 if e["event_id"] in keep]


def build(workshop_id: str, events: list[dict], *, topic: str | None = None, resolver=None, now: datetime | None = None,
          teammates: tuple[str, ...] = (), journal_status: str = "ok") -> dict:
    """The brief for the whole workshop or one topic, from journal order alone."""
    flow = workflow.derive(events)
    scope = _in_topic(events, flow, topic)
    last_all = events[-1] if events else None
    out: dict = {
        "v": BRIEF_VERSION, "workshop": workshop_id, "topic": topic,
        "as_of": {"seq": last_all.get("seq") if last_all else 0, "event_id": last_all.get("event_id") if last_all else None,
                  "recorded_at": last_all.get("recorded_at", last_all.get("at")) if last_all else None},
        "journal_status": journal_status,
        "freshness": {"journal_latest": last_all.get("at") if last_all else None, "native_source": NOT_OBSERVED,
                      "capture": NOT_OBSERVED, "production_ack": NOT_OBSERVED},
        "gaps": [],
    }
    gaps = out["gaps"]
    if journal_status not in ("ok",):
        gaps.append({"code": f"journal_{journal_status}", "text": f"The journal read is {journal_status}; the newest bytes may be missing."})

    # teammates: last entry each; absence is a gap of knowledge, not a status
    seen: dict[str, dict] = {}
    for ev in scope:
        if ev["actor"] not in ("host",):
            seen[ev["actor"]] = ev
    names = sorted(set(seen) | set(teammates))
    out["teammates"] = [({"actor": n, "last": _line(seen[n]), "note": f"nothing newer than {seen[n]['at']}"} if n in seen
                         else {"actor": n, "last": None, "note": "no entry in this scope"}) for n in names]

    # requests and their per-recipient lifecycle
    requests = []
    for rid, req in flow.requests.items():
        ev = req["event"]
        if ev not in scope:
            continue
        p = ev["payload"]
        per = {}
        for r in req["recipients"]:
            res = req["results"].get(r) or []
            rec = req["receipts"].get(r) or []
            per[r] = {"status": workflow.recipient_status(req, r), "receipt_seq": rec[0]["seq"] if rec else None,
                      "result_seq": res[-1]["seq"] if res else None, "result_outcome": res[-1]["payload"]["outcome"] if res else None}
        state = workflow.request_state(req)
        due = _parse(p.get("due_at"))
        overdue = None if (due is None or now is None) else (state == "open" and due < now)
        requests.append({"id": rid, "seq": ev["seq"], "from": ev["actor"], "to": req["recipients"], "action": p["action"],
                         "expected_result": p["expected_result"], "state": state, "due_at": p.get("due_at"), "overdue": overdue,
                         "recipients": per, "text": ev["text"], "refs": _refs(ev)})
        if state == "open":
            for r, info in per.items():
                if info["status"] == "pending":
                    gaps.append({"code": "no_receipt", "request_id": rid, "recipient": r,
                                 "text": f"No receipt from {r} for request {rid} (sent at seq {ev['seq']})."})
                elif info["status"] == "received":
                    gaps.append({"code": "no_result", "request_id": rid, "recipient": r,
                                 "text": f"{r} acknowledged request {rid} but has returned no result."})
        if overdue:
            gaps.append({"code": "overdue", "request_id": rid, "text": f"Request {rid} was due {p['due_at']} and is still open."})
    out["requests"] = requests

    # results: worker reports, with their evidence; test numbers per reporter, never summed
    done, tests = [], []
    for ev in scope:
        if ev["kind"] == "result":
            p = ev["payload"]
            done.append({"request_id": p["request_id"], "reported_by": ev["actor"], "outcome": p["outcome"], "seq": ev["seq"],
                         "limitations": p["limitations"], "evidence": _refs(ev) + _evidence(p),
                         "newer_entries_in_scope": sum(1 for e in scope if e["seq"] > ev["seq"]),
                         "note": "a worker report, not an independent check"})
            if p.get("test_summary"):
                tests.append({"reported_by": ev["actor"], "seq": ev["seq"], "summary": p["test_summary"], "request_id": p["request_id"],
                              "pin": [r for r in _refs(ev) + _evidence(p) if r.get("type") == "test" and r.get("sha256")]})
        elif ev["kind"] in ("progress", "done") and (ev.get("payload") or {}).get("evidence_refs"):
            pass
    out["results"] = done
    out["test_reports"] = tests

    # owner questions: only the owner resolver closes one
    state = reducer.reduce(workshop_id, scope, resolver=resolver, now=now or datetime(1970, 1, 1, tzinfo=timezone.utc))
    out["needs_you"] = state["needs_you"]
    closed = reducer.closed_needs(scope, resolver)

    # decisions: a proposal is only a proposal; the owner resolver alone makes a decision the owner's
    proposals: dict[str, dict] = {}
    owner, links, unconfirmed = [], [], []
    for ev in scope:
        if ev.get("supersedes"):
            links.append({"newer": ev["event_id"], "older": ev["supersedes"], "seq": ev["seq"]})
        if ev["kind"] != "decision":
            continue
        p = ev["payload"]
        row = {**_line(ev), "record_event_kind": p["record_event_kind"], "reason": p["reason"], "resolves": p["resolves"]}
        if p["record_event_kind"] == "proposed":
            proposals[ev["event_id"]] = {**row, "status": "open", "settled_by": None}
            continue
        try:
            by_owner = p["record_event_kind"] != "withdrawn" and bool(resolver and resolver(ev) is True)
        except Exception:
            by_owner = False
        if p["record_event_kind"] == "withdrawn":
            for target in p["resolves"]:
                if target in proposals and proposals[target]["actor"] == ev["actor"]:
                    proposals[target].update(status="withdrawn", settled_by=ev["event_id"])
        elif not by_owner and p["record_event_kind"] in ("approved-direct", "approved-under-mandate", "rejected", "verified", "superseded"):
            unconfirmed.append(row)                       # someone's claim of owner authority that no owner control confirms
        elif by_owner:
            owner.append(row)
            outcome = {"approved-direct": "approved", "approved-under-mandate": "approved", "rejected": "rejected",
                       "verified": "verified", "superseded": "superseded"}[p["record_event_kind"]]
            for target in p["resolves"]:
                if target in proposals:
                    proposals[target].update(status=outcome, settled_by=ev["event_id"])
    for link in links:
        old = proposals.get(link["older"])
        if old is not None and old["status"] == "open":
            newer = next((e for e in scope if e["event_id"] == link["newer"]), None)
            owned = newer is not None and (newer["kind"] != "decision" or any(o["event_id"] == newer["event_id"] for o in owner))
            old.update(status="superseded" if owned else "revised", settled_by=link["newer"])
    out["decisions"] = {"proposals": list(proposals.values()), "owner": owner, "unconfirmed": unconfirmed, "superseded": links}
    for row in unconfirmed:
        gaps.append({"code": "unconfirmed_owner_claim", "event_id": row["event_id"],
                     "text": f"Entry {row['seq']} ({row['actor']}) claims an owner {row['record_event_kind']} that no owner control confirms; it settles nothing."})

    # blocked and uncertain work, claims, next actor
    out["blocked"] = [{**_line(ev), "reason_code": (ev["payload"] or {}).get("reason_code")} for ev in scope if ev["kind"] == "blocked"] + \
                     [{**_line(ev), "reason_code": ev["payload"]["outcome"]} for ev in scope
                      if ev["kind"] == "result" and ev["payload"]["outcome"] in ("blocked", "uncertain", "failed")]
    out["claims"] = [{"claim_id": c.id, "resource": c.resource, "claimant": c.claimant, "generation": c.generation,
                      "request_id": c.request_id, "state": "active" if c.active else "stopped",
                      "uncertain_releases": len(c.unstopped_releases)} for c in flow.claims.values() if c.event in scope]
    nxt: dict[str, dict] = {}
    for ev in scope:
        if ev["kind"] == "next":
            nxt[ev["item"]] = {**_line(ev), "next_actor": ev["payload"]["next_actor"], "action": ev["payload"]["action"]}
    out["next"] = [nxt[k] for k in sorted(nxt)]

    # handoffs from the inbox that name nobody: routing is needed (derived, not stored)
    out["unaddressed"] = [_line(ev) for ev in scope if ev["kind"] == "progress" and (ev.get("origin") or {}).get("adapter") == "inbox"
                          and not ev.get("recipients")]
    for row in out["unaddressed"]:
        gaps.append({"code": "routing_needed", "event_id": row["event_id"], "text": f"Handoff at seq {row['seq']} names no recipient."})
    for n in out["needs_you"]:
        gaps.append({"code": "owner_question_open", "event_id": n["event_id"], "text": f"Owner question open since seq {n['seq']}."})
    for c in out["claims"]:
        if c["state"] == "active" and c["uncertain_releases"]:
            gaps.append({"code": "claim_release_uncertain", "claim_id": c["claim_id"],
                         "text": f"{c['claimant']} released {c['resource']} without saying it stopped; the claim stays effective."})
    out["counts"] = {"events_in_scope": len(scope), "open_requests": sum(1 for r in requests if r["state"] == "open"),
                     "open_owner_questions": len(out["needs_you"]), "gaps": len(gaps)}
    return out


def history(events: list[dict], *, topic: str | None = None, through_seq: int | None = None) -> list[dict]:
    """Every entry in a topic, oldest first, up to a sequence number. Pure read of the journal order."""
    flow = workflow.derive(events)
    rows = []
    for ev in _in_topic(events, flow, topic):
        if through_seq is not None and ev["seq"] > through_seq:
            continue
        rows.append({**_line(ev), "stage": ev.get("stage"), "in_reply_to": ev.get("in_reply_to"), "recipients": ev.get("recipients"),
                     "refs": _refs(ev)})
    return rows


# ── a plain-text rendering of the same view ──────────────────────────────────────────────────────────────────────────
def render_markdown(brief: dict) -> str:
    out = [f"# Brief: {brief['workshop']}" + (f" / {brief['topic']}" if brief["topic"] else ""),
           f"As of seq {brief['as_of']['seq']} (journal {brief['journal_status']}). Latest entry: {human_time(brief['freshness']['journal_latest']) if brief['freshness']['journal_latest'] else 'none'}. "
           "Native source, capture and production acknowledgement: not observed."]
    if brief["needs_you"]:
        out += ["", "## Waiting for Robert"] + [f"- seq {n['seq']}: {n['text']}" for n in brief["needs_you"]]
    out += ["", "## Teammates"] + [
        f"- {t['actor']}: {t['last']['kind']} at {human_time(t['last']['at'])} (entry {t['last']['seq']}), \"{_snippet(t['last']['text'])}\"; nothing newer"
        if t["last"] else f"- {t['actor']}: {t['note']}" for t in brief["teammates"]]
    if brief["requests"]:
        out += ["", "## Requests"]
        for r in brief["requests"]:
            who = ", ".join(f"{k} {v['status']}" + (f" ({v['result_outcome']})" if v["result_outcome"] else "") for k, v in r["recipients"].items())
            out.append(f"- [{r['state']}] seq {r['seq']} {r['from']} -> {', '.join(r['to'])}: {r['action']}; {who}"
                       + ("; OVERDUE" if r["overdue"] else ""))
    if brief["results"]:
        out += ["", "## Reported results (worker reports, not independent checks)"]
        out += [f"- seq {x['seq']} {x['reported_by']}: {x['outcome']}" + (f"; limits: {'; '.join(x['limitations'])}" if x["limitations"] else "")
                + (f" [{x['newer_entries_in_scope']} newer entr{'y' if x['newer_entries_in_scope'] == 1 else 'ies'} since: read them before relying on this]"
                   if x.get("newer_entries_in_scope") else "") for x in brief["results"]]
    if brief["test_reports"]:
        out += ["", "## Test reports (per reporter; never summed)"]
        out += [f"- seq {x['seq']} {x['reported_by']}: {_counts_text(x['summary'])}"
                + (f" at pin {x['pin'][0]['sha256'][:12]}" if x["pin"] else " (no pin given)") for x in brief["test_reports"]]
    d = brief["decisions"]
    if d["proposals"] or d["owner"] or d["unconfirmed"]:
        out += ["", "## Decisions"] + [f"- owner {x['record_event_kind']}: seq {x['seq']} {x['text']}" for x in d["owner"]] + \
              [f"- proposal by {x['actor']} ({x['status']}): seq {x['seq']} {x['text']}" for x in d["proposals"]] + \
              [f"- CLAIMED by {x['actor']}, NOT CONFIRMED as the owner's ({x['record_event_kind']}): seq {x['seq']} {x['text']}" for x in d["unconfirmed"]]
    if d["superseded"]:
        out += ["- replaced: " + "; ".join(f"seq {x['seq']} replaces {x['older']}" for x in d["superseded"])]
    if brief["blocked"]:
        out += ["", "## Blocked or uncertain"] + [f"- seq {x['seq']} {x['actor']}: {x['reason_code']}" for x in brief["blocked"]]
    if brief["claims"]:
        out += ["", "## Claims"] + [f"- {c['resource']} gen {c['generation']}: {c['claimant']} ({c['state']})" for c in brief["claims"]]
    if brief["next"]:
        out += ["", "## Next"] + [f"- {x['next_actor']}: {x['action']}" for x in brief["next"]]
    if brief["gaps"]:
        out += ["", "## Gaps"] + [f"- {g['text']}" for g in brief["gaps"]]
    return "\n".join(out) + "\n"


def render_history_markdown(rows: list[dict]) -> str:
    return "\n".join(f"{r['seq']:>5}  {r['at']}  {r['actor']:<12} {r['kind']:<10} {r['item']}  {r['text']}" for r in rows) + ("\n" if rows else "")
