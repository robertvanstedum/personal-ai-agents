"""Guild 1.1 dev, slice 1: the Shop floor layout (Robert, September 29).
Desktop: history | clean chat | live context; Needs you in one place; a quiet
rail; chat blockers above the composer; the status strip low; post-its on the
wall. Guild 1.1 slice 1 (spec §3) moved the hero into the chat header; the
navigation is in test_guild_nav.py. Server side; the browser side is in
browser_checks_b1.py."""
from __future__ import annotations

import re
from pathlib import Path

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

from minimoi_portal.guild_ui.floor_state import chat_blockers, conversation_focus

REPO = Path(__file__).resolve().parents[3]


def _floor(staging):
    return staging.owner().get("/guild-next/guild/build").get_data(as_text=True)


def test_the_floor_is_history_chat_and_context_with_the_hero_in_the_chat_header(staging):
    # Guild 1.1 slice 1 (spec §3, v4 design): the hero moved from the rail's
    # Build card into the chat's header; there is no hero chooser.
    page = _floor(staging)
    assert page.count('class="fh-row"') == 1                                   # one real thread, no fake rows
    assert "Shop floor thread" in page
    assert page.count("data-chat-hero") == 1 and 'class="mc-floorbar mc-hero"' in page
    assert '<h1 class="hero-name">Master Craftsman' in page and 'class="hero-portrait"' in page
    assert page.count("data-mc-header") == 1                                     # the state is in the hero only
    assert "data-build-card" not in page and "data-build-strip" not in page    # no second hero
    assert "guild-card.css" not in page and "data-floor-hero" not in page
    assert not re.search(r"hero[-_ ]?(chooser|picker|option)|data-hero=", page)
    assert 'data-mode="rail"' not in page and "data-postit=" not in page        # no post-it stack on the floor
    assert page.index('data-chat-hero') < page.index('data-mc-thread')         # the header above the thread
    assert page.index('data-layout') < page.index('data-zone="status"')          # the strip is low on the page


def test_the_chat_hero_images_are_same_origin_and_exist():
    css = (REPO / "minimoi_portal/guild_ui/static/components.css").read_text()
    for name in ("guild-chat-hero.jpg", "guild-mc-portrait.jpg"):
        assert f"url('/static/guild/{name}')" in css
        assert (REPO / "minimoi_portal/static/guild" / name).stat().st_size > 1000


def test_selection_actions_are_on_the_record_only_and_rooms_is_disabled(staging):
    page = _floor(staging)
    bar = page[page.index("data-sel-bar"):]
    bar = bar[:bar.index("</div>")]
    assert "hidden" in bar.split(">", 1)[0]                                      # hidden until text is selected
    assert "data-sel-pin>Pin to Board<" in bar
    assert re.search(r"data-sel-room disabled[^>]*>Take to a Room", bar)
    js = (REPO / "minimoi_portal/guild_ui/static/js/selection.js").read_text()
    assert "live.off || !live.known" in js and "removeAllRanges" in js
    assert "li[data-kind=\"note\"][data-note]" in js                            # stored notes only
    css = (REPO / "minimoi_portal/guild_ui/static/components.css").read_text()
    assert 'body.gu[data-off-record="true"] .sel-bar' in css
    for other in ("/guild-next/guild/build/bench", "/guild-next/guild/operate"):
        assert "data-sel-bar" not in staging.owner().get(other).get_data(as_text=True)


def test_needs_you_is_in_one_place_the_chat_header_badge(staging):
    page = _floor(staging)
    assert page.count("data-needs-badge") == 1 and 'data-reminder-count>1<' in page
    assert 'href="/guild-next/guild/build/bench" data-needs-badge' in page
    assert "Most urgent" in page and "#31" in page                              # the single most urgent action
    assert page.count("Needs you <span") == 1


def test_the_hero_image_is_named_in_exactly_one_place():
    hits = sorted(str(p.relative_to(REPO)) for p in (REPO / "minimoi_portal").rglob("*")
                  if p.suffix in (".html", ".py", ".js", ".css") and "guild-build.jpg" in p.read_text(errors="ignore"))
    assert hits == ["minimoi_portal/templates/guild/_build_card.html"], hits
    card = (REPO / "minimoi_portal/templates/guild/_build_card.html").read_text()
    assert card.count("guild-build.jpg") == 1


def test_the_landing_still_shows_the_same_build_card(load_portal):
    portal = load_portal()
    html = portal.owner().get("/guild").get_data(as_text=True)
    assert '<a href="/guild/build/queue" class="guild-card">' in html
    assert "Queue · Log · Roadmap · Docs" in html and "spec → build → ship" in html and "Queue →" in html
    assert html.count('class="guild-card"') == 4


def test_the_floor_api_carries_the_conversation_focus_and_chat_blockers(floored):
    client = floored.owner()
    floor = client.get("/guild-next/api/v1/floor").get_json()
    assert floor["focus"]["source"] == "continue" and floor["focus"]["target"] is None
    assert floor["blockers"] == [] and floor["cost_level"] is None
    floored.extra["floor"].store().set_continue("robert", kind="item", ref="12", label="#12 Floor API",
                                                idempotency_key="focus-0001")
    floor = client.get("/guild-next/api/v1/floor").get_json()
    assert floor["focus"]["target"]["label"] == "#12 Floor API"
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "Last opened" in page and "#12 Floor API" in page      # labelled as what it is until slice 2 (review F4)


def test_the_conversation_focus_seam_prefers_a_conversations_own_work_item():
    cont = {"state": "ok", "target": {"kind": "item", "ref": "12", "label": "#12"}, "text": "#12"}
    assert conversation_focus(cont)["source"] == "continue"
    own = conversation_focus(cont, {"work_item": {"kind": "item", "ref": "40", "label": "#40 Rooms"}})
    assert own["source"] == "conversation" and own["target"]["ref"] == "40"


def test_chat_blockers_are_mc_down_and_a_bad_cost_level_only():
    assert chat_blockers({"turns": False, "state": "unavailable"}) == []           # MC off by config is not "down"
    down = chat_blockers({"turns": True, "state": "unavailable", "header": "Master Craftsman is unavailable · x"})
    assert down == [{"kind": "mc_down", "text": "Master Craftsman is unavailable · x"}]
    assert chat_blockers({"turns": True, "state": "live"}, "good") == []
    assert chat_blockers({"turns": True, "state": "live"}, "tight") == []
    assert [b["kind"] for b in chat_blockers({"turns": True, "state": "live"}, "stop")] == ["cost"]


def test_the_wall_has_continue_filters_and_room_for_cards(staging):
    wall = staging.owner().get("/guild-next/guild/build/bench").get_data(as_text=True)
    for f in ("all", "needs", "continue", "postits", "motion", "blocked", "discussions"):
        assert f'data-wall-filter="{f}"' in wall
    assert 'data-panel="continue"' in wall and 'data-page-open="true"' in wall   # one readable column on a phone
    assert "Current conversation · Shop floor thread" in wall
