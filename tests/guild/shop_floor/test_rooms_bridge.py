"""Guild 1.1 slice 4 (spec §6, §11, R4) and the Rooms identity (draft 3): Rooms on
Records through the portal's dev-only bridge. There is no second sign-in: the
mini-moi login is the identity, a Rooms binding (data the owner controls, checked
live on every call) names the Records principal, and the bridge sends one signed
assertion per request. The portal never forwards its cookie, a Records cookie or a
credential; the bridge reaches exactly one configured internal origin and is never
installed in production. The real Records app (prototype-lab) answers, on a
temporary folder, through a transport into its test client."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.serving import make_server

from minimoi_portal import records_bridge as RB

from floor_helpers import load_portal, write_headers  # noqa: F401  (pytest fixture)
from floor_db_helpers import SqliteFloor, attach, keyed
from rooms_helpers import (BACKEND, DEV, RECORDS_DIR, RecordsTransport, dev_owner, owner_key, records_app,
                           records_login, rget, rpost)

KEY = ("k1", "p" * 48)
ROOM = {"title": "Screen review", "purpose": "Review the Rooms slice", "mode": "meeting", "recording_acknowledged": True}


@pytest.fixture
def records(tmp_path):
    return records_app(tmp_path / "records", portal_key=KEY)


def _bind(directory, rows):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bindings.json").write_text(json.dumps({"version": 1, "bindings": rows}))


def _binding(account="account:robert", principal="robert", active=True):
    return {"account": account, "principal": principal, "active": active, "granted_by": "test", "granted_at": "2026-10-11T00:00:00Z"}


@pytest.fixture
def bridged(load_portal, records, monkeypatch, tmp_path):
    monkeypatch.setenv("RECORDS_BACKEND", BACKEND)
    access = tmp_path / "rooms-access"
    _bind(access, [_binding()])
    keyfile = tmp_path / "portal-bridge.key"
    keyfile.write_text(f"{KEY[0]}:{KEY[1]}\n")
    monkeypatch.setenv("MINIMOI_ROOMS_ACCESS_DIR", str(access))
    monkeypatch.setenv("RECORDS_PORTAL_KEY_FILE", str(keyfile))
    import minimoi_portal.auth as auth
    users = [{"username": "robert", "tier": "owner", "display_name": "Robert"}, {"username": "test-agent-full", "tier": "owner"},
             {"username": "dana", "tier": "family", "display_name": "Dana"}]
    monkeypatch.setattr(auth, "load_users", lambda: list(users))
    transport = RecordsTransport(records)
    monkeypatch.setattr(RB, "_TRANSPORT", transport)
    portal = load_portal()
    portal.extra.update(records=records, transport=transport, access=access, keyfile=keyfile, users=users)
    return portal


def _signed_in(portal):
    """The portal owner: signed in to mini-moi, nothing else (no Records sign-in exists any more)."""
    return dev_owner(portal)


def _room(client, key=None):
    r = rpost(client, "/v1/rooms", ROOM, key or uuid.uuid4().hex)
    assert r.status_code == 201, r.data
    return r.get_json()["result"]


# ── installation and the backend origin ─────────────────────────────────────

def test_the_bridge_is_installed_on_dev_with_one_valid_backend_only(load_portal, monkeypatch):
    monkeypatch.setenv("RECORDS_BACKEND", BACKEND)
    on = load_portal()
    assert on.app.extensions["records_bridge"]["state"] == "on"
    assert {"/app/records", "/app/records/", "/app/records/<path:path>"} <= {r.rule for r in on.app.url_map.iter_rules()}
    prod = load_portal(base_url="https://minimoi.ai")
    assert prod.app.extensions["records_bridge"]["state"] == "off_not_dev"
    assert not [r.rule for r in prod.app.url_map.iter_rules() if r.rule.startswith("/app/records")]
    monkeypatch.setenv("RECORDS_BACKEND", "http://evil.example:18880")
    bad = load_portal()
    assert bad.app.extensions["records_bridge"]["state"] == "refused_backend"
    assert not [r.rule for r in bad.app.url_map.iter_rules() if r.rule.startswith("/app/records")]
    monkeypatch.delenv("RECORDS_BACKEND")
    assert load_portal().app.extensions["records_bridge"]["state"] == "off_no_backend"


@pytest.mark.parametrize("url", [
    "https://minimoi-records:18880", "http://minimoi-records:18880/", "http://minimoi-records:18880/api",
    "http://minimoi-records:18880?x=1", "http://minimoi-records:18880#f", "http://user:pw@minimoi-records:18880",
    "http://user@minimoi-records:18880", "http://localhost:18880", "http://minimoi-portal:5001",
    "http://169.254.169.254", "http://MINIMOI-RECORDS:18880", "http://minimoi-records:99999", " http://minimoi-records:18880",
    "http://minimoi-records.evil.example:18880", "", None, "minimoi-records:18880",
])
def test_the_backend_origin_is_validated_strictly(url):
    with pytest.raises(RB.BackendRefused):
        RB.validate_backend(url)


@pytest.mark.parametrize("url", ["http://minimoi-records:18880", "http://minimoi-records", "http://127.0.0.1:18880"])
def test_the_allowed_backend_origins(url):
    assert RB.validate_backend(url) == url


# ── R4: Records' own sign-in, no identity forwarded ──────────────────────────

def test_a_bound_owner_reaches_rooms_with_no_second_sign_in_and_the_page_has_no_records_login(bridged):
    client = dev_owner(bridged)
    r = rget(client, "/v1/me")
    assert r.status_code == 200 and r.get_json()["id"] == "robert"
    page = client.get("/guild-next/guild/rooms", base_url=DEV).get_data(as_text=True)
    assert "Sign in to Rooms" not in page and 'href="/app/records/"' not in page and "Records sign-in" not in page
    assert '"state": "on"' in page and '"access": "ok"' in page


def test_an_account_without_a_binding_gets_403_and_nothing_reaches_records(bridged):
    other = bridged.app.test_client()
    from floor_helpers import OWNER
    with other.session_transaction(base_url=DEV) as sess:
        sess["user"] = {**OWNER, "username": "test-agent-full"}                    # an owner-tier account with no Rooms binding
    r = rget(other, "/v1/rooms")
    assert r.status_code == 403 and r.get_json()["code"] == "rooms_no_access"
    assert "access to Rooms" in r.get_json()["error"] and "Sign in" not in r.get_json()["error"]
    assert bridged.extra["transport"].calls == []
    page = other.get("/guild-next/guild/rooms", base_url=DEV).get_data(as_text=True)
    assert '"access": "no_access"' in page and "Sign in to Rooms" not in page


def test_a_withdrawn_or_damaged_binding_or_a_missing_key_or_a_removed_account_applies_on_the_very_next_call(bridged, monkeypatch):
    client = dev_owner(bridged)
    access, keyfile = bridged.extra["access"], bridged.extra["keyfile"]
    assert rget(client, "/v1/me").status_code == 200
    _bind(access, [_binding(active=False)])                                   # withdrawn
    assert rget(client, "/v1/me").get_json()["code"] == "rooms_no_access"
    (access / "bindings.json").write_text("{not json")                        # unreadable: refused, not trusted
    r = rget(client, "/v1/me")
    assert r.status_code == 503 and r.get_json()["code"] == "rooms_unavailable"
    _bind(access, [_binding()])
    assert rget(client, "/v1/me").status_code == 200
    keyfile.rename(keyfile.with_suffix(".gone"))                              # the signing key is not in place
    assert rget(client, "/v1/me").status_code == 503
    keyfile.with_suffix(".gone").rename(keyfile)
    assert rget(client, "/v1/me").status_code == 200
    import minimoi_portal.domain_auth as dauth
    monkeypatch.setattr(dauth, "get_user_by_id", lambda _id: None)                                    # no domain row either
    bridged.extra["users"][:] = [u for u in bridged.extra["users"] if u["username"] != "robert"]     # the account no longer exists
    assert rget(client, "/v1/me").get_json()["code"] == "rooms_no_access"
    calls_before = len(bridged.extra["transport"].calls)
    rget(client, "/v1/me")
    assert len(bridged.extra["transport"].calls) == calls_before             # refused before Records


def test_what_records_receives_is_one_signed_assertion_and_never_a_cookie_or_a_credential(bridged):
    client = dev_owner(bridged)
    calls = bridged.extra["transport"].calls
    n = len(calls)
    client.post("/app/records/api/v1/rooms?b=2&a=1", base_url=DEV, data=json.dumps(ROOM), content_type="application/json",
                headers={"Origin": DEV, "Idempotency-Key": "identity-0001", "Authorization": "Bearer not-forwarded",
                         "X-Forwarded-User": "robert", "X-User": "robert", "X-CSRF-Token": "portal-token",
                         "X-Forwarded-For": "1.2.3.4", "Host": "dev.minimoi.ai", "X-Portal-Identity": "client-forged",
                         "Cookie": "minimoi_room_poc=forged"})
    sent = calls[n]
    assert sent["url"].startswith(f"{BACKEND}/api/v1/rooms") and sent["allow_redirects"] is False
    assert set(sent["headers"]) <= {"Accept", "Content-Type", "Idempotency-Key", "Origin", "X-Portal-Identity"}
    assert sent["headers"]["X-Portal-Identity"].startswith("v2.") and "forged" not in sent["headers"]["X-Portal-Identity"]
    assert sent["headers"]["Origin"] == BACKEND and sent["headers"]["Idempotency-Key"] == "identity-0001"
    for call in calls:
        assert "Authorization" not in call["headers"] and "Cookie" not in call["headers"]
    assert not any(c.key == "minimoi_room_poc" for c in client._cookies.values()) if hasattr(client, "_cookies") else True


def test_a_records_refusal_of_the_assertion_is_a_connection_fault_never_a_sign_in_prompt(bridged, tmp_path, monkeypatch):
    wrong = records_app(tmp_path / "records-other-key", portal_key=("k1", "z" * 48))        # Records holds a different secret
    bridged.extra["transport"].app = wrong
    bridged.extra["transport"].client = wrong.test_client(use_cookies=False)
    r = rget(dev_owner(bridged), "/v1/rooms")
    assert r.status_code == 502 and r.get_json()["code"] == "rooms_integration"
    assert "not a sign-in problem" in r.get_json()["error"] and r.status_code != 401
    monkeypatch.delenv("RECORDS_PORTAL_KEY_FILE")                                           # (same process: the portal already holds its own path)
    unkeyed = records_app(tmp_path / "records-no-key")                                       # Records has no key at all
    bridged.extra["transport"].client = unkeyed.test_client(use_cookies=False)
    r = rget(dev_owner(bridged), "/v1/rooms")
    assert r.status_code == 503 and r.get_json()["code"] == "rooms_unavailable"


def test_a_second_person_bound_to_their_own_principal_sees_only_their_rooms(bridged):
    """The binding names the principal, so a second account acts as a member-only person: only invited rooms, no owner routes."""
    owner = _signed_in(bridged)
    shared = _room(owner, "room-shared-0001")
    private = rpost(owner, "/v1/rooms", {**ROOM, "title": "Private budget talk", "purpose": "Not for Dana"}, "room-priv-0001").get_json()["result"]
    store = bridged.extra["records"].extensions["records_store"]
    with store.connect() as db:
        db.execute("INSERT INTO principals VALUES('dana','Dana','human','x','2026-01-01T00:00:00+00:00')")
    assert rpost(owner, f"/v1/rooms/{shared['id']}/members", {"actor": "dana", "role": "contributor"}, "member-dana-0001").status_code == 200
    _bind(bridged.extra["access"], [_binding(), _binding("account:test-agent-full", "dana")])
    dana = bridged.app.test_client()
    from floor_helpers import OWNER
    with dana.session_transaction(base_url=DEV) as sess:
        sess["user"] = {**OWNER, "username": "test-agent-full"}
    assert [r["id"] for r in rget(dana, "/v1/rooms").get_json()["rooms"]] == [shared["id"]]
    denied = rget(dana, f"/v1/rooms/{private['id']}")
    assert denied.status_code in (403, 404) and "Private budget talk" not in denied.get_data(as_text=True)
    assert rget(dana, "/v1/principals").status_code == 403
    assert rget(dana, "/v1/me").get_json()["id"] == "dana"


def test_the_portal_signs_what_records_verifies(tmp_path):
    from minimoi_portal import rooms_identity as RI
    import portal_identity as PI                                                     # Records' side, imported from prototype-lab
    body = b'{"a":1}'
    target = RI.canonical_target("/api/v1/rooms", "z=1&a=2&a=1")
    header = RI.sign(KEY, principal="robert", method="post", target=target, body=body, env="dev")
    db = tmp_path / "n.sqlite"
    import sqlite3

    class Conn:
        def __enter__(self):
            self.c = sqlite3.connect(db, isolation_level=None)
            self.c.executescript(PI.SCHEMA)
            return self.c

        def __exit__(self, *a):
            self.c.close()
    assert PI.verify(header, KEY, env="dev", method="POST", target=PI.canonical_target("/api/v1/rooms", b"a=1&z=1&a=2"), body=body, connect=Conn) == "robert"


def test_the_portal_owner_guard_comes_first(bridged):
    from floor_helpers import GUEST
    guest = bridged.app.test_client()
    with guest.session_transaction(base_url=DEV) as sess:
        sess["user"] = dict(GUEST)
    for client in (guest, bridged.app.test_client()):
        assert rget(client, "/v1/rooms").status_code in (302, 403)
    assert bridged.extra["transport"].calls == []                       # never reached Records


def test_a_cross_origin_or_origin_less_write_through_the_bridge_is_403(bridged):
    client = _signed_in(bridged)
    calls = bridged.extra["transport"].calls
    n = len(calls)
    for origin in ("https://evil.example", "http://dev.minimoi.ai", None):
        assert rpost(client, "/v1/rooms", ROOM, uuid.uuid4().hex, origin=origin).status_code == 403
    assert len(calls) == n                                                # refused before Records


def test_records_writes_keep_idempotency_and_size_limits(bridged):
    client = _signed_in(bridged)
    first = rpost(client, "/v1/rooms", ROOM, "room-create-0001")
    again = rpost(client, "/v1/rooms", ROOM, "room-create-0001")
    assert first.status_code == again.status_code == 201
    assert first.get_json()["receipt"]["id"] == again.get_json()["receipt"]["id"]
    big = client.post("/app/records/api/v1/rooms", base_url=DEV, headers={"Origin": DEV, "Idempotency-Key": "big-0001"},
                      data=b"x" * (RB.MAX_BODY + 1), content_type="application/json")
    assert big.status_code == 413


def test_a_paused_room_refuses_writes_with_409(bridged):
    client = _signed_in(bridged)
    room = _room(client)
    assert rpost(client, f"/v1/rooms/{room['id']}/events", {"body": "first", "kind": "message"}, "msg-0001").status_code == 201
    paused = rpost(client, f"/v1/rooms/{room['id']}/state", {"state": "paused", "version": room["version"],
                                                            "checkpoint": "Pausing for lunch"}, "pause-0001")
    assert paused.status_code == 200
    refused = rpost(client, f"/v1/rooms/{room['id']}/events", {"body": "while paused", "kind": "message"}, "msg-0002")
    assert refused.status_code == 409
    read = rget(client, f"/v1/rooms/{room['id']}").get_json()
    assert read["state"] == "paused" and "while paused" not in json.dumps(read["events"])


def _agent_with_grant_on(owner_client, records, room_id, label="Claude Code"):
    """Provision an agent and its key directly in Records (as the owner's operator tools do): credential management is not reachable
    through the portal on purpose."""
    direct = records.test_client()
    base = {"base_url": BACKEND}
    bearer = {"Authorization": "Bearer " + owner_key(records)}
    agent = direct.post("/api/v1/principals", json={"id": "claude-code", "label": label},
                        headers={**bearer, "Idempotency-Key": "principal-0001"}, **base).get_json()
    direct.post(f"/api/v1/rooms/{room_id}/members", json={"actor": "claude-code", "role": "contributor"},
                headers={**bearer, "Idempotency-Key": "member-0001"}, **base)
    issued = direct.post("/api/v1/platform/credentials", json={
        "principal": "claude-code", "label": "Claude Code on the Mac",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "grants": {room_id: ["read", "post"]}}, headers={**bearer, "Idempotency-Key": "credential-0001"}, **base)
    assert issued.status_code == 201, issued.data
    return agent, issued.get_json()["access_token"]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_a_roomctl_agent_message_is_attributed_to_the_agent(bridged, tmp_path):
    """roomctl (the existing CLI adapter, run as a real process) posts with the
    agent's own key; the owner reads it through the bridge, attributed to the
    agent actor and labelled as an agent."""
    owner = _signed_in(bridged)
    room = _room(owner)
    _agent, token = _agent_with_grant_on(owner, bridged.extra["records"], room["id"])
    port = _free_port()
    loopback = records_app(tmp_path / "records", hosts=(f"127.0.0.1:{port}", "minimoi-records:18880"), port=port)
    server = make_server("127.0.0.1", port, loopback, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        key_file = tmp_path / "agent.key"
        key_file.write_text(token + "\n")
        key_file.chmod(0o600)
        done = subprocess.run([sys.executable, str(RECORDS_DIR / "roomctl.py"), "--url", f"http://127.0.0.1:{port}",
                               "--token-file", str(key_file), "post", room["id"], "--text", "Reviewed p.18: fixed."],
                              capture_output=True, text=True, timeout=30, cwd=str(RECORDS_DIR))
        assert done.returncode == 0, done.stderr
    finally:
        server.shutdown()
    events = rget(owner, f"/v1/rooms/{room['id']}").get_json()["events"]
    posted = [e for e in events if e["body"] == "Reviewed p.18: fixed."]
    assert len(posted) == 1
    assert posted[0]["actor"] == "claude-code" and posted[0]["actor_kind"] == "agent"
    assert posted[0]["actor_label"] == "Claude Code"


def test_files_may_be_untagged_and_keep_their_provenance_within_8_000_000_bytes(bridged):
    import base64
    client = _signed_in(bridged)
    room = _room(client)
    content = b"# notes\nshared without a description\n"
    shared = rpost(client, f"/v1/rooms/{room['id']}/documents",
                   {"name": "notes.md", "base64": base64.b64encode(content).decode()}, "file-0001")
    assert shared.status_code == 201, shared.data
    doc = shared.get_json()["result"]
    assert doc["source_note"] == "" and doc["actor"] == "robert" and doc["created"] and doc["event_id"]
    import hashlib
    assert doc["sha256"] == hashlib.sha256(content).hexdigest()
    read = rget(client, f"/v1/rooms/{room['id']}").get_json()
    assert read["documents"][0]["name"] == "notes.md"
    assert any(e["kind"] == "document" and e["reference"] == doc["id"] for e in read["events"])
    saved = rpost(client, f"/v1/rooms/{room['id']}/artifact-refs", {
        "event_id": doc["event_id"], "kind": "work_artifact", "value": "docs/design/notes.md", "revision": "v1",
        "sha256": doc["sha256"], "label": "notes.md"}, "save-0001")
    assert saved.status_code == 201 and saved.get_json()["result"]["revision"] == "v1"
    too_big = rpost(client, f"/v1/rooms/{room['id']}/documents",
                    {"name": "big.bin", "base64": base64.b64encode(b"x" * 8_000_001).decode()}, "file-0002")
    assert too_big.status_code == 400 and "8,000,000" in too_big.get_json()["error"] and "8,000,001" in too_big.get_json()["error"]


# ── Take to a Room: a stored on-the-record note, by id only ─────────────────

def test_take_to_a_room_answers_the_stored_note_only_and_refuses_text(load_portal, tmp_path):
    from minimoi_portal.guild_ui.stores import Author
    portal = load_portal()
    db = SqliteFloor(tmp_path / "floor")
    attach(portal, db.store())
    robert = Author("robert", "owner", "Robert")
    note = db.store().add_note("take-note-0001", "Ship the Rooms slice after review.", robert).value
    conv = db.store("guild/c-0123456789ab").add_note("take-note-0002", "From a conversation", robert).value
    other = db.store("someone-else").add_note("take-note-0003", "Not yours", robert).value
    client = portal.owner()
    token = portal.csrf(client)
    url = "/guild-next/api/v1/notes/{}/share"
    ok = client.post(url.format(note["id"]), json=keyed(), headers=write_headers(token))
    assert ok.status_code == 200 and ok.get_json()["note"]["text"] == "Ship the Rooms slice after review."
    assert "floor" not in ok.get_json()["note"]
    assert client.post(url.format(conv["id"]), json=keyed(), headers=write_headers(token)).status_code == 200
    assert client.post(url.format(other["id"]), json=keyed(), headers=write_headers(token)).status_code == 404
    assert client.post(url.format(999999), json=keyed(), headers=write_headers(token)).status_code == 404
    raw = client.post(url.format(note["id"]), json=keyed(text="anything from the screen"), headers=write_headers(token))
    assert raw.status_code == 422
    off = client.post(url.format(note["id"]), json=keyed(), headers=write_headers(token, mode="off_record"))
    assert off.status_code == 409


def test_sentry_never_gets_a_rooms_body():
    from minimoi_portal.guild_mounts import sentry_before_send
    event = {"request": {"url": "https://dev.minimoi.ai/app/records/api/v1/rooms/x/events",
                         "data": {"body": "a private room message"}, "cookies": {"minimoi_room_poc": "x"},
                         "headers": {"Cookie": "minimoi_room_poc=x", "Accept": "*/*"}}}
    out = sentry_before_send(event)
    assert "data" not in out["request"] and "cookies" not in out["request"]
    assert "Cookie" not in out["request"]["headers"]


def test_the_records_container_never_ships_in_production():
    from scripts.ci.classify_release import classify
    for path in ("docker/Dockerfile.records", "docker/requirements.records.txt", "docker-compose.records.yml",
                 "scripts/staging/records.sh"):
        assert classify([path]) == ("documents", ()), path
    repo = RECORDS_DIR.parents[2]
    prod = (repo / "docker-compose.prod.yml").read_text()
    assert "records" not in prod.lower() and "RECORDS_BACKEND" not in prod
    staging = (repo / "docker-compose.staging.yml").read_text()
    assert "RECORDS_BACKEND=http://minimoi-records:18880" in staging and "minimoi-staging-records" in staging
    import yaml
    compose = yaml.safe_load((repo / "docker-compose.records.yml").read_text())
    service = compose["services"]["records"]
    assert "ports" not in service and "env_file" not in service and service["networks"] == ["records-net"]
    assert compose["networks"]["records-net"] == {"name": "minimoi-staging-records", "external": True}
    assert yaml.safe_load(staging)["networks"]["records-net"]["internal"] is True    # no egress
    assert 'mkdir -p "$STAGING_ROOT/data/records"' in (repo / "scripts/staging/build.sh").read_text()
    assert os.access(repo / "scripts/staging/records.sh", os.X_OK)


@pytest.mark.parametrize("value,ok", [("minimoi-records:18880", True), ("", True), ("a:80,b:443", True),
                                      ("minimoi-records", False), ("http://x:1", False), ("X:1", False), ("a:1/b", False)])
def test_records_answers_only_exact_configured_hosts(value, ok):
    from rooms_helpers import records_module
    hosts = records_module().configured_hosts
    if ok:
        hosts(value)
    else:
        with pytest.raises(ValueError):
            hosts(value)


# ── #288 review: cookies, loopback ───────────────────────────────────────────

@pytest.mark.parametrize("url", ["http://127.0.0.1:5001", "http://127.0.0.1", "http://127.0.0.1:80"])
def test_loopback_backend_must_not_be_the_portal_itself(url):
    """F7: the backend may not be the portal's own port."""
    with pytest.raises(RB.BackendRefused):
        RB.validate_backend(url)


def test_local_hosts_are_accepted_only_beside_a_loopback_backend():
    """F7: in the staging container (backend minimoi-records) only the dev host is served."""
    assert RB._allowed_host("dev.minimoi.ai", "http://minimoi-records:18880")
    assert not RB._allowed_host("localhost:5001", "http://minimoi-records:18880")
    assert not RB._allowed_host("127.0.0.1:5001", "http://minimoi-records:18880")
    assert RB._allowed_host("127.0.0.1:5001", "http://127.0.0.1:18880")
    assert not RB._allowed_host("evil.example", "http://127.0.0.1:18880")


# ── Slice 2: the owner switches Rooms on or off for an account ────────────────

def _api(client, method, path, body=None):
    from floor_helpers import write_headers
    import minimoi_portal.guild_ui.security as sec
    token = client.get("/guild-next/api/v1/session", base_url=DEV).get_json()["csrf_token"]
    headers = write_headers(token)
    url = f"/guild-next/api/v1{path}"
    if method == "GET":
        return client.get(url, base_url=DEV)
    return client.post(url, base_url=DEV, json={**(body or {}), "idempotency_key": body.get("idempotency_key") if body and "idempotency_key" in body else uuid.uuid4().hex[:24]},
                       headers={**headers, "Origin": DEV})


def _bindings(bridged):
    return {r["account"]: r for r in json.loads((bridged.extra["access"] / "bindings.json").read_text())["bindings"]}


def test_the_owner_sees_every_account_and_who_has_rooms(bridged):
    owner = dev_owner(bridged)
    r = _api(owner, "GET", "/rooms/access")
    assert r.status_code == 200 and r.get_json()["enabled"] is True
    rows = {a["username"]: a for a in r.get_json()["accounts"]}
    assert rows["robert"]["state"] == "granted" and rows["robert"]["is_owner"] is True
    assert rows["dana"]["state"] == "none" and rows["test-agent-full"]["state"] == "none"


def test_granting_creates_the_person_in_records_then_activates_and_is_idempotent(bridged):
    owner = dev_owner(bridged)
    first = _api(owner, "POST", "/rooms/access/grant", {"username": "dana"})
    assert first.status_code == 200 and first.get_json() == {"username": "dana", "state": "granted", "changed": True}
    row = _bindings(bridged)["account:dana"]
    assert row["active"] is True and row["pending"] is False and row["principal"].startswith("u-dana-") and row["granted_by"] == "robert"
    store = bridged.extra["records"].extensions["records_store"]
    with store.connect() as db:
        person = db.execute("SELECT kind FROM principals WHERE id=?", (row["principal"],)).fetchone()
    assert person["kind"] == "human"
    again = _api(owner, "POST", "/rooms/access/grant", {"username": "dana"})
    assert again.status_code == 200 and again.get_json()["changed"] is False
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM principals WHERE id LIKE 'u-dana-%'").fetchone()[0] == 1       # never two people
    events = [json.loads(l)["event"] for l in (bridged.extra["access"] / "audit.jsonl").read_text().splitlines()]
    assert events.count("grant_started") == 1 and events.count("grant_completed") == 1
    assert [a["state"] for a in _api(owner, "GET", "/rooms/access").get_json()["accounts"] if a["username"] == "dana"] == ["granted"]


def test_a_grant_whose_records_step_fails_stays_pending_and_never_active_then_finishes_on_retry(bridged, tmp_path):
    owner = dev_owner(bridged)
    transport = bridged.extra["transport"]
    good = transport.client
    broken = records_app(tmp_path / "records-down", portal_key=("k1", "z" * 48))          # Records refuses the portal: the step cannot finish
    transport.client = broken.test_client(use_cookies=False)
    failed = _api(owner, "POST", "/rooms/access/grant", {"username": "dana"})
    assert failed.status_code == 502 and failed.get_json()["state"] == "pending" and "try again" in failed.get_json()["message"]
    row = _bindings(bridged)["account:dana"]
    assert row["active"] is False and row["pending"] is True                                  # never active before the person exists
    assert [a["state"] for a in _api(owner, "GET", "/rooms/access").get_json()["accounts"] if a["username"] == "dana"] == ["pending"]
    transport.client = good
    done = _api(owner, "POST", "/rooms/access/grant", {"username": "dana"})
    assert done.status_code == 200 and _bindings(bridged)["account:dana"]["active"] is True
    assert _bindings(bridged)["account:dana"]["principal"] == row["principal"]               # the same person, not a second one


def test_withdrawing_applies_at_once_keeps_the_audit_and_never_touches_the_owner(bridged):
    owner = dev_owner(bridged)
    _api(owner, "POST", "/rooms/access/grant", {"username": "dana"})
    gone = _api(owner, "POST", "/rooms/access/withdraw", {"username": "dana"})
    assert gone.status_code == 200 and gone.get_json()["state"] == "withdrawn"
    from minimoi_portal import rooms_identity as RI
    assert RI.RoomsAccess(str(bridged.extra["access"])).decide("account:dana").kind == "no_access"
    assert "withdrawn" in (bridged.extra["access"] / "audit.jsonl").read_text()
    refused = _api(owner, "POST", "/rooms/access/withdraw", {"username": "robert"})
    assert refused.status_code == 409 and _bindings(bridged)["account:robert"]["active"] is True


def test_only_the_owner_changes_access_and_input_is_checked(bridged):
    from floor_helpers import GUEST
    guest = bridged.app.test_client()
    with guest.session_transaction(base_url=DEV) as sess:
        sess["user"] = dict(GUEST)
    assert guest.get("/guild-next/api/v1/rooms/access", base_url=DEV).status_code in (401, 403)
    owner = dev_owner(bridged)
    assert _api(owner, "POST", "/rooms/access/grant", {"username": "nobody-here"}).status_code == 404
    assert _api(owner, "POST", "/rooms/access/grant", {}).status_code == 422
    no_key = owner.post("/guild-next/api/v1/rooms/access/grant", base_url=DEV, json={"username": "dana"},
                        headers={**__import__("floor_helpers").write_headers(owner.get("/guild-next/api/v1/session", base_url=DEV).get_json()["csrf_token"]), "Origin": DEV})
    assert no_key.status_code == 422
    assert owner.post("/guild-next/api/v1/rooms/access/grant", base_url=DEV, json={"username": "dana", "idempotency_key": "abcdefgh1234"}).status_code == 403   # no CSRF token


def test_a_granted_family_account_still_cannot_open_the_owners_rooms_page_yet(bridged):
    """The honest limit of this slice: a grant prepares the person; the portal's owner guard still keeps every Guild page, Rooms included, to the owner."""
    owner = dev_owner(bridged)
    _api(owner, "POST", "/rooms/access/grant", {"username": "dana"})
    dana = bridged.app.test_client()
    with dana.session_transaction(base_url=DEV) as sess:
        sess["user"] = {"username": "dana", "tier": "family", "display_name": "Dana"}
    assert dana.get("/guild-next/guild/rooms", base_url=DEV).status_code in (302, 403)
    assert dana.get("/app/records/api/v1/rooms", base_url=DEV).status_code in (302, 403)
