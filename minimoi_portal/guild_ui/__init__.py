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
import hashlib
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

ALL_ROUTES = ("floor", "bench", "labs", "workshop", "queue", "item", "postits", "operate", "buildlog", "board",
              "media", "rooms", "references", "assets", "api")
B1_ROUTES = ALL_ROUTES   # improve, experiment and any reset are not in this package


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
    response.headers.setdefault("Content-Security-Policy", CSP)        # a download sets its own stricter one
    response.headers["X-Content-Type-Options"] = "nosniff"
    # A stream is never stored either, and must not be transformed (compressed
    # or buffered) on its way (streaming spec v0.2 §3; no-store is stricter
    # than the spec's no-cache).
    streaming = (response.mimetype or "") == "application/x-ndjson"
    response.headers["Cache-Control"] = "no-store, no-transform" if streaming else "no-store"
    if getattr(response, "_guild_immutable_asset", False):      # a versioned page asset: the one thing the browser may keep
        response.headers["Cache-Control"] = IMMUTABLE_CACHE
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


IMMUTABLE_CACHE = "private, max-age=31536000, immutable"
_asset_version: str | None = None


def asset_version() -> str:
    """A short fingerprint of everything under static/, taken once per process. Page assets are addressed by it
    (``/ui-assets/_v/<fingerprint>/js/x.js``), so a browser keeps each file until the next deploy changes the
    fingerprint and every address with it. Relative imports inside the scripts stay under the same fingerprint."""
    global _asset_version
    if _asset_version is None:
        h = hashlib.sha256()
        for path in sorted(p for p in STATIC.rglob("*") if p.is_file()):
            h.update(str(path.relative_to(STATIC)).encode())
            h.update(hashlib.sha256(path.read_bytes()).digest())
        _asset_version = h.hexdigest()[:12]
    return _asset_version


@owner_page
def asset(filename, ver=None):
    """The unversioned address always revalidates; the versioned one is kept by the browser, but only when the
    fingerprint in it is this deploy's (an address from an older page is served fresh and never stored)."""
    response = send_from_directory(STATIC, filename, max_age=0)
    response._guild_immutable_asset = ver is not None and ver == asset_version()
    return response


def _asset_url_defaults(endpoint, values):
    """Every url_for of the page assets carries this deploy's fingerprint, without touching a template."""
    if endpoint.endswith(".asset") and "ver" not in values:
        values["ver"] = asset_version()


def _make_blueprint(name: str, routes) -> Blueprint:
    from . import api, board_api, pages

    bp = Blueprint(name, __name__, template_folder=str(PACKAGE / "templates"))
    bp.after_request(_headers)
    bp.url_defaults(_asset_url_defaults)
    bp.register_error_handler(Exception, _api_error)
    page_rules = {
        "floor": [("/guild/build", "floor", pages.floor),
                  # The mount's own root is the Guild home, paired with Curator
                  # (Guild 1.1 slice 1); it used to redirect to the Shop floor.
                  ("/", "home", pages.home), ("/guild", "guild_home", pages.home)],
        "bench": [("/guild/build/bench", "bench", pages.bench)],
        "labs": [("/guild/labs", "labs", pages.labs)],
        "workshop": [("/guild/workshop", "workshop", pages.workshop)],
        "queue": [("/guild/build/queue", "queue", pages.queue)],
        "item": [("/guild/build/items/<int:item_id>", "item", pages.item)],
        "postits": [("/guild/build/postits", "postits", pages.postits)],
        "operate": [("/guild/operate", "operate", pages.operate)],
        # The Build Log (Guild 1.1 slice 2, spec §4): every item in every status.
        "buildlog": [("/guild/build/log", "build_log", pages.build_log)],
        # The Board and the Media library (Guild 1.1 slice 3, spec §5); images
        # are served to their owner only, same origin.
        "board": [("/guild/board", "board", pages.board)],
        "media": [("/guild/media", "media_library", pages.media_library),
                  ("/media/<asset_id>/<variant>", "media_file", board_api.media_file)],
        # Rooms (slice 4, spec §6): group chat on Records, through the dev bridge.
        "rooms": [("/guild/rooms", "rooms", pages.rooms), ("/rooms", "rooms_short", pages.rooms)],
        "references": [("/guild/docs", "docs", pages.docs),
                       ("/guild/docs/read", "document", pages.document),
                       ("/guild/build/items/<int:item_id>/spec", "item_spec", pages.item_spec),
                       ("/guild/improve", "improve", pages.improve),
                       ("/guild/experiment", "experiment", pages.experiment),
                       ("/guild/experiment/<slug>", "prototype_detail", pages.prototype_detail),
                       ("/guild/operate/<view>", "operate_view", pages.operate_view)],
        "assets": [("/guild/ui-assets/<path:filename>", "asset", asset),
                   ("/guild/ui-assets/_v/<ver>/<path:filename>", "asset", asset)],
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
    from .jobs_wiring import attach_jobs
    attach_jobs(app, blueprint_name, services)         # Master Craftsman jobs: nothing happens unless MINIMOI_GUILD_JOBS is on
    return bp


__all__ = ["ALL_ROUTES", "B1_ROUTES", "GuildBindingError", "register_guild_ui", "cfg", "load_layout"]
