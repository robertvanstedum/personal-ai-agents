"""The Journeyman's check-in (v0.7 Unit 7): "where are they", answered from the record, with no model call.

The report is the brief, worded plainly: the last entry from each teammate, what is waiting for the owner, which requests have no
receipt or no result, and what is overdue. No entry is "nothing reported since <time>", never "idle" or "done". A refresh is an
ordinary addressed request, so the answer comes back as a correlated result. An optional model-written summary may be appended
for reading ease; it must cite only IDs that are in the brief, it is placed *after* the base report, and it can neither remove a
gap nor add an approval. Judgment calls need a configured profile (``profiles``); routine checks need none because they use none.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from core.workshop_journal import profiles as profile_mod


def report(brief: dict) -> str:
    """The plain-language check-in from a brief (as ``Journal.brief`` returns it)."""
    if "status" in brief:
        return f"I cannot report: the journal read is {brief['status']} ({brief.get('reason') or 'no detail'}). Nothing is assumed.\n"
    out = [f"Check-in for {brief['workshop']}" + (f", topic {brief['topic']}" if brief["topic"] else "")
           + f" (as of journal entry {brief['as_of']['seq']}; journal {brief['journal_status']})."]
    for t in brief["teammates"]:
        if t["last"]:
            out.append(f"- {t['actor']}: last entry was a {t['last']['kind']} at {t['last']['at']} (entry {t['last']['seq']}); "
                       f"nothing newer has been reported since {t['last']['at']}.")
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


def cited_ids(brief: dict) -> set[str]:
    """Every ID a summary may cite: events and requests the brief mentions."""
    ids: set[str] = set()
    for key in ("requests",):
        ids |= {r["id"] for r in brief.get(key, [])}
    for key in ("needs_you", "blocked", "unaddressed"):
        ids |= {r["event_id"] for r in brief.get(key, [])}
    for t in brief.get("teammates", []):
        if t["last"]:
            ids.add(t["last"]["event_id"])
    d = brief.get("decisions", {})
    ids |= {r["event_id"] for r in d.get("proposals", [])} | {r["event_id"] for r in d.get("owner", [])}
    ids |= {r["request_id"] for r in brief.get("results", [])}
    return ids


class BadSummary(ValueError):
    pass


def check_summary(summary: dict, brief: dict) -> dict:
    """A model summary is acceptable only as ``{"text": str, "cites": [ids from the brief]}`` with at least one citation, no
    unknown ID, and no claim of approval or completion that the brief does not carry."""
    if not isinstance(summary, dict) or set(summary) != {"text", "cites"}:
        raise BadSummary("shape")
    if not isinstance(summary["text"], str) or not 1 <= len(summary["text"]) <= 2000:
        raise BadSummary("text")
    cites = summary["cites"]
    if not isinstance(cites, list) or not cites or not all(isinstance(c, str) for c in cites):
        raise BadSummary("cites_required")
    if set(cites) - cited_ids(brief):
        raise BadSummary("cites_unknown_id")
    lowered = summary["text"].lower()
    if any(word in lowered for word in ("approved", "all clear", "nothing is missing", "everything is done", "no gaps")):
        raise BadSummary("claims_what_the_brief_does_not")
    return {"text": summary["text"], "cites": list(cites)}


def summarise(brief: dict, profiles: dict, caller: Callable[[dict, dict], dict], purpose: str = "routine") -> tuple[str, dict]:
    """(base report + the checked summary below it, the ``model`` object to record). Raises ProfileMissing with no profile,
    BadSummary if the model's answer breaks the rules. ``caller(profile, brief)`` is the only thing that touches a model and is
    injected, so nothing in this module imports or configures one."""
    profile = profile_mod.require(profiles, purpose)
    checked = check_summary(caller(profile, brief), brief)
    text = report(brief) + "\nSummary (written by a model, cites " + ", ".join(checked["cites"]) + "):\n" + checked["text"] + "\n"
    return text, profile


def refresh_request(to: list[str], text: str = "Where are you on this? One line: done, in progress, or blocked.") -> dict:
    """The ordinary addressed request a refresh is. The caller appends it as ``journeyman``."""
    return {"actor": "journeyman", "kind": "request", "item": "topic:checkin", "topic": "checkin", "recipients": list(to), "text": text,
            "payload": {"action": "refresh", "expected_result": "One line: done, in progress, or blocked."}}
