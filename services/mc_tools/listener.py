"""The internal tool listener the MC runtime plugin calls (design §3).

    POST /internal/mc/v1/tools/<tool name>
    Authorization: Bearer <executor credential>      the service keeps only its SHA-256
    X-MC-Session: <ctx.sessionKey>                    host context, never a model argument
    X-MC-Agent:   <ctx.agentId>
    X-MC-Run:     <OpenClaw run id>                   sealed by the plugin's before_tool_call hook (spec v1.2 §7.1)
    X-MC-Tool-Call: <host tool-call id>               likewise
    X-MC-Instance: <the plugin's gateway-process id>  announced first, below (spec v1.2 §7.1 steps 2, 4, 5)
    body: {"arguments": {...}} or, for host health, {"arguments": {}, "observation": {...}}
    House/domain descriptions and gap records may also carry "offered_tools": [...].
    This is the plugin factory's host-owned offer list, outside model arguments;
    it supplies descriptive evidence only and cannot grant admission.

    POST /internal/mc/v1/instance                     the plugin announces its gateway process, same credential
    body: {"instance": "<32 hex>"}                    the newest is current; a replacement revokes older turns

    POST /internal/mc/v1/handoffs                     the Chief of Staff hands Robert's request to MC (A11a), with
                                                      its OWN credential; off unless configured

It authenticates the plugin and hands the call to ToolService, which binds it to the owner's live
turn. It runs on its own port inside the portal process; deployment publishes that port to the
runtime host's loopback only and never routes it through the public tunnel.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

PATH = "/internal/mc/v1/tools/<name>"
INSTANCE_PATH = "/internal/mc/v1/instance"
HANDOFF_PATH = "/internal/mc/v1/handoffs"
MAX_BODY = 16 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_log = logging.getLogger("mc_tools")


def _duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def create_app(service, *, token_sha256: str, handoff=None, handoff_token_sha256: str | None = None,
               agent_worker_token_sha256: str | None = None) -> Flask:
    """``handoff(body) -> acknowledgement`` (core.mc_tools.handoffs.receive bound to this mount), authenticated by the
    Chief of Staff's own credential, never MC's executor token. Both or neither."""
    if type(token_sha256) is not str or not _DIGEST.fullmatch(token_sha256):
        raise ValueError("mc_tools_listener_config_invalid")
    if (handoff is None) != (handoff_token_sha256 is None) or (
            handoff_token_sha256 is not None and (not _DIGEST.fullmatch(handoff_token_sha256)
                                                  or handoff_token_sha256 == token_sha256)):
        raise ValueError("mc_tools_handoff_config_invalid")
    app = Flask(__name__)
    if agent_worker_token_sha256 is not None and (not _DIGEST.fullmatch(agent_worker_token_sha256) or
            agent_worker_token_sha256 in (token_sha256,handoff_token_sha256) or getattr(service,'agent_contact',None) is None):
        raise ValueError('mc_agent_worker_config_invalid')
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="tool_request_too_large"), 413

    @app.get("/healthz")
    def health():
        return jsonify(status="enabled")

    def authorized(digest=token_sha256):
        header = request.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        return 32 <= len(presented) <= 256 and secrets.compare_digest(
            hashlib.sha256(presented.encode("utf-8")).hexdigest(), digest)

    @app.post(HANDOFF_PATH)
    def handoffs():
        if handoff is None:
            return jsonify(error="not_found"), 404
        if not authorized(handoff_token_sha256):
            return jsonify(error="handoff_unauthorized"), 401
        if request.mimetype != "application/json":
            return jsonify(error="json_required"), 415
        body = json_body()
        if body is None:
            return jsonify(error="handoff_invalid"), 400
        from core.mc_tools.handoffs import HandoffRefused, HandoffUnknown
        try:
            ack = handoff(body)
        except HandoffUnknown:
            return jsonify(state="unknown"), 503                    # not verified: retry the same transfer id
        except HandoffRefused as exc:
            code = str(exc)
            return (jsonify(state="refused", code=code), 200) if code != "handoff_invalid" else \
                (jsonify(error=code), 400)
        except Exception:
            _log.exception("mc handoff: outcome unconfirmed")
            return jsonify(state="unknown"), 503
        _log.info("mc handoff %s delivered", ack["transfer_id"][:8])
        return jsonify(ack)

    def json_body():
        try:
            return json.loads(request.get_data(cache=False).decode("utf-8"), object_pairs_hook=_duplicate_keys)
        except (ValueError, UnicodeError, RecursionError):
            return None

    @app.post('/internal/mc/v1/agent-worker')
    def agent_worker():
        if agent_worker_token_sha256 is None:
            return jsonify(error='not_found'),404
        if not authorized(agent_worker_token_sha256):
            return jsonify(error='agent_worker_unauthorized'),401
        if request.mimetype != 'application/json':
            return jsonify(error='json_required'),415
        body = json_body()
        try:
            watch = getattr(service, 'task_watch', None)
            if type(body) is dict and str(body.get('phase', '')).startswith('task_'):
                if watch is None: raise ValueError('task_watch_unavailable')
                answer = watch.worker(service, body)
            else:
                answer = service.agent_contact.worker(service,body)
                if watch is not None and body == {'phase':'poll'}:
                    # Alternate task/contact slots: task observation cannot be
                    # starved by an indefinitely pending contact pair.
                    task_jobs = watch.poll(service)
                    if task_jobs:
                        answer['requests'] = [task_jobs[0], *answer['requests'][:1], *task_jobs[1:]][:20]
            service.agent_contact.deliver_notices(service)
            if watch is not None: watch.deliver_notices(service)
            return jsonify(answer)
        except ValueError:
            return jsonify(error='agent_worker_refused'),409
        except Exception:
            # No raw action, native output, credential or exception detail in logs.
            return jsonify(error='agent_worker_unknown'),503

    @app.post(INSTANCE_PATH)
    def instance():
        if not authorized():
            return jsonify(error="tool_unauthorized"), 401
        if request.mimetype != "application/json":
            return jsonify(error="json_required"), 415
        body = json_body()
        if type(body) is not dict or set(body) != {"instance"} or type(body["instance"]) is not str:
            return jsonify(error="tool_request_invalid"), 400
        from core.minimoi_actions.context import ActionContractError
        try:
            replaced = service.register_instance(body["instance"])
        except ActionContractError as exc:
            _log.warning("mc tools: runtime instance %s refused (%s)", body["instance"][:8], exc)
            return jsonify(error=str(exc)), 409
        except Exception:
            _log.exception("mc tools: runtime instance not recorded")
            return jsonify(error="instance_unrecorded"), 503
        _log.info("mc tools: runtime instance %s%s", body["instance"][:8], " (replaced the previous one)" if replaced else "")
        return jsonify(status="recorded")

    @app.post(PATH)
    def call(name):
        if not authorized():
            return jsonify(error="tool_unauthorized"), 401
        if request.mimetype != "application/json":
            return jsonify(error="json_required"), 415
        body = json_body()
        if body is None:
            return jsonify(error="tool_request_invalid"), 400
        if type(body) is not dict or not set(body) <= {"arguments", "observation", "offered_tools", "host_completion", "artifact_request"} or "arguments" not in body:
            return jsonify(error="tool_request_invalid"), 400
        evidence = {}
        from core.mc_tools.host_files import TOOLS as HOST_TOOLS
        from core.mc_tools.host_artifacts import TOOLS as ARTIFACT_TOOLS
        from core.mc_tools.agent_contact import TOOLS as AGENT_TOOLS
        if "artifact_request" in body:
            if name not in ARTIFACT_TOOLS or body["artifact_request"] is None:
                return jsonify(error="tool_request_invalid"), 400
            evidence["artifact_request"] = body["artifact_request"]
        if "host_completion" in body:
            if name not in HOST_TOOLS or body["host_completion"] is None:
                return jsonify(error="tool_request_invalid"), 400
            evidence["host_completion"] = body["host_completion"]
        if "offered_tools" in body:
            from core.mc_tools.availability import DESCRIBING_TOOLS, offered_list
            try:
                if name not in DESCRIBING_TOOLS | HOST_TOOLS | ARTIFACT_TOOLS | AGENT_TOOLS | {"minimoi_recall"} or body["offered_tools"] is None:
                    raise ValueError("offered_tools_invalid")
                offered_list(body["offered_tools"])
            except ValueError:
                return jsonify(error="tool_request_invalid"), 400
            evidence["offered_tools"] = body["offered_tools"]
        try:
            answer = service.call(name, body["arguments"], observation=body.get("observation"),
                                  session_key=request.headers.get("X-MC-Session", ""),
                                  agent_id=request.headers.get("X-MC-Agent", ""),
                                  run_id=request.headers.get("X-MC-Run") or None,
                                  tool_call_id=request.headers.get("X-MC-Tool-Call") or None,
                                  instance_id=request.headers.get("X-MC-Instance") or None, **evidence)
        except Exception:
            # A connection loss may hide a committed effect: the receipt says what happened; never auto-retry.
            _log.exception("mc tool %s: outcome unconfirmed", name[:64])
            answer = {"status": "unknown", "code": "tool_outcome_unconfirmed",
                      "message": ("Read could not be confirmed. No mutation was requested. You may retry this read; do not infer its result."
                                  if name == "minimoi_github_issues" or name in HOST_TOOLS else
                                  "MiniMoi could not confirm what happened. Do not repeat it; tell Robert."), "data": {}}
        _log.info("mc tool %s -> %s/%s", name[:64], answer["status"], answer["code"])
        encoded = json.dumps(answer, ensure_ascii=False, separators=(",", ":"), default=str)
        if len(encoded.encode("utf-8")) > 64 * 1024:
            return jsonify(error="tool_result_too_large"), 500
        return Response(encoded, status=200, mimetype="application/json")

    return app


def serve(app, *, host: str, port: int) -> threading.Thread:
    """Start the listener on its own thread (daemon); the portal process owns it."""
    from werkzeug.serving import make_server
    server = make_server(host, port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="mc-tools-listener", daemon=True)
    thread.start()
    return thread
