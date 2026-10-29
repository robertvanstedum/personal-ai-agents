"""The agent door (spec v0.2 §3.8): ``POST /internal/notices/v1/raise`` and ``/resolve`` on the portal's public port.

A multi-digest variant of ``guild_ui.supervision.door`` (review S6): the bearer token's SHA-256 is compared in
constant time against EVERY configured entry, and the matching agent is passed to the handler. The agent always comes
from the token, never from the body. Tokens are attribution, not isolation: both agents run as one macOS user.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time

RAISE, RESOLVE = "/internal/notices/v1/raise", "/internal/notices/v1/resolve"
BODY_MAX = 8192
PER_MINUTE = 30
_log = logging.getLogger("action_waiting")


def caller(tokens: dict, header: str):
    """The agent whose token this is, or None. Every entry is compared, so timing says nothing about which matched."""
    presented = header[7:] if isinstance(header, str) and header.startswith("Bearer ") else ""
    if not 32 <= len(presented) <= 256:
        return None
    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    found = None
    for configured, agent in tokens.items():
        if secrets.compare_digest(digest, configured):
            found = agent
    return found


def attach(app, service, *, clock=time.monotonic, per_minute=PER_MINUTE):
    if "action_notices_raise" in app.view_functions:
        return False
    from flask import jsonify, request
    from .service import KeyConflict, Refused, TooManyOpen
    seen = {}

    def guarded(handler):
        def view():
            now, who = clock(), request.headers.get("X-Real-IP") or request.remote_addr or "-"
            window = [t for t in seen.get(who, ()) if now - t < 60]
            if len(window) >= per_minute:
                return jsonify(error="rate_limited"), 429
            seen[who] = window + [now]
            if len(seen) > 1000:
                seen.clear()
            agent = caller(service.settings.agent_tokens, request.headers.get("Authorization", ""))
            if agent is None:
                return jsonify(error="unauthorized"), 401
            if request.mimetype != "application/json" or (request.content_length or 0) > BODY_MAX:
                return jsonify(error="request_invalid"), 400
            raw = request.get_data(cache=False)[:BODY_MAX + 1]
            if len(raw) > BODY_MAX:
                return jsonify(error="request_invalid"), 400
            try:
                answer = handler(agent, json.loads(raw))
            except KeyConflict:
                return jsonify(error="key_conflict"), 409
            except TooManyOpen:
                return jsonify(error="too_many_open"), 429
            except (Refused, ValueError):
                return jsonify(error="request_invalid"), 400
            except Exception as exc:
                _log.warning("action notices: door call failed (%s)", type(exc).__name__)
                return jsonify(error="unavailable"), 503
            return jsonify(answer)
        return view

    app.add_url_rule(RAISE, "action_notices_raise", guarded(service.raise_agent), methods=["POST"])
    app.add_url_rule(RESOLVE, "action_notices_resolve", guarded(service.resolve_agent), methods=["POST"])
    return True
