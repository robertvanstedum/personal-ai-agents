"""Only the B1 routes are registered under /guild-next; the portal's own routes,
/static and the legacy Guild pages are untouched (spec §8.3, C2)."""
from __future__ import annotations

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

EXPECTED = {
    "/guild-next/guild/admin",
    "/guild-next/api/v1/rooms/archive",
    "/guild-next/rooms",
    "/guild-next/guild/docs",
    "/guild-next/guild/docs/read",
    "/guild-next/guild/build/items/<int:item_id>/spec",
    "/guild-next/guild/improve",
    "/guild-next/guild/experiment",
    "/guild-next/guild/experiment/<slug>",          # one prototype: Look inside and Run it (5 Oct)
    "/guild-next/guild/operate/<view>",
    "/guild-next/",
    "/guild-next/guild",
    # Owner-only companion routes reuse the existing conversation.
    "/guild-next/guild/companion",
    "/guild-next/guild/companion/<domain>",
    "/guild-next/guild/build",
    "/guild-next/guild/build/bench",
    # Planning Studio and Prototype Lab: truthful entry points (Guild 1.1 dev, slice 1).
    "/guild-next/guild/labs",
    # The local Workshop (4a: read only, no model calls).
    "/guild-next/guild/workshop",
    "/guild-next/guild/build/queue",
    "/guild-next/guild/build/items/<int:item_id>",
    "/guild-next/guild/build/postits",
    "/guild-next/guild/operate",
    # Guild 1.1: the Build Log (slice 2); Rooms (slice 4) says "coming in a later slice".
    "/guild-next/guild/build/log",
    "/guild-next/guild/rooms",
    # Guild 1.1 slice 3: the Board, the Media library, and owner-only image serving.
    "/guild-next/guild/board",
    "/guild-next/guild/board/popout",
    "/guild-next/guild/media",
    "/guild-next/media/<asset_id>/<variant>",
    "/guild-next/guild/ui-assets/<path:filename>",
    "/guild-next/guild/ui-assets/_v/<ver>/<path:filename>",
    # The topic workshop (W1): owner-only topics, items, comments and the collaboration journal.
    "/guild-next/api/v1/topics",
    "/guild-next/api/v1/topics/create",
    "/guild-next/api/v1/topics/<tid>",
    "/guild-next/api/v1/topics/<tid>/update",
    "/guild-next/api/v1/topics/<tid>/layout",
    "/guild-next/api/v1/topics/<tid>/items",
    "/guild-next/api/v1/topics/<tid>/items/design",
    "/guild-next/api/v1/topics/<tid>/items/<iid>",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/revisions",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/revisions/design",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/archive",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/stage",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/image",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/comments",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/comments/<cid>/resolve",
    "/guild-next/api/v1/topics/<tid>/items/<iid>/comments/<cid>/disposition",
    "/guild-next/api/v1/topics/<tid>/conversation",
    "/guild-next/api/v1/topics/<tid>/journal",
    "/guild-next/api/v1/topics/<tid>/journal/add",
    "/guild-next/api/v1/topics/<tid>/inbox",
    "/guild-next/api/v1/topics/<tid>/inbox/import",
    "/guild-next/api/v1/topics/<tid>/record",
    "/guild-next/api/v1/topics/<tid>/record-link",
    "/guild-next/api/v1/session",
    # Action-waiting notices (spec v0.2 Appendix A): the strip's read-only contract; "mode: off" while notices are off.
    "/guild-next/api/v1/waiting",
    "/guild-next/api/v1/wiki/pages",
    "/guild-next/api/v1/floor",
    "/guild-next/api/v1/queue",
    "/guild-next/api/v1/queue/items/<int:item_id>",
    "/guild-next/api/v1/queue/items/<int:item_id>/history",
    "/guild-next/api/v1/queue/items/<int:item_id>/status",
    # Guild 1.1 slice 2 (spec §4.3-4.5): a new item, the rank, the file journal.
    "/guild-next/api/v1/queue/items",
    "/guild-next/api/v1/queue/items/<int:item_id>/rank",
    "/guild-next/api/v1/queue/items/<int:item_id>/journal",
    "/guild-next/api/v1/queue/journal/<op_id>/checked",
    "/guild-next/api/v1/notes",
    "/guild-next/api/v1/postits",
    "/guild-next/api/v1/postits/bin",
    "/guild-next/api/v1/postits/<int:postit_id>/bin",
    "/guild-next/api/v1/postits/<int:postit_id>/restore",
    # Guild 1.1 slice 3 (spec §5.1-5.2): the Board's writes and the Media library.
    "/guild-next/api/v1/board",
    "/guild-next/api/v1/board/rev",
    "/guild-next/api/v1/postits/<int:postit_id>/done",
    "/guild-next/api/v1/postits/<int:postit_id>/undone",
    "/guild-next/api/v1/postits/<int:postit_id>/label",
    "/guild-next/api/v1/postits/<int:postit_id>/text",
    "/guild-next/api/v1/postits/<int:postit_id>/link",
    "/guild-next/api/v1/postits/photo",
    "/guild-next/api/v1/postits/reorder",
    "/guild-next/api/v1/postits/layout",
    "/guild-next/api/v1/postits/trash/empty",
    # Guild 1.1 slice 4: Take to a Room (a kept note, by id only).
    "/guild-next/api/v1/notes/<int:note_id>/share",
    "/guild-next/api/v1/media",
    "/guild-next/api/v1/media/<asset_id>/trash",
    "/guild-next/api/v1/media/<asset_id>/restore",
    "/guild-next/api/v1/media/purge",
    "/guild-next/api/v1/media/<asset_id>/uses",
    "/guild-next/api/v1/continue",
    # Master Craftsman's owner route: a seam, disabled (always refuses; tests/guild/shop_floor/test_mc_backend_switch.py).
    "/guild-next/api/v1/mc/turns",
    "/guild-next/api/v1/mc/turns/stream",
    "/guild-next/api/v1/mc/turns/ledger",
    "/guild-next/api/v1/mc/turns/continue",
    "/guild-next/api/v1/mc/turns/queue/<int:ledger_id>/cancel",
    "/guild-next/api/v1/mc/turns/queue/<int:ledger_id>/dispatch",
    "/guild-next/api/v1/mc/turns/<turn_id>/stop",
    "/guild-next/api/v1/mc/private",
    # Conversations (Guild 1.1 slice 2): owner only, CSRF-checked writes, no model calls.
    "/guild-next/api/v1/conversations",
    # Authenticated retained-voice export; not a write or model reconstruction.
    "/guild-next/api/v1/conversations/<cid>/voice-transcript",
    "/guild-next/api/v1/conversations/<cid>/deliveries/<delivery_id>",
    "/guild-next/api/v1/workshop",
    "/guild-next/api/v1/conversations/<cid>/rename",
    "/guild-next/api/v1/conversations/<cid>/pin",
    "/guild-next/api/v1/conversations/<cid>/unpin",
    "/guild-next/api/v1/conversations/<cid>/archive",
    "/guild-next/api/v1/conversations/<cid>/restore",
    # Build refinement, 5 Oct: linked work and kept images; owner only, CSRF-checked, no model calls.
    "/guild-next/api/v1/conversations/<cid>/work-item",
    "/guild-next/api/v1/conversations/<cid>/work-item/clear",
    "/guild-next/api/v1/conversations/<cid>/attachments",
    "/guild-next/api/v1/conversations/<cid>/attachments/remove",
    "/guild-next/api/v1/conversations/<cid>/attachments/retry",
    "/guild-next/api/v1/conversations/<cid>/documents",
    "/guild-next/api/v1/conversations/<cid>/documents/remove",
    # Step 2 (7 Oct): the owner's own original file, as a download (owner-checked, id resolved on the server, attachment only).
    "/guild-next/api/v1/conversations/<cid>/documents/<doc_id>/original",
    # Step 3 (7 Oct): Master Craftsman jobs; every one is owner-only and answers "jobs are off" unless MINIMOI_GUILD_JOBS is on.
    "/guild-next/api/v1/mc/jobs",
    "/guild-next/api/v1/conversations/<cid>/jobs",
    "/guild-next/api/v1/conversations/<cid>/jobs/<job_id>/stop",
    "/guild-next/api/v1/conversations/<cid>/jobs/<job_id>/result",
    "/guild-next/api/v1/conversations/<cid>/jobs/<job_id>/note",
    # MC front door, slice 1 (11 Oct): asking Claude Code / Codex through MC; owner-only, off unless MINIMOI_GUILD_AGENT_REQUESTS is on.
    "/guild-next/api/v1/conversations/<cid>/agent-requests",
    "/guild-next/api/v1/conversations/<cid>/agent-requests/sync",
    # MC voice, v1 (12 Oct): owner-only; every one answers "not switched on" (404) unless MINIMOI_MC_VOICE=1; the bootstrap is a CSRF-checked on-the-record write.
    "/guild-next/api/v1/mc/voice/capabilities",
    "/guild-next/api/v1/mc/voice/bootstrap",
    "/guild-next/api/v1/mc/voice/speech/<note_request_id>",
    "/guild-next/api/v1/mc/voice/static/<path:name>",
    # Dictation (12 Oct): owner-only; every one answers "not switched on" (404) unless MINIMOI_DICTATION=1; the bootstrap is a CSRF-checked on-the-record write.
    "/guild-next/api/v1/dictation/capabilities",
    "/guild-next/api/v1/dictation/bootstrap",
    "/guild-next/api/v1/dictation/static/<path:name>",
    # Rooms identity, slice 2 (11 Oct): the owner switches Rooms on or off for a mini-moi account; owner-only, CSRF-checked, no model calls.
    "/guild-next/api/v1/rooms/access",
    # Hand to agents (11 Oct, #298 G1): the owner turns his own room message into ledger requests; owner-only, CSRF-checked.
    "/guild-next/api/v1/rooms/handoff",
    "/guild-next/api/v1/rooms/handoff/sync",
    "/guild-next/api/v1/rooms/access/grant",
    "/guild-next/api/v1/rooms/access/withdraw",
    # Approved agents (11 Oct, Codex): the owner's approval list, per-agent switch and standing brief; owner-only, CSRF-checked.
    "/guild-next/api/v1/rooms/agents",
    "/guild-next/api/v1/rooms/agents/<agent>",
    "/guild-next/api/v1/rooms/agents/<agent>/brief",
    "/guild-next/api/v1/library/links",
    "/guild-next/api/v1/",
    "/guild-next/api/v1/<path:rest>",
}


def test_exactly_the_allowlisted_routes(staging):
    rules = {r.rule for r in staging.app.url_map.iter_rules() if r.rule.startswith("/guild-next")}
    assert rules == EXPECTED
    for word in ("reset", "landing", "proto"):
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
    assert staging.owner().get("/guild-next/guild/not-a-section").status_code == 404
    assert staging.owner().get("/guild-next/nope").status_code == 404
    # The mount's root is the Guild home (test_landing_pairing.py), not a redirect.
    home = staging.owner().get("/guild-next/guild")
    assert home.status_code == 200 and 'data-page="home"' in home.get_data(as_text=True)
