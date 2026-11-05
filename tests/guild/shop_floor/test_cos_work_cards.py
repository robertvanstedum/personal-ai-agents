"""Work cards in CoS Actions (Blue Lantern, 5 Nov 2026): Robert approves or declines MC's delegated work where he works
with CoS. The portal serves the two routes itself (owner gate, CoS write guard), reuses the reviewed admission logic
(the exact card digest shown, once), and the CoS page carries the section and its script."""
from pathlib import Path

import pytest

from tests.guild.shop_floor.test_tasks_owner_api import _queued_card, rig  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[3]
OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 2}
TOKEN = "w" * 43


@pytest.fixture
def owner(portal_client):
    import minimoi_portal.app as portal_app
    with portal_client.session_transaction() as sess:
        sess["user"] = dict(OWNER)
        sess[portal_app.COS_CSRF_SESSION_KEY] = TOKEN
    yield portal_client
    with portal_client.session_transaction() as sess:
        sess.pop("user", None)
        sess.pop(portal_app.COS_CSRF_SESSION_KEY, None)


def headers(token=TOKEN):
    import minimoi_portal.config as cfg
    origin = cfg.BASE_URL.rstrip("/")
    return {"X-CSRF-Token": token, "Origin": origin, "Sec-Fetch-Site": "same-origin"}


def test_robert_sees_the_card_in_cos_and_approves_exactly_what_he_saw(rig, owner):
    svc, guild = rig
    d = _queued_card(svc, guild, "OB-own-card-7", "C-own-0007")
    r = owner.get("/app/cos/work-cards")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store, private"
    rows = r.json["admissions"]
    assert [x["operation_id"] for x in rows] == ["OB-own-card-7"] and rows[0]["card_digest"] == d
    assert rows[0]["card"]["workers"]["builder"]["route"] == "fakework"
    wrong = owner.post("/app/cos/work-cards/approve", json={"operation_id": "OB-own-card-7",
                                                           "card_digest": "sha256:" + "0" * 64}, headers=headers())
    assert wrong.status_code == 409
    ok = owner.post("/app/cos/work-cards/approve", json={"operation_id": "OB-own-card-7", "card_digest": d},
                    headers=headers())
    assert ok.status_code == 200 and ok.json["state"] == "approved", ok.json
    ob = svc.get("outbox", "OB-own-card-7", "tok-principal")
    assert ob["approval"]["card_digest"] == d
    again = owner.post("/app/cos/work-cards/approve", json={"operation_id": "OB-own-card-7", "card_digest": d},
                       headers=headers())
    assert again.status_code == 409                                  # one approval; never a second


def test_a_decision_without_the_cos_write_token_changes_nothing(rig, owner):
    svc, guild = rig
    d = _queued_card(svc, guild, "OB-own-card-8", "C-own-0008")
    for h in ({}, headers("x" * 43)):
        r = owner.post("/app/cos/work-cards/approve", json={"operation_id": "OB-own-card-8", "card_digest": d}, headers=h)
        assert r.status_code == 403, r.json
    ob = svc.get("outbox", "OB-own-card-8", "tok-principal")
    assert ob.get("approval") is None and ob["state"] == "pending"


def test_decline_from_cos_and_unknown_decisions(rig, owner):
    svc, guild = rig
    d = _queued_card(svc, guild, "OB-own-card-9", "C-own-0009")
    assert owner.post("/app/cos/work-cards/accept-all", json={}, headers=headers()).status_code == 404
    r = owner.post("/app/cos/work-cards/decline", json={"operation_id": "OB-own-card-9", "card_digest": d},
                   headers=headers())
    assert r.status_code == 200 and r.json["state"] == "declined"
    assert svc.get("outbox", "OB-own-card-9", "tok-principal")["state"] == "declined"


def test_only_the_owner_reaches_work_cards(portal_client):
    with portal_client.session_transaction() as sess:
        sess["user"] = {"username": "guest@example.com", "tier": "guest", "display_name": "G", "auth_id": 9}
    try:
        assert portal_client.get("/app/cos/work-cards").status_code in (302, 403)
        assert portal_client.post("/app/cos/work-cards/approve", json={}).status_code in (302, 403)
    finally:
        with portal_client.session_transaction() as sess:
            sess.pop("user", None)


def test_the_cos_actions_page_carries_the_work_cards_section_and_script():
    page = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    assert "data-wc" in page and "cos-work-cards.js" in page
    js = (ROOT / "domains/cos/static/cos-work-cards.js").read_text()
    assert "/work-cards" in js and "card_digest: a.card_digest" in js and "load()" in js
    assert "cos-work-cards.js" in (ROOT / "core/founders_ui/__init__.py").read_text()
