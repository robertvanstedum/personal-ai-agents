"""Records (Rooms) behind the portal on dev only (Guild 1.1 slice 4, spec §6).

Reviewed copy of prototype-lab/projects/project-records-room-poc/
dev_portal_bridge.py, with one reviewed change: the backend is exactly one
configured internal origin (RECORDS_BACKEND, for example
http://minimoi-records:18880) instead of a fixed loopback address, still
with no client-selected hosts.

What it keeps (R4):
* Records keeps its own login. Its session cookie, minimoi_room_poc, is the
  only cookie forwarded, and it is re-issued scoped to /app/records/
  (HttpOnly, SameSite=Strict, Secure on dev).
* The portal cookie, bearer tokens, forwarded identity and every other
  request header are never passed through. The owner credential is never
  injected: Robert signs in to Records separately.
* Records answers only on the dev origin (and a local test host). A non-GET
  needs Origin equal to that origin; the Idempotency-Key header passes
  through; bodies are capped (3,000,000 bytes) while streaming.
* Records' own actor and grant checks decide every action; the portal adds
  only its owner guard in front.

Installed only when BASE_URL is the dev origin and RECORDS_BACKEND is set
and valid; in production nothing is registered and /app/records/ is 404.
"""
from __future__ import annotations

import logging
import re
from http.cookies import SimpleCookie
from urllib.parse import quote, urlsplit

import requests
from flask import Response, request

log = logging.getLogger(__name__)

PREFIX = "/app/records"
COOKIE = "minimoi_room_poc"
DEV_ORIGIN = "https://dev.minimoi.ai"
DEV_HOST = "dev.minimoi.ai"
MAX_BODY = 3_000_000
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


def install(portal, require_login, require_owner, *, backend: str, transport=None) -> str:
    backend = validate_backend(backend)

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
        # Only these headers reach Records: never the portal cookie, a bearer
        # token, forwarded identity, or a client-chosen host.
        headers = {"Accept": request.headers.get("Accept", "*/*")}
        if request.headers.get("Content-Type"):
            headers["Content-Type"] = request.headers["Content-Type"]
        if request.headers.get("Idempotency-Key"):
            headers["Idempotency-Key"] = request.headers["Idempotency-Key"]
        if request.cookies.get(COOKIE):
            headers["Cookie"] = COOKIE + "=" + request.cookies[COOKIE]
        if request.method not in {"GET", "HEAD"}:
            headers["Origin"] = backend
        try:
            target = backend + "/" + quote(path, safe="/")
            if request.query_string:
                target += "?" + request.query_string.decode("ascii")
            payload = request.stream.read(MAX_BODY + 1)
            if len(payload) > MAX_BODY:
                return Response("Request too large", status=413)
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
        cookie = _records_cookie(upstream)
        if cookie is not None:
            value, max_age, expires = cookie
            response.set_cookie(COOKIE, value, path=PREFIX + "/", httponly=True, samesite="Strict",
                                secure=request.host == DEV_HOST, max_age=max_age,
                                expires=None if max_age is not None else expires)
        return response

    wrapped = require_login(require_owner(forward))
    portal.add_url_rule(PREFIX, endpoint="records_dev_root", view_func=wrapped, methods=["GET", "HEAD"],
                        defaults={"path": ""})
    portal.add_url_rule(PREFIX + "/", endpoint="records_dev_slash", view_func=wrapped, methods=["GET", "HEAD"],
                        defaults={"path": ""})
    portal.add_url_rule(PREFIX + "/<path:path>", endpoint="records_dev_proxy", view_func=wrapped,
                        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
    return backend


def _set_cookie_headers(upstream) -> list[str]:
    """Each Set-Cookie header on its own: joined into one string they cannot
    be split safely (an Expires date has a comma)."""
    raw = getattr(getattr(upstream, "raw", None), "headers", None)
    if raw is not None and hasattr(raw, "getlist"):
        return list(raw.getlist("Set-Cookie"))
    joined = upstream.headers.get("Set-Cookie")
    return [joined] if joined else []


def _records_cookie(upstream):
    """(value, max_age as int or None, expires or None) of Records' own cookie,
    or None. A malformed attribute is dropped, never a 500 (#288 review F5)."""
    for header in _set_cookie_headers(upstream):
        jar = SimpleCookie()
        try:
            jar.load(header)
        except Exception:                                  # a cookie we cannot parse is not re-issued
            continue
        if COOKIE not in jar:
            continue
        morsel = jar[COOKIE]
        max_age = None
        raw_age = (morsel["max-age"] or "").strip().rstrip(",")
        if raw_age:
            try:
                max_age = max(0, int(raw_age))
            except ValueError:
                max_age = None
        expires = (morsel["expires"] or "").strip().rstrip(",") or None
        return morsel.value, max_age, expires
    return None


def install_if_dev(portal, *, base_url: str | None, environ, require_login, require_owner) -> dict:
    """Install the bridge only on the dev origin with a valid RECORDS_BACKEND.
    Returns the state, also kept at portal.extensions["records_bridge"]."""
    if (base_url or "").rstrip("/") != DEV_ORIGIN:
        state = {"state": "off_not_dev"}
    elif not environ.get("RECORDS_BACKEND"):
        state = {"state": "off_no_backend"}
    else:
        try:
            backend = install(portal, require_login, require_owner, backend=environ.get("RECORDS_BACKEND"))
            state = {"state": "on", "prefix": PREFIX, "backend_host": urlsplit(backend).hostname}
        except BackendRefused as exc:
            log.warning("records bridge: not installed: %s", exc)
            state = {"state": "refused_backend", "reason": str(exc)}
    portal.extensions["records_bridge"] = state
    return state
