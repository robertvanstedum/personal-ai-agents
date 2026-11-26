"""Owner approvals in Guildhall (#681 A/B; Lane G spec v0.2 changes 1-4).

Records owns a registry of the proposals MC and CoS publish into owner-private rooms. Routing reads the registry,
never message text:

- **Publication.** A partner's committed reply may carry ``room_proposal`` (built by the partner's platform code, not
  its model). Inside the commit's transaction Records checks the room is owner-private and the turn's addressee is
  MC or CoS, inserts the row, and posts the card as a separate ``proposal`` event under the reply. A row cannot exist
  without its card, and a card that was never published cannot be approved.
- **Routing.** An owner message in an owner-private room that is exactly a room command (``parse``) and names a
  published, unexpired code becomes an *action turn* for the row's partner, linked to the row. A code with no row,
  or an expired one, gets a routing note and no turn: nobody is called.
- **Decision.** The approval turn's committed reply carries ``room_decision``; Records records it once against the
  row (``decided_turn_id``), so a replay after a restart finds the first decision.

Authority stays with the partner's executor (MC's ToolProposals, CoS's owner confirm through the portal): Records
only says which partner published which code here, and when.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS room_proposals(
    room TEXT NOT NULL,code TEXT NOT NULL,partner TEXT NOT NULL,source_ref TEXT NOT NULL,payload_digest TEXT NOT NULL,
    summary TEXT NOT NULL,card_event TEXT NOT NULL,published_turn TEXT NOT NULL,expires_at TEXT NOT NULL,
    state TEXT NOT NULL,decided_turn TEXT,decision TEXT,created TEXT NOT NULL,updated TEXT NOT NULL,
    PRIMARY KEY(room,code));
CREATE TABLE IF NOT EXISTS room_approval_turns(
    turn_id TEXT PRIMARY KEY,room TEXT NOT NULL,code TEXT NOT NULL,kind TEXT NOT NULL);
"""

PARTNERS = {"mc": "MC", "cos": "CoS"}
CODE = re.compile(r"^P-[A-Z0-9]{4}$")
EXPIRY_S = 3600
STATES = ("published", "confirmed", "cancelled", "expired", "failed", "refused")
DECISIONS = {"confirmed", "cancelled", "failed", "refused"}
# The whole message: an optional teammate name, yes or no, the code, an optional full stop. Nothing else.
_COMMAND = re.compile(
    r"^\s*(?:@?(?P<who>mc|cos|master\s+craftsman|chief\s+of\s+staff)\s*[,:]?\s+)?"
    r"(?P<word>yes|no)\s*[,:]?\s+(?:proposal\s+)?p-(?P<code>[a-z0-9]{4})\s*[.!]?\s*$", re.I)
_WHO = {"mc": "mc", "master craftsman": "mc", "cos": "cos", "chief of staff": "cos"}


def parse(text):
    """(kind, code, named) for an exact room command, else None. kind is 'confirm' or 'cancel'; named is the partner
    the owner addressed ('mc', 'cos') or None. Anything more ("yes P-7K3M, wait", two codes, a question) is not a
    command: it routes as an ordinary message and never cancels anything."""
    if not isinstance(text, str) or len(text) > 80:
        return None
    m = _COMMAND.match(text)
    if not m:
        return None
    who = m.group("who")
    named = _WHO[re.sub(r"\s+", " ", who.lower())] if who else None
    return ("confirm" if m.group("word").lower() == "yes" else "cancel"), "P-" + m.group("code").upper(), named


def ensure_schema(db):
    db.executescript(SCHEMA)


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat()


def owner_private(db, room, principal="robert"):
    members = [r["actor"] for r in db.execute("SELECT actor FROM members WHERE room=?", (room,))]
    return bool(members) and all(m in {principal, *PARTNERS} for m in members)


def card_text(partner, code, summary, expires_at):
    """The platform card. ``summary`` is the partner's own stored proposal text (MC's shown_text; CoS's outcome and
    done-when), never the model's reply."""
    at = datetime.fromisoformat(expires_at).astimezone(timezone.utc).strftime("%H:%M UTC")
    return (f"Proposal {code} from {PARTNERS[partner]}:\n{summary}\n\n"
            f"To approve, reply here: yes {code} (or no {code}). It expires at {at}.")


def publish(db, store, turn, reply_event, proposal):
    """Inside the commit's transaction. Raises ValueError (the reply is refused) when the proposal is malformed or
    the room is not owner-private; the caller turns that into a 409."""
    if not isinstance(proposal, dict) or set(proposal) != {"code", "source_ref", "payload_digest", "summary"}:
        raise ValueError("room_proposal_invalid")
    partner = turn["addressee"]
    code, source_ref = proposal["code"], proposal["source_ref"]
    digest, summary = proposal["payload_digest"], proposal["summary"]
    if (partner not in PARTNERS or not isinstance(code, str) or not CODE.match(code)
            or not isinstance(source_ref, str) or not 1 <= len(source_ref) <= 200
            or not isinstance(digest, str) or not 8 <= len(digest) <= 100
            or not isinstance(summary, str) or not summary.strip() or len(summary) > 6000):
        raise ValueError("room_proposal_invalid")
    if not owner_private(db, turn["room"]):
        raise ValueError("room_proposal_not_private")
    existing = db.execute("SELECT * FROM room_proposals WHERE room=? AND code=?", (turn["room"], code)).fetchone()
    if existing is not None:
        if (existing["partner"], existing["source_ref"], existing["payload_digest"]) == (partner, source_ref, digest):
            return dict(existing)                       # the same card again (a recovery delivery): no second card
        return None                                     # a code collision here: the card stays in its own app
    moment = _now()
    expires = _iso(moment + timedelta(seconds=EXPIRY_S))
    card = store._event(db, turn["room"], partner, "proposal", card_text(partner, code, summary.strip(), expires),
                        None, reply_event["id"], None, {"mode": "platform_card", "code": code})
    db.execute("INSERT INTO room_proposals VALUES(?,?,?,?,?,?,?,?,?,'published',NULL,NULL,?,?)",
               (turn["room"], code, partner, source_ref, digest, summary.strip(), card["id"], turn["id"], expires,
                _iso(moment), _iso(moment)))
    return {"code": code, "card_event": card["id"], "expires_at": expires}


def _current(db, room, code):
    row = db.execute("SELECT * FROM room_proposals WHERE room=? AND code=?", (room, code)).fetchone()
    if row is not None and row["state"] == "published" and row["expires_at"] <= _iso(_now()):
        db.execute("UPDATE room_proposals SET state='expired',updated=? WHERE room=? AND code=?",
                   (_iso(_now()), room, code))
        row = db.execute("SELECT * FROM room_proposals WHERE room=? AND code=?", (room, code)).fetchone()
    return row


def route(meetings, db, room, event, meeting, recipients_or_target=None):
    """Called first for an owner message. True when it was a room command and has been handled (a turn for the
    publishing partner, or a routing note); False to route the message as usual."""
    if meeting.get("kind") == "proof":
        return False
    parsed = parse(event["body"])
    if parsed is None or not owner_private(db, room):
        return False
    kind, code, named = parsed
    chosen = recipients_or_target if isinstance(recipients_or_target, list) else (
        [recipients_or_target] if recipients_or_target else [])
    chosen = [c.lower() for c in chosen if isinstance(c, str) and c.lower() not in ("everyone", "all")]
    if named is None and len(chosen) == 1 and chosen[0] in PARTNERS:
        named = chosen[0]                               # "To: MC" counts like "MC, yes P-XXXX"
    row = _current(db, room, code)

    def note(disposition, label=code):
        db.execute("INSERT OR IGNORE INTO routing_notes VALUES(?,?,?,?,?,?)",
                   (room, event["seq"], label.lower(), label, disposition, _iso(_now())))
        db.execute("UPDATE meetings SET cursor=MAX(cursor,?),updated=? WHERE room=?", (event["seq"], _iso(_now()), room))
        return True

    if row is None or (named is not None and named != row["partner"]):
        return note("no_such_proposal")
    if row["state"] == "expired":
        return note("proposal_expired")
    if row["state"] != "published":
        return note("proposal_already_" + row["state"])
    refused = meetings.ineligible(db, room, row["partner"])
    if refused:
        return note(refused, PARTNERS[row["partner"]])
    turn = meetings.queue_turn(db, room, row["partner"], event["seq"], 1)
    db.execute("INSERT INTO turn_order VALUES(?,?,?,?,?,NULL,NULL)", (turn, event["id"], 0, None, _iso(_now())))
    db.execute("INSERT INTO room_approval_turns VALUES(?,?,?,?)", (turn, room, code, kind))
    db.execute("UPDATE meetings SET cursor=MAX(cursor,?),updated=? WHERE room=?", (event["seq"], _iso(_now()), room))
    return True


def for_turn(db, turn_id):
    """What an approval turn's snapshot carries, or None for any other turn."""
    link = db.execute("SELECT * FROM room_approval_turns WHERE turn_id=?", (turn_id,)).fetchone()
    if link is None:
        return None
    row = db.execute("SELECT * FROM room_proposals WHERE room=? AND code=?", (link["room"], link["code"])).fetchone()
    if row is None:
        return None
    return {"code": row["code"], "kind": link["kind"], "partner": row["partner"], "source_ref": row["source_ref"],
            "payload_digest": row["payload_digest"], "expires_at": row["expires_at"], "state": row["state"],
            "decided_turn": row["decided_turn"],
            "decision": json.loads(row["decision"]) if row["decision"] else None}


def decide(db, turn, decision):
    """Inside the commit's transaction: record the approval turn's outcome once."""
    link = db.execute("SELECT * FROM room_approval_turns WHERE turn_id=?", (turn["id"],)).fetchone()
    if link is None:
        raise ValueError("room_decision_without_approval_turn")
    if (not isinstance(decision, dict) or set(decision) - {"code", "state", "receipt"}
            or decision.get("code") != link["code"] or decision.get("state") not in DECISIONS
            or not isinstance(decision.get("receipt", ""), str) or len(decision.get("receipt", "")) > 500):
        raise ValueError("room_decision_invalid")
    row = db.execute("SELECT * FROM room_proposals WHERE room=? AND code=?", (link["room"], link["code"])).fetchone()
    if row is None:
        raise ValueError("room_decision_invalid")
    if row["decided_turn"] is not None:
        return                                          # first decision stands; a replay changes nothing
    final = decision["state"] if decision["state"] != "refused" else "published"   # a refusal leaves it approvable
    if decision["state"] == "refused":
        db.execute("UPDATE room_proposals SET decision=?,updated=? WHERE room=? AND code=?",
                   (json.dumps(decision, sort_keys=True), _iso(_now()), link["room"], link["code"]))
        return
    db.execute("UPDATE room_proposals SET state=?,decided_turn=?,decision=?,updated=? WHERE room=? AND code=?",
               (final, turn["id"], json.dumps(decision, sort_keys=True), _iso(_now()), link["room"], link["code"]))
