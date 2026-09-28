"""Staging-only Guild mounts on the portal: /guild-next and /guild-proto.

Each mount is switched on by its own environment variable, read once when the
portal starts:

    MINIMOI_GUILD_NEXT=1    the real Shop floor (minimoi_portal/guild_ui) at /guild-next
    MINIMOI_GUILD_PROTO=1   the Guild interaction prototype, prototype data, at /guild-proto
    MINIMOI_GUILD_MC=...    Master Craftsman's backend on /guild-next: off (default),
                            stub, openclaw or grok (minimoi_portal/guild_ui/mc)

Unset (production) means nothing is registered, so both prefixes fall through
to the portal's own routes and answer 404. Only "1", "true", "on" or "yes"
switch a mount on; any other value is off.

The switches count only on a staging origin (review F8): ``BASE_URL``'s host
must be ``dev.minimoi.ai``, ``localhost`` or ``127.0.0.1``, or be named in
``MINIMOI_GUILD_ALLOWED_HOSTS`` (comma-separated; for tests or a future
always-on staging host). An empty, unknown or production ``BASE_URL`` refuses
both mounts, whatever the switches say.

Both mounts use the portal's real owner guard. A mount that is switched on but
fails to register never falls back to anything: its prefix answers 503 (JSON
under /api/, a short page otherwise), still behind the owner guard, so an
anonymous API call is a JSON 401 and not the portal's login redirect
(review S9).

Two mounts in one app (C18): the prototype is loaded by file path under its
own module name, ``minimoi_guild_proto``, and keeps its blueprint
``guild_ui`` and templates ``guild/ui_*.html``; the real package is imported
as ``minimoi_portal.guild_ui`` with blueprint ``guild_ui_next`` and
templates ``guild_floor/*``. Neither is ever imported as a top-level
``guild_ui``.
"""
from __future__ import annotations

import importlib.util
import re
import logging
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from flask import Blueprint, Response, current_app, jsonify

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
FLAG_VALUES_ON = {"1", "true", "on", "yes"}

NEXT_FLAG, NEXT_PREFIX, NEXT_NAME = "MINIMOI_GUILD_NEXT", "/guild-next", "guild_ui_next"
PROTO_FLAG, PROTO_PREFIX, PROTO_STUB_NAME = "MINIMOI_GUILD_PROTO", "/guild-proto", "guild_proto_unavailable"
PROTO_DIR = REPO / "prototype-lab" / "projects" / "guild-interaction-prototype"
PROTO_MODULE = "minimoi_guild_proto"

UNAVAILABLE_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                       "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}


def flag_on(environ, name: str) -> bool:
    return str(environ.get(name, "")).strip().lower() in FLAG_VALUES_ON


def register_unavailable(app, *, url_prefix: str, name: str, owner_guard: Callable | None,
                         current_user: Callable | None, label: str) -> bool:
    """Answer 503 under ``url_prefix`` after a failed registration."""
    if name in app.blueprints:
        log.error("guild mount %s: cannot register the 503 answer, name already taken", name)
        return False
    bp = Blueprint(name, __name__)

    def unavailable(rest: str = ""):
        is_api = rest.startswith("api/") or rest == "api"

        def answer():
            if is_api:
                response = jsonify({"error": "unavailable", "message": f"{label} is unavailable on this portal."})
                response.status_code = 503
            else:
                response = Response(
                    f"<!DOCTYPE html><title>{label} unavailable</title>"
                    f"<p>{label} is unavailable on this portal. Nothing else is affected.</p>",
                    status=503, mimetype="text/html")
            response.headers.update(UNAVAILABLE_HEADERS)
            return response

        if owner_guard is None:
            return answer()
        response = current_app.make_response(owner_guard(answer)())
        if is_api and response.status_code in (301, 302, 303, 307, 308):
            signed_in = bool(current_user and current_user())
            response = jsonify({"error": "not_allowed" if signed_in else "not_signed_in",
                                "message": "This needs the owner's sign-in." if signed_in else "Sign in first."})
            response.status_code = 403 if signed_in else 401
        return response

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    bp.add_url_rule("/", "unavailable_root", unavailable, methods=methods)
    bp.add_url_rule("/<path:rest>", "unavailable", unavailable, methods=methods)
    app.register_blueprint(bp, url_prefix=url_prefix)
    return True


def mount_guild_next(app, *, environ, owner_guard, current_user, queue_path, operations_status_url=None,
                     records_db=None, base_url=None, audit=None, database_url=lambda: None) -> str:
    """Return "off", "on" or "unavailable"."""
    if not flag_on(environ, NEXT_FLAG):
        return "off"
    try:
        from minimoi_portal.guild_ui import register_guild_ui
        from minimoi_portal.guild_ui.mc import backend_from_env
        from minimoi_portal.guild_ui.services import build_services
        # MINIMOI_GUILD_MC (off | stub | openclaw | grok); never raises, and an
        # MC problem never fails this mount (it shows "unavailable").
        mc = backend_from_env(environ)
        log.info("guild mount: Master Craftsman backend %s", mc.kind)
        services = build_services(queue_path=queue_path, operations_status_url=operations_status_url,
                                  records_db=records_db, database_url=database_url, audit=audit, mc=mc)
        register_guild_ui(app, owner_guard=owner_guard, current_user=current_user, url_prefix=NEXT_PREFIX,
                          blueprint_name=NEXT_NAME, services=services, base_url=base_url)
        log.info("guild mount: /guild-next registered")
    except Exception:
        log.exception("guild mount: /guild-next failed to register; answering 503 there")
        register_unavailable(app, url_prefix=NEXT_PREFIX, name=NEXT_NAME, owner_guard=owner_guard,
                             current_user=current_user, label="Guild next")
        return "unavailable"
    reconcile_once(services.store)
    return "on"


def reconcile_once(store) -> list | None:
    """Spec §6.3 "at store start" (review F10): settle any Save a crash left
    half-done, so its Check shows before the next write. Best effort: a
    failure is logged and never stops the mount."""
    try:
        outcomes = store.reconcile()
    except Exception:
        log.exception("guild mount: queue reconcile at start failed; the next Save reconciles instead")
        return None
    if outcomes:
        log.warning("guild mount: reconciled %d unfinished Save(s) at start: %s", len(outcomes),
                    ", ".join(f"{o.get('op_id')}={o.get('outcome', o.get('kind'))}" for o in outcomes))
    else:
        log.info("guild mount: queue journal reconciled at start; nothing unfinished")
    return outcomes


def _load_prototype():
    module = sys.modules.get(PROTO_MODULE)
    if module is not None:
        return module
    package = PROTO_DIR / "guild_ui"
    spec = importlib.util.spec_from_file_location(PROTO_MODULE, package / "__init__.py",
                                                  submodule_search_locations=[str(package)])
    if spec is None or spec.loader is None:
        raise ImportError(f"prototype package not found at {package}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PROTO_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(PROTO_MODULE, None)
        raise
    return module


def mount_guild_proto(app, *, environ, owner_guard, current_user) -> str:
    """The prototype for comparison: prototype mode, prototype data only, owner-guarded."""
    if not flag_on(environ, PROTO_FLAG):
        return "off"
    try:
        if owner_guard is None:
            raise RuntimeError("the prototype mount needs the portal's owner guard")
        proto = _load_prototype()
        proto.register_guild_ui(app, prototype=True, owner_guard=owner_guard, current_user=current_user,
                                url_prefix=PROTO_PREFIX, sources="sample")
        log.info("guild mount: /guild-proto registered (prototype data)")
        return "on"
    except Exception:
        log.exception("guild mount: /guild-proto failed to register; answering 503 there")
        register_unavailable(app, url_prefix=PROTO_PREFIX, name=PROTO_STUB_NAME, owner_guard=owner_guard,
                             current_user=current_user, label="Guild prototype")
        return "unavailable"


STAGING_HOSTS = frozenset({"dev.minimoi.ai", "localhost", "127.0.0.1"})
NEVER_ALLOWED_HOSTS = frozenset({"minimoi.ai", "www.minimoi.ai"})
ALLOWED_HOSTS_VAR = "MINIMOI_GUILD_ALLOWED_HOSTS"


def origin_host(base_url) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(str(base_url or "")).hostname or "").lower()
    except ValueError:
        return ""


def allowed_hosts(environ) -> frozenset:
    extra = {h.strip().lower() for h in str(environ.get(ALLOWED_HOSTS_VAR, "")).split(",") if h.strip()}
    # Production hosts can never be allowed, even if listed: one stray override
    # line plus one stray switch must not expose these routes on minimoi.ai.
    return (STAGING_HOSTS | extra) - NEVER_ALLOWED_HOSTS


def is_staging_origin(base_url, environ) -> bool:
    """True only for a staging origin (an allowlist, review F8). The Guild
    switches are ignored anywhere else even if set: /opt/minimoi/.env is
    shared by every production service, so one stray line must not expose
    these routes, and a missing or unfamiliar BASE_URL is not staging."""
    host = origin_host(base_url)
    return bool(host) and host in allowed_hosts(environ)


def mount_all(app, *, environ, owner_guard, current_user, **next_kwargs) -> dict:
    base_url = next_kwargs.get("base_url") or environ.get("BASE_URL")
    if not is_staging_origin(base_url, environ):
        if flag_on(environ, NEXT_FLAG) or flag_on(environ, PROTO_FLAG):
            log.warning("guild mounts: MINIMOI_GUILD_NEXT/PROTO ignored; %r is not a staging origin "
                        "(allowed: %s)", base_url, ", ".join(sorted(allowed_hosts(environ))))
        return {"guild_proto": "refused_not_staging", "guild_next": "refused_not_staging"}
    return {
        "guild_proto": mount_guild_proto(app, environ=environ, owner_guard=owner_guard, current_user=current_user),
        "guild_next": mount_guild_next(app, environ=environ, owner_guard=owner_guard, current_user=current_user,
                                       **next_kwargs),
    }


_GUILD_API_PATH = re.compile(r"/guild[\w-]*/api/")


def sentry_before_send(event, hint=None):
    """Sentry hook (review B1c #5): an error event from a Shop floor API call
    never carries the request body, which can hold a note or post-it before
    its payment details are scrubbed, nor stack-frame local variables, its
    query string, cookies or CSRF header. Every other portal event passes unchanged."""
    try:
        request = event.get("request") or {}
        url = str(request.get("url") or "")
        if _GUILD_API_PATH.search(urlsplit(url).path or url):
            # Stack-frame locals could hold the body or a note's text (the
            # portal also turns them off at init; this holds if that changes).
            for exc in ((event.get("exception") or {}).get("values") or []):
                for frame in ((exc.get("stacktrace") or {}).get("frames") or []):
                    frame.pop("vars", None)
            for thread in ((event.get("threads") or {}).get("values") or []):
                for frame in ((thread.get("stacktrace") or {}).get("frames") or []):
                    frame.pop("vars", None)
            for key in ("data", "query_string", "cookies"):
                request.pop(key, None)
            headers = request.get("headers")
            if isinstance(headers, dict):
                for name in list(headers):
                    if name.lower() in ("cookie", "x-csrf-token"):
                        headers.pop(name)
    except Exception:  # the hook never drops a report
        pass
    return event
