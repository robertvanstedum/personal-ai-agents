"""Rooms identity from the mini-moi sign-in (draft 3): the portal's signed assertion names one Records principal for one exact request.
A bad, stale, replayed, altered or misdirected assertion is refused and never falls back to a cookie or a key; a person is never an agent;
the worker and credential routes are not reachable this way; membership and owner-only rules still apply."""
import hashlib
import json
import threading
import time
from uuid import uuid4

import pytest

import portal_identity as pi
from app import create_app

KEY = ("k1", "s" * 40)
OTHER_KEY = ("k1", "t" * 40)


def make(tmp_path, key=KEY):
    app = create_app(tmp_path / "private", testing=True, portal_key=key)
    store = app.extensions["records_store"]
    return app, store


def add_human(store, pid, label):
    with store.connect() as db:
        db.execute("INSERT INTO principals VALUES(?,?,?,?,?)", (pid, label, "human", hashlib.sha256(pid.encode()).hexdigest(), "2026-01-01T00:00:00+00:00"))


def call(app, method, path, *, principal="robert", body=None, key=KEY, query="", env="dev", mutate=None, headers=None, now=None, nonce=None, lifetime=30):
    client = app.test_client()
    raw = b"" if body is None else json.dumps(body).encode()
    target = pi.canonical_target(path, query)
    header = pi.sign(key, principal=principal, method=method, target=target, body=raw, env=env, now=now, nonce=nonce, lifetime=lifetime)
    if mutate:
        header = mutate(header)
    hdr = {pi.HEADER: header, **(headers or {})}
    if body is not None:
        hdr["Content-Type"] = "application/json"
    url = path + (("?" + query) if query else "")
    return client.open(url, method=method, data=raw if body is not None else None, headers=hdr)


def test_the_owner_acts_as_robert_with_no_cookie_and_no_key(tmp_path):
    app, store = make(tmp_path)
    r = call(app, "GET", "/api/v1/me")
    assert r.status_code == 200 and r.json["id"] == "robert" and r.json["scope"] == "portal"
    made = call(app, "POST", "/api/v1/rooms", body=dict(title="Via portal", purpose="p", recording_acknowledged=True), headers={"Idempotency-Key": str(uuid4())})
    assert made.status_code == 201
    assert any(x["title"] == "Via portal" for x in call(app, "GET", "/api/v1/rooms").json["rooms"])


def test_bad_signatures_and_misdirected_assertions_are_refused(tmp_path):
    app, store = make(tmp_path)
    assert call(app, "GET", "/api/v1/me", key=OTHER_KEY).status_code == 401              # signed with another secret
    assert call(app, "GET", "/api/v1/me", env="prod").status_code == 401                 # another environment
    assert call(app, "GET", "/api/v1/me", mutate=lambda h: h[:-3] + "AAA").status_code == 401
    assert call(app, "GET", "/api/v1/me", mutate=lambda h: "garbage").status_code == 401
    assert call(app, "GET", "/api/v1/me", mutate=lambda h: "v1." + h.split(".", 1)[1]).status_code == 401
    assert call(app, "GET", "/api/v1/me", lifetime=31).status_code == 401               # longer than the rule allows
    assert call(app, "GET", "/api/v1/me", now=time.time() - 120).status_code == 401      # expired
    assert call(app, "GET", "/api/v1/me", now=time.time() + 120).status_code == 401      # dated in the future


def test_an_assertion_binds_method_path_query_and_body(tmp_path):
    app, store = make(tmp_path)
    room = store.create_room("robert", str(uuid4()), dict(title="R", purpose="p", recording_acknowledged=True))["result"]["id"]
    client = app.test_client()
    raw = json.dumps({"kind": "message", "body": "hello"}).encode()
    header = pi.sign(KEY, principal="robert", method="POST", target=f"/api/v1/rooms/{room}/events", body=raw, env="dev")
    altered = json.dumps({"kind": "message", "body": "HELLO"}).encode()
    send = lambda data, path, method="POST", h=header: client.open(path, method=method, data=data, headers={pi.HEADER: h, "Content-Type": "application/json", "Idempotency-Key": str(uuid4())})
    assert send(altered, f"/api/v1/rooms/{room}/events").status_code == 401           # a changed body
    h2 = pi.sign(KEY, principal="robert", method="POST", target=f"/api/v1/rooms/{room}/events", body=raw, env="dev")
    assert send(raw, f"/api/v1/rooms/{room}/state", h=h2).status_code == 401           # another path
    h3 = pi.sign(KEY, principal="robert", method="GET", target="/api/v1/rooms?a=1", body=b"", env="dev")
    assert client.get("/api/v1/rooms?a=2", headers={pi.HEADER: h3}).status_code == 401  # a changed query
    h4 = pi.sign(KEY, principal="robert", method="GET", target="/api/v1/rooms?a=1&b=2", body=b"", env="dev")
    assert client.get("/api/v1/rooms?b=2&a=1", headers={pi.HEADER: h4}).status_code == 200   # the same query in another order is the same request
    h5 = pi.sign(KEY, principal="robert", method="GET", target="/api/v1/rooms", body=b"", env="dev")
    assert client.post("/api/v1/rooms", json={}, headers={pi.HEADER: h5}).status_code == 401  # another method


def test_an_assertion_works_once_also_across_a_restart_and_when_two_copies_race(tmp_path):
    app, store = make(tmp_path)
    nonce = "n" * 24
    assert call(app, "GET", "/api/v1/me", nonce=nonce).status_code == 200
    assert call(app, "GET", "/api/v1/me", nonce=nonce).status_code == 401              # the same assertion again
    again = create_app(tmp_path / "private", testing=True, portal_key=KEY)             # Records restarted on the same data
    assert call(again, "GET", "/api/v1/me", nonce=nonce).status_code == 401
    results = []
    race = "r" * 24
    barrier = threading.Barrier(6)

    def go():
        barrier.wait()
        results.append(call(app, "GET", "/api/v1/me", nonce=race).status_code)
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200, 401, 401, 401, 401, 401], results                  # exactly one wins


def test_no_key_on_this_records_means_a_portal_identity_is_unavailable_not_trusted(tmp_path):
    app = create_app(tmp_path / "private", testing=True)                               # no key configured
    header = pi.sign(KEY, principal="robert", method="GET", target="/api/v1/me", body=b"", env="dev")
    assert app.test_client().get("/api/v1/me", headers={pi.HEADER: header}).status_code == 503


def test_a_person_is_never_an_agent_or_unknown(tmp_path):
    app, store = make(tmp_path)
    assert call(app, "GET", "/api/v1/me", principal="claude-code").status_code in (403,)   # created below as an agent
    with store.connect() as db:
        db.execute("INSERT INTO principals VALUES('claude-code','Claude Code','agent','x','2026-01-01T00:00:00+00:00')")
    assert call(app, "GET", "/api/v1/me", principal="claude-code").status_code == 403
    assert call(app, "GET", "/api/v1/me", principal="nobody").status_code == 403


def test_a_bad_assertion_never_falls_back_to_a_cookie_or_a_key(tmp_path):
    app, store = make(tmp_path)
    client = app.test_client()
    assert client.post("/api/login", json={"token": store.owner_key}).status_code == 200      # a valid owner cookie exists
    assert client.get("/api/v1/me").status_code == 200
    assert client.get("/api/v1/me", headers={pi.HEADER: "v2.bad.bad"}).status_code == 401    # cookie present, header bad: refused
    assert app.test_client().get("/api/v1/me", headers={pi.HEADER: "v2.bad.bad", "Authorization": "Bearer " + store.owner_key}).status_code == 401


def test_bearer_and_cookie_clients_are_unchanged(tmp_path):
    app, store = make(tmp_path)
    assert app.test_client().get("/api/v1/me", headers={"Authorization": "Bearer " + store.owner_key}).json["id"] == "robert"
    assert app.test_client().get("/api/v1/me").status_code == 401


def test_the_worker_and_credential_routes_are_not_reachable_through_the_portal(tmp_path):
    app, store = make(tmp_path)
    assert call(app, "GET", "/api/v1/platform/credentials").status_code == 403
    assert call(app, "POST", "/api/v1/turns/claim", body={}).status_code == 403
    assert call(app, "GET", "/api/v1/hosted-teammates").status_code == 403
    assert call(app, "POST", "/api/logout", body={}).status_code == 403


def test_a_member_sees_only_their_rooms_and_cannot_use_owner_routes(tmp_path):
    app, store = make(tmp_path)
    add_human(store, "dana", "Dana")
    mine = store.create_room("robert", str(uuid4()), dict(title="Shared", purpose="p", recording_acknowledged=True))["result"]["id"]
    store.create_room("robert", str(uuid4()), dict(title="Private to Robert", purpose="p", recording_acknowledged=True))
    assert call(app, "GET", "/api/v1/rooms", principal="dana").json["rooms"] == []                       # no room until invited
    assert call(app, "POST", "/api/v1/rooms/%s/members" % mine, body={"actor": "dana", "role": "contributor"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 200
    titles = [r["title"] for r in call(app, "GET", "/api/v1/rooms", principal="dana").json["rooms"]]
    assert titles == ["Shared"]
    assert call(app, "GET", "/api/v1/rooms/" + mine, principal="dana").status_code == 200
    assert call(app, "GET", "/api/v1/principals", principal="dana").status_code == 403                  # owner-only stays owner-only
    assert call(app, "POST", "/api/v1/rooms/%s/members" % mine, principal="dana", body={"actor": "dana", "role": "observer"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 403
    assert call(app, "GET", "/api/v1/status", principal="dana").status_code == 403


def test_a_principal_removed_after_the_assertion_was_signed_is_refused_live(tmp_path):
    app, store = make(tmp_path)
    add_human(store, "dana", "Dana")
    assert call(app, "GET", "/api/v1/me", principal="dana").status_code == 200
    with store.connect() as db:
        db.execute("UPDATE principals SET kind='agent' WHERE id='dana'")
    assert call(app, "GET", "/api/v1/me", principal="dana").status_code == 403


def test_only_the_owners_portal_creates_a_mini_moi_person_and_only_a_human_u_id(tmp_path):
    app, store = make(tmp_path)
    ok = call(app, "POST", "/api/v1/portal/principals", body={"id": "u-isabella-3f2a", "label": "Isabella"})
    assert ok.status_code == 200 and ok.json == {"id": "u-isabella-3f2a", "label": "Isabella", "kind": "human", "created": True}
    again = call(app, "POST", "/api/v1/portal/principals", body={"id": "u-isabella-3f2a", "label": "Isabella"})
    assert again.status_code == 200 and again.json["created"] is False                     # idempotent: the same person
    with store.connect() as db:
        assert db.execute("SELECT kind FROM principals WHERE id='u-isabella-3f2a'").fetchone()["kind"] == "human"
        assert db.execute("SELECT COUNT(*) FROM client_credentials WHERE installation LIKE '%u-isabella%'").fetchone()[0] in (0, 1)
    for bad in ("robert", "claude-code", "isabella", "u-", "u-A", "x" * 70, "u-" + "a" * 80):
        assert call(app, "POST", "/api/v1/portal/principals", body={"id": bad, "label": "x"}).status_code in (400, 403, 409), bad
    with store.connect() as db:
        db.execute("INSERT INTO principals VALUES('u-bot','Bot','agent','x','2026-01-01T00:00:00+00:00')")
    assert call(app, "POST", "/api/v1/portal/principals", body={"id": "u-bot", "label": "Bot"}).status_code == 409    # never turns an agent into a person
    add_human(store, "u-dana", "Dana")
    assert call(app, "POST", "/api/v1/portal/principals", principal="u-dana", body={"id": "u-new", "label": "N"}).status_code == 403   # not the owner
    # no cookie or bearer route to it
    client = app.test_client()
    assert client.post("/api/login", json={"token": store.owner_key}).status_code == 200
    assert client.post("/api/v1/portal/principals", json={"id": "u-x", "label": "X"}).status_code == 403
