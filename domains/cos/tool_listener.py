"""The bound CoS tool listener the agent's sealed plugin calls (F2 design v0.2 D3, v0.3; step 2).

    POST /internal/cos/v1/tools/<tool>
    Authorization: Bearer <listener credential>     the listener keeps only its SHA-256
    X-CoS-Session:   <ctx.sessionKey>                host context, never a model argument
    X-CoS-Agent:     <ctx.agentId>
    X-CoS-Run:       <OpenClaw run id>               sealed by the plugin's before_tool_call hook
    X-CoS-Tool-Call: <host tool-call id>             likewise
    X-CoS-Instance:  <the plugin's gateway-process id>
    body: {"arguments": {...}}

    POST /internal/cos/v1/instance                   the plugin announces its gateway process
    body: {"instance": "<32 hex>"}

    POST /internal/cos/v1/served                     the plugin reports the model call a run completed (provider, model, api),
    body: {"provider": "...", "model": "..."}        from the runtime's model_call_ended hook. Evidence only.

    POST /internal/cos/v1/native                     the plugin's hook reports one native write/edit/exec call in an
    body: {"tool": "...", "target": "..."}           owner turn, with the same sealed headers. Logged, never refused
                                                     (Robert, Q1); the answer never changes what the agent may do.

The listener credential authenticates the plugin, not a turn: identity comes only from resolving the sealed run and
call ids to a live binding (``domains/cos/binding.py``). Writes go to the actions tool v2 (``actions_v2.serve``),
which takes who, why, where and when from that binding; the store receipt is linked back to the call.

It runs inside cos-scheduler on its own port, published only on the agent network, never through the portal.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from domains.cos.binding import NotBound
from domains.cos.board_tool import MUTATIONS as board_mutations
from domains.cos.store import actions_v2

PREFIX = "/internal/cos/v1"
MAX_BODY = 16 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SESSION_KEY = re.compile(r"agent:(?P<agent>[a-z][a-z0-9-]{0,63}):openai-user:(?P<user>cos-confer:[0-9a-f]{32})")
STATUSES = ("done", "refused", "unknown")
_SERVED_FIELD = re.compile(r"[A-Za-z0-9._:/-]{1,80}")
# Per-turn admission limits for each bound tool (MC-equivalent defaults, design v0.1 §4).
LIMITS = {"cos_actions": 20, "cos_tasks": 5, "cos_board": 20, "cos_send_to_mc": 3}
NATIVE_TOOLS = frozenset({"write", "edit", "exec", "apply_patch", "process"})
_log = logging.getLogger("cos_tools")


def _answer(status, code, message, data=None, http=200):
    body = {"status": status, "code": code, "message": message, "data": data or {}}
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        return jsonify(error="tool_result_too_large"), 500
    return Response(encoded, status=http, mimetype="application/json")


def _strict_json(raw: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        return None


def session_user(session_key, agent_id, allowed_agents) -> str:
    """The adapter's session user from the host-supplied session key, or NotBound."""
    if not isinstance(session_key, str) or not isinstance(agent_id, str) or len(session_key) > 200:
        raise NotBound("session_invalid")
    m = _SESSION_KEY.fullmatch(session_key)
    if m is None or m.group("agent") != agent_id or agent_id not in allowed_agents:
        raise NotBound("session_invalid")
    return m.group("user")


def create_app(*, bindings, writer, token_sha256: str, allowed_agents=("cos-agent-a", "cos-meeting"),
               wait_s: float | None = None, task_tools=None, board_tool=None, mc_send=None) -> Flask:
    """``task_tools`` (``domains.cos.task_tools.CosTaskTools``) serves ``cos_tasks`` and ``board_tool``
    (``domains.cos.board_tool.CosBoardTool``) serves ``cos_board``; ``mc_send`` (``domains.cos.mc_send.CosMcSend``) serves
    ``cos_send_to_mc``; None: that tool is refused."""
    if type(token_sha256) is not str or not _DIGEST.fullmatch(token_sha256):
        raise ValueError("cos_tools_listener_config_invalid")
    if bindings.environment != writer.environment:
        raise ValueError("cos_tools_listener_environment_mismatch")
    allowed = tuple(allowed_agents)
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="tool_request_too_large"), 413

    def authorized():
        header = request.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        return 32 <= len(presented) <= 256 and secrets.compare_digest(
            hashlib.sha256(presented.encode("utf-8")).hexdigest(), token_sha256)

    def sealed():
        h = request.headers
        return dict(session_key=h.get("X-CoS-Session", ""), agent_id=h.get("X-CoS-Agent", ""),
                    run_id=h.get("X-CoS-Run") or None, tool_call_id=h.get("X-CoS-Tool-Call") or None,
                    instance_id=h.get("X-CoS-Instance") or None)

    def preflight():
        if not authorized():
            return None, (jsonify(error="tool_unauthorized"), 401)
        if request.mimetype != "application/json":
            return None, (jsonify(error="json_required"), 415)
        body = _strict_json(request.get_data(cache=False))
        if type(body) is not dict:
            return None, (jsonify(error="tool_request_invalid"), 400)
        return body, None

    @app.post(PREFIX + "/instance")
    def instance():
        body, refused = preflight()
        if refused:
            return refused
        if set(body) != {"instance"} or type(body["instance"]) is not str:
            return jsonify(error="tool_request_invalid"), 400
        try:
            bindings.register_instance(body["instance"])
        except NotBound as exc:
            return jsonify(error=exc.code), 409
        return jsonify(status="recorded")

    @app.post(PREFIX + "/served")
    def served():
        body, refused = preflight()
        if refused:
            return refused
        if set(body) - {"provider", "model", "api", "harness"} or not all(
                type(v) is str and _SERVED_FIELD.fullmatch(v) for v in body.values()) or not {"provider", "model"} <= set(body):
            return jsonify(error="tool_request_invalid"), 400
        try:
            known = bindings.record_served(run_id=sealed()["run_id"], fields=body)
        except Exception:
            _log.exception("cos served evidence: not kept")
            return jsonify(status="not_kept"), 503
        return jsonify(status="kept" if known else "ignored")

    @app.post(PREFIX + "/native")
    def native():
        body, refused = preflight()
        if refused:
            return refused
        if set(body) - {"tool", "target"} or type(body.get("tool")) is not str:
            return jsonify(error="tool_request_invalid"), 400
        ids = sealed()
        try:
            bound = bindings.log_native(run_id=ids["run_id"], tool_call_id=ids["tool_call_id"],
                                        tool=body["tool"] if body["tool"] in NATIVE_TOOLS else "other:" + body["tool"][:48],
                                        target=body.get("target") if isinstance(body.get("target"), str) else None)
        except Exception:
            _log.exception("cos native call: not logged")
            return jsonify(status="not_logged"), 503
        return jsonify(status="logged", bound=bound)

    @app.post(PREFIX + "/tools/<name>")
    def call(name):
        body, refused = preflight()
        if refused:
            return refused
        if set(body) != {"arguments"} or type(body["arguments"]) is not dict:
            return jsonify(error="tool_request_invalid"), 400
        if name not in LIMITS:
            return _answer("refused", "tool_unknown", "That tool is not served here, so nothing was done.")
        if name == "cos_tasks" and task_tools is None:
            return _answer("refused", "tool_unavailable", "Task tracking is not connected here, so nothing was done.")
        if name == "cos_board" and board_tool is None:
            return _answer("refused", "tool_unavailable", "The Board is not connected here, so nothing was done.")
        if name == "cos_send_to_mc" and mc_send is None:
            return _answer("refused", "tool_unavailable", "Sending to MC is switched off here, so nothing was sent.")
        ids = sealed()
        try:
            user = session_user(ids["session_key"], ids["agent_id"], allowed)
            kwargs = {} if wait_s is None else {"wait_s": wait_s}
            binding = bindings.resolve(run_id=ids["run_id"], tool_call_id=ids["tool_call_id"],
                                       instance_id=ids["instance_id"], agent_id=ids["agent_id"],
                                       session_user=user, **kwargs)
            args = dict(body["arguments"])
            op = args.pop("operation", None)
            if not isinstance(op, str):
                return _answer("refused", "arguments_invalid", "Name the operation, so nothing was done.")
            bound_call = bindings.admit(binding, tool=name, tool_call_id=ids["tool_call_id"],
                                        instance_id=ids["instance_id"], session_user=user, limit=LIMITS[name])
        except NotBound as exc:
            _log.info("cos tool %s refused: %s", name, exc.code)
            return _answer("refused", exc.code, "This call does not belong to a live CoS turn, so nothing was done.")
        if name == "cos_tasks":
            try:
                out = task_tools.serve(binding, op, args)
            except Exception:
                _log.exception("cos tool cos_tasks: outcome unconfirmed")
                return _answer("unknown", "tool_outcome_unconfirmed",
                               "CoS could not confirm what happened. Do not repeat it; tell Robert.")
            if out["status"] == 200:
                return _answer("done", "ok", out["body"].get("message", "Done."), out["body"])
            return _answer("refused", str(out["body"].get("error", "refused")), "Nothing was recorded.", out["body"])
        if name == "cos_send_to_mc":
            try:
                out = mc_send.serve(binding, op, args)
            except Exception:
                _log.exception("cos tool cos_send_to_mc: outcome unconfirmed")
                return _answer("unknown", "tool_outcome_unconfirmed",
                               "CoS could not confirm whether MC received it. Do not repeat it; tell Robert it is unknown.")
            answer = out["body"]
            if out["status"] == 200:
                return _answer("done", "sent", answer["message"], answer)
            if answer.get("state") == "unknown":
                return _answer("unknown", "outcome_unknown", answer["message"], answer)
            return _answer("refused", str(answer.get("error", "refused")), answer.get("message", "Nothing was sent."), answer)
        if name == "cos_board":
            try:
                out = board_tool.serve(bound_call, op, args)
            except Exception:
                _log.exception("cos tool cos_board: outcome unconfirmed")
                return _answer("unknown", "tool_outcome_unconfirmed",
                               "CoS could not confirm what happened. Do not repeat it; read the result first.")
            body, outcome = out["body"], out["outcome"]
            if out["status"] != 200:
                return _answer("refused", str(body.get("error", "refused")), "Nothing was sent to the Board.", body)
            receipt = body.get("receipt") if isinstance(body.get("receipt"), dict) else None
            if op in board_mutations and outcome == "done" and receipt and receipt.get("target_ref"):
                # Only a verified write links the call to the note; refusals and doubt never replace that link.
                try:
                    bindings.record_receipt(binding, ids["tool_call_id"], command_id=body["operation_id"],
                                            receipt_id=str(receipt["target_ref"]))
                except Exception:
                    _log.exception("cos tool cos_board: receipt link not recorded")
            code = body.get("code") or ("verified" if outcome == "done" and op in board_mutations else "ok")
            return _answer(outcome, str(code), "Board answer. Report saved only when state is verified.", body)
        try:
            out = actions_v2.serve(writer, bound_call, op, args)
            result = out["body"].get("result") if isinstance(out.get("body"), dict) else None
            if result and result.get("receipt_id"):
                bindings.record_receipt(binding, ids["tool_call_id"], command_id=result["command_id"],
                                        receipt_id=result["receipt_id"])
        except Exception:
            _log.exception("cos tool %s: outcome unconfirmed", name)
            return _answer("unknown", "tool_outcome_unconfirmed",
                           "CoS could not confirm what happened. Do not repeat it; tell Robert.")
        if out["status"] == 200:
            return _answer("done", "ok", "Done.", out["body"])
        return _answer("refused", str(out["body"].get("error", "refused")), "Nothing was saved.", out["body"])

    return app
