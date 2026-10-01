"""Guild 1.1 slice 4 (spec §6, §11, R4): Rooms on Records through the portal's
dev-only bridge. Records keeps its own sign-in and its own grant checks; the
portal never forwards its cookie or identity and never injects a credential;
the bridge reaches exactly one configured internal origin and is never
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

ROOM = {"title": "Screen review", "purpose": "Review the Rooms slice", "mode": "meeting", "recording_acknowledged": True}


@pytest.fixture
def records(tmp_path):
    return records_app(tmp_path / "records")


@pytest.fixture
def bridged(load_portal, records, monkeypatch):
    monkeypatch.setenv("RECORDS_BACKEND", BACKEND)
    transport = RecordsTransport(records)
    monkeypatch.setattr(RB, "_TRANSPORT", transport)
    portal = load_portal()
    portal.extra.update(records=records, transport=transport)
    return portal


def _signed_in(portal):
    client = dev_owner(portal)
    r = records_login(client, owner_key(portal.extra["records"]))
    assert r.status_code == 200, r.data
    return client


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

def test_without_a_records_login_the_api_answers_401_and_the_page_can_say_sign_in(bridged):
    client = dev_owner(bridged)
    r = rget(client, "/v1/rooms")
    assert r.status_code == 401 and "Sign in" in r.get_json()["error"]
    page = client.get("/guild-next/guild/rooms", base_url=DEV).get_data(as_text=True)
    assert "Sign in to Rooms" in page and 'href="/app/records/"' in page
    assert '"state": "on"' in page


def test_the_portal_owner_guard_comes_first(bridged):
    from floor_helpers import GUEST
    guest = bridged.app.test_client()
    with guest.session_transaction(base_url=DEV) as sess:
        sess["user"] = dict(GUEST)
    for client in (guest, bridged.app.test_client()):
        assert rget(client, "/v1/rooms").status_code in (302, 403)
    assert bridged.extra["transport"].calls == []                       # never reached Records


def test_no_portal_cookie_or_identity_reaches_records_and_the_records_cookie_is_scoped(bridged):
    client = _signed_in(bridged)
    cookie = client.get_cookie(RB.COOKIE, domain="dev.minimoi.ai", path="/app/records/")
    assert cookie is not None and cookie.path == "/app/records/" and cookie.http_only and cookie.secure
    assert cookie.same_site == "Strict"
    calls = bridged.extra["transport"].calls
    n = len(calls)
    client.post("/app/records/api/v1/rooms", base_url=DEV, data=json.dumps(ROOM), content_type="application/json",
                headers={"Origin": DEV, "Idempotency-Key": "identity-0001", "Authorization": "Bearer not-forwarded",
                         "X-Forwarded-User": "robert", "X-User": "robert", "X-CSRF-Token": "portal-token",
                         "X-Forwarded-For": "1.2.3.4", "Host": "dev.minimoi.ai"})
    sent = calls[n]
    assert sent["url"] == f"{BACKEND}/api/v1/rooms" and sent["allow_redirects"] is False
    assert set(sent["headers"]) <= {"Accept", "Content-Type", "Idempotency-Key", "Cookie", "Origin"}
    assert sent["headers"]["Cookie"].startswith(f"{RB.COOKIE}=") and "session=" not in sent["headers"]["Cookie"]
    assert ";" not in sent["headers"]["Cookie"]                         # the Records cookie only
    assert sent["headers"]["Origin"] == BACKEND and sent["headers"]["Idempotency-Key"] == "identity-0001"
    for call in calls:
        assert "Authorization" not in call["headers"]                    # the owner credential is never injected


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
    agent = rpost(owner_client, "/v1/principals", {"id": "claude-code", "label": label}, "principal-0001").get_json()
    rpost(owner_client, f"/v1/rooms/{room_id}/members", {"actor": "claude-code", "role": "contributor"}, "member-0001")
    issued = rpost(owner_client, "/v1/platform/credentials", {
        "principal": "claude-code", "label": "Claude Code on the Mac",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "grants": {room_id: ["read", "post"]}}, "credential-0001")
    assert issued.status_code == 201, issued.data
    return agent, issued.get_json()["access_token"]


def test_a_room_without_a_grant_is_403_and_leaks_nothing(bridged):
    owner = _signed_in(bridged)
    allowed = _room(owner, "room-a-0001")
    secret = rpost(owner, "/v1/rooms", {**ROOM, "title": "Private budget talk", "purpose": "Not for agents"},
                   "room-b-0001").get_json()["result"]
    _agent, token = _agent_with_grant_on(owner, bridged.extra["records"], allowed["id"])
    agent = dev_owner(bridged)                                            # another browser, same portal owner
    assert records_login(agent, token).status_code == 200
    denied = rget(agent, f"/v1/rooms/{secret['id']}")
    assert denied.status_code == 403
    assert "Private budget talk" not in denied.get_data(as_text=True) and "Not for agents" not in denied.get_data(as_text=True)
    listed = rget(agent, "/v1/rooms").get_json()["rooms"]
    assert [r["id"] for r in listed] == [allowed["id"]]
    assert rget(agent, f"/v1/rooms/{allowed['id']}").status_code == 200


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


def test_files_may_be_untagged_and_keep_their_provenance_within_2_000_000_bytes(bridged):
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
                    {"name": "big.bin", "base64": base64.b64encode(b"x" * 2_000_001).decode()}, "file-0002")
    assert too_big.status_code == 400 and "2,000,000" in too_big.get_json()["error"]


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
