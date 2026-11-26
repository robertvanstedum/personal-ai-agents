"""The staging-only UAT sign-in door (SPEC_AUTOMATIC_UAT v0.1 §2): open only on staging with the right header token,
404 everywhere else, and never configured in production."""
import hashlib
import re
from pathlib import Path

import pytest

from minimoi_portal import uat_signin

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 20 + "0123456789abcdef0123"          # 40 characters, above the 32 minimum
HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


@pytest.fixture
def portal_client():
    """A client of its own: the shared session-scoped client must never keep the owner session these tests create."""
    from minimoi_portal.app import app
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


def _open(monkeypatch, env="staging", value=HASH):
    monkeypatch.setenv("MINIMOI_ENV", env)
    monkeypatch.setenv("MINIMOI_UAT_SIGNIN_SHA256", value)


def test_staging_with_right_token_signs_in_owner_marked_uat(portal_client, monkeypatch):
    _open(monkeypatch)
    r = portal_client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "uat": True}
    with portal_client.session_transaction() as sess:
        assert sess["user"] == {"username": "robert", "tier": "owner", "display_name": "Robert", "uat": True}


def test_production_is_404_even_with_hash_and_token(portal_client, monkeypatch):
    _open(monkeypatch, env="production")
    r = portal_client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN})
    assert r.status_code == 404
    with portal_client.session_transaction() as sess:
        assert "user" not in sess


def test_unset_environment_is_404(portal_client, monkeypatch):
    monkeypatch.delenv("MINIMOI_ENV", raising=False)
    monkeypatch.setenv("MINIMOI_UAT_SIGNIN_SHA256", HASH)
    assert portal_client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN}).status_code == 404


def test_staging_without_hash_is_404(portal_client, monkeypatch):
    _open(monkeypatch, value="")
    assert portal_client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN}).status_code == 404


def test_wrong_missing_short_or_query_token_is_404(portal_client, monkeypatch):
    _open(monkeypatch)
    assert portal_client.post("/uat/sign-in", headers={uat_signin.HEADER: TOKEN + "x"}).status_code == 404
    assert portal_client.post("/uat/sign-in").status_code == 404
    short = "abc"
    monkeypatch.setenv("MINIMOI_UAT_SIGNIN_SHA256", hashlib.sha256(short.encode()).hexdigest())
    assert portal_client.post("/uat/sign-in", headers={uat_signin.HEADER: short}).status_code == 404
    _open(monkeypatch)
    assert portal_client.post(f"/uat/sign-in?token={TOKEN}").status_code == 404     # never from the URL


def test_get_is_not_allowed(portal_client, monkeypatch):
    _open(monkeypatch)
    r = portal_client.get("/uat/sign-in", headers={uat_signin.HEADER: TOKEN})
    assert r.status_code != 200                                 # 405, or the portal's own sign-in redirect
    with portal_client.session_transaction() as sess:
        assert "user" not in sess


def test_malformed_hash_keeps_door_closed():
    assert uat_signin.expected_hash({"MINIMOI_ENV": "staging", "MINIMOI_UAT_SIGNIN_SHA256": "not-hex"}) is None
    assert uat_signin.expected_hash({"MINIMOI_ENV": "Staging ", "MINIMOI_UAT_SIGNIN_SHA256": HASH}) is None


def test_production_compose_files_never_configure_the_door():
    for path in ROOT.glob("docker-compose*.yml"):
        if "staging" in path.name or path.name.endswith(".dev.yml"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "MINIMOI_UAT_SIGNIN_SHA256" not in text, path.name
        assert not re.search(r"MINIMOI_ENV\s*=\s*staging", text), path.name


def test_staging_compose_wires_the_door_closed_by_default():
    text = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    assert "MINIMOI_UAT_SIGNIN_SHA256=${MINIMOI_UAT_SIGNIN_SHA256:-}" in text
