"""The topic Workshop's window onto the shared Workshop record (the journal), read only.

A topic on this page and a topic in the Workshop journal are different things with different IDs, so the link between them is an
explicit, owner-made file (``topics/<tid>/record_link.json``: which workshop, which journal topic). Nothing is guessed from titles.
This module reads the journal through the Workshop backend (``core.workshop_journal``) as a **replica**: the portal sees the copy
that ``workshop.py sync`` puts in ``MINIMOI_WORKSHOPS_DIR`` (events and state, no lock file) and can never write to it.

What the page is told is deliberately plain and honest: a journal that is damaged is *damaged* (no entries are shown as if whole),
one that is mid-write is *incomplete*, a person's approval that no owner control confirms is *claimed, not confirmed*, a result is a
worker's report with a count of what came after it, and a missing record is *unavailable*, never an empty page. Answering a
question from this page is not connected yet and says so. No model, network or shell is touched.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from core.workshop_journal import brief as brief_view
from core.workshop_journal import schema
from core.workshop_journal.errors import JournalError
from core.workshop_journal.journal import Journal

LINK_FILE = "record_link.json"
ROW_LIMIT = 400
NOT_YET = "Answering from this page is not connected yet. Reply in the chat, or record the answer with the Workshop helper."


def home() -> str | None:
    """Where the synced workshops live (the same folder the host screen reads), or None when none is configured."""
    return os.environ.get("MINIMOI_WORKSHOPS_DIR") or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── the link ────────────────────────────────────────────────────────────────────────────────────────────────────────
def get_link(store, tid: str) -> dict | None:
    got = store._read_json(os.path.join(store._tdir(tid), LINK_FILE))
    if not got or not schema.WORKSHOP_RE.fullmatch(str(got.get("workshop", ""))) or not schema.TOPIC_RE.fullmatch(str(got.get("topic", ""))):
        return None
    return {"workshop": got["workshop"], "topic": got["topic"], "linked_at": got.get("linked_at"), "linked_by": got.get("linked_by")}


def set_link(store, tid: str, principal: str, workshop: str, topic: str) -> dict:
    from .topics import Refused
    if not schema.WORKSHOP_RE.fullmatch(workshop or "") or not schema.TOPIC_RE.fullmatch(topic or ""):
        raise Refused("invalid", "That workshop or topic name is not valid.")
    with store._lock():
        store._topic(tid, principal)
        link = {"v": 1, "workshop": workshop, "topic": topic, "linked_at": _now(), "linked_by": principal}
        store._write_json(os.path.join(store._tdir(tid), LINK_FILE), link)
    return get_link(store, tid)


def clear_link(store, tid: str, principal: str) -> None:
    """Unlinking sets the file aside (renamed, never deleted), so the earlier link stays on disk."""
    with store._lock():
        store._topic(tid, principal)
        path = os.path.join(store._tdir(tid), LINK_FILE)
        if os.path.exists(path):
            os.replace(path, f"{path}.unlinked-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}")


# ── the journal ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _journal(workshop: str) -> Journal | None:
    root = home()
    if not root:
        return None
    try:
        return Journal(root, workshop, replica=True)
    except JournalError:
        return None


def candidates(workshop: str) -> list[dict]:
    """The topics that exist in a workshop's journal (name and number of entries), to choose from when linking."""
    j = _journal(workshop)
    if j is None:
        return []
    read = j.read(deep=False)
    if read.status not in ("ok", "torn_tail", "tail_in_progress"):
        return []
    counts: dict[str, list] = {}
    for e in read.events:
        if e.get("v") == 2 and e.get("topic"):
            row = counts.setdefault(e["topic"], [0, e["at"]])
            row[0] += 1
            row[1] = e["at"]
    return [{"topic": t, "entries": n, "last_at": at} for t, (n, at) in sorted(counts.items(), key=lambda kv: kv[1][1], reverse=True)]


def _author(actor: str) -> dict:
    return {"name": actor, "kind": "owner" if actor == "robert" else "mc" if actor in ("mc", "journeyman") else "agent"}


def _refs(ev: dict) -> list[dict]:
    out = []
    for r in ev.get("refs") or []:
        label = r.get("locator") or r.get("id") or ""
        kept = r.get("availability") == "retained"
        out.append({"type": r.get("type") or "ref", "ref": f"{label}{'' if kept else ' (not kept)'}", "sha256": r.get("sha256"), "kept": kept})
    return out


def _status_note(status: str) -> str | None:
    return {"missing": "The shared record for this topic is not on this server yet (nothing has been synced).",
            "damaged": "The shared record is damaged, so no entries are shown rather than a partial or wrong picture. Nothing was changed.",
            "incomplete": "The shared record is being written or was cut off; the newest entries may be missing from this view.",
            "unavailable": "The shared record could not be read right now.",
            "unconfigured": "No shared record is configured on this server.",
            "unlinked": "This topic is not linked to a shared record."}.get(status)


def record(tid: str, link: dict | None, *, since_seq: int | None = None) -> dict:
    """Everything the Record pane needs for one topic, never raising: a state, a plain sentence for any state that is not ``ok``, and
    the rows. ``rows`` are oldest first; the page reverses them as it does for its own journal."""
    base = {"v": 1, "topic_id": tid, "link": link, "observed_at": _now(), "answer_note": NOT_YET, "rows": [], "waiting_for_you": []}
    if link is None:
        return {**base, "status": "unlinked", "notice": _status_note("unlinked")}
    if not home():
        return {**base, "status": "unconfigured", "notice": _status_note("unconfigured")}
    j = _journal(link["workshop"])
    if j is None:
        return {**base, "status": "unavailable", "notice": _status_note("unavailable")}
    # ONE read of the journal, and everything below is built from that single list of events. The brief, the history and the badges can
    # therefore never disagree with each other when the synced copy changes under the page, and a refusal is never turned into an empty list.
    read = j.read(deep=False)
    if read.status in ("missing", "unsafe_root", "unsupported_writer", "corrupt"):
        status = {"missing": "missing", "corrupt": "damaged"}.get(read.status, "unavailable")
        return {**base, "status": status, "notice": _status_note(status), "journal_reason": read.reason}
    try:
        brief = brief_view.build(j.id, read.events, topic=link["topic"], resolver=j.resolver, journal_status=read.status, since_seq=since_seq)
        history = brief_view.history(read.events, topic=link["topic"])
    except Exception:                                                  # an unreadable view is a refusal, never an empty page
        return {**base, "status": "unavailable", "notice": _status_note("unavailable")}
    hist = {"events": history}
    unconfirmed = {x["event_id"] for x in brief["decisions"]["unconfirmed"]}
    proposal_status = {p["event_id"]: p["status"] for p in brief["decisions"]["proposals"]}
    owner_ok = {x["event_id"] for x in brief["decisions"]["owner"]}
    newer = {r["request_id"]: r["newer_entries_in_scope"] for r in brief["results"]}
    events = {e["event_id"]: e for e in read.events if e.get("v") == 2}
    rows = []
    shown = hist["events"][-ROW_LIMIT:]
    for h in shown:
        ev = events.get(h["event_id"], {})
        badges = []
        if h["event_id"] in unconfirmed:
            badges.append({"text": "claimed approval, not confirmed", "tone": "warn"})
        elif h["event_id"] in owner_ok:
            badges.append({"text": "owner decision, confirmed", "tone": "ok"})
        elif h["event_id"] in proposal_status:
            badges.append({"text": f"proposal · {proposal_status[h['event_id']]}", "tone": "warn" if proposal_status[h["event_id"]] == "open" else ""})
        if h["kind"] == "result":
            n = newer.get((ev.get("payload") or {}).get("request_id"), 0)
            badges.append({"text": "worker report" + (f" · {n} newer entr{'y' if n == 1 else 'ies'} since" if n else ""), "tone": "warn" if n else ""})
        rows.append({"id": h["event_id"], "seq": h["seq"], "at": h["at"], "kind": h["kind"], "author": _author(h["actor"]), "text": h["text"] or "",
                     "refs": _refs(ev), "badges": badges, "via": "journal"})
    status = "incomplete" if brief["journal_status"] in ("torn_tail", "tail_in_progress") else "ok"
    return {**base, "status": status, "notice": _status_note(status), "as_of": brief["as_of"], "complete": status == "ok",
            "rows": rows, "rows_total": len(hist["events"]), "rows_shown": len(rows),
            "waiting_for_you": [{"id": n["event_id"], "seq": n["seq"], "at": n["at"], "text": n["text"]} for n in brief["needs_you"]],
            "teammates": [{"actor": t["actor"], "last": t["last"], "note": t["note"]} for t in brief["teammates"]],
            "requests": brief["requests"], "decisions": {"unconfirmed": brief["decisions"]["unconfirmed"], "owner": brief["decisions"]["owner"]},
            "gaps": brief["gaps"], "blocked": brief["blocked"], "next": brief["next"], "claims": brief["claims"],
            "changed_since": brief.get("changed_since")}
