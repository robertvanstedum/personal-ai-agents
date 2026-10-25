"""MC's receiving side of a CoS -> MC handoff (spec v1.2 §16.1 "Handoff boundary"; A11a).

The Chief of Staff, after Robert's explicit yes, transfers his original request. MiniMoi persists the transfer first,
delivers it as a platform note in a Guild conversation for Robert (deterministic note key, read back), and only then
acknowledges, so CoS can truthfully say "handed over". Idempotent by transfer id: a retry with the same body gets the
same acknowledgement; a different body under a seen id is refused. Nothing executes from a transfer: it carries
Robert's words to Master Craftsman, and any consequential step still needs its own proposal and approval.

Contract: _working/CONTRACT_COS_TO_MC_HANDOFF_v0.1_2026-10-22.md (body shape, credential, outcomes).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from core.minimoi_actions.receipts import _cursor

_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_SHA = re.compile(r"[0-9a-f]{64}")


class HandoffRefused(Exception):
    """A fixed code; proven before anything was delivered (invalid, not for this owner, id reused)."""


class HandoffUnknown(Exception):
    """Delivery was attempted but not verified: the transfer stays received; the sender keeps it pending and retries
    with the same id (Codex H1). Never reported as refused."""


def _aware_time(value: str) -> bool:
    from datetime import datetime
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def canonical(body) -> tuple[dict, str]:
    """(validated body, digest) or HandoffRefused. Exact words only; nothing else is accepted."""
    if type(body) is not dict or set(body) != {"transfer_id", "principal", "environment", "source", "approval", "summary"}:
        raise HandoffRefused("handoff_invalid")
    try:
        tid = str(uuid.UUID(str(body["transfer_id"])))
    except ValueError:
        raise HandoffRefused("handoff_invalid") from None
    src, ok = body["source"], body["approval"]
    if (type(src) is not dict or set(src) != {"agent", "conversation_id", "request_note_id", "request_text",
                                               "request_text_sha256"}
            or src["agent"] != "cos" or not all(isinstance(src[k], str) for k in src)
            or not _ID.fullmatch(src["conversation_id"]) or not _ID.fullmatch(src["request_note_id"])
            or not 1 <= len(src["request_text"]) <= 4000 or not _SHA.fullmatch(src["request_text_sha256"])
            or hashlib.sha256(src["request_text"].encode("utf-8")).hexdigest() != src["request_text_sha256"]):
        raise HandoffRefused("handoff_source_invalid")
    if (type(ok) is not dict or set(ok) != {"note_id", "text", "at"} or not all(isinstance(ok[k], str) for k in ok)
            or not _ID.fullmatch(ok["note_id"]) or not 1 <= len(ok["text"]) <= 500 or not 1 <= len(ok["at"]) <= 40
            or not _aware_time(ok["at"])):                       # a timezone-aware ISO time (Codex H3)
        raise HandoffRefused("handoff_approval_invalid")
    if not isinstance(body["summary"], str) or len(body["summary"]) > 200 or body["environment"] not in ("dev", "prod") \
            or not isinstance(body["principal"], str) or not body["principal"]:
        raise HandoffRefused("handoff_invalid")
    doc = {**body, "transfer_id": tid}
    return doc, hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                               .encode("utf-8")).hexdigest()


def note_text(doc) -> str:
    src, ok = doc["source"], doc["approval"]
    words = src["request_text"]
    return (f"Handed over from the Chief of Staff. Robert asked: “{words}”\n\nHe approved handing it to Master Craftsman "
            f"(“{ok['text']}”, {ok['at']}). The Chief of Staff's summary: {doc['summary'] or '(none)'}\n\n"
            "This hand-over approves nothing by itself; any change still needs its own proposal and his approval.")


def receive(connect, body, *, principal: str, environment: str, deliver, read_back, link_for) -> dict:
    """The acknowledgement dict, HandoffRefused (nothing delivered) or HandoffUnknown (not verified; retry the same id).
    ``deliver(transfer_id, title, text) -> (conversation_id, note_ref)`` keeps the note under a key fixed by the
    transfer id; ``read_back(conversation_id, note_ref) -> text | None``; ``link_for(conversation_id)`` is the fixed,
    same-origin route to that Guild conversation (Codex H2)."""
    doc, digest = canonical(body)
    if doc["principal"] != principal or doc["environment"] != environment:
        raise HandoffRefused("handoff_not_for_this_owner")
    tid = doc["transfer_id"]
    conn = connect()
    try:
        with conn, _cursor(conn) as cur:
            cur.execute("""INSERT INTO guild.mc_handoffs (transfer_id, digest, principal_id, environment, source, approval)
                VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT (transfer_id) DO NOTHING""",
                (tid, digest, principal, environment, json.dumps(doc["source"]), json.dumps(doc["approval"])))
            cur.execute("SELECT * FROM guild.mc_handoffs WHERE transfer_id=%s FOR UPDATE", (tid,))
            row = cur.fetchone()
            if row["digest"] != digest:
                raise HandoffRefused("handoff_id_reused")
            if row["state"] == "delivered":
                return _ack(row, link_for)
        text = note_text(doc)
        title = ("From Chief of Staff: " + " ".join(doc["source"]["request_text"].split()))[:80]
        try:
            conversation_id, note_ref = deliver(tid, title, text)
            delivered = read_back(conversation_id, note_ref) == text
        except Exception:
            delivered = False
        if not delivered:
            raise HandoffUnknown("handoff_unconfirmed")             # stays received; CoS keeps it pending and retries
        with conn, _cursor(conn) as cur:
            cur.execute("""UPDATE guild.mc_handoffs SET state='delivered', mc_conversation_id=%s, note_ref=%s,
                acknowledged_at=clock_timestamp() WHERE transfer_id=%s AND state='received' RETURNING *""",
                (conversation_id, note_ref, tid))
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT * FROM guild.mc_handoffs WHERE transfer_id=%s", (tid,))
                row = cur.fetchone()
            return _ack(row, link_for)
    finally:
        conn.close()


def _ack(row, link_for) -> dict:
    return {"transfer_id": str(row["transfer_id"]), "state": "delivered", "mc_conversation_id": row["mc_conversation_id"],
            "note_ref": row["note_ref"], "acknowledged_at": row["acknowledged_at"].isoformat(),
            "link": link_for(row["mc_conversation_id"])}
