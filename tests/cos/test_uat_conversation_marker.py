"""A CoS conversation is marked ``uat`` only by the staging UAT session's signed write: the portal's signed ``uat``
claim, read by a CoS that is itself the staging standby. A header, a body field, an unsigned request, an ordinary
signed owner write or any other environment never marks one. A marked conversation is refused by the Vault reader."""
import json

import pytest

from core import portal_assertion as PA
from core.memory_shelf import cos as shelf_cos
from domains.cos import owner_assertion
from owner_signing import OWNER_SIGNING_KEY
from test_history_wiring import _load, cos  # noqa: F401  (pytest fixture)

OWNER = {"X-Minimoi-Auth-Id": "1"}
PATH = "/ui/history/conversations"


def _staging(monkeypatch, env="staging", role="standby"):
    monkeypatch.setenv("MINIMOI_ENV", env)
    monkeypatch.setenv("MINIMOI_ROLE", role)


def _signed(body: dict, *, uat: bool, key=OWNER_SIGNING_KEY) -> dict:
    raw = json.dumps(body).encode()
    header = PA.sign(key, audience="cos-owner", subject="owner:1", method="POST", target=PA.canonical_target(PATH, ""),
                     body=raw, env="test", uat=uat)
    return {"data": raw, "content_type": "application/json", "headers": {**OWNER, owner_assertion.HEADER: header}}


def _on_disk(cos, cid):  # noqa: F811
    return json.loads((cos._history.root / "conversations" / f"{cid}.json").read_text())


def test_the_uat_sessions_signed_write_marks_the_conversation_and_the_vault_reader_refuses_it(cos, monkeypatch):  # noqa: F811
    _staging(monkeypatch)
    client = cos.app.test_client()
    made = client.post(PATH, **_signed({"title": "uat-20261128T101500Z C1"}, uat=True))
    assert made.status_code == 201 and made.json["uat"] is True
    cid = made.json["conversation_id"]
    assert _on_disk(cos, cid)["uat"] is True
    sent = client.post("/ui/send", json={"text": "Did we talk about employers?", "channel": "html_text", "conversation_id": cid,
                                         "request_id": "aaaaaaaa-0000-4000-8000-000000000001"}, headers=OWNER)
    assert sent.status_code == 200 and sent.json["conversation_id"] == cid     # the chat itself works like any other
    with pytest.raises(shelf_cos.Refused, match="^uat$"):
        shelf_cos.read(cos._history.root, cid)


def test_an_ordinary_signed_owner_write_is_never_marked_whatever_the_body_or_headers_say(cos, monkeypatch):  # noqa: F811
    _staging(monkeypatch)
    client = cos.app.test_client()
    plain = client.post(PATH, **_signed({"title": "German plan"}, uat=False))
    body = client.post(PATH, **_signed({"title": "x", "uat": True}, uat=False))
    header = client.post(PATH, json={"title": "y", "uat": True}, headers={**OWNER, "X-Minimoi-Uat": "1", "X-Portal-Uat": "true"})
    for made in (plain, body, header):
        assert made.status_code == 201 and "uat" not in made.json
        assert "uat" not in _on_disk(cos, made.json["conversation_id"])
    parsed, _ = shelf_cos.read(cos._history.root, plain.json["conversation_id"])     # and the Vault reader still takes it
    assert parsed.source_id == f"dev:{plain.json['conversation_id']}"


@pytest.mark.parametrize("env,role", [("production", "production"), ("staging", "production"), ("prod", "standby"),
                                      ("", "standby"), ("staging", "")])
def test_the_claim_is_ignored_anywhere_but_the_staging_standby(cos, monkeypatch, env, role):  # noqa: F811
    _staging(monkeypatch, env, role)
    made = cos.app.test_client().post(PATH, **_signed({"title": "x"}, uat=True))
    assert made.status_code == 201 and "uat" not in made.json


def test_a_claim_the_portals_key_did_not_sign_marks_nothing(cos, monkeypatch):  # noqa: F811
    _staging(monkeypatch)
    forged = _signed({"title": "x"}, uat=True, key=("test-kid", "another-secret-0123456789abcdef-0123456789"))
    assert cos.app.test_client().post(PATH, **forged).status_code == 403
    assert not list((cos._history.root / "conversations").glob("cosc-*.json"))
    # an assertion signed WITHOUT the claim, then edited to carry it, no longer verifies
    good = _signed({"title": "x"}, uat=False)
    version, claims, mac = good["headers"][owner_assertion.HEADER].split(".")
    edited = PA.b64(json.dumps({**json.loads(PA.unb64(claims)), "uat": True}, separators=(",", ":"), sort_keys=True).encode())
    good["headers"][owner_assertion.HEADER] = f"{version}.{edited}.{mac}"
    assert cos.app.test_client().post(PATH, **good).status_code == 403
    assert not list((cos._history.root / "conversations").glob("cosc-*.json"))


def test_sign_without_uat_adds_no_claim():
    def claims(**kw):
        header = PA.sign(OWNER_SIGNING_KEY, audience="cos-owner", subject="owner:1", method="POST", target="/x", body=b"",
                         env="test", now=1000, nonce="n" * 24, **kw)
        return json.loads(PA.unb64(header.split(".")[1])), header
    plain, plain_header = claims()
    assert "uat" not in plain and PA.claimed_uat(plain_header) is False
    assert claims(uat=False)[1] == plain_header                                # byte for byte the assertion it always was
    marked, marked_header = claims(uat=True)
    assert marked == {**plain, "uat": True} and PA.claimed_uat(marked_header) is True
    assert PA.claimed_uat("garbage") is False and PA.claimed_uat(None) is False


def test_the_contract_allows_only_true():
    from domains.cos.store import contracts
    doc = {"schema": "minimoi.cos.conversation/1", "conversation_id": "cosc-0123456789ab", "title": "x", "private": False,
           "created_at": "2026-11-28T10:00:00Z", "read_only": False, "data_scope": "personal", "environment": "dev"}
    assert not contracts.errors("conversation", "conversation", doc)
    assert not contracts.errors("conversation", "conversation", {**doc, "uat": True})
    assert contracts.errors("conversation", "conversation", {**doc, "uat": False})
    assert contracts.errors("conversation", "conversation", {**doc, "uat": "yes"})
