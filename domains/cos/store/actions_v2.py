"""CoS actions tool, contract v2 (F2 step 0b): every read and write is served for one bound tool call.

v1 (`actions_api`, `/internal/cos-actions/v1`) trusts a static bearer and records every write as actor `cos`, origin
`consultation`, with no turn. v1 is frozen and is retired from agent use in F2 step 2. v2 is called only by the bound
listener in cos-scheduler, after the listener has resolved the host-sealed run and tool-call ids to a live binding.

What the agent may send is only the operation's own fields (`ARGS`). Everything that says who, why, where and when
comes from the binding (`BoundCall`), never from the body:

- `actor` and `seat` are `cos`; `origin` stays `consultation` (the row is CoS's draft, never Robert's own words, so it
  cannot gain owner-only powers such as must_not_miss);
- `environment` is the binding's, and must equal the store's;
- `command_id`, `op_id` and the new item id are derived from (environment, call id), so a replayed sealed call returns
  its first receipt and cannot open a second row;
- `at` is when the binding store first resolved this call (server clock, kept with the call), so a replay is
  byte-identical;
- `basis_ref` is `{"type": "cos.tool_call", "id": <call ref>}` for the writer's checks. The writer does not keep it;
  the durable trace is the store journal's `command_id` (`cmd-cos-v2-` + the ref's hex) and the returned
  `receipt_id`. The listener records both in the binding store with the call's turn id, run id, owner request id or
  service, audience and scope, so every row traces to the turn that caused it without changing the frozen v1
  store-command contract.

Scopes: `owner` may read and write; `meeting` may read only (F2 D8: owner-private rooms, no writes); anything else is
refused. Results are plain dicts with a status code, for the listener to send on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from core.canonical_json import digest
from domains.cos.in_flight import _ITEM_ID, _event
from domains.cos.store.writer import StoreRefused

SCHEMA = "minimoi.cos.actions_tool/2"
ARGS = {
    "list": {"offset"},
    "get": {"item_id"},
    "open": {"title", "kind", "priority", "status", "with"},
    "update": {"item_id", "revision", "title", "kind", "priority", "status", "with"},
    "comment": {"item_id", "revision", "text"},
    "close": {"item_id", "revision", "outcome", "note"},
}
READS = frozenset({"list", "get"})
WRITES = frozenset(ARGS) - READS
SCOPES = {"owner": READS | WRITES, "meeting": READS}
_ROW_FIELDS = ("item_id", "revision", "title", "kind", "status", "priority", "with", "closed_at", "outcome")
_PAGE = 50


@dataclass(frozen=True)
class BoundCall:
    """The identity of one tool call, as the resolver found it in the binding store. Never built from a request."""
    environment: str
    scope: str
    call_id: str  # host-sealed run id + tool-call id, as the binding store keys it
    at: datetime  # when the binding store first resolved this call; timezone-aware

    @property
    def ref(self) -> str:
        return "tc-" + hashlib.sha256(f"{self.environment}\0{self.call_id}".encode()).hexdigest()[:40]


def _refuse(status: int, error: str, **extra) -> dict:
    return {"status": status, "body": dict(error=error, **extra)}


def serve(writer, call: BoundCall, op: str, args: dict) -> dict:
    """Serve one bound call. `args` is the tool's argument object exactly as the agent sent it."""
    if not isinstance(call, BoundCall) or not call.call_id:
        return _refuse(403, "unbound")
    if not isinstance(call.at, datetime) or call.at.tzinfo is None:
        return _refuse(403, "unbound")
    if call.environment != writer.environment:
        return _refuse(403, "wrong_environment")
    if op not in ARGS:
        return _refuse(400, "invalid_operation")
    if op not in SCOPES.get(call.scope, frozenset()):
        return _refuse(403, "scope_read_only" if call.scope in SCOPES else "scope_not_allowed")
    if not isinstance(args, dict):
        return _refuse(422, "invalid_arguments")
    unexpected = sorted(set(args) - ARGS[op])
    if unexpected:
        return _refuse(422, "unexpected_fields", fields=unexpected)
    if op == "list":
        return _list(writer, args)
    if op == "get":
        return _get(writer, args)
    return _write(writer, call, op, args)


def _list(writer, args: dict) -> dict:
    offset = args.get("offset", 0)
    if type(offset) is not int or offset < 0:
        return _refuse(400, "invalid_offset")
    rows = sorted(writer.docs("cos.in_flight"), key=lambda r: r["item_id"])
    page = [{k: r.get(k) for k in _ROW_FIELDS} for r in rows[offset:offset + _PAGE]]
    return {"status": 200, "body": {"items": page,
                                    "next_offset": offset + _PAGE if len(rows) > offset + _PAGE else None}}


def _get(writer, args: dict) -> dict:
    item = args.get("item_id")
    if not isinstance(item, str) or not _ITEM_ID.fullmatch(item):
        return _refuse(400, "invalid_id")
    row = writer.get("cos.in_flight", item)
    return {"status": 200, "body": {"row": row}} if row else _refuse(404, "not_found")


def _write(writer, call: BoundCall, op: str, args: dict) -> dict:
    op_id = call.ref[3:43]
    item = "IF-" + op_id if op == "open" else args.get("item_id")
    revision = args.get("revision")
    if not isinstance(item, str) or not _ITEM_ID.fullmatch(item):
        return _refuse(422, "invalid_id")
    if op != "open" and (type(revision) is not int or revision < 1):
        return _refuse(422, "read_current_revision_first")
    if args.get("status") == "done":
        return _refuse(422, "use_close_for_completion")
    body = dict({"kind": "action", "with": "cos"} if op == "open" else {}, **args)
    at = call.at.astimezone(timezone.utc).isoformat(timespec="seconds")
    event, why = _event(op, body, item, op_id, at, writer.environment)
    if event is None:
        return _refuse(422, "invalid", message=why)
    event["provenance"] = "agent_draft"
    event.pop("ref", None)
    event["author" if op == "comment" else "by"] = "cos"
    command = {"schema": "minimoi.cos.store_command/1", "command_id": "cmd-cos-v2-" + op_id,
               "kind": "in_flight." + op, "target": {"type": "cos.in_flight", "id": item},
               "expected_revision": None if op == "open" else revision, "actor": "cos", "seat": "cos",
               "origin": "consultation", "basis_ref": {"type": "cos.tool_call", "id": call.ref},
               "payload": event, "payload_digest": digest(event),
               "data_scope": "personal", "environment": writer.environment}
    try:
        result = writer.submit(command)
    except StoreRefused as refused:
        return _refuse(409 if refused.code == "command_id_reused" else 422, refused.code)
    if result["outcome"] == "accepted":
        return {"status": 200, "body": {"schema": SCHEMA, "result": result, "call_ref": call.ref,
                                        "row": writer.get("cos.in_flight", item)}}
    if result["outcome"] == "refused":
        return _refuse(409 if result.get("code") == "command_id_reused" else 422, result.get("code", "refused"),
                       result=result)
    return {"status": 409 if result["outcome"] == "conflict" else 422, "body": {"schema": SCHEMA, "result": result}}
