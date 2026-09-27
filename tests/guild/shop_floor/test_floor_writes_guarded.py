"""Every new (c) endpoint answers like the rest of the API (spec §5, reviews
S7/S8, W9): JSON 401/403 and no redirect; every write needs the CSRF token,
JSON, a same-site request, the record mode and an idempotency key; a refused
write touches nothing in the floor store."""
from __future__ import annotations

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
WRITES = [
    ("POST", "/notes", {"text": "a note"}),
    ("POST", "/postits", {"text": "a post-it"}),
    ("POST", "/postits/1/bin", {}),
    ("POST", "/postits/1/restore", {}),
    ("PUT", "/continue", {"kind": "item", "ref": 12}),
]
READS = ["/notes", "/postits", "/postits/bin", "/continue"]
IDS = [f"{m} {p}" for m, p, _ in WRITES]


def _send(client, method, path, body, headers=None, **kw):
    return client.open(f"{API}{path}", method=method, headers=headers or {}, **kw) if "data" in kw else \
        client.open(f"{API}{path}", method=method, json=body, headers=headers or {})


def _untouched(db, before):
    return {t: db.count(t) for t in ("floor_messages", "floor_postits", "floor_continue", "floor_requests")} == before


def _counts(db):
    return {t: db.count(t) for t in ("floor_messages", "floor_postits", "floor_continue", "floor_requests")}


@pytest.fixture
def one_postit(floored):
    """A post-it with id 1, binned, so bin and restore have a target."""
    from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
    store = floored.extra["floor"].store()
    store.add_postit("target", MASTER_CRAFTSMAN, idempotency_key="target-00001")
    return floored


@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_anonymous_and_guest_writes_are_json_401_403(one_postit, method, path, body):
    db = one_postit.extra["floor"]
    before = _counts(db)
    anonymous = _send(one_postit.client(), method, path, keyed(**body))
    assert anonymous.status_code == 401 and anonymous.get_json()["error"] == "not_signed_in"
    assert "Location" not in anonymous.headers
    guest = _send(one_postit.guest(), method, path, keyed(**body))
    assert guest.status_code == 403 and guest.get_json()["error"] == "not_allowed"
    assert "Location" not in guest.headers
    assert _untouched(db, before)


@pytest.mark.parametrize("path", READS)
def test_anonymous_and_guest_reads_are_json_401_403(floored, path):
    assert floored.client().get(f"{API}{path}").status_code == 401
    assert floored.guest().get(f"{API}{path}").status_code == 403


@pytest.mark.parametrize("case", ["missing", "wrong", "form", "cross_origin", "cross_site"])
@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_csrf_refusals_write_nothing(one_postit, method, path, body, case):
    db = one_postit.extra["floor"]
    client = one_postit.owner()
    token = one_postit.csrf(client)
    before = _counts(db)
    payload = keyed(**body)
    if case == "missing":
        response = _send(client, method, path, payload, {"X-Record-Mode": "on_record"})
    elif case == "wrong":
        response = _send(client, method, path, payload, write_headers("y" * 43))
    elif case == "form":
        response = client.open(f"{API}{path}", method=method, data={k: str(v) for k, v in payload.items()},
                               headers=write_headers(token))
    elif case == "cross_origin":
        response = _send(client, method, path, payload, write_headers(token, origin="https://evil.example"))
    else:
        response = _send(client, method, path, payload, write_headers(token, fetch_site="cross-site"))
    assert response.status_code == 403 and response.get_json()["error"] == "csrf"
    assert _untouched(db, before)


@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_record_mode_and_idempotency_key_are_required(one_postit, method, path, body):
    db = one_postit.extra["floor"]
    client = one_postit.owner()
    token = one_postit.csrf(client)
    before = _counts(db)
    no_mode = _send(client, method, path, keyed(**body), {"X-CSRF-Token": token})
    assert no_mode.status_code == 422
    other_mode = _send(client, method, path, keyed(**body), {"X-CSRF-Token": token, "X-Record-Mode": "maybe"})
    assert other_mode.status_code == 422
    off = _send(client, method, path, keyed(**body), write_headers(token, mode="off_record"))
    assert off.status_code == 409 and off.get_json()["error"] == "not_listening"
    no_key = _send(client, method, path, dict(body), write_headers(token))
    assert no_key.status_code == 422 and "idempotency key" in no_key.get_json()["message"]
    bad_key = _send(client, method, path, {**body, "idempotency_key": "x"}, write_headers(token))
    assert bad_key.status_code == 422
    assert _untouched(db, before)


@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_the_owner_write_succeeds_with_everything_in_place(one_postit, method, path, body):
    client = one_postit.owner()
    token = one_postit.csrf(client)
    if path.endswith("/restore"):
        _send(client, "POST", "/postits/1/bin", keyed(), write_headers(token))
    response = _send(client, method, path, keyed(**body), write_headers(token))
    assert response.status_code == 200, response.get_json()


def test_there_is_no_delete(floored):
    client = floored.owner()
    token = floored.csrf(client)
    for path in ("/postits/1", "/postits/bin", "/notes", "/continue"):
        response = client.delete(f"{API}{path}", json=keyed(), headers=write_headers(token))
        assert response.status_code in (404, 405)


@pytest.mark.parametrize("method,path,body", WRITES + [("GET", p, None) for p in READS],
                         ids=IDS + [f"GET {p}" for p in READS])
def test_an_unexpected_error_on_a_new_endpoint_is_json_500(one_postit, monkeypatch, method, path, body):
    """(b)'s F11 handler covers (c)'s endpoints too: never Flask's HTML 500."""
    store = one_postit.extra["floor"].store()

    def broken(*_a, **_k):
        raise RuntimeError("unexpected (test)")
    for name in ("add_note", "add_postit", "bin_postit", "restore_postit", "set_continue", "list_notes",
                 "list_postits", "list_bin", "get_continue"):
        monkeypatch.setattr(store, name, broken)
    one_postit.app.extensions["guild_ui_next"]["services"].floor = store
    client = one_postit.owner()
    token = one_postit.csrf(client)
    if body is None:
        response = client.get(f"{API}{path}")
    else:
        response = _send(client, method, path, keyed(**body), write_headers(token))
    assert response.status_code == 500 and response.is_json
    assert response.get_json()["error"] == "server_error"
