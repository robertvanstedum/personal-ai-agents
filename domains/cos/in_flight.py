"""CoS's In flight table: two routes in the CoS app over the CoS store (Review 06, slice 3; the UI note of 1 Nov 2026).

    GET  /ui/api/in-flight          the open rows (folded), and the closed ones grouped by week (Earlier)
    POST /ui/api/in-flight/<op>     op = open, update, comment or close: one store command, in Robert's name

Owner only. A read needs the portal's owner identity; a write also needs the portal's verified-write marker, which the
portal adds only after its owner and CSRF gates (minimoi_portal/app.py cos_proxy). No browser call reaches the store.

Each write is one in_flight.* command (contracts/cos/v1/store-command.schema.json) through the CoS app's own writer,
the one the app already holds for memory (chief_of_staff._cos_store_writer). Provenance (spec v0.3 section 4.2): the
store's verifier gives robert_source only to words it can find verbatim in one of Robert's turns, and a click on this
page has no turn. So Robert's edits here are ``approved``, citing the page action itself ({type: cos.owner_ui, id: the
operation}), as an explicit note cites its operation; the command's actor is robert, its seat null (Robert acting
directly) and its origin owner_turn, with the same page action as its basis.

The page names each operation (op_id) and its time (at), so a retry replays the first receipt instead of writing twice.
When the store isn't configured on this host, the read says so and every write is refused: no rows are invented.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from core.canonical_json import digest

NOT_CONFIGURED = "In flight isn't recorded on this host yet."
_OPS = ("open", "update", "comment", "close")
_OP_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_ITEM_ID = re.compile(r"^IF-[A-Za-z0-9_-]{1,64}$")
_TZ = ZoneInfo("America/Chicago")
_KINDS = ("follow-up", "research", "decision", "risk")
_STATUSES = ("open", "doing", "waiting", "done")
_WITH = ("robert", "cos", "mc")
_MESSAGES = {
    "transition_not_allowed": "That row has moved on (it is closed, or not on the table). Reload to see it as it is.",
    "command_id_reused": "This change was already sent with different words. Reload and try again.",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(at: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    return when if when.tzinfo is not None else None


def _week_label(start: date, this_week: date) -> str:
    if start == this_week:
        return "This week"
    if start == this_week - timedelta(days=7):
        return "Last week"
    return f"Week of {start.day} {start.strftime('%b')}"


def _view(row: dict) -> dict:
    latest = row["comments"][-1] if row["comments"] else None
    return dict(row, latest_comment=latest)


def rows_payload(writer, now: datetime) -> dict:
    """The table as the page reads it: open rows by priority then last moved; Earlier by week, newest first."""
    rows = writer.docs("cos.in_flight")
    open_rows = [_view(r) for r in rows if r.get("closed_at") is None]
    open_rows.sort(key=lambda r: r["last_moved_at"], reverse=True)
    open_rows.sort(key=lambda r: r["priority"])
    this_week = now.astimezone(_TZ).date()
    this_week -= timedelta(days=this_week.weekday())
    weeks: dict[date, list] = {}
    for row in rows:
        closed = _parse(row.get("closed_at") or "")
        if closed is None:
            continue
        day = closed.astimezone(_TZ).date()
        weeks.setdefault(day - timedelta(days=day.weekday()), []).append(_view(row))
    earlier = []
    for start in sorted(weeks, reverse=True):
        items = sorted(weeks[start], key=lambda r: r["closed_at"], reverse=True)
        earlier.append({"week_start": start.isoformat(), "label": _week_label(start, this_week), "count": len(items),
                        "rows": items})
    return {"state": "ok", "open": open_rows, "earlier": earlier, "as_of": now.isoformat(timespec="seconds")}


def _refuse(status: int, code: str, message: str):
    return jsonify({"outcome": "refused", "code": code, "message": message}), status


def _event(op: str, body: dict, item_id: str, op_id: str, at: str, environment: str) -> tuple[dict | None, str]:
    """The command's payload for one page action, or (None, why) when the page sent something the table can't hold."""
    action = {"type": "cos.owner_ui", "id": op_id}
    event = {"schema": f"minimoi.cos.in_flight_{op}/1", "data_scope": "personal", "environment": environment,
             "item_id": item_id, "at": at, "provenance": "approved", "ref": action}
    text = lambda key, limit: (body.get(key) or "").strip()[:limit] if isinstance(body.get(key), str) else ""
    if op == "open":
        title = " ".join(text("title", 400).split())[:200]
        if not title:
            return None, "Type what the row is about, then press Enter."
        event.update(kind=body.get("kind") or "follow-up", title=title, priority=body.get("priority") or 2,
                     status=body.get("status") or "open", by="robert", source=None)
        event["with"] = body.get("with") or "robert"
    elif op == "update":
        for key in ("priority", "status", "with"):
            if key in body:
                event[key] = body[key]
        if "title" in body:
            title = " ".join(text("title", 400).split())[:200]
            if not title:
                return None, "A row needs a few words. Nothing was changed."
            event["title"] = title
        if not any(k in event for k in ("title", "priority", "status", "with")):
            return None, "Nothing to change."
        event["by"] = "robert"
    elif op == "comment":
        words = text("text", 4000)
        if not words:
            return None, "Type a comment first."
        event.update(author="robert", text=words)
    else:
        event.update(outcome=body.get("outcome") or "done", by="robert")
        note = text("note", 2000)
        if note:
            event["note"] = note
    checks = (("kind", _KINDS), ("status", _STATUSES), ("with", _WITH), ("outcome", ("done", "dropped")))
    for key, allowed in checks:
        if key in event and event[key] not in allowed:
            return None, f"That {key} isn't one the table knows. Nothing was changed."
    if "priority" in event and (type(event["priority"]) is not int or not 1 <= event["priority"] <= 3):
        return None, "Priority is P1, P2 or P3. Nothing was changed."
    return event, ""


def create_blueprint(get_writer: Callable[[], object], *, is_owner: Callable[[], bool],
                     write_verified: Callable[[], bool], clock: Callable[[], datetime] = _now) -> Blueprint:
    bp = Blueprint("cos_in_flight", __name__)

    @bp.get("/ui/api/in-flight")
    def read():
        if not is_owner():
            return jsonify({"state": "not_allowed", "message": "In flight is the owner's."}), 403
        writer = get_writer()
        if writer is None:
            return jsonify({"state": "not_configured", "message": NOT_CONFIGURED})
        try:
            body = rows_payload(writer, clock())
        except OSError:
            return jsonify({"state": "unavailable",
                            "message": "In flight couldn't be read just now. Nothing is lost; try again."}), 503
        response = jsonify(body)
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @bp.post("/ui/api/in-flight/<op>")
    def write(op: str):
        if op not in _OPS:
            return _refuse(404, "not_found", "Unknown change.")
        if not is_owner():
            return _refuse(403, "not_allowed", "In flight is the owner's.")
        if not write_verified():
            return _refuse(403, "csrf", "This page is out of date. Reload it and try again.")
        writer = get_writer()
        if writer is None:
            return _refuse(503, "not_configured", NOT_CONFIGURED + " Nothing was saved.")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _refuse(400, "invalid", "Nothing to save.")
        op_id, at = body.get("op_id"), body.get("at")
        when = _parse(at) if isinstance(at, str) else None
        if not isinstance(op_id, str) or not _OP_ID.match(op_id) or when is None:
            return _refuse(400, "invalid", "This page is out of date. Reload it and try again.")
        if abs((clock() - when).total_seconds()) > 86400:
            return _refuse(400, "invalid", "This change is too old to send. Reload and try again.")
        at = when.astimezone(timezone.utc).isoformat(timespec="seconds")
        item_id = "IF-" + op_id if op == "open" else body.get("item_id")
        if not isinstance(item_id, str) or not _ITEM_ID.match(item_id):
            return _refuse(400, "invalid", "That row isn't known. Reload and try again.")
        event, why = _event(op, body, item_id, op_id, at, writer.environment)
        if event is None:
            return _refuse(422, "invalid", why)
        action = {"type": "cos.owner_ui", "id": op_id}
        for _ in range(2):                    # one retry when another change landed between the read and the write
            try:
                current = writer.get("cos.in_flight", item_id)
            except OSError:
                current = None
            command = {"schema": "minimoi.cos.store_command/1", "command_id": f"cmd-if-{op}-{op_id}",
                       "kind": f"in_flight.{op}", "target": {"type": "cos.in_flight", "id": item_id},
                       "expected_revision": None if current is None else int(current["revision"]),
                       "actor": "robert", "seat": None, "origin": "owner_turn", "basis_ref": action,
                       "payload": event, "payload_digest": digest(event), "data_scope": "personal",
                       "environment": writer.environment}
            result = writer.submit(command)
            if result["outcome"] != "conflict":
                break
        outcome = result["outcome"]
        if outcome == "accepted":
            row = writer.get("cos.in_flight", item_id)
            return jsonify({"outcome": "accepted", "replayed": result.get("replayed", False), "row": _view(row)})
        if outcome == "conflict":
            return _refuse(409, "conflict", "That row changed in another window. Reload to see it as it is.")
        code = result.get("code", outcome)
        if outcome == "refused":
            message = _MESSAGES.get(code, "That change wasn't saved. Nothing was changed.")
            return _refuse(409 if code in _MESSAGES else 422, code, message)
        return jsonify({"outcome": "failed", "code": code,
                        "message": "The store couldn't confirm that change. Nothing is claimed; try again."}), 503

    return bp
