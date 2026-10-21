"""The internal tool listener the MC runtime plugin calls (design §3).

    POST /internal/mc/v1/tools/<tool name>
    Authorization: Bearer <executor credential>      the service keeps only its SHA-256
    X-MC-Session: <ctx.sessionKey>                    host context, never a model argument
    X-MC-Agent:   <ctx.agentId>
    body: {"arguments": {...}} or, for host health, {"arguments": {}, "observation": {...}}

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


def create_app(service, *, token_sha256: str) -> Flask:
    if type(token_sha256) is not str or not _DIGEST.fullmatch(token_sha256):
        raise ValueError("mc_tools_listener_config_invalid")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="tool_request_too_large"), 413

    @app.get("/healthz")
    def health():
        return jsonify(status="enabled")

    @app.post(PATH)
    def call(name):
        header = request.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        if not 32 <= len(presented) <= 256 or not secrets.compare_digest(
                hashlib.sha256(presented.encode("utf-8")).hexdigest(), token_sha256):
            return jsonify(error="tool_unauthorized"), 401
        if request.mimetype != "application/json":
            return jsonify(error="json_required"), 415
        try:
            body = json.loads(request.get_data(cache=False).decode("utf-8"), object_pairs_hook=_duplicate_keys)
        except (ValueError, UnicodeError, RecursionError):
            return jsonify(error="tool_request_invalid"), 400
        if type(body) is not dict or not set(body) <= {"arguments", "observation"} or "arguments" not in body:
            return jsonify(error="tool_request_invalid"), 400
        try:
            answer = service.call(name, body["arguments"], observation=body.get("observation"),
                                  session_key=request.headers.get("X-MC-Session", ""),
                                  agent_id=request.headers.get("X-MC-Agent", ""))
        except Exception:
            # A connection loss may hide a committed effect: the receipt says what happened; never auto-retry.
            _log.exception("mc tool %s: outcome unconfirmed", name[:64])
            answer = {"status": "unknown", "code": "tool_outcome_unconfirmed",
                      "message": "MiniMoi could not confirm what happened. Do not repeat it; tell Robert.", "data": {}}
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
