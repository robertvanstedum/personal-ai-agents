"""B1a: the CoS store's internal command and read API, and its one client (spec v0.3 section 4.5, N7).

Holds: it installs only when fully configured; a missing or wrong token is refused in constant time; outcomes travel
in the body; reads answer in the reader_response contract (contracts/cos/v1/read-surface.schema.json); and the client
turns every transport problem into an honest store_unavailable, never a claimed save.
"""
from __future__ import annotations

import pytest
import requests
from flask import Flask

from domains.cos.store import contracts
from domains.cos.store.api import PREFIX, install
from core.canonical_json import digest
from domains.cos.store.client import StoreClient

SCOPE = {"data_scope": "personal", "environment": "dev"}
TURN = {"type": "cos.turn", "id": "cosc-0a1b2c3d4e5f:t-0a1b2c3d4e5f6a"}


def commitment_command(command_id="c1"):
    payload = {"schema": "minimoi.cos.obligation_definition/1", **SCOPE, "id": "OBL-cos-followup-0001", "revision": 0,
               "who": "cos", "what": "Ask MC where the three ideas go", "basis_ref": TURN,
               "recurrence": {"kind": "once", "tz": "America/Chicago", "due": "2026-10-30T18:00:00-05:00"},
               "practice_period": "until_due", "reminder_window": None, "evidence_rule": None,
               "must_not_miss": False, "state": "active"}
    return {"schema": "minimoi.cos.store_command/1", "command_id": command_id, "kind": "commitment.open",
            "target": {"type": "cos.obligation", "id": "OBL-cos-followup-0001"}, "expected_revision": None,
            "actor": "cos", "seat": "cos", "origin": "owner_turn", "basis_ref": TURN, "payload": payload,
            "payload_digest": digest(payload), **SCOPE}


@pytest.fixture
def configured(tmp_path):
    token = tmp_path / "token"
    token.write_text("s3cret-token\n")
    app = Flask(__name__)
    writer = install(app, environ={"COS_STORE_DIR": str(tmp_path / "store"), "COS_STORE_ENVIRONMENT": "dev",
                                   "COS_STORE_TOKEN_FILE": str(token)})
    assert writer is not None
    return app, writer, token


AUTH = {"Authorization": "Bearer s3cret-token"}


@pytest.mark.parametrize("environ", [
    {},
    {"COS_STORE_DIR": "/x", "COS_STORE_ENVIRONMENT": "dev"},
    {"COS_STORE_DIR": "/x", "COS_STORE_ENVIRONMENT": "qa", "COS_STORE_TOKEN_FILE": "/etc/hosts"},
    {"COS_STORE_DIR": "/x", "COS_STORE_ENVIRONMENT": "dev", "COS_STORE_TOKEN_FILE": "/nonexistent/token"},
])
def test_nothing_mounts_unless_fully_configured(environ):
    app = Flask(__name__)
    assert install(app, environ=environ) is None
    assert not any(str(rule).startswith(PREFIX) for rule in app.url_map.iter_rules())


def test_a_missing_or_wrong_token_is_refused(configured):
    app, _, _ = configured
    client = app.test_client()
    assert client.post(f"{PREFIX}/commands", json=commitment_command()).status_code == 401
    assert client.post(f"{PREFIX}/commands", json=commitment_command(),
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(f"{PREFIX}/commands", json=commitment_command(),
                       headers={"X-Minimoi-Actor": "robert"}).status_code == 401


def test_outcomes_travel_in_the_body_and_replay_is_safe(configured):
    app, _, _ = configured
    client = app.test_client()
    first = client.post(f"{PREFIX}/commands", json=commitment_command(), headers=AUTH)
    assert first.status_code == 200 and first.get_json()["outcome"] == "accepted"
    again = client.post(f"{PREFIX}/commands", json=commitment_command(), headers=AUTH).get_json()
    assert again["replayed"] is True and again["receipt_id"] == first.get_json()["receipt_id"]
    assert client.post(f"{PREFIX}/commands", data="not json", headers=AUTH).status_code == 400


def test_reads_answer_in_the_reader_contract(configured):
    app, _, _ = configured
    client = app.test_client()
    missing = client.get(f"{PREFIX}/records/cos.obligation/OBL-cos-followup-0001", headers=AUTH)
    assert missing.status_code == 404
    assert not contracts.errors("read-surface", "reader_response", missing.get_json())
    client.post(f"{PREFIX}/commands", json=commitment_command(), headers=AUTH)
    found = client.get(f"{PREFIX}/records/cos.obligation/OBL-cos-followup-0001", headers=AUTH).get_json()
    assert found["state"] == "ok" and found["value"]["revision"] == 1
    assert not contracts.errors("read-surface", "reader_response", found)
    assert client.get(f"{PREFIX}/records/cos.journal/x", headers=AUTH).status_code == 404
    assert client.get(f"{PREFIX}/records/cos.goal/..%2Fescape", headers=AUTH).status_code == 404
    occ = client.get(f"{PREFIX}/occurrences/OBL-cos-followup-0001@2026-10-30", headers=AUTH).get_json()
    assert occ["state"] == "open" and occ["open_claims"] == []


class _FlaskSession:
    """A requests-like session over a Flask test client."""

    def __init__(self, app):
        self.client = app.test_client()

    @staticmethod
    def _path(url):
        return "/" + url.split("://", 1)[1].split("/", 1)[1]

    def post(self, url, json, headers, timeout):
        return _Response(self.client.post(self._path(url), json=json, headers=headers))

    def get(self, url, headers, timeout):
        return _Response(self.client.get(self._path(url), headers=headers))


class _Response:
    def __init__(self, response):
        self.status_code = response.status_code
        self._response = response

    def json(self):
        body = self._response.get_json(silent=True)
        if body is None:
            raise ValueError("no json")
        return body


def test_the_client_round_trips_through_the_api(configured):
    app, _, token = configured
    client = StoreClient("http://cos-scheduler:8769", str(token), session=_FlaskSession(app))
    assert client.submit(commitment_command())["outcome"] == "accepted"
    assert client.get("cos.obligation", "OBL-cos-followup-0001")["state"] == "ok"


class _Broken:
    def post(self, *a, **k):
        raise requests.ConnectionError("down")

    def get(self, *a, **k):
        raise requests.Timeout("slow")


def test_the_client_never_claims_a_save_it_did_not_get(tmp_path, configured):
    _, _, token = configured
    down = StoreClient("http://cos-scheduler:8769", str(token), session=_Broken())
    assert down.submit(commitment_command()) == {"schema": "minimoi.cos.store_result/1", "command_id": "c1",
                                                  "outcome": "failed", "code": "store_unavailable"}
    assert down.get("cos.goal", "GOAL-x")["state"] == "unavailable"
    no_token = StoreClient("http://cos-scheduler:8769", str(tmp_path / "missing"), session=_Broken())
    assert no_token.submit(commitment_command())["code"] == "store_unavailable"
