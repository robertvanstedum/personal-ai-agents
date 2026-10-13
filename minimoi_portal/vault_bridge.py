"""Optional machine Ask ingress on the existing encrypted dev tunnel.

The Ask registry authenticates the supplied agent bearer and owns its grants.
No portal cookie, owner identity, path or destination supplied by the caller
is forwarded. This does not install/start Ask or grant any agent access.
"""
from __future__ import annotations

import json
import re
import time

import requests
from flask import Response, request

PREFIX = "/internal/vault"
ORIGIN = "https://dev.minimoi.ai"
BACKENDS = frozenset({"http://127.0.0.1:8771", "http://host.lima.internal:8771"})
MAX_BODY = 4096
MAX_RESPONSE = 256 * 1024
BEARER = re.compile(r"Bearer [A-Za-z0-9_-]{32,256}\Z", re.I)
ERRORS = frozenset({"unauthorized", "bad_arguments", "empty_query", "bad_k", "bad_date",
    "invalid_json", "tier_not_permitted", "too_large", "unsupported_media_type",
    "rate_limited", "registry_unavailable", "index_missing", "index_incompatible", "pin_mismatch"})


def _refuse(status, code):
    return Response(json.dumps({"error": code}), status=status,
                    content_type="application/json", headers={"Cache-Control": "no-store"})


def install_if_dev(portal, *, base_url, environ, transport=None):
    """Only the explicit dev switch registers the single fixed operation."""
    if environ.get("MINIMOI_VAULT_BRIDGE") != "1" or base_url != ORIGIN:
        return False
    backend = environ.get("MINIMOI_VAULT_BRIDGE_BACKEND")
    if backend not in BACKENDS:
        return False

    def ask():
        if request.host != "dev.minimoi.ai":
            return _refuse(404, "unavailable")
        # Machine credentials only: a signed-in portal session is insufficient.
        auth = request.headers.get("Authorization", "")
        if not BEARER.fullmatch(auth):
            return _refuse(401, "unauthorized")
        if request.query_string:
            return _refuse(400, "bad_arguments")
        if not request.is_json:
            return _refuse(415, "unsupported_media_type")
        if request.content_length is not None and request.content_length > MAX_BODY:
            return _refuse(413, "too_large")
        payload = request.stream.read(MAX_BODY + 1)
        if len(payload) > MAX_BODY:
            return _refuse(413, "too_large")
        # Do not log exception messages, credentials, body or source evidence.
        session = transport or requests.Session()
        if transport is None:
            session.trust_env = False
        try:
            deadline = time.monotonic() + 8
            with session.post(backend + "/v1/ask", data=payload,
                    headers={"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"},
                    timeout=(2, 8), allow_redirects=False, stream=True) as upstream:
                # requests' read timeout is per blocking read. The elapsed
                # check stops progressing/dribbling responses; an in-progress
                # read can still take its eight-second timeout to return.
                # This is not a strict eight-second wall-clock cancellation.
                if time.monotonic() > deadline:
                    return _refuse(503, "unavailable")
                if upstream.status_code not in {200, 400, 401, 403, 413, 415, 429, 503}:
                    return _refuse(503, "unavailable")
                if upstream.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return _refuse(503, "unavailable")
                raw = bytearray()
                # One-byte yields expose progress to the deadline check;
                # buffered chunks can hide a slowly dribbling response.
                for chunk in upstream.iter_content(chunk_size=1):
                    if time.monotonic() > deadline:
                        return _refuse(503, "unavailable")
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE:
                        return _refuse(503, "unavailable")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    return _refuse(503, "unavailable")
                if upstream.status_code != 200:
                    code = result.get("error")
                    return _refuse(upstream.status_code, code if isinstance(code, str) and code in ERRORS else "unavailable")
                return Response(json.dumps(result), content_type="application/json",
                                headers={"Cache-Control": "no-store"})
        except Exception:
            return _refuse(503, "unavailable")
        finally:
            if transport is None:
                session.close()

    portal.add_url_rule(PREFIX + "/v1/ask", "vault_machine_ask", ask, methods=["POST"])
    return True
