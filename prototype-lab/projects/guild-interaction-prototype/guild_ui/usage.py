"""Usage & limits (SPEC rev 3.1): per-source state, projected time-to-limit,
vendor-reported evidence with reset/remaining sanity checks, and the aggregate
light. Pure functions over sample data (plus evidence Robert filed in this
browser); tested.

Evidence marks: our numbers are measured (used %, balance) or projected (linear
burn over a stated recent window); what the tools themselves say is REPORTED.
Too little data gives "no projection" with its reason, never an invented time.
"""
from __future__ import annotations

DAYS = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
DEFAULT_PRECEDENCE = ("red", "yellow", "unknown", "green")
WORD = {"green": "OK", "yellow": "Watch", "red": "Problem", "unknown": "Unknown"}
SHAPE = {"green": "circle", "yellow": "triangle", "red": "square", "unknown": "ring"}
RANK = {"green": 0, "unknown": 1, "yellow": 2, "red": 3}   # for per-source escalation only
VENDOR_KINDS = ("warning", "limit_reached")


def when(minutes: float) -> str:
    """Minutes from Sat 00:00 of the scenario week → 'Sun 13:15' (or 'next Sat 20:00')."""
    m = int(round(minutes))
    weeks = m // 10080
    day = DAYS[(m // 1440) % 7]
    prefix = "" if weeks <= 0 else ("next " if weeks == 1 else f"in {weeks} weeks, ")
    return f"{prefix}{day} {(m % 1440) // 60}:{m % 60:02d}"


def _short(src):
    return src.get("short") or src["label"]


def _at_least(state, floor):
    return floor if RANK[floor] > RANK[state] else state


def vendor_view(evidence: list, now: int, fresh_h: float) -> dict | None:
    """Latest vendor report seen at or before `now`; fresh or ignored-with-reason."""
    seen = [e for e in evidence or [] if e.get("seen_at") is not None and e["seen_at"] <= now]
    if not seen:
        return None
    v = max(seen, key=lambda e: e["seen_at"])
    fresh = (now - v["seen_at"]) <= fresh_h * 60
    out = {**v, "fresh": fresh, "seen_text": when(v["seen_at"]), "mark": "reported"}
    out["summary"] = f"“{v['text']}” · {v['tool']} · seen {out['seen_text']}"
    if v.get("stated_reset_at") is not None:
        out["summary"] += f" · says resets {when(v['stated_reset_at'])}"
    if v.get("stated_remaining_pct") is not None:
        out["summary"] += f" · says {v['stated_remaining_pct']} % left"
    if not fresh:
        out["ignored"] = f"ignored — seen {out['seen_text']}, older than {fresh_h:g} h"
    return out


def refills_view(src: dict, reading: dict, now: int, filed: list | None = None) -> list[dict]:
    """Refill receipts (REPORTED, receipt) paid after this reading and not after now."""
    read_at = reading.get("read_at", now)
    out = []
    for e in list(src.get("refills", [])) + [e for e in (filed or []) if e.get("source") == src["id"]]:
        if read_at < e["paid_at"] <= now:
            out.append({**e, "paid_text": when(e["paid_at"]), "mark": "reported (receipt)",
                        "summary": f"+${e['amount']:.2f} · {e['vendor']} · paid {when(e['paid_at'])}"})
    return sorted(out, key=lambda e: e["paid_at"])


def evaluate(src: dict, clock: str, doc: dict, filed: list | None = None, refills: list | None = None) -> dict:
    """One source at one clock → state, texts, projection and vendor checks."""
    now = doc["now"][clock]
    min_h = doc.get("min_window_hours", 6)
    horizon_h = doc.get("balance_horizon_hours", 48)
    fresh_h = doc.get("vendor_fresh_hours", 12)
    tol_min = doc.get("reset_tolerance_min", 30)
    tol_pct = doc.get("remaining_tolerance_pct", 10)
    vendor = vendor_view(list(src.get("vendor", [])) + [e for e in (filed or []) if e.get("source") == src["id"]],
                         now, fresh_h)
    fresh_v = vendor if vendor and vendor["fresh"] else None
    base = {"id": src["id"], "kind": src["kind"], "label": src["label"], "short": _short(src),
            "source_mark": "sample", "bar_pct": None, "used_text": "—", "reset_text": "—",
            "headroom_text": "—", "projection": None, "projection_text": "", "projected_at": None,
            "before_reset": False, "severity": 0.0, "item_text": "", "vendor": vendor, "mismatches": [],
            "reset_source": "configured", "refills": []}

    def vendor_escalate(state, item):
        if not fresh_v:
            return state, item
        if fresh_v.get("kind") == "limit_reached":
            return "red", f"{fresh_v['tool']} reported limit reached · seen {fresh_v['seen_text']}"
        if state in ("green", "unknown"):
            return "yellow", f"{fresh_v['tool']} reported a usage warning · seen {fresh_v['seen_text']}"
        return state, item

    if src.get("instrumented") is False:
        planned = src.get("planned_source", "planned")
        state, item = vendor_escalate("unknown", f"{src['label']} not instrumented ({planned})")
        return {**base, "state": state, "source_mark": "not instrumented", "planned": planned,
                "projection_text": "no projection — not instrumented", "item_text": item,
                "severity": 2.0 if state == "red" else (0.5 if state == "yellow" else 0.0)}
    r = src.get(clock) or {}
    if src.get("stale") or not r:
        state, item = vendor_escalate("unknown", f"{src['label']} stale — treat as unknown")
        return {**base, "state": state, "projection_text": "no projection — stale reading", "item_text": item}
    hours, out = r.get("window_hours") or 0, dict(base)
    if src["kind"] == "plan":
        used, reset = r["used_pct"], r["reset_at"]
        mismatches = []
        if fresh_v and fresh_v.get("stated_reset_at") is not None and abs(fresh_v["stated_reset_at"] - reset) > tol_min:
            mismatches.append(f"{fresh_v['tool']} says resets {when(fresh_v['stated_reset_at'])} · we assumed {when(reset)}"
                              " — using the vendor time for the projection")
            reset = fresh_v["stated_reset_at"]
            out["reset_source"] = "vendor-stated"
        if fresh_v and fresh_v.get("stated_remaining_pct") is not None and abs(fresh_v["stated_remaining_pct"] - (100 - used)) > tol_pct:
            mismatches.append(f"{fresh_v['tool']} says {fresh_v['stated_remaining_pct']} % left · we measured {max(0, 100 - used)} % left")
        rate = (r.get("window_pct") or 0) / hours if hours else 0
        out.update(bar_pct=min(used, 100), used_text=f"{used} %",
                   reset_text=f"resets {when(reset)}" + (" (vendor-stated)" if out["reset_source"] == "vendor-stated" else ""),
                   headroom_text=f"{max(0, 100 - used)} % left", mismatches=mismatches)
        if hours < min_h:
            out["projection_text"] = f"no projection — only {hours:g} h of data (need {min_h} h)"
        elif rate <= 0:
            out["projection_text"] = "no projection — no usage in the window"
        else:
            at = now + (100 - used) / rate * 60
            out.update(projected_at=at, before_reset=at < reset,
                       projection_text=f"projected limit {when(at)} (last {hours:g} h pace)")
        if used >= 100:
            state = "red"
        elif used >= src.get("warn_pct", 80) or out["before_reset"] or mismatches:
            state = "yellow"
        else:
            state = "green"
        if out["before_reset"]:
            item = f"{_short(src)} {used} % · projected limit {when(out['projected_at'])}, before reset {when(reset)}"
        elif mismatches:
            item = f"{_short(src)} reset mismatch: {mismatches[0].split(' — ')[0]}"
        else:
            item = f"{_short(src)} {used} % · resets {when(reset)}"
        state, item = vendor_escalate(state, item)
        out.update(state=state, item_text=item,
                   severity=(3 if state == "red" else 0) + used / 100 + (1 if out["before_reset"] else 0) + (0.5 if mismatches else 0))
    else:
        bal, refill, warn = r["balance"], src["refill_at"], src["warn_at"]
        spent = r.get("window_spent") or 0
        paid = refills_view(src, r, now, refills)
        origin = now
        if paid:                      # a recorded refill raises the balance and restarts the projection
            bal = bal + sum(e["amount"] for e in paid)
            origin = paid[-1]["paid_at"]
        out.update(used_text=f"${bal:.2f}" + (" (after refill)" if paid else ""), reset_text=f"refill at ${refill:.2f}",
                   headroom_text=f"${max(0, bal - refill):.2f} above refill", refills=paid)
        soon = False
        if bal <= refill:
            out.update(projected_at=now, projection_text=f"refill needed now (at or below ${refill:.2f})")
        elif hours < min_h:
            out["projection_text"] = f"no projection — only {hours:g} h of data (need {min_h} h)"
        elif spent <= 0:
            out["projection_text"] = f"no projection — no spend in the last {hours:g} h"
        else:
            at = origin + (bal - refill) / (spent / hours) * 60
            soon = (at - now) <= horizon_h * 60
            since = f", from refill {when(origin)}" if paid else ""
            out.update(projected_at=at, projection_text=f"refill needed by {when(at)} (last {hours:g} h pace{since})")
        if bal <= refill:
            state = "red"
        elif bal <= warn or soon:
            state = "yellow"
        else:
            state = "green"
        item = f"{_short(src)} ${bal:.2f}"
        if bal <= refill:
            item += " · refill needed now"
        elif out["projected_at"] is not None:
            item += f" · refill needed by {when(out['projected_at'])}"
        else:
            item += f" · refill at ${refill:.2f}"
        state, item = vendor_escalate(state, item)
        out.update(state=state, item_text=item,
                   severity=(3 if state == "red" else 0) + 1 + (warn - bal) / max(warn, 1) + (1 if soon else 0))
    return out


def _rank_key(item):
    # worst first: highest severity class, then earliest projected limit/refill
    at = item["projected_at"] if (item["before_reset"] or item["kind"] == "balance") and item["projected_at"] is not None else float("inf")
    return (-(item["severity"] >= 3), at, -item["severity"])


def aggregate(items: list[dict], precedence=DEFAULT_PRECEDENCE) -> dict:
    by = {s: [i for i in items if i["state"] == s] for s in ("red", "yellow", "unknown", "green")}
    state = next(s for s in precedence if by[s]) if items else "unknown"
    n_unknown = len(by["unknown"])
    tail = f" · {n_unknown} not instrumented" if n_unknown else ""
    if state in ("red", "yellow"):
        worst = sorted(by[state], key=_rank_key)[0]
        reason = worst["item_text"] + tail
    elif state == "unknown":
        names = ", ".join(i["short"] for i in by["unknown"])
        reason = f"{n_unknown} source{'s' if n_unknown != 1 else ''} not instrumented or stale: {names}"
        worst = by["unknown"][0]
        if by["red"] or by["yellow"]:  # only reachable with a non-default precedence
            w = sorted(by["red"] or by["yellow"], key=_rank_key)[0]
            reason += f" · worst known: {w['item_text']}"
    else:
        worst = None
        reason = f"all {len(items)} sources have headroom"
    return {"state": state, "word": WORD[state], "shape": SHAPE[state], "reason": reason,
            "worst": worst, "source": "sample", "source_mark": "sample"}


def summarize(doc: dict, clock: str, precedence=DEFAULT_PRECEDENCE, filed: list | None = None,
              refills: list | None = None) -> dict:
    """Everything the pages need for one clock: items, agent rows, light, shift, mismatches, Ask reply."""
    tools = doc.get("vendor_tools", {})
    filed = [{**e, "source": tools.get(e.get("tool"))} for e in (filed or []) if tools.get(e.get("tool"))]
    # Evidence filed during this simulated session is later than the fixture's
    # observation time; "now" for this clock advances to include it (≤ 6 h).
    base_now = doc["now"][clock]
    stamps = [e["seen_at"] for e in filed] + [e["paid_at"] for e in (refills or [])]
    in_session = [t for t in stamps if base_now < t <= base_now + doc.get("session_slack_hours", 6) * 60]
    if in_session:
        doc = {**doc, "now": {**doc["now"], clock: max(in_session)}}
    items = [evaluate(s, clock, doc, filed, refills) for s in doc["sources"]]
    by_id = {i["id"]: i for i in items}
    light = aggregate(items, precedence)
    rows = []
    for a in doc["agents"]:
        it = by_id[a["source"]]
        rows.append({**a, "draws_on": it["label"], "kind": it["kind"], "item": it,
                     "word": WORD[it["state"]], "shape": SHAPE[it["state"]], "shift": ""})
    shift = None
    w = light["worst"]
    if light["state"] in ("red", "yellow") and w and w["id"] in doc.get("shift", {}):
        cfg = doc["shift"][w["id"]]
        target = by_id[cfg["to_source"]]
        reset = w["reset_text"].replace("resets ", "").replace(" (vendor-stated)", "") if w["kind"] == "plan" else "refill"
        proj = f", limit projected {when(w['projected_at'])}" if w["projected_at"] is not None and w["before_reset"] else ""
        shift = {
            "work": cfg["work"], "from_agent": cfg["from_agent"], "to_agent": cfg["to_agent"], "until": reset,
            "hint": f"Move {cfg['work']} to {cfg['to_agent']} until the {w['short']} resets {reset}",
            "fields": [
                {"key": "action", "label": "Action", "value": f"Route {cfg['work']} to {cfg['to_agent']}", "editable": True},
                {"label": "From", "value": f"{cfg['from_agent']} — {w['label']} {w['used_text']}{proj}"},
                {"label": "To", "value": f"{cfg['to_agent']} — {target['label']}, {target['headroom_text']}"},
                {"label": "Until", "value": f"{w['short']} resets {reset}"},
                {"label": "Effect", "value": "simulated · no routing changed"},
            ],
        }
        for r in rows:
            if r["source"] == w["id"]:
                r["shift"] = shift["hint"]
    mismatches = [{"source": i["id"], "short": i["short"], "text": m} for i in items for m in i["mismatches"]]
    overall = dict(doc.get("overall_spend") or {})
    paid = [e for i in items for e in i["refills"]]
    if overall:
        overall["refills"] = round(sum(e["amount"] for e in paid), 2)
        overall["refill_count"] = len(paid)
    return {"clock": clock, "items": items, "rows": rows, "light": light, "shift": shift,
            "mismatches": mismatches, "reply": _reply(light, items, shift),
            "observed": doc["observed"][clock], "overall": overall}


def _reply(light, items, shift):
    unknown = [i for i in items if i["state"] == "unknown"]
    tail = ""
    if unknown:
        tail = " " + "; ".join(f"{i['label']} is not instrumented ({i.get('planned', 'stale')})" for i in unknown) + "."
    vendor_notes = []
    for i in items:
        v = i["vendor"]
        if v and v["fresh"]:
            note = f" {v['tool']} itself reported “{v['text']}” at {v['seen_text']}"
            if i["reset_source"] == "vendor-stated":
                note += f" and says it resets {when(v['stated_reset_at'])}, not when we assumed; I'm using its time"
            vendor_notes.append(note + ".")
    if light["state"] in ("red", "yellow"):
        w = light["worst"]
        text = f"Tightest limit: {w['label']} at {w['used_text']}"
        if w["projected_at"] is not None and (w["before_reset"] or w["kind"] == "balance"):
            text += f"; at the recent pace it {'runs out' if w['kind'] == 'plan' else 'needs a refill'} around {when(w['projected_at'])}"
            if w["kind"] == "plan":
                text += f", before it {w['reset_text'].replace(' (vendor-stated)', '')}"
        text += "." + "".join(vendor_notes)
        if shift:
            text += f" I propose moving {shift['work']} to {shift['to_agent']} until then ({shift['fields'][2]['value'].split(' — ')[1]})."
        return text + tail + " Sample figures; nothing is rerouted unless you confirm, and even then this prototype changes no routing."
    if light["state"] == "unknown":
        return "Nothing known is near a limit now." + "".join(vendor_notes) + tail + " So the light stays grey rather than green. Sample figures."
    return "Every plan and balance has headroom, and no limit is projected before its reset." + "".join(vendor_notes) + " Sample figures."
