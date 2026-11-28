"""The staging-only UAT sign-in door (SPEC_AUTOMATIC_UAT v0.1 §2): open only on staging (env, role and host) with
the right header token, 404 for every method everywhere else, short-lived, never in Sentry, and never configured in
production."""
import hashlib
import re
from pathlib import Path

import pytest
import yaml

from minimoi_portal import uat_signin

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "0123456789abcdef" * 4                       # 64 hex, the provisioned format
HASH = hashlib.sha256(TOKEN.encode()).hexdigest()
STAGING = {"MINIMOI_ENV": "staging", "MINIMOI_ROLE": "standby", "BASE_URL": "https://dev.minimoi.ai",
           "MINIMOI_UAT_SIGNIN_SHA256": HASH}


@pytest.fixture
def portal_client():
    """A client of its own: the shared session-scoped client must never keep the owner session these tests create."""
    from minimoi_portal.app import app
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


def _open(monkeypatch, **over):
    for k, v in {**STAGING, **over}.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def _post(client, token=TOKEN):
    return client.post("/uat/sign-in", headers={uat_signin.HEADER: token} if token is not None else {})


def _user(client):
    with client.session_transaction() as sess:
        return sess.get("user")


def test_staging_with_right_token_signs_in_owner_marked_uat(portal_client, monkeypatch):
    _open(monkeypatch)
    r = _post(portal_client)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "uat": True}
    user = _user(portal_client)
    assert {k: user[k] for k in ("username", "tier", "display_name", "uat")} == \
        {"username": "robert", "tier": "owner", "display_name": "Robert", "uat": True}
    assert isinstance(user["uat_issued_at"], int)


@pytest.mark.parametrize("over", [
    {"MINIMOI_ENV": "production"}, {"MINIMOI_ENV": "prod"}, {"MINIMOI_ENV": None}, {"MINIMOI_ENV": "Staging "},
    {"MINIMOI_ROLE": "production"}, {"MINIMOI_ROLE": None},
    {"BASE_URL": "https://minimoi.ai"}, {"BASE_URL": "http://dev.minimoi.ai"}, {"BASE_URL": None},
    {"MINIMOI_UAT_SIGNIN_SHA256": ""}, {"MINIMOI_UAT_SIGNIN_SHA256": "not-hex"},
])
def test_door_closed_unless_every_staging_condition_holds(portal_client, monkeypatch, over):
    _open(monkeypatch, **over)
    assert _post(portal_client).status_code == 404
    assert _user(portal_client) is None


def test_wrong_missing_non_hex_or_query_token_is_404(portal_client, monkeypatch):
    _open(monkeypatch)
    assert _post(portal_client, TOKEN[:-1] + "0").status_code == 404
    assert _post(portal_client, None).status_code == 404
    passphrase = "correct horse battery staple " * 3
    monkeypatch.setenv("MINIMOI_UAT_SIGNIN_SHA256", hashlib.sha256(passphrase.encode()).hexdigest())
    assert _post(portal_client, passphrase).status_code == 404          # not 64 hex: refused even if the hash matches
    _open(monkeypatch)
    assert portal_client.post(f"/uat/sign-in?token={TOKEN}").status_code == 404     # never from the URL
    assert _user(portal_client) is None


@pytest.mark.parametrize("method", ["get", "head", "put", "patch", "delete", "options"])
def test_every_other_method_is_404_even_on_staging(portal_client, monkeypatch, method):
    _open(monkeypatch)
    r = getattr(portal_client, method)("/uat/sign-in", headers={uat_signin.HEADER: TOKEN})
    assert r.status_code == 404
    assert _user(portal_client) is None


def test_production_options_get_and_post_look_like_a_missing_route(portal_client, monkeypatch):
    _open(monkeypatch, MINIMOI_ENV="prod", MINIMOI_ROLE="production", BASE_URL="https://minimoi.ai")
    for method in ("get", "options", "post"):
        assert getattr(portal_client, method)("/uat/sign-in", headers={uat_signin.HEADER: TOKEN}).status_code == 404


def test_sign_in_clears_every_earlier_session_key(portal_client, monkeypatch):
    _open(monkeypatch)
    with portal_client.session_transaction() as sess:
        sess["queue_csrf"] = "planted"
        sess["user"] = {"username": "someone", "tier": "guest"}
    assert _post(portal_client).status_code == 200
    with portal_client.session_transaction() as sess:
        assert "queue_csrf" not in sess and sess["user"]["username"] == "robert"


def test_uat_session_expires_after_max_age(portal_client, monkeypatch):
    _open(monkeypatch)
    assert _post(portal_client).status_code == 200
    with portal_client.session_transaction() as sess:
        user = dict(sess["user"])
        user["uat_issued_at"] -= uat_signin.MAX_AGE_S + 5
        sess["user"] = user
    portal_client.get("/health")
    assert _user(portal_client) is None


def test_expiry_never_touches_a_normal_session():
    assert uat_signin.expired({"username": "robert", "tier": "owner"}) is False
    assert uat_signin.expired({"username": "robert", "uat": True}) is True          # no issue time: refused
    assert uat_signin.expired(uat_signin.owner_session_user()) is False


def test_sentry_scrub_removes_the_token_header():
    ev = {"request": {"url": "https://dev.minimoi.ai/uat/sign-in", "headers": {"X-Minimoi-Uat-Token": TOKEN, "Accept": "*/*"}}}
    out = uat_signin.scrub_sentry_event(ev)
    assert TOKEN not in str(out) and out["request"]["headers"]["Accept"] == "*/*"
    ev2 = {"request": {"headers": [["X-MiniMoi-UAT-Token", TOKEN], ["Accept", "*/*"]]}}
    assert TOKEN not in str(uat_signin.scrub_sentry_event(ev2))


def _environment(service) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
    return {str(k): str(v) for k, v in env.items()}


def test_no_production_compose_file_configures_the_door():
    for path in ROOT.glob("docker-compose*.yml"):
        if path.name == "docker-compose.staging.yml":
            continue
        text = path.read_text(encoding="utf-8")
        assert "MINIMOI_UAT_SIGNIN_SHA256" not in text, path.name
        assert not re.search(r"MINIMOI_ENV\s*[:=]\s*['\"]?staging", text), path.name


def test_production_portal_resolves_to_production_role():
    prod = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    env = _environment(prod["services"]["portal"])
    assert env.get("MINIMOI_ROLE") == "production"
    assert "MINIMOI_UAT_SIGNIN_SHA256" not in env


def test_staging_compose_wires_the_door_closed_by_default():
    staging = yaml.safe_load((ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8"))
    env = _environment(staging["services"]["portal"])
    assert env.get("MINIMOI_UAT_SIGNIN_SHA256") == "${MINIMOI_UAT_SIGNIN_SHA256:-}"
    assert env.get("MINIMOI_ENV") == "staging" and env.get("MINIMOI_ROLE") == "standby"
    assert env.get("BASE_URL") == "https://dev.minimoi.ai"
