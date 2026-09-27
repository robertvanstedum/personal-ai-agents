"""Review S6 and decision 4: rows are scoped by floor, not by the writer, so a
post-it written by Master Craftsman is on Robert's floor, shows its writer,
and Robert can bin it. Continue stays per principal. The API never lets a
caller write as someone else."""
from __future__ import annotations

from floor_helpers import OWNER, write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN

API = "/guild-next/api/v1"


def test_a_master_craftsman_post_it_is_visible_to_robert_and_he_can_bin_it(floored):
    db = floored.extra["floor"]
    mc = db.store().add_postit("Queue #31 has been blocked for five days", MASTER_CRAFTSMAN,
                               idempotency_key="mc-direct-0001").value
    client = floored.owner()
    board = client.get(f"{API}/postits").get_json()["postits"]
    assert [(p["text"], p["author_label"], p["author_kind"]) for p in board] == [
        ("Queue #31 has been blocked for five days", "Master Craftsman", "agent")]
    rail = client.get(f"{API}/floor").get_json()["postits"]["shown"]
    assert rail[0]["author_label"] == "Master Craftsman"
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert 'data-author-kind="agent">Master Craftsman<' in page
    token = floored.csrf(client)
    binned = client.post(f"{API}/postits/{mc['id']}/bin", json=keyed(), headers=write_headers(token)).get_json()
    assert binned["result"] == "binned" and binned["postit"]["binned_by"] == OWNER["username"]
    assert binned["postit"]["author_label"] == "Master Craftsman"   # the writer is kept


def test_robert_and_master_craftsman_share_one_board(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    client.post(f"{API}/postits", json=keyed(text="from Robert"), headers=write_headers(token))
    db.store().add_postit("from MC", MASTER_CRAFTSMAN, idempotency_key="mc-direct-0002")
    labels = [p["author_label"] for p in client.get(f"{API}/postits").get_json()["postits"]]
    assert labels == ["Master Craftsman", "Robert"]
    mc_view = db.store().list_postits().data["postits"]           # what MC's own tool reads (B3)
    assert [p["text"] for p in mc_view] == ["from MC", "from Robert"]


def test_another_floor_is_not_shown(floored):
    db = floored.extra["floor"]
    db.store(floor="elsewhere").add_postit("not this floor", MASTER_CRAFTSMAN, idempotency_key="other-floor-01")
    assert floored.owner().get(f"{API}/postits").get_json()["postits"] == []


def test_the_api_never_writes_as_anyone_else(floored):
    client = floored.owner()
    token = floored.csrf(client)
    body = client.post(f"{API}/postits", json=keyed(text="me", author="master_craftsman", author_label="Master Craftsman",
                                                    author_kind="agent"), headers=write_headers(token)).get_json()
    assert body["postit"]["author"] == OWNER["username"] and body["postit"]["author_label"] == "Robert"
    note = client.post(f"{API}/notes", json=keyed(text="me too", who="platform"), headers=write_headers(token)).get_json()
    assert note["note"]["who"] == OWNER["username"] and note["note"]["author_kind"] == "owner"


def test_continue_is_per_principal(load_portal, floor_db):
    from floor_db_helpers import attach
    portal = load_portal()
    attach(portal, floor_db.store())
    other = {"username": "second_owner", "tier": "owner", "display_name": "Second"}
    robert, second = portal.owner(), portal.client(other)
    for client, ref in ((robert, 12), (second, 7)):
        token = portal.csrf(client)
        assert client.put(f"{API}/continue", json=keyed(kind="item", ref=ref), headers=write_headers(token)).status_code == 200
    assert robert.get(f"{API}/continue").get_json()["continue"]["ref"] == "12"
    assert second.get(f"{API}/continue").get_json()["continue"]["ref"] == "7"
    # Post-its are the floor's, so both see the same board.
    token = portal.csrf(second)
    second.post(f"{API}/postits", json=keyed(text="from second"), headers=write_headers(token))
    assert [p["author_label"] for p in robert.get(f"{API}/postits").get_json()["postits"]] == ["Second"]
