"""The Journeyman's check-in (v0.7 Unit 7): "where are they", answered from the record, with no model call.

The report is the brief, worded plainly: the last entry from each teammate, what is waiting for the owner, which requests have no
receipt or no result, and what is overdue. No entry is "nothing reported since <time>", never "idle" or "done". A refresh is an
ordinary addressed request, so the answer comes back as a correlated result. An optional model-written summary may be appended
for reading ease, but it may only *select* facts the brief already holds; the sentences are generated here from fixed templates, so a
summary cannot add an approval, a completion or a gap. Judgment calls need a configured profile (``profiles``); routine checks need none because they use none.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from core.workshop_journal import brief as brief_view, profiles as profile_mod


def report(brief: dict) -> str:
    """The plain-language check-in from a brief (as ``Journal.brief`` returns it)."""
    if "status" in brief:
        return f"I cannot report: the journal read is {brief['status']} ({brief.get('reason') or 'no detail'}). Nothing is assumed.\n"
    out = [f"Check-in for {brief['workshop']}" + (f", topic {brief['topic']}" if brief["topic"] else "")
           + f" (as of journal entry {brief['as_of']['seq']}; journal {brief['journal_status']})."]
    for t in brief["teammates"]:
        if t["last"]:
            when = brief_view.human_time(t["last"]["at"])
            out.append(f"- {t['actor']}: last entry was a {t['last']['kind']} at {when} (entry {t['last']['seq']}), "
                       f"\"{brief_view._snippet(t['last']['text'])}\"; nothing newer has been reported since {when}.")
        else:
            out.append(f"- {t['actor']}: no entry in this scope; nothing has been reported.")
    if brief["needs_you"]:
        out.append("Waiting for Robert:")
        out += [f"- entry {n['seq']}: {n['text']}" for n in brief["needs_you"]]
    else:
        out.append("Nothing is waiting for Robert in this scope.")
    gaps = [g for g in brief["gaps"] if g["code"] != "owner_question_open"]
    if gaps:
        out.append("Gaps:")
        out += [f"- {g['text']}" for g in gaps]
    else:
        out.append("No missing receipts, results or overdue requests are recorded.")
    out.append("Freshness: only the journal is observed here; native source, capture and production acknowledgement are not observed.")
    return "\n".join(out) + "\n"


class BadSummary(ValueError):
    pass


# A model may only *select* facts the brief already holds, by kind and ID. It writes no prose: every sentence of the summary is
# produced here from a fixed template and the brief's own data, so it cannot invent authority, completion or a gap that is not
# there (Codex R10). A fact the brief does not contain is refused, whatever it is called.
FACT_KINDS = ("request_open", "request_closed", "no_receipt", "no_result", "owner_question_open", "last_entry", "proposal_open",
              "claim_active", "gap")


def _facts(brief: dict) -> dict[tuple, str]:
    """Every sentence the brief can support, keyed by (kind, identifier...)."""
    out: dict[tuple, str] = {}
    for r in brief.get("requests", []):
        who = ", ".join(r["to"])
        if r["state"] == "open":
            out[("request_open", r["id"])] = f"Request {r['id']} from {r['from']} to {who} ({r['action']}) is still open."
        else:
            out[("request_closed", r["id"])] = f"Request {r['id']} from {r['from']} to {who} ({r['action']}) has a result from every recipient."
        for name, info in r["recipients"].items():
            if info["status"] == "pending":
                out[("no_receipt", r["id"], name)] = f"{name} has not picked up request {r['id']}."
            elif info["status"] == "received":
                out[("no_result", r["id"], name)] = f"{name} picked up request {r['id']} but has returned no result."
    for n in brief.get("needs_you", []):
        out[("owner_question_open", n["event_id"])] = f"A question for Robert is open: entry {n['seq']}."
    for t in brief.get("teammates", []):
        if t["last"]:
            out[("last_entry", t["actor"], t["last"]["event_id"])] = (
                f"{t['actor']}'s last entry was a {t['last']['kind']} at {brief_view.human_time(t['last']['at'])} (entry {t['last']['seq']}).")
    for p in brief.get("decisions", {}).get("proposals", []):
        if p["status"] == "open":
            out[("proposal_open", p["event_id"])] = f"{p['actor']}'s proposal at entry {p['seq']} has not been settled."
    for c in brief.get("claims", []):
        if c["state"] == "active":
            out[("claim_active", c["claim_id"])] = f"{c['claimant']} holds {c['resource']} (generation {c['generation']})."
    for g in brief.get("gaps", []):
        out[("gap", g["code"], g.get("request_id") or g.get("event_id") or g.get("claim_id"))] = g["text"]
    return out


def check_summary(summary: dict, brief: dict) -> list[str]:
    """The summary lines for ``{"facts": [{"kind": ..., "id": ..., "actor": ...}, ...]}``, each checked against the brief and worded
    here. Anything else is refused: free text, an unknown kind, or a fact the brief does not hold."""
    if not isinstance(summary, dict) or set(summary) != {"facts"} or not isinstance(summary["facts"], list):
        raise BadSummary("shape")
    if not 1 <= len(summary["facts"]) <= 12:
        raise BadSummary("fact_count")
    known = _facts(brief)
    lines, seen = [], set()
    for fact in summary["facts"]:
        if not isinstance(fact, dict) or fact.get("kind") not in FACT_KINDS or set(fact) - {"kind", "id", "actor", "recipient", "code"}:
            raise BadSummary("bad_fact")
        kind = fact["kind"]
        if kind in ("no_receipt", "no_result"):
            key = (kind, fact.get("id"), fact.get("recipient"))
        elif kind == "last_entry":
            key = (kind, fact.get("actor"), fact.get("id"))
        elif kind == "gap":
            key = (kind, fact.get("code"), fact.get("id"))
        else:
            key = (kind, fact.get("id"))
        if key not in known:
            raise BadSummary("fact_not_in_the_brief")
        if key not in seen:
            seen.add(key)
            lines.append(known[key])
    return lines


def summarise(brief: dict, profiles: dict, caller: Callable[[dict, dict], dict], purpose: str = "routine") -> tuple[str, dict]:
    """(base report + the verified highlights below it, the ``model`` object to record). Raises ProfileMissing with no profile and
    BadSummary if the model's answer is anything but facts the brief holds. ``caller(profile, brief)`` is the only thing that
    touches a model and is injected, so nothing in this module imports or configures one."""
    profile = profile_mod.require(profiles, purpose)
    lines = check_summary(caller(profile, brief), brief)
    text = report(brief) + "\nHighlights (chosen by a model; every line is generated from the brief, not written by it):\n"
    return text + "".join(f"- {line}\n" for line in lines), profile


def refresh_request(to: list[str], text: str = "Where are you on this? One line: done, in progress, or blocked.") -> dict:
    """The ordinary addressed request a refresh is. The caller appends it as ``journeyman``."""
    return {"actor": "journeyman", "kind": "request", "item": "topic:checkin", "topic": "checkin", "recipients": list(to), "text": text,
            "payload": {"action": "refresh", "expected_result": "One line: done, in progress, or blocked."}}
