"""W4, W8 and decision 5: Continue ("the work item you were on") is server data
per principal, shared by every front end. Two clients (two sessions, a
desktop and a phone user agent) see the same Continue; bench layout stays
per device and is not part of it."""
from __future__ import annotations

from floor_helpers import OWNER, write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import DownFloor, attach, floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"
PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def _put(client, token, ref, ua, **extra):
    return client.put(f"{API}/continue", json=keyed(kind="item", ref=ref, **extra),
                      headers={**write_headers(token), "User-Agent": ua})


def test_nothing_to_continue_by_default(floored):
    body = floored.owner().get(f"{API}/continue").get_json()
    assert body["continue"] is None and body["text"] == "Nothing to continue — open a queue item"
    assert body["available"] is True


def test_continue_round_trips_across_two_clients(floored):
    desktop, phone = floored.owner(), floored.owner()      # two sessions, same owner
    t_desk, t_phone = floored.csrf(desktop), floored.csrf(phone)
    assert t_desk != t_phone
    response = _put(desktop, t_desk, 12, DESKTOP_UA)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["result"] == "set" and body["text"] == "#12 Floor API"
    assert body["continue"]["href"] == "/guild-next/guild/build/items/12"

    seen = phone.get(f"{API}/continue", headers={"User-Agent": PHONE_UA}).get_json()
    assert seen["continue"]["ref"] == "12" and seen["text"] == "#12 Floor API"
    floor = phone.get(f"{API}/floor", headers={"User-Agent": PHONE_UA}).get_json()["continue"]
    assert floor["state"] == "ok" and floor["target"]["href"] == "/guild-next/guild/build/items/12"
    page = phone.get("/guild-next/guild/build", headers={"User-Agent": PHONE_UA}).get_data(as_text=True)
    assert 'href="/guild-next/guild/build/items/12" data-continue-link>#12 Floor API</a>' in page
    queue_page = phone.get("/guild-next/guild/build/queue", headers={"User-Agent": PHONE_UA}).get_data(as_text=True)
    assert 'class="ps-continue small">Continue: ' in queue_page and "#12 Floor API" in queue_page

    # The phone moves on; the desktop follows.
    assert _put(phone, t_phone, 7, PHONE_UA).status_code == 200
    assert desktop.get(f"{API}/continue").get_json()["text"] == "#7 Queue lock hardening"
    rows = floored.extra["floor"].rows("floor_continue")
    assert len(rows) == 1 and rows[0]["principal"] == OWNER["username"] and rows[0]["ref"] == "7"


def test_opening_an_item_page_does_not_write_continue_by_itself(floored):
    """GET never writes: the page's script sends PUT /continue on the record
    (browser check W4), so off the record nothing is sent."""
    client = floored.owner()
    client.get("/guild-next/guild/build/items/12")
    assert floored.extra["floor"].count("floor_continue") == 0


def test_continue_takes_only_a_real_queue_item(floored):
    client = floored.owner()
    token = floored.csrf(client)
    assert _put(client, token, 999, DESKTOP_UA).status_code == 404
    bad = client.put(f"{API}/continue", json=keyed(kind="topic", ref=12), headers=write_headers(token))
    assert bad.status_code == 422
    assert client.put(f"{API}/continue", json=keyed(kind="item", ref="12"), headers=write_headers(token)).status_code == 422
    assert client.put(f"{API}/continue", json=keyed(kind="item", ref=True), headers=write_headers(token)).status_code == 422
    assert floored.extra["floor"].count("floor_continue") == 0


def test_the_label_comes_from_the_queue_not_the_client(floored):
    client = floored.owner()
    token = floored.csrf(client)
    body = _put(client, token, 12, DESKTOP_UA, label="<script>x</script>").get_json()
    assert body["text"] == "#12 Floor API"


def test_an_unreadable_queue_leaves_continue_unchanged(floored):
    client = floored.owner()
    token = floored.csrf(client)
    _put(client, token, 12, DESKTOP_UA)
    floored.queue_path.write_text("[{")
    response = _put(client, token, 7, DESKTOP_UA)
    assert response.status_code == 503 and response.get_json()["error"] == "unavailable"
    assert floored.extra["floor"].rows("floor_continue")[0]["ref"] == "12"


def test_a_replayed_old_continue_does_not_move_it_back(floored):
    client = floored.owner()
    token = floored.csrf(client)
    old = {"kind": "item", "ref": 12, "idempotency_key": "contkey-0001"}
    client.put(f"{API}/continue", json=old, headers=write_headers(token))
    _put(client, token, 7, DESKTOP_UA)
    replay = client.put(f"{API}/continue", json=old, headers=write_headers(token)).get_json()
    assert replay["repeated"] is True and replay["text"] == "#7 Queue lock hardening"
    assert floored.extra["floor"].rows("floor_continue")[0]["ref"] == "7"


def test_continue_unavailable_is_said_not_blank(load_portal):
    portal = load_portal()
    attach(portal, DownFloor())
    client = portal.owner()
    body = client.get(f"{API}/continue").get_json()
    assert body["available"] is False and body["continue"] is None
    assert body["text"] == "Continue unavailable — treat as unknown"
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "Continue unavailable — treat as unknown" in page and "Nothing to continue" not in page
