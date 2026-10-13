"""Records (Rooms) behind the portal on dev only (Guild 1.1 slice 4, spec §6).

Reviewed copy of prototype-lab/projects/project-records-room-poc/
dev_portal_bridge.py, with one reviewed change: the backend is exactly one
configured internal origin (RECORDS_BACKEND, for example
http://minimoi-records:18880) instead of a fixed loopback address, still
with no client-selected hosts.

What it keeps (R4) and what changed (Rooms identity, draft 3):
* Records no longer asks for its own sign-in here. The portal has already signed
  the person in; for each request the bridge asks minimoi_portal.rooms_identity
  whether this account has a Rooms binding (data the owner controls, checked live
  on every call) and, if so, sends Records one signed assertion naming that
  principal for that exact request (method, path, sorted query, body digest,
  30 s, single use). No Records cookie is forwarded or re-issued, no credential
  is in the page, and no binding means a plain 403, never another identity.
* The portal cookie, bearer tokens, forwarded identity and every other
  request header are never passed through. A client-supplied X-Portal-Identity is
  dropped; the bridge sets its own.
* Records answers only on the dev origin (and a local test host). A non-GET
  needs Origin equal to that origin; the Idempotency-Key header passes
  through; bodies are capped (3,000,000 bytes) while streaming.
* Records' own actor, membership and owner checks decide every action; the
  portal adds its login and owner guard in front.
* Error meanings stay apart: the portal session ended (the portal's own redirect
  or 401), no Rooms access (403 rooms_no_access), Rooms unavailable or not set up
  (502/503 rooms_unavailable / rooms_integration). A Records 401 for the
  assertion is an integration fault, never a request to sign in.

Installed only when BASE_URL is the dev origin and RECORDS_BACKEND is set
and valid; in production nothing is registered and /app/records/ is 404.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, urlsplit

import requests
from flask import Response, request

from minimoi_portal import rooms_identity as RI

log = logging.getLogger(__name__)

PREFIX = "/app/records"
ENV_NAME = "dev"
RI_HEADER = "X-Portal-Identity"
KEY_FILE_DEFAULT = "/run/secrets/rooms-bridge/portal-bridge.key"
ACCESS_DIR_DEFAULT = "/app/runtime/guild/rooms"
DEV_ORIGIN = "https://dev.minimoi.ai"
DEV_HOST = "dev.minimoi.ai"
MAX_BODY = 12_000_000
# The only hostnames RECORDS_BACKEND may name: the staging container, or the
# loopback address for a local run beside a native portal.
BACKEND_HOSTS = {"minimoi-records", "127.0.0.1"}
# A local portal's own host for tests and a native dev run; accepted only when
# the backend is loopback too (#288 review F7), never beside the container.
LOCAL_HOSTS = {"127.0.0.1:5001", "localhost:5001"}
PORTAL_PORT = 5001
# Tests may set a transport (an object with .request(...) like a requests
# Session); None means a fresh requests.Session per request.
_TRANSPORT = None
_HOSTNAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


class BackendRefused(ValueError):
    """RECORDS_BACKEND is not exactly one allowed internal HTTP origin."""


def validate_backend(url: str | None) -> str:
    """The configured backend as a canonical origin, or BackendRefused.
    http only; a hostname from BACKEND_HOSTS (exact); an optional port; no
    user info, path, query or fragment."""
    if not isinstance(url, str) or not url or url != url.strip():
        raise BackendRefused("RECORDS_BACKEND is not set")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise BackendRefused("RECORDS_BACKEND is not a URL") from None
    if parts.scheme != "http":
        raise BackendRefused("RECORDS_BACKEND must be http (an internal network origin)")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise BackendRefused("RECORDS_BACKEND must not carry user info")
    if parts.path not in ("",) or parts.query or parts.fragment or url.endswith(("?", "#")):
        raise BackendRefused("RECORDS_BACKEND must be an origin: no path, query or fragment")
    host = parts.hostname or ""
    if host not in BACKEND_HOSTS or (host != "127.0.0.1" and not _HOSTNAME.fullmatch(host)):
        raise BackendRefused(f"RECORDS_BACKEND host must be one of {sorted(BACKEND_HOSTS)}")
    if parts.netloc != (f"{host}:{port}" if port is not None else host):
        raise BackendRefused("RECORDS_BACKEND must be a plain host[:port]")
    if host == "127.0.0.1" and port in (None, 80, PORTAL_PORT):
        raise BackendRefused("RECORDS_BACKEND on loopback needs Records' own port, not the portal's")
    return f"http://{parts.netloc}"


def rewrite_paths(text: str) -> str:
    # Only absolute application URL literals, and only in trusted static
    # HTML/JS/CSS responses (below); never stored contributions or uploads.
    return re.sub(r'([\'"`])/(api|static)(?=/)', lambda m: m.group(1) + PREFIX + "/" + m.group(2), text)


def _allowed_host(host: str, backend: str) -> bool:
    return host == DEV_HOST or (host in LOCAL_HOSTS and backend.startswith("http://127.0.0.1:"))


def _json(status: int, code: str, message: str) -> Response:
    return Response(json.dumps({"error": message, "code": code}), status=status, content_type="application/json")


def install(portal, require_login, require_owner, *, backend: str, transport=None, identity=None) -> str:
    backend = validate_backend(backend)
    identity = identity or {}
    access = identity.get("access")
    key_file = identity.get("key_file") or KEY_FILE_DEFAULT
    current_user = identity.get("current_user") or (lambda: None)
    account_active = identity.get("account_active") or (lambda user: True)

    def who():
        """(Decision, signing key). The decision is live on every call: signed in, account active, binding active."""
        user = current_user() or {}
        account = f"account:{user['username']}" if user.get("username") else None
        if access is None:
            return RI.Decision("unavailable", reason="Rooms access is not configured"), None
        try:
            active = bool(account_active(user))
        except Exception:
            return RI.Decision("unavailable", reason="the account list cannot be read"), None
        decision = access.decide(account, account_active=active)
        key = RI.load_key(key_file) if decision.ok else None
        if decision.ok and key is None:
            return RI.Decision("unavailable", reason="the Rooms signing key is not in place"), None
        return decision, key

    def forward(path=""):
        if not _allowed_host(request.host, backend):
            return Response("Records is available only on dev.minimoi.ai", status=404)
        if request.method not in {"GET", "HEAD"}:
            expected = DEV_ORIGIN if request.host == DEV_HOST else "http://" + request.host
            if request.headers.get("Origin") != expected:
                return Response("Same-origin request required", status=403)
        if request.content_length and request.content_length > MAX_BODY:
            return Response("Request too large", status=413)
        if any(part in {".", ".."} for part in path.split("/")):
            return Response("Invalid path", status=400)
        decision, key = who()
        if decision.kind == "no_access":
            log.info("rooms bridge: no access (%s)", decision.reason)
            return _json(403, "rooms_no_access", "You don't have access to Rooms. Ask the owner to switch it on for your account.")
        if not decision.ok:
            log.warning("rooms bridge: unavailable (%s)", decision.reason)
            return _json(503, "rooms_unavailable", "Rooms is not set up for sign-in here right now. Nothing was sent.")
        # Only these headers reach Records: never the portal cookie, a bearer
        # token, forwarded identity (a client-supplied X-Portal-Identity included),
        # or a client-chosen host. The identity header is the bridge's own.
        headers = {"Accept": request.headers.get("Accept", "*/*")}
        if request.headers.get("Content-Type"):
            headers["Content-Type"] = request.headers["Content-Type"]
        if request.headers.get("Idempotency-Key"):
            headers["Idempotency-Key"] = request.headers["Idempotency-Key"]
        if request.method not in {"GET", "HEAD"}:
            headers["Origin"] = backend
        try:
            target = backend + "/" + quote(path, safe="/")
            if request.query_string:
                target += "?" + request.query_string.decode("ascii")
            payload = request.stream.read(MAX_BODY + 1)
            if len(payload) > MAX_BODY:
                return Response("Request too large", status=413)
            headers[RI_HEADER] = RI.sign(key, principal=decision.principal, method=request.method,
                                         target=RI.canonical_target("/" + path, request.query_string), body=payload, env=ENV_NAME)
            sender = transport if transport is not None else _TRANSPORT
            if sender is not None:
                upstream = sender.request(request.method, target, headers=headers, data=payload, timeout=20,
                                          allow_redirects=False)
            else:
                # A session per request: one browser's Records cookie is never
                # kept and applied to another portal request.
                with requests.Session() as connection:
                    connection.trust_env = False
                    upstream = connection.request(request.method, target, headers=headers, data=payload,
                                                  timeout=20, allow_redirects=False)
        except (requests.RequestException, UnicodeError, OSError):
            return Response('{"error":"Rooms (Records) is unavailable right now."}', status=502,
                            content_type="application/json")
        if 300 <= upstream.status_code < 400:
            return Response("Unexpected Records redirect", status=502)
        if upstream.status_code == 401 or (upstream.status_code == 403 and _identity_refusal(upstream)):
            # Records refused the assertion itself: a connection or set-up fault, never a request to sign in (the person IS signed in).
            log.warning("rooms bridge: Records refused the portal identity (%s)", upstream.status_code)
            return _json(502, "rooms_integration", "Rooms could not confirm your account just now. This is a connection problem, not a sign-in problem; nothing was sent.")
        if upstream.status_code == 503 and path.startswith("api/"):
            return _json(503, "rooms_unavailable", "Rooms is not set up for sign-in here right now. Nothing was sent.")
        content = upstream.content
        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        if (not path or path.startswith("static/")) and any(t in content_type for t in ("text/html", "javascript", "text/css")):
            content = rewrite_paths(content.decode("utf-8")).encode("utf-8")
        response = Response(content, status=upstream.status_code, content_type=content_type)
        for name in ("Content-Disposition", "Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy"):
            if name in upstream.headers:
                response.headers[name] = upstream.headers[name]
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Records-Environment"] = "dev"
        return response

    def call_records(method: str, path: str, payload: dict | None = None, *, operation_key=None):
        """One server-to-server call to Records as the signed-in OWNER (used by the access grant to create the person a grant needs).
        Returns (status, json or None); status 0 means Records could not be reached. Refused unless the caller acts as the owner principal."""
        decision, key = who()
        if not decision.ok or decision.principal != RI.OWNER_PRINCIPAL:
            return 403, None
        raw = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = {"Accept": "application/json", RI_HEADER: RI.sign(key, principal=decision.principal, method=method,
                                                                    target=RI.canonical_target(path, b""), body=raw, env=ENV_NAME)}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            headers["Origin"] = backend
        if operation_key:
            headers["Idempotency-Key"] = operation_key
        try:
            sender = transport if transport is not None else _TRANSPORT
            if sender is not None:
                up = sender.request(method, backend + path, headers=headers, data=raw, timeout=20, allow_redirects=False)
            else:
                with requests.Session() as connection:
                    connection.trust_env = False
                    up = connection.request(method, backend + path, headers=headers, data=raw, timeout=20, allow_redirects=False)
        except (requests.RequestException, UnicodeError, OSError):
            return 0, None
        try:
            return up.status_code, json.loads(up.content.decode("utf-8")) if up.content else None
        except ValueError:
            return up.status_code, None

    portal.extensions["rooms_call"] = call_records
    portal.extensions["rooms_who"] = who          # the Rooms page asks the same live question the bridge does
    wrapped = require_login(require_owner(forward))
    portal.add_url_rule(PREFIX, endpoint="records_dev_root", view_func=wrapped, methods=["GET", "HEAD"],
                        defaults={"path": ""})
    portal.add_url_rule(PREFIX + "/", endpoint="records_dev_slash", view_func=wrapped, methods=["GET", "HEAD"],
                        defaults={"path": ""})
    portal.add_url_rule(PREFIX + "/<path:path>", endpoint="records_dev_proxy", view_func=wrapped,
                        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    return backend


def _identity_refusal(upstream) -> bool:
    """Records says 403 for 'This account has no Rooms identity' (the assertion named a principal that is not a person there)."""
    try:
        return "no Rooms identity" in (upstream.json() or {}).get("error", "")
    except Exception:
        return False


def install_if_dev(portal, *, base_url: str | None, environ, require_login, require_owner, identity=None) -> dict:
    """Install the bridge only on the dev origin with a valid RECORDS_BACKEND.
    Returns the state, also kept at portal.extensions["records_bridge"]."""
    if (base_url or "").rstrip("/") != DEV_ORIGIN:
        state = {"state": "off_not_dev"}
    elif not environ.get("RECORDS_BACKEND"):
        state = {"state": "off_no_backend"}
    else:
        try:
            backend = install(portal, require_login, require_owner, backend=environ.get("RECORDS_BACKEND"), identity=identity)
            state = {"state": "on", "prefix": PREFIX, "backend_host": urlsplit(backend).hostname}
        except BackendRefused as exc:
            log.warning("records bridge: not installed: %s", exc)
            state = {"state": "refused_backend", "reason": str(exc)}
    portal.extensions["records_bridge"] = state
    return state
