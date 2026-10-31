"""Separate internal Flask application; never mounted on the browser portal.

The runner owns network/process isolation. Key profiles and Board owner/store
are supplied by trusted installation code. Model text supplies no identity.
"""
from flask import Flask, request, Response
from jsonschema import Draft202012Validator

from core.domain_api.auth import Authenticator
from core.domain_api.contract import DomainError, decode, packed, require
from core.domain_api.flask_adapter import verify_request
from core.domain_api.protocol import response_signature
from domains.guild.board_operations import operations, LABELS
from .board_actions import BoardActions, BoardContext, BoardRefused
from domains.guild.board_text import TextRefused, LIMIT, CONVENTION
from .stores import FloorStoreUnavailable


def create_app(*, store, owner, environment, cursor_key, registry, item_allowed=None, authenticator=None):
    app = Flask("minimoi_board_internal")
    auth = authenticator or Authenticator(registry.lookup, domain="guild", environment=environment,
                                         startup_quarantine=33)
    rows = operations()
    all_caps = frozenset(op.capability for op in rows)

    def route(op):
        def invoke(**refs):
            admitted = None
            def reply(value, status):
                raw = packed(value)
                headers = {"Cache-Control": "no-store", "Content-Type": "application/json"}
                if admitted is not None:
                    headers["X-MiniMoi-Receipt"] = response_signature(admitted.profile, nonce=admitted.nonce,
                                                                       status=status, raw=raw)
                return Response(raw, status=status, headers=headers)
            try:
                with registry.admission():
                    admitted, raw = verify_request(auth, request, capability=op.capability)
                    require(admitted.profile.principal == owner, "scope_denied", 403)
                    # This API represents separately provisioned applications; never a forged native MC call.
                    require(admitted.profile.actor not in {"mc", "mc-chat", "master_craftsman"}, "native_actor_refused", 403)
                    body = {} if not raw else decode(raw)
                    require(not (set(body) - set(op.arguments.get("properties", {}))), "unknown_fields", 400)
                    require(not list(Draft202012Validator(op.arguments).iter_errors(body)), "arguments_invalid", 422)
                    context = BoardContext(owner, environment, store.floor, admitted.profile.actor,
                                           "headless:" + admitted.nonce, admitted.profile.capabilities)
                    service = BoardActions(store, owner=owner, environment=environment, cursor_key=cursor_key,
                        item_allowed=item_allowed,
                        transaction_guard=lambda q, cap: auth.revalidate(admitted, capability=cap))
                    auth.revalidate(admitted, capability=op.capability)
                    if op.id == "guild.board.notes.query":
                        result = service.query(context, **body)
                    elif op.id == "guild.board.note.read":
                        result = service.read(context, refs["note_id"])
                    elif op.id == "guild.board.note.create":
                        result = service.create(context, admitted.operation_id, body)
                    elif op.id == "guild.board.note.update":
                        result = service.update(context, admitted.operation_id, refs["note_id"],
                                                body["expected_revision"], body["changes"])
                    elif op.id == "guild.board.operation.read":
                        result = service.result(context, refs["operation_id"])
                    elif op.id == "guild.board.operation.undo":
                        result = service.undo(context, admitted.operation_id, refs["operation_id"], body["expected_revision"])
                    else:
                        result = {"version": 1, "capabilities": sorted(admitted.profile.capabilities & all_caps),
                                  "labels": list(LABELS), "text_limit": LIMIT, "count_convention": CONVENTION}
                    require(not list(Draft202012Validator(op.result).iter_errors(result)), "result_unconfirmed", 503)
                    if result.get("state") == "not_applied":
                        code = result["code"]
                        status = 404 if code in {"note_not_found", "operation_not_found"} else 409
                        return reply({"error": code, "state": "not_applied"}, status)
                    if result.get("state") == "unknown" and result.get("code") == "board_unavailable":
                        return reply({"error": "board_unavailable", "state": "unknown"}, 503)
                    return reply(result, 200)
            except DomainError as exc:
                # Guard/response failures after dispatch are conservatively unknown.
                state = "unknown" if exc.status >= 500 else exc.state
                return reply({"error": exc.code, "state": state}, exc.status)
            except (BoardRefused, TextRefused) as exc:
                return reply({"error": str(exc), "state": "not_applied"}, 404 if str(exc) == "note_not_found" else 422)
            except FloorStoreUnavailable:
                return reply({"error": "board_unavailable", "state": "unknown"}, 503)
            except Exception:
                # Never leak note bodies, credentials, connection strings or paths.
                return reply({"error": "board_unconfirmed", "state": "unknown"}, 503)
        return invoke

    for op in rows:
        path = op.path.replace("{note_id}", "<note_id>").replace("{operation_id}", "<operation_id>")
        app.add_url_rule(path, op.handler, route(op), methods=[op.method], provide_automatic_options=False)
    return app
