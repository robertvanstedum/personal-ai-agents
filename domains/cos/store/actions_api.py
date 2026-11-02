"""Actions-only grant for CoS tools; no general store authority or external execution."""
from datetime import datetime, timezone
import hmac
from pathlib import Path
from flask import Blueprint, jsonify, request
from core.canonical_json import digest
from domains.cos.in_flight import _event, _OP_ID, _ITEM_ID, _parse

PREFIX = "/internal/cos-actions/v1"
_FIELDS = {
    "open": {"title", "kind", "priority", "status", "with"},
    "update": {"title", "kind", "priority", "status", "with"},
    "comment": {"text"}, "close": {"outcome", "note"},
}


def create_blueprint(writer, token_file):
    bp = Blueprint("cos_actions_tool", __name__)

    @bp.before_request
    def authorize():
        try:
            expected = Path(token_file).read_text().strip()
        except OSError:
            expected = ""
        supplied = request.headers.get("Authorization", "")
        if not expected or not hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
            return jsonify(error="unauthorized"), 401
        if (request.content_length or 0) > 16384:
            return jsonify(error="too_large"), 413

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store, private"
        return response

    @bp.get(PREFIX + "/items")
    def listing():
        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except ValueError:
            return jsonify(error="invalid_offset"), 400
        rows = sorted(writer.docs("cos.in_flight"), key=lambda r: r["item_id"])
        fields = ("item_id", "revision", "title", "kind", "status", "priority", "with", "closed_at", "outcome")
        return jsonify(items=[{k:r.get(k) for k in fields} for r in rows[offset:offset+50]],
                       next_offset=offset+50 if len(rows)>offset+50 else None)

    @bp.get(PREFIX + "/items/<item_id>")
    def get(item_id):
        if not _ITEM_ID.fullmatch(item_id):
            return jsonify(error="invalid_id"), 400
        row = writer.get("cos.in_flight", item_id)
        return (jsonify(row=row), 200) if row else (jsonify(error="not_found"), 404)

    @bp.post(PREFIX + "/<op>")
    def write(op):
        body = request.get_json(silent=True)
        if op not in _FIELDS or not isinstance(body, dict):
            return jsonify(error="invalid_operation"), 400
        allowed = _FIELDS[op] | {"op_id", "at", "item_id", "revision"}
        if set(body) - allowed:
            return jsonify(error="unexpected_fields"), 422
        for key, limit in (("title",200),("text",4000),("note",2000)):
            if key in body and (not isinstance(body[key],str) or len(body[key])>limit):
                return jsonify(error="text_too_long_or_invalid", field=key, max_length=limit), 422
        op_id, at = body.get("op_id"), body.get("at")
        when = _parse(at) if isinstance(at, str) else None
        if not isinstance(op_id, str) or not _OP_ID.fullmatch(op_id) or when is None:
            return jsonify(error="invalid_operation_identity"), 422
        item = "IF-" + op_id if op == "open" else body.get("item_id")
        revision = body.get("revision")
        if not isinstance(item, str) or not _ITEM_ID.fullmatch(item):
            return jsonify(error="invalid_id"), 422
        if op != "open" and (type(revision) is not int or revision < 1):
            return jsonify(error="read_current_revision_first"), 422
        if body.get("status") == "done":
            return jsonify(error="use_close_for_completion"), 422
        event, why = _event(op, dict({"kind":"action", "with":"cos"} if op == "open" else {}, **body),
                            item, op_id, when.astimezone(timezone.utc).isoformat(timespec="seconds"), writer.environment)
        if event is None:
            return jsonify(error="invalid", message=why), 422
        event["provenance"] = "agent_draft"
        event.pop("ref", None)
        event["author" if op == "comment" else "by"] = "cos"
        command = {"schema":"minimoi.cos.store_command/1", "command_id":"cmd-cos-actions-"+op_id,
                   "kind":"in_flight."+op, "target":{"type":"cos.in_flight","id":item},
                   "expected_revision":None if op == "open" else revision, "actor":"cos", "seat":"cos",
                   "origin":"consultation", "basis_ref":None, "payload":event, "payload_digest":digest(event),
                   "data_scope":"personal", "environment":writer.environment}
        result = writer.submit(command)
        if result["outcome"] == "accepted":
            return jsonify(result=result, row=writer.get("cos.in_flight", item))
        return jsonify(result=result), 409 if result["outcome"] == "conflict" else 422

    return bp
