"""The real Guild Shop floor (B1 deliverable (b)), a Flask blueprint.

Lifted from the Guild interaction prototype into real mode only (spec §8.1):
live reads through the queue store and the Operations agent, grey "not
instrumented" everywhere else, Master Craftsman off, filing off, no model or
paid call. There is no prototype mode: ``prototype=True`` raises.

Two mounts in one portal (C18): this package is imported as
``minimoi_portal.guild_ui`` (never as a top-level ``guild_ui``), takes its
blueprint name as a parameter, keys its configuration by that name
(``app.extensions[blueprint_name]``), links with relative endpoints
(``url_for('.floor')``) and keeps its templates in ``guild_floor/``, a folder
no other blueprint uses.

Registration fails closed (spec §8.3): a real owner guard and a ready service
bundle are required, and only the allowlisted routes are registered.
"""
from __future__ import annotations

import functools
import json
import logging
from pathlib import Path

from flask import Blueprint, current_app, request, send_from_directory
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)

PACKAGE = Path(__file__).resolve().parent
STATIC = PACKAGE / "static"
LAYOUT = PACKAGE / "config" / "layout.json"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")

ALL_ROUTES = ("floor", "bench", "labs", "workshop", "queue", "item", "postits", "operate", "assets", "api")
B1_ROUTES = ALL_ROUTES   # the landing page, improve, experiment and any reset are not in this package


class GuildBindingError(RuntimeError):
    """Registration refused: the floor would not be real, guarded and ready."""


def cfg() -> dict:
    return current_app.extensions[request.blueprint]


def load_layout() -> dict:
    return json.loads(LAYOUT.read_text(encoding="utf-8"))


def owner_page(view):
    """Pages and assets: the portal's owner guard, bound per app at request time."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        return cfg()["owner_guard"](view)(*args, **kwargs)
    return wrapped


def owner_api(view):
    """API calls: the same owner guard, answered as JSON 401/403 (spec §5.2)."""
    from .security import json_guard

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        c = cfg()
        return json_guard(c["owner_guard"], c["current_user"])(view)(*args, **kwargs)
    return wrapped


def _headers(response):
    """Scoped to this blueprint's responses; host-app routes are untouched."""
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    # A stream is never stored either, and must not be transformed (compressed
    # or buffered) on its way (streaming spec v0.2 §3; no-store is stricter
    # than the spec's no-cache).
    streaming = (response.mimetype or "") == "application/x-ndjson"
    response.headers["Cache-Control"] = "no-store, no-transform" if streaming else "no-store"
    return response


def _api_error(exc):
    """Unexpected errors under <mount>/api/ answer JSON, never Flask's HTML 500
    (spec §5.1, review F11). Pages and HTTP errors keep their usual answers."""
    if isinstance(exc, HTTPException):
        return exc
    prefix = current_app.extensions.get(request.blueprint, {}).get("url_prefix", "")
    if not request.path.startswith(f"{prefix}/api/"):
        raise exc
    log.exception("guild floor API: unexpected error on %s %s", request.method, request.path)
    from .security import json_error
    return json_error("server_error", "Something went wrong on the server. Reload to see the current "
                      "state before trying again.", 500)


@owner_page
def asset(filename):
    return send_from_directory(STATIC, filename, max_age=0)


def _make_blueprint(name: str, routes) -> Blueprint:
    from . import api, pages

    bp = Blueprint(name, __name__, template_folder=str(PACKAGE / "templates"))
    bp.after_request(_headers)
    bp.register_error_handler(Exception, _api_error)
    page_rules = {
        "floor": [("/guild/build", "floor", pages.floor)],
        "bench": [("/guild/build/bench", "bench", pages.bench)],
        "labs": [("/guild/labs", "labs", pages.labs)],
        "workshop": [("/guild/workshop", "workshop", pages.workshop)],
        "queue": [("/guild/build/queue", "queue", pages.queue)],
        "item": [("/guild/build/items/<int:item_id>", "item", pages.item)],
        "postits": [("/guild/build/postits", "postits", pages.postits)],
        "operate": [("/guild/operate", "operate", pages.operate)],
        "assets": [("/guild/ui-assets/<path:filename>", "asset", asset)],
    }
    for route in routes:
        for rule, endpoint, view in page_rules.get(route, []):
            bp.add_url_rule(rule, endpoint, view, strict_slashes=False)
    if "api" in routes:
        for rule, endpoint, view, methods in api.RULES:
            bp.add_url_rule(f"/api/v1{rule}", endpoint, view, methods=methods)
    return bp


def register_guild_ui(app, *, owner_guard, current_user, url_prefix: str, blueprint_name: str,
                      services, routes=B1_ROUTES, base_url: str | None = None, prototype: bool = False):
    """Mount the real Shop floor, or raise GuildBindingError and register nothing."""
    if prototype:
        raise GuildBindingError("prototype mode lives in prototype-lab")
    if owner_guard is None or not callable(owner_guard):
        raise GuildBindingError("a real owner guard is required (bind the portal's _require_owner)")
    if current_user is None or not callable(current_user):
        raise GuildBindingError("current_user is required")
    url_prefix = (url_prefix or "").rstrip("/")
    if not url_prefix.startswith("/") or url_prefix == "":
        raise GuildBindingError("url_prefix must be a path such as '/guild-next'")
    if not blueprint_name or not blueprint_name.isidentifier():
        raise GuildBindingError("blueprint_name must be an identifier")
    if blueprint_name in app.blueprints or blueprint_name in app.extensions:
        raise GuildBindingError(f"'{blueprint_name}' is already registered in this app")
    routes = tuple(routes)
    unknown = set(routes) - set(ALL_ROUTES)
    if unknown:
        raise GuildBindingError(f"routes outside the allowlist: {sorted(unknown)}")
    if services is None:
        raise GuildBindingError("the service bundle is required")
    problems = services.ready()
    if problems:
        raise GuildBindingError("services not ready: " + "; ".join(problems))
    layout = load_layout()
    bp = _make_blueprint(blueprint_name, routes)
    app.extensions[blueprint_name] = {
        "owner_guard": owner_guard,
        "current_user": current_user,
        "url_prefix": url_prefix,
        "blueprint_name": blueprint_name,
        "routes": routes,
        "services": services,
        "base_url": base_url,
        "layout": layout,
        "storage_ns": "guild" + url_prefix.replace("/", "."),
    }
    app.register_blueprint(bp, url_prefix=url_prefix)
    return bp


__all__ = ["ALL_ROUTES", "B1_ROUTES", "GuildBindingError", "register_guild_ui", "cfg", "load_layout"]
