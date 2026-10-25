"""One pending offer, proven on screen, with two fixed clocks (spec v1.2 §9.1–9.2; A02+, A03, A04).

An offer (a row in guild.mc_tool_proposals) is approvable only once its exact platform note is durably present and
read back: it is written 'prepared' with a deterministic note key and the digest of the card, the platform posts the
note under that key (idempotent: a repeat keeps one note, changed text is refused), reads it back, and only then
marks it 'published' with the note's own creation time. A crash anywhere in between leaves an offer nobody can
approve; ``reconcile`` finishes the job from the same key the next time anyone inspects it, and never posts again.

The two clocks are reviewed constants: an offer lapses PROPOSAL_WINDOW after it was made (publication does not reset
it); an approval accepted strictly before then fixes an execution deadline EXECUTION_GRACE later, and nothing new is
dispatched at or after it. The exact grant's approval and lifetime both end at that deadline, so the C07 cap of
fifteen minutes from issue is the most any approval can ever reach.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PROPOSAL_WINDOW = timedelta(minutes=10)
EXECUTION_GRACE = timedelta(minutes=5)
# An offer still unshown this long after it was made is not mid-publication: its admission ended or crashed.
UNSHOWN_GRACE = timedelta(minutes=1)
DISPLAY_TZ = "America/Chicago"                 # Robert's clock; the card always names it


def note_key(proposal_id: str) -> str:
    """The platform note's request id: fixed by the proposal, so a retry can only ever find or keep that one note."""
    return f"mc-tool-proposal-{proposal_id}"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expiry_words(expires_at: datetime, tz: str = DISPLAY_TZ) -> str:
    """'14:35 CDT (America/Chicago) on 22 Oct 2026': an absolute time with its zone, never only 'in ten minutes'."""
    local = expires_at.astimezone(ZoneInfo(tz))
    return f"{local:%H:%M} {local.tzname()} ({tz}) on {local.day} {local:%b %Y}"


def lock_conversation(cur, principal: str, environment: str, conversation_id: str) -> None:
    """Serialize every offer admission, approval and cancellation in one conversation for this transaction, so two
    calls can never both pass the one-pending check (the unique index is the last line, not the first)."""
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (json.dumps(["mc-offer", principal, environment, conversation_id]),))


def _read_time(note) -> datetime | None:
    created = note.get("created_at") if isinstance(note, dict) else None
    if isinstance(created, datetime):
        return created
    if isinstance(created, str):
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def read_back(read_shown, conversation_id: str, key: str, want_digest: str):
    """('published', note time) when the note under ``key`` holds exactly the card; ('missing', None) when there is
    no such note; ('changed', None) when it differs; ('unreadable', None) when the store cannot answer now."""
    try:
        note = read_shown(conversation_id, key)
    except Exception:
        return "unreadable", None
    if note is None:
        return "missing", None
    if not isinstance(note, dict) or not isinstance(note.get("text"), str) or digest(note["text"]) != want_digest:
        return "changed", None
    at = _read_time(note)
    return ("published", at) if at is not None else ("unreadable", None)


def mark(cur, proposal_id: str, outcome: str, at) -> str:
    """Record a read-back outcome on a prepared pending offer; returns the display state the row actually has
    afterwards (another caller may have closed it meanwhile). A missing or unreadable note leaves it prepared (never
    approvable, recoverable later); a changed one fails it for good."""
    if outcome == "published":
        cur.execute("""UPDATE guild.mc_tool_proposals SET display_state='published', published_at=%s
            WHERE proposal_id=%s AND display_state='prepared' AND state='pending'""", (at, proposal_id))
    elif outcome == "changed":
        cur.execute("""UPDATE guild.mc_tool_proposals SET display_state='failed', state='cancelled',
            closed_reason='display_changed' WHERE proposal_id=%s AND display_state='prepared' AND state='pending'""",
                    (proposal_id,))
    cur.execute("SELECT display_state FROM guild.mc_tool_proposals WHERE proposal_id=%s", (proposal_id,))
    row = cur.fetchone()
    return (row["display_state"] if isinstance(row, dict) else row[0]) if row else "failed"


def close_unshown(cur, proposal_id: str) -> None:
    """Withdraw an offer whose note never reached the page: nothing can approve it, and it must not hold the
    conversation's one pending place or be described to Robert as waiting."""
    cur.execute("""UPDATE guild.mc_tool_proposals SET display_state='failed', state='cancelled',
        closed_reason='display_failed' WHERE proposal_id=%s AND display_state='prepared' AND state='pending'""",
                (proposal_id,))


def reconcile(cur, read_shown, p) -> str:
    """For an inspected offer row ``p`` (locked by the caller): its display state, finishing a publication that a
    crash interrupted. Never posts a note: only what is already on the page can make an offer approvable."""
    if p["display_state"] != "prepared" or p["state"] != "pending" or read_shown is None:
        return p["display_state"]
    outcome, at = read_back(read_shown, p["conversation_id"], p["display_note_ref"], p["display_digest"])
    return mark(cur, str(p["proposal_id"]), outcome, at)
