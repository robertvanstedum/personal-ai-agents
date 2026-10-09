"""Every write needs the session token, JSON, and a same-site request; and says
whether it is on the record (spec §5.3, reviews S7 and S8). A refused write
touches nothing."""
from __future__ import annotations

import pytest

from domains.guild import queue_store as qs

from floor_helpers import write_headers
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)
from minimoi_portal.guild_ui.security import OFF_RECORD_TEXT

URL = "/guild-next/api/v1/queue/items/12/status"


def _body(portal, to="done"):
    item = next(i for i in qs.QueueStore(str(portal.queue_path)).read_items() if i["id"] == 12)
    return {"to": to, "expect_item_digest": qs.item_digest(item), "idempotency_key": "k" * 16}


def _unchanged(portal, before):
    return portal.queue_path.read_bytes() == before and portal.journal() == []


@pytest.mark.parametrize("case", ["missing", "wrong", "form", "cross_origin", "cross_site", "null_origin"])
def test_refused_writes_are_403_csrf_and_write_nothing(staging, case):
    client = staging.owner()
    token = staging.csrf(client)
    before = staging.queue_path.read_bytes()
    body = _body(staging)
    if case == "missing":
        response = client.post(URL, json=body, headers={"X-Record-Mode": "on_record"})
    elif case == "wrong":
        response = client.post(URL, json=body, headers=write_headers("x" * 43))
    elif case == "form":
        response = client.post(URL, data=body, headers=write_headers(token))
    elif case == "cross_origin":
        response = client.post(URL, json=body, headers=write_headers(token, origin="https://evil.example"))
    elif case == "cross_site":
        response = client.post(URL, json=body, headers=write_headers(token, fetch_site="cross-site"))
    else:
        response = client.post(URL, json=body, headers=write_headers(token, origin="null"))
    assert response.status_code == 403
    assert response.get_json()["error"] == "csrf"
    assert _unchanged(staging, before)


def test_https_origin_through_the_tunnel_is_accepted(staging):
    """Behind the tunnel the portal sees plain HTTP while the browser sends an
    https Origin for the same host; the host matches, so the write proceeds."""
    client = staging.owner()
    token = staging.csrf(client)
    # The test client serves "localhost" over plain HTTP; the browser's Origin is https.
    response = client.post(URL, json=_body(staging), base_url="http://localhost",
                           headers=write_headers(token, origin="https://localhost", fetch_site="same-origin"))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["result"] == "saved"


def test_configured_base_url_host_is_accepted_when_the_host_header_differs(staging):
    client = staging.owner()
    token = staging.csrf(client)
    response = client.post(URL, json=_body(staging), base_url="http://localhost:5001",
                           headers=write_headers(token, origin="https://dev.minimoi.ai"))
    assert response.status_code == 200


def test_forwarded_headers_are_not_trusted(staging):
    client = staging.owner()
    token = staging.csrf(client)
    before = staging.queue_path.read_bytes()
    response = client.post(URL, json=_body(staging), base_url="http://localhost:5001",
                           headers={**write_headers(token, origin="https://evil.example"),
                                    "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert response.status_code == 403 and _unchanged(staging, before)


def test_record_mode_is_required_on_every_write(staging):
    client = staging.owner()
    token = staging.csrf(client)
    before = staging.queue_path.read_bytes()
    headers = {"X-CSRF-Token": token}
    for url in (URL, "/guild-next/api/v1/queue/journal/" + "b" * 32 + "/checked"):
        response = client.post(url, json=_body(staging), headers=headers)
        assert response.status_code == 422 and response.get_json()["error"] == "invalid", url
        response = client.post(url, json=_body(staging), headers={**headers, "X-Record-Mode": "maybe"})
        assert response.status_code == 422, url
    assert _unchanged(staging, before)


def test_body_and_header_record_modes_must_agree(staging):
    client = staging.owner()
    token = staging.csrf(client)
    response = client.post(URL, json={**_body(staging), "record_mode": "off_record"}, headers=write_headers(token))
    assert response.status_code == 422


def test_off_the_record_is_refused_with_platform_text_before_the_queue_is_touched(staging, monkeypatch):
    services = staging.app.extensions["guild_ui_next"]["services"]

    def boom(*_a, **_k):
        raise AssertionError("the store must not be reached off the record")

    monkeypatch.setattr(services.store, "save_status", boom)
    monkeypatch.setattr(services.store, "mark_checked", boom)
    monkeypatch.setattr(services.store, "read_items", boom)
    client = staging.owner()
    token = staging.csrf(client)
    for url in (URL, "/guild-next/api/v1/queue/journal/" + "c" * 32 + "/checked"):
        response = client.post(url, json=_body_static(), headers=write_headers(token, mode="off_record"))
        assert response.status_code == 409, url
        body = response.get_json()
        assert body["error"] == "not_listening"
        assert body["message"] == OFF_RECORD_TEXT
        assert "MiniMoi keeps no notes" in body["message"]


def _body_static():
    return {"to": "done", "expect_item_digest": "0" * 64}


def test_csrf_token_is_per_session_and_stable(staging):
    a, b = staging.owner(), staging.owner()
    ta, tb = staging.csrf(a), staging.csrf(b)
    assert ta != tb and staging.csrf(a) == ta and len(ta) >= 40
    response = b.post(URL, json=_body(staging), headers=write_headers(ta))
    assert response.status_code == 403
