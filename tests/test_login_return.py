"""
tests/test_login_return.py — sign-in returns only to this portal.

After sign-in the portal goes back to the page that asked for it, query
included, and nowhere else: anything that is not a same-origin path falls
back to the dashboard. DB calls are mocked, as in tests/test_auth.py.
"""
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

FAMILY = {"username": "family", "display_name": "Family", "tier": "family"}


@pytest.fixture
def client():
    from minimoi_portal.app import app
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as c:
        yield c


def _sign_in(client, next_value):
    with patch("minimoi_portal.auth.authenticate", return_value=(FAMILY, None)), \
         patch("minimoi_portal.auth.check_must_change_password", return_value=False):
        return client.post("/login", query_string={"next": next_value},
                           data={"username": "family", "password": "secret"},
                           follow_redirects=False)


def _location_path(resp):
    """The redirect target as a browser would resolve it: (netloc, path?query)."""
    parts = urlsplit(resp.headers["Location"])
    return parts.netloc, parts.path + (f"?{parts.query}" if parts.query else "")


# ── Legitimate returns keep their path and query ──────────────────────────────

def test_sign_in_returns_to_path_with_query(client):
    target = "/guild-next/guild/build?c=abc123&attach=42"
    resp = _sign_in(client, target)
    assert resp.status_code == 302
    netloc, path = _location_path(resp)
    assert netloc in ("", "localhost")
    assert path == target


def test_login_required_redirect_carries_path_and_query(client):
    resp = client.get("/dashboard?view=week&x=1", follow_redirects=False)
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert query["next"] == ["/dashboard?view=week&x=1"]


def test_login_required_redirect_without_query_has_no_trailing_marker(client):
    resp = client.get("/dashboard", follow_redirects=False)
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert query["next"] == ["/dashboard"]


def test_login_form_keeps_an_encoded_next_with_query(client):
    target = "/guild-next/guild/build?c=abc123&attach=42"
    resp = client.get("/login", query_string={"next": target})
    action = BeautifulSoup(resp.data, "html.parser").find("form")["action"]
    parts = urlsplit(action)
    assert parts.path == "/login"
    assert parse_qs(parts.query) == {"next": [target]}


def test_owner_required_redirect_keeps_query_and_notice(client):
    with client.session_transaction() as sess:
        sess["user"] = dict(FAMILY)
    with patch("minimoi_portal.auth.check_must_change_password", return_value=False):
        resp = client.get("/app/cos?tab=notes", follow_redirects=False)
    assert resp.status_code == 302
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert query["next"] == ["/app/cos?tab=notes"]
    assert query["notice"] == ["owner_required"]


# ── Anything that could leave the portal goes to the dashboard ────────────────

UNSAFE = [
    "https://evil.example/x",
    "http:evil.example",
    "//evil.example/x",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    " //evil.example",
    "/%2F%2Fevil.example",
    "/%5Cevil.example",
    "/%5C%5Cevil.example",
    "javascript:alert(1)",
    "evil.example",
    "dashboard",
    "/x\r\nSet-Cookie: a=b",
    "/x\x00y",
    pytest.param("/" + "a" * 3000, id="overlong"),
]


@pytest.mark.parametrize("target", UNSAFE)
def test_sign_in_never_leaves_the_portal(client, target):
    resp = _sign_in(client, target)
    assert resp.status_code == 302
    netloc, path = _location_path(resp)
    assert netloc in ("", "localhost")
    assert path == "/dashboard"


@pytest.mark.parametrize("target", ["https://evil.example/x", "//evil.example", "/\\evil.example"])
def test_login_form_drops_an_unsafe_next(client, target):
    resp = client.get("/login", query_string={"next": target})
    action = BeautifulSoup(resp.data, "html.parser").find("form")["action"]
    assert action == "/login"


def test_missing_next_still_goes_to_dashboard(client):
    with patch("minimoi_portal.auth.authenticate", return_value=(FAMILY, None)), \
         patch("minimoi_portal.auth.check_must_change_password", return_value=False):
        resp = client.post("/login", data={"username": "family", "password": "secret"},
                           follow_redirects=False)
    assert _location_path(resp)[1] == "/dashboard"


def test_raw_non_ascii_query_is_percent_encoded_not_lost(client):
    resp = client.get("/dashboard", environ_overrides={"QUERY_STRING": "q=\xc3\xa9&x=1"}, follow_redirects=False)
    target = parse_qs(urlsplit(resp.headers["Location"]).query)["next"][0]
    assert target == "/dashboard?q=%C3%A9&x=1"
    assert _location_path(_sign_in(client, target))[1] == target
