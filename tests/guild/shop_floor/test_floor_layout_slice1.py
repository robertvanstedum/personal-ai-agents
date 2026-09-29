"""Guild 1.1 dev, slice 1: the Shop floor layout (Robert, September 29).
Desktop: history | clean chat | the Build card and live context; Needs you in
one place; a quiet rail; chat blockers above the composer; the status strip
low; post-its on the wall; coherent navigation. Server side; the browser side
is in browser_checks_b1.py."""
from __future__ import annotations

from pathlib import Path

from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

from minimoi_portal.guild_ui.floor_state import chat_blockers, conversation_focus

REPO = Path(__file__).resolve().parents[3]


def _floor(staging):
    return staging.owner().get("/guild-next/guild/build").get_data(as_text=True)


def test_the_floor_is_history_chat_and_the_build_card_rail(staging):
    page = _floor(staging)
    assert page.count('class="fh-row"') == 1                                   # one real thread, no fake rows
    assert "Shop floor thread" in page
    assert 'data-build-card' in page and ">Build<" in page and "spec → build → ship" in page
    for label, href in (("Queue", "/guild-next/guild/build/queue"), ("Log", "/guild/build"),
                        ("Roadmap", "/guild/build/roadmap"), ("Docs", "/guild/docs")):
        assert f'<a href="{href}">{label}</a>' in page, label
    assert '<link rel="stylesheet" href="/static/guild/guild-card.css">' in page
    assert 'data-mode="rail"' not in page and "data-postit=" not in page        # no post-it stack on the floor
    assert page.index('data-layout') < page.index('data-zone="status"')          # the strip is low on the page


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


def test_navigation_and_the_truthful_labs_page(staging):
    page = _floor(staging)
    for label in ("Shop floor", "Wall", "Queue", "Workshop", "Operate", "Build Log", "Planning Studio", "Prototype Lab"):
        assert f">{label}</a>" in page, label
    labs = staging.owner().get("/guild-next/guild/labs")
    body = labs.get_data(as_text=True)
    assert labs.status_code == 200 and "neither is served on dev yet" in body
    assert "planning-studio/" in body and "prototype-lab/" in body
    assert staging.guest().get("/guild-next/guild/labs").status_code in (302, 403)


def test_the_wall_has_continue_filters_and_room_for_cards(staging):
    wall = staging.owner().get("/guild-next/guild/build/bench").get_data(as_text=True)
    for f in ("all", "needs", "continue", "postits", "motion", "blocked", "discussions"):
        assert f'data-wall-filter="{f}"' in wall
    assert 'data-panel="continue"' in wall and 'data-page-open="true"' in wall   # one readable column on a phone
    assert "Current conversation · Shop floor thread" in wall
