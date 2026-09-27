"""W6 and decision 4: post-its are added and removed directly (no proposal, no
confirm, no receipt), removed ones go to a kept bin that can be viewed and
restored, each shows its writer, four are shown on the rail, and they
trigger nothing. The same on desktop and phone: the phone reaches the bin."""
from __future__ import annotations

import re

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def _post(portal, client, token, path, **body):
    return client.post(f"{API}{path}", json=keyed(**body), headers=write_headers(token))


def _add(portal, client, token, text):
    response = _post(portal, client, token, "/postits", text=text)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["postit"]


def test_add_remove_restore_round_trip(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    added = _post(floored, client, token, "/postits", text="Check the lock timeout")
    body = added.get_json()
    assert added.status_code == 200 and body["result"] == "added" and body["message"] == "Post-it added"
    for word in ("receipt", "receipt_id", "proposal", "confirm"):
        assert word not in body, word           # direct: no proposal, confirm or receipt
    postit = body["postit"]
    assert postit["author_label"] == "Robert" and postit["author_kind"] == "owner" and postit["state"] == "active"
    board = client.get(f"{API}/postits").get_json()
    assert [p["text"] for p in board["postits"]] == ["Check the lock timeout"] and board["bin_total"] == 0

    removed = _post(floored, client, token, f"/postits/{postit['id']}/bin")
    assert removed.status_code == 200 and removed.get_json()["result"] == "binned"
    assert client.get(f"{API}/postits").get_json()["postits"] == []
    bin_ = client.get(f"{API}/postits/bin").get_json()
    assert bin_["total"] == 1 and bin_["bin"][0]["text"] == "Check the lock timeout"
    assert bin_["bin"][0]["binned_by_label"] == "Robert" and bin_["bin"][0]["binned_at"]
    row = db.rows("floor_postits")[0]
    assert row["binned_at"] and row["text"] == "Check the lock timeout"   # kept, not deleted

    restored = _post(floored, client, token, f"/postits/{postit['id']}/restore")
    assert restored.status_code == 200 and restored.get_json()["result"] == "restored"
    assert [p["id"] for p in client.get(f"{API}/postits").get_json()["postits"]] == [postit["id"]]
    assert client.get(f"{API}/postits/bin").get_json()["total"] == 0
    row = db.rows("floor_postits")[0]
    assert row["binned_at"] is None and row["restored_at"]
    assert row["binned_by"] == "robert" and row["restored_by"] == "robert"   # history kept (review B1c #8)
    assert restored.get_json()["postit"]["restored_by_label"] == "Robert"
    assert db.count("floor_postits") == 1


def test_a_second_remove_is_already_in_the_bin_and_restore_twice_is_harmless(floored):
    client = floored.owner()
    token = floored.csrf(client)
    postit = _add(floored, client, token, "twice")
    assert _post(floored, client, token, f"/postits/{postit['id']}/bin").get_json()["result"] == "binned"
    again = _post(floored, client, token, f"/postits/{postit['id']}/bin")
    assert again.status_code == 200 and again.get_json()["result"] == "already_binned"
    assert _post(floored, client, token, f"/postits/{postit['id']}/restore").get_json()["result"] == "restored"
    assert _post(floored, client, token, f"/postits/{postit['id']}/restore").get_json()["result"] == "already_active"
    missing = _post(floored, client, token, "/postits/99999/bin")
    assert missing.status_code == 404 and missing.get_json()["error"] == "not_found"


def test_a_retried_add_is_kept_once_and_a_reused_key_for_other_text_is_refused(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    body = {"text": "retry me", "idempotency_key": "addkey-0001"}
    first = client.post(f"{API}/postits", json=body, headers=write_headers(token)).get_json()
    second = client.post(f"{API}/postits", json=body, headers=write_headers(token)).get_json()
    assert first["postit"]["id"] == second["postit"]["id"] and second["repeated"] is True
    other = client.post(f"{API}/postits", json={**body, "text": "different"}, headers=write_headers(token))
    assert other.status_code == 409 and other.get_json()["error"] == "idempotency_mismatch"
    assert db.count("floor_postits") == 1


def test_a_replayed_remove_after_a_restore_changes_nothing(floored):
    client = floored.owner()
    token = floored.csrf(client)
    postit = _add(floored, client, token, "replay")
    remove = {"idempotency_key": "binkey-0001"}
    client.post(f"{API}/postits/{postit['id']}/bin", json=remove, headers=write_headers(token))
    _post(floored, client, token, f"/postits/{postit['id']}/restore")
    replay = client.post(f"{API}/postits/{postit['id']}/bin", json=remove, headers=write_headers(token)).get_json()
    assert replay["repeated"] is True
    assert [p["id"] for p in client.get(f"{API}/postits").get_json()["postits"]] == [postit["id"]]


def test_the_rail_shows_four_and_links_the_rest_and_the_bin(floored):
    client = floored.owner()
    token = floored.csrf(client)
    for n in range(6):
        _add(floored, client, token, f"post-it {n}")
    zone = client.get(f"{API}/floor").get_json()["postits"]
    assert zone["cap"] == 4 and len(zone["shown"]) == 4 and zone["active_total"] == 6 and zone["more"] == 2
    assert [p["text"] for p in zone["shown"]] == ["post-it 5", "post-it 4", "post-it 3", "post-it 2"]
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    rail = page[page.index('data-mode="rail"'):page.index("</section>", page.index('data-mode="rail"'))]
    assert len(re.findall(r'data-postit="\d+"', rail)) == 4
    assert "2 more →" in rail and "Bin (0) →" in rail and "/guild-next/guild/build/postits#bin" in rail
    assert len(client.get(f"{API}/postits").get_json()["postits"]) == 6


def test_post_its_trigger_nothing(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    queue_before = floored.queue_path.read_bytes()
    postit = _add(floored, client, token, "no side effects")
    _post(floored, client, token, f"/postits/{postit['id']}/bin")
    _post(floored, client, token, f"/postits/{postit['id']}/restore")
    assert floored.queue_path.read_bytes() == queue_before and floored.journal() == []
    assert db.count("floor_messages") == 0 and db.count("floor_continue") == 0


def test_post_it_length_and_emptiness(floored):
    client = floored.owner()
    token = floored.csrf(client)
    assert _post(floored, client, token, "/postits", text="x" * 140).status_code == 200
    too_long = _post(floored, client, token, "/postits", text="x" * 141)
    assert too_long.status_code == 422 and "140" in too_long.get_json()["message"]
    assert _post(floored, client, token, "/postits", text="   ").status_code == 422
    assert _post(floored, client, token, "/postits").status_code == 422


@pytest.mark.parametrize("user_agent", [None, PHONE_UA], ids=["desktop", "phone"])
def test_board_and_bin_pages_show_writer_and_restore_on_desktop_and_phone(floored, user_agent):
    client = floored.owner()
    token = floored.csrf(client)
    kept = _add(floored, client, token, "on the board")
    gone = _add(floored, client, token, "in the bin")
    _post(floored, client, token, f"/postits/{gone['id']}/bin")
    headers = {"User-Agent": user_agent} if user_agent else {}
    page = client.get("/guild-next/guild/build/postits", headers=headers)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-page-open="true"' in html          # the phone shows it without an extra tap
    assert f'data-postit-bin="{kept["id"]}"' in html and f'data-postit-restore="{gone["id"]}"' in html
    assert 'id="bin"' in html and "1 in the bin" in html and "kept, never emptied" in html
    assert html.count("data-postit-author") == 2 and ">Robert<" in html
    floor = client.get("/guild-next/guild/build", headers=headers).get_data(as_text=True)
    assert "Bin (1) →" in floor
    queue = client.get("/guild-next/guild/build/queue", headers=headers).get_data(as_text=True)
    assert "data-ps-postits" in queue and "Post-its (1) →" in queue   # phone summary link to the board and bin


def test_the_desktop_bench_panel_shows_the_board_and_the_bin(floored):
    client = floored.owner()
    token = floored.csrf(client)
    postit = _add(floored, client, token, "bench one")
    _post(floored, client, token, f"/postits/{postit['id']}/bin")
    html = client.get("/guild-next/guild/build/bench").get_data(as_text=True)
    panel = html[html.index('data-panel="postits"'):]
    assert 'data-mode="board"' in panel and 'data-mode="bin"' in panel
    assert f'data-postit-restore="{postit["id"]}"' in panel
