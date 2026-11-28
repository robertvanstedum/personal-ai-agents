"""An MC chat is marked ``uat`` only when it is created under a session the staging UAT door issued, on a host where
that door is open. The request body, a header, an ordinary owner session, or a session that merely says ``uat`` on a
host without the door never marks one. The portal passes the same fact to CoS as a signed claim."""
from __future__ import annotations

import hashlib
import json

import pytest
from flask import Flask

from floor_helpers import OWNER, write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

from core import portal_assertion as PA
from minimoi_portal import proxy, uat_signin

API = "/guild-next/api/v1"
TOKEN = "0123456789abcdef" * 4
STAGING = {"MINIMOI_ENV": "staging", "MINIMOI_ROLE": "standby", "BASE_URL": "https://dev.minimoi.ai",
           "MINIMOI_UAT_SIGNIN_SHA256": hashlib.sha256(TOKEN.encode()).hexdigest()}


def _door(monkeypatch, **over):
    for k, v in {**STAGING, **over}.items():
        monkeypatch.setenv(k, v)


def _records(portal):
    return portal.queue_path.parent / "conversations"              # the guild data folder's conversation records


def _create(portal, client, **body):
    r = client.post(f"{API}/conversations", json=keyed(**body), headers=write_headers(portal.csrf(client)))
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()["conversation"]["id"]
    assert "uat" not in r.get_json()["conversation"]                       # never part of the browser's view
    return json.loads((_records(portal) / f"{cid}.json").read_text())


def test_a_chat_created_through_the_uat_door_is_marked(floored, monkeypatch):
    _door(monkeypatch)
    client = floored.client()
    assert client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN}).status_code == 200
    assert _create(floored, client)["uat"] is True


def test_an_ordinary_owner_chat_is_never_marked_whatever_the_request_says(floored, monkeypatch):
    _door(monkeypatch)                                                    # even on staging with the door open
    client = floored.owner()
    assert "uat" not in _create(floored, client)
    assert "uat" not in _create(floored, client, uat=True)
    r = client.post(f"{API}/conversations", json=keyed(uat=True),
                    headers={**write_headers(floored.csrf(client)), "X-Minimoi-Uat": "1", uat_signin.HEADER: "f" * 64})
    assert r.status_code == 200
    records = list(_records(floored).glob("*.json"))
    assert len(records) >= 3 and all("uat" not in json.loads(p.read_text()) for p in records)


@pytest.mark.parametrize("over", [{"MINIMOI_ENV": "production"}, {"MINIMOI_ROLE": "production"},
                                  {"BASE_URL": "https://minimoi.ai"}, {"MINIMOI_UAT_SIGNIN_SHA256": ""}])
def test_a_session_that_says_uat_marks_nothing_where_the_door_is_closed(floored, monkeypatch, over):
    _door(monkeypatch, **over)
    client = floored.client({**OWNER, **uat_signin.owner_session_user()})   # as if such a cookie could exist there
    assert client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN}).status_code == 404
    assert "uat" not in _create(floored, client)


def test_is_uat_needs_the_door_the_flag_and_a_live_session():
    user = uat_signin.owner_session_user(now=1000)
    assert uat_signin.is_uat(user, STAGING, now=1000 + 60) is True
    assert uat_signin.is_uat(user, STAGING, now=1000 + uat_signin.MAX_AGE_S + 1) is False
    assert uat_signin.is_uat({**user, "uat": "yes"}, STAGING, now=1060) is False
    assert uat_signin.is_uat(dict(OWNER), STAGING, now=1060) is False
    assert uat_signin.is_uat(None, STAGING) is False
    for over in ({"MINIMOI_ENV": "production"}, {"MINIMOI_ROLE": "production"}, {"MINIMOI_UAT_SIGNIN_SHA256": ""}):
        assert uat_signin.is_uat(user, {**STAGING, **over}, now=1060) is False


def test_the_portal_signs_the_uat_claim_for_cos_only_for_the_uat_session(tmp_path, monkeypatch):
    key = tmp_path / "cos-owner.key"
    key.write_text("kid1:" + "s" * 40 + "\n")
    monkeypatch.setenv("PORTAL_COS_OWNER_KEY_FILE", str(key))
    monkeypatch.setenv("PORTAL_COS_OWNER_ENV", "staging")

    def claims(user):
        with Flask(__name__).test_request_context("/app/cos/ui/history/conversations", method="POST"):
            header = proxy._cos_owner_assertion(user, "/ui/history/conversations", b"{}")
        return json.loads(PA.unb64(header.split(".")[1]))

    _door(monkeypatch)
    assert claims({**OWNER, **uat_signin.owner_session_user()})["uat"] is True
    assert "uat" not in claims(dict(OWNER))
    _door(monkeypatch, MINIMOI_ENV="production")
    assert "uat" not in claims({**OWNER, **uat_signin.owner_session_user()})
