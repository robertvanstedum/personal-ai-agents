"""Only the B1 routes are registered under /guild-next; the portal's own routes,
/static and the legacy Guild pages are untouched (spec §8.3, C2)."""
from __future__ import annotations

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

EXPECTED = {
    "/guild-next/guild/build",
    "/guild-next/guild/build/bench",
    "/guild-next/guild/build/queue",
    "/guild-next/guild/build/items/<int:item_id>",
    "/guild-next/guild/build/postits",
    "/guild-next/guild/operate",
    "/guild-next/guild/ui-assets/<path:filename>",
    "/guild-next/api/v1/session",
    "/guild-next/api/v1/floor",
    "/guild-next/api/v1/queue",
    "/guild-next/api/v1/queue/items/<int:item_id>",
    "/guild-next/api/v1/queue/items/<int:item_id>/history",
    "/guild-next/api/v1/queue/items/<int:item_id>/status",
    "/guild-next/api/v1/queue/journal/<op_id>/checked",
    "/guild-next/api/v1/notes",
    "/guild-next/api/v1/postits",
    "/guild-next/api/v1/postits/bin",
    "/guild-next/api/v1/postits/<int:postit_id>/bin",
    "/guild-next/api/v1/postits/<int:postit_id>/restore",
    "/guild-next/api/v1/continue",
    "/guild-next/api/v1/",
    "/guild-next/api/v1/<path:rest>",
}


def test_exactly_the_allowlisted_routes(staging):
    rules = {r.rule for r in staging.app.url_map.iter_rules() if r.rule.startswith("/guild-next")}
    assert rules == EXPECTED
    for word in ("improve", "experiment", "reset", "landing", "proto"):
        assert not any(word in r for r in rules), word


def test_writes_are_post_only_and_reads_get_only(staging):
    by_rule = {r.rule: r.methods for r in staging.app.url_map.iter_rules() if r.rule.startswith("/guild-next/api")}
    assert by_rule["/guild-next/api/v1/queue/items/<int:item_id>/status"] >= {"POST"}
    assert "GET" not in by_rule["/guild-next/api/v1/queue/items/<int:item_id>/status"]
    assert "POST" not in by_rule["/guild-next/api/v1/floor"]
    for rule in ("/guild-next/api/v1/postits/<int:postit_id>/bin", "/guild-next/api/v1/postits/<int:postit_id>/restore"):
        assert by_rule[rule] >= {"POST"} and "GET" not in by_rule[rule]
    methods = {}
    for r in staging.app.url_map.iter_rules():
        if r.rule in ("/guild-next/api/v1/notes", "/guild-next/api/v1/postits", "/guild-next/api/v1/continue"):
            methods.setdefault(r.rule, set()).update(r.methods)
    assert methods["/guild-next/api/v1/notes"] >= {"GET", "POST"}
    assert methods["/guild-next/api/v1/postits"] >= {"GET", "POST"}
    assert methods["/guild-next/api/v1/continue"] >= {"GET", "PUT"}
    assert "DELETE" not in set().union(*methods.values())  # nothing is ever deleted


def test_portal_routes_and_headers_are_untouched(staging):
    app = staging.app
    rules = {r.rule: r.endpoint for r in app.url_map.iter_rules()}
    assert rules["/guild/build"] == "guild_build"
    assert rules["/guild/operate"] == "guild_operate"
    assert rules["/static/<path:filename>"] == "static"
    owner = staging.owner()
    health = owner.get("/health")
    assert "Content-Security-Policy" not in health.headers
    floor = owner.get("/guild-next/guild/build")
    assert "script-src 'self'" in floor.headers["Content-Security-Policy"]
    assert "no-store" in floor.headers["Cache-Control"]


def test_unknown_page_under_the_prefix_is_not_a_floor_page(staging):
    assert staging.owner().get("/guild-next/guild/improve").status_code == 404
    assert staging.owner().get("/guild-next/guild").status_code == 404
