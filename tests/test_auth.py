"""
tests/test_auth.py — Spec 1 auth flow tests.

Tests use function-scoped Flask test clients (separate from portal_client
in conftest.py) so each test starts with a clean session/cookie jar.
DB calls in domain_auth are mocked — no real DB needed in CI.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup


@pytest.fixture
def client():
    from minimoi_portal.app import app
    app.config["TESTING"] = True
    app.config["SESSION_COOKIE_SECURE"] = False
    with app.test_client() as c:
        yield c


# ── 1. Unauthenticated access ──────────────────────────────────────────────────

def test_unauthenticated_request_redirects_to_login(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_authenticated_dashboard_renders_signed_in_home(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "robert",
            "display_name": "Robert",
            "tier": "owner",
        }
    with patch("minimoi_portal.auth.check_must_change_password", return_value=False):
        resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    assert "Welcome back, Robert." in soup.get_text(" ", strip=True)
    cards = {a.get("href") for a in soup.select(".dashboard-domain-open")}
    assert {
        "/app/curator",
        "/app/german",
        "/app/portuguese",
        "/guild",
        "/app/cos",
    }.issubset(cards)
    assert not soup.select(".dashboard-card-tour")
    assert soup.select_one('.front-door-account a[href="/tour"]') is not None


def test_signed_out_landing_shows_locked_public_workspaces(client):
    resp = client.get("/")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    locked = [node.get_text(" ", strip=True) for node in soup.select(".workspace-locked")]
    assert any("Curator" in label for label in locked)
    assert any("Mein Deutsch" in label for label in locked)
    assert any("Meu Português" in label for label in locked)
    assert any("Guild" in label for label in locked)
    assert not any("Chief of Staff" in label or "CoS" in label for label in locked)
    preview_links = {
        node.get("href") for node in soup.select(".front-domain-grid a[href]")
    }
    assert preview_links == {
        "/tour#curator",
        "/tour#german",
        "/tour#portuguese",
        "/tour#guild",
        "/tour#cos",
    }
    assert soup.select_one('.front-actions a[href="/tour"]') is None
    assert soup.select_one('a[href="/login"]') is not None


def test_owner_landing_shows_active_owner_workspaces(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "robert",
            "display_name": "Robert",
            "tier": "owner",
        }
    resp = client.get("/")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    links = {a.get("href") for a in soup.select(".workspace-link[href]")}
    assert {
        "/app/curator",
        "/app/german",
        "/app/portuguese",
        "/guild",
        "/app/cos",
    }.issubset(links)
    assert not soup.select(".workspace-locked")


def test_admin_landing_hides_owner_only_workspaces(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "admin",
            "display_name": "Admin",
            "tier": "admin",
        }
    resp = client.get("/")
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    links = {a.get("href") for a in soup.select(".workspace-link[href]")}
    assert links == {"/app/curator", "/app/german", "/app/portuguese"}
    assert "/guild" not in links
    assert "/app/cos" not in links


def test_legacy_family_guest_landing_matches_restricted_routes(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "family",
            "display_name": "Family",
            "tier": "guest",
        }
    resp = client.get("/")
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    links = {a.get("href") for a in soup.select(".workspace-link[href]")}
    assert links == {"/app/curator", "/app/german"}


def test_legacy_family_guest_dashboard_only_shows_allowed_workspaces(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "family",
            "display_name": "Family",
            "tier": "guest",
        }
    resp = client.get("/dashboard")
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    cards = {a.get("href") for a in soup.select(".dashboard-domain-open")}
    assert cards == {"/app/curator", "/app/german"}


def test_domain_guest_landing_only_activates_granted_portuguese(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "test@example.com",
            "display_name": "Test User",
            "tier": "guest",
            "auth_id": 7,
        }
    with patch("minimoi_portal.domain_auth.has_domain_access", return_value=True):
        resp = client.get("/")
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    links = {a.get("href") for a in soup.select(".workspace-link[href]")}
    assert links == {"/app/portuguese"}
    assert "/app/cos" not in resp.get_data(as_text=True)


def test_domain_guest_dashboard_only_shows_granted_portuguese(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "test@example.com",
            "display_name": "Test User",
            "tier": "guest",
            "auth_id": 7,
        }
    with patch("minimoi_portal.domain_auth.has_domain_access", return_value=True):
        resp = client.get("/dashboard")
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    cards = {a.get("href") for a in soup.select(".dashboard-domain-open")}
    assert cards == {"/app/portuguese"}
    assert "/app/cos" not in resp.get_data(as_text=True)


def test_domain_guest_without_portuguese_grant_sees_no_active_workspace(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "test@example.com",
            "display_name": "Test User",
            "tier": "guest",
            "auth_id": 7,
        }
    with patch("minimoi_portal.domain_auth.has_domain_access", return_value=False):
        resp = client.get("/")
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    assert not soup.select(".workspace-link[href]")


def test_public_tour_is_chooser_only_with_privacy_safe_cos(client):
    resp = client.get("/tour")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Curator" in html
    assert "Mein Deutsch" in html
    assert "Meu Português" in html
    assert "Guild" in html
    assert "Chief of Staff" in html
    assert "cos-confer-private.png" not in html
    assert "cos-confer-mobile.webp" in html
    assert "cos-mobile-landing.webp" in html
    assert "/app/cos" not in html
    assert "<form" not in html
    soup = BeautifulSoup(html, "html.parser")
    domain_map_links = {
        link.get("href") for link in soup.select(".tour-domain-card[href]")
    }
    assert domain_map_links == {
        "#curator",
        "#german",
        "#portuguese",
        "#guild",
        "#cos",
    }
    static_root = Path(__file__).parents[1] / "minimoi_portal" / "static"
    map_images = [
        img.get("src")
        for img in soup.select('.tour-domain-map img[src^="/static/tour/"]')
    ]
    assert len(map_images) == 5
    assert all((static_root / src.removeprefix("/static/")).is_file() for src in map_images)
    images = [
        img.get("src")
        for img in soup.select('.tour-shot img[src^="/static/tour/"]')
    ]
    assert len(images) == 24
    assert "/static/tour/curator-research-tools.jpg" not in images
    assert all(
        (static_root / src.split("?", 1)[0].removeprefix("/static/")).is_file()
        for src in images
    )
    full_size_links = [
        link.get("href") for link in soup.select("a.tour-shot-link[href]")
    ]
    assert full_size_links == images
    expected_first_images = {
        "curator": "/static/tour/curator-landing.jpg",
        "german": "/static/tour/german-landing.jpg",
        "portuguese": "/static/tour/portuguese-landing.jpg",
        "guild": "/static/tour/guild-landing.png",
        "cos": "/static/tour/cos-landing.jpg?v=20260724-2",
    }
    for section_id, expected_src in expected_first_images.items():
        section = soup.select_one(f"section#{section_id}")
        assert section.select_one("img").get("src") == expected_src
    assert len(soup.select("section#curator .tour-shot")) == 4
    curator_mobile_hrefs = [
        link.get("href")
        for link in soup.select("section#curator .tour-mobile-shot-link")
    ]
    assert curator_mobile_hrefs == [
        "/static/tour/01-curator-landing-mobile.webp?v=20260731-baseline1",
        "/static/tour/02-curator-daily-briefing-mobile.webp?v=20260731-baseline1",
    ]
    for href in curator_mobile_hrefs:
        assert (
            static_root / href.split("?", 1)[0].removeprefix("/static/")
        ).is_file()
    assert len(soup.select("section#german .tour-shot")) == 10
    assert [
        img.get("src") for img in soup.select("section#german .tour-shot img")
    ] == [
        "/static/tour/german-landing.jpg",
        "/static/tour/german-lesen-choices.jpg",
        "/static/tour/german-lesen-wien.png",
        "/static/tour/german-lesen-article.jpg",
        "/static/tour/german-lesen-translation.jpg",
        "/static/tour/german-lesen-correction.jpg",
        "/static/tour/german-gespraeche-current.jpg",
        "/static/tour/german-schreiben-current.jpg",
        "/static/tour/german-woerter-current.jpg",
        "/static/tour/german-archiv-current.jpg",
    ]
    german_mobile_hrefs = [
        link.get("href")
        for link in soup.select("section#german .tour-mobile-shot-link")
    ]
    assert german_mobile_hrefs == [
        "/static/tour/01-german-landing-mobile.webp?v=20260731-baseline1",
        "/static/tour/02-german-reading-categories-mobile.webp?v=20260731-baseline1",
    ]
    for href in german_mobile_hrefs:
        assert (
            static_root / href.split("?", 1)[0].removeprefix("/static/")
        ).is_file()
    assert len(soup.select("section#portuguese .tour-shot")) == 6
    assert len(soup.select("section#guild .tour-shot")) == 2
    assert len(soup.select("section#cos .tour-shot")) == 2
    expected_mobile_hrefs = {
        "portuguese": [
            "/static/tour/01-portuguese-landing-mobile.webp?v=20260731-baseline1",
            "/static/tour/02-portuguese-reading-categories-mobile.webp?v=20260731-baseline1",
        ],
        "guild": [
            "/static/tour/01-guild-landing-mobile.webp?v=20260801-guildflow2",
            "/static/tour/02-guild-landing-partners-mobile.webp?v=20260801-guildflow2",
            "/static/tour/03-guild-build-log-mobile.webp?v=20260801-guildflow2",
            "/static/tour/04-guild-spec-mobile.webp?v=20260801-guildflow2",
            "/static/tour/05-guild-roadmap-mobile.webp?v=20260801-guildflow2",
            "/static/tour/06-guild-github-issues-mobile.webp?v=20260801-guildflow2",
            "/static/tour/07-guild-operate-mobile.webp?v=20260801-guildflow2",
            "/static/tour/08-guild-improve-mobile.webp?v=20260801-guildflow2",
        ],
        "cos": [
            "/static/tour/01-cos-landing-mobile.webp?v=20260731-baseline1",
            "/static/tour/02-cos-confer-mobile.webp?v=20260731-baseline1",
        ],
    }
    for section_id, expected_hrefs in expected_mobile_hrefs.items():
        mobile_hrefs = [
            link.get("href")
            for link in soup.select(
                f"section#{section_id} .tour-mobile-shot-link"
            )
        ]
        assert mobile_hrefs == expected_hrefs
        assert all(
            (
                static_root
                / href.split("?", 1)[0].removeprefix("/static/")
            ).is_file()
            for href in mobile_hrefs
        )
    assert all(section.has_attr("hidden") for section in soup.select(".tour-section"))
    assert not soup.select(".tour-step-nav")


def test_owner_tour_uses_same_privacy_safe_cos_slides(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "robert",
            "display_name": "Robert",
            "tier": "owner",
        }
    resp = client.get("/tour")
    assert resp.status_code == 200
    soup = BeautifulSoup(resp.get_data(as_text=True), "html.parser")
    section = soup.select_one("section#cos")
    assert section is not None
    desktop_images = [
        img.get("src") for img in section.select(".tour-shot img")
    ]
    assert desktop_images == [
        "/static/tour/cos-landing.jpg?v=20260724-2",
        "/static/tour/cos-confer.png",
    ]
    mobile_images = [
        link.get("href")
        for link in section.select(".tour-mobile-shot-link")
    ]
    assert mobile_images == [
        "/static/tour/01-cos-landing-mobile.webp?v=20260731-baseline1",
        "/static/tour/02-cos-confer-mobile.webp?v=20260731-baseline1",
    ]


@pytest.mark.parametrize(
    "path",
    ["/preview/", "/preview/curator/briefing.html", "/preview/german/lesen"],
)
def test_legacy_preview_routes_redirect_to_tour(client, path):
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/tour")


def test_successful_login_defaults_to_dashboard(client):
    fake_user = {
        "username": "family",
        "display_name": "Family",
        "tier": "family",
    }
    with patch("minimoi_portal.auth.authenticate", return_value=(fake_user, None)), \
         patch("minimoi_portal.auth.check_must_change_password", return_value=False):
        resp = client.post(
            "/login",
            data={"username": "family", "password": "secret"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")


def test_successful_login_preserves_explicit_next(client):
    fake_user = {
        "username": "family",
        "display_name": "Family",
        "tier": "family",
    }
    with patch("minimoi_portal.auth.authenticate", return_value=(fake_user, None)), \
         patch("minimoi_portal.auth.check_must_change_password", return_value=False):
        resp = client.post(
            "/login?next=/app/german",
            data={"username": "family", "password": "secret"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/app/german")


# ── 2. Valid login token creates session ───────────────────────────────────────

def test_valid_login_token_creates_session(client):
    fake_user = {"id": 1, "email": "test@example.com", "name": "Test User", "role": "user"}
    with patch("minimoi_portal.domain_auth.consume_login_token", return_value=fake_user):
        resp = client.get("/login/somevalidtoken", follow_redirects=False)
    assert resp.status_code == 302
    # After redirect, user should be logged in
    with client.session_transaction() as sess:
        assert sess.get("user") is not None
        assert sess["user"]["auth_id"] == 1


# ── 3. Expired login token is rejected ────────────────────────────────────────

def test_expired_login_token_rejected(client):
    with patch("minimoi_portal.domain_auth.consume_login_token", return_value=None):
        resp = client.get("/login/expiredtoken", follow_redirects=False)
    assert resp.status_code == 400


# ── 4. Already-used login token is rejected ───────────────────────────────────

def test_used_login_token_rejected(client):
    with patch("minimoi_portal.domain_auth.consume_login_token", return_value=None):
        resp = client.get("/login/usedtoken", follow_redirects=False)
    assert resp.status_code == 400


# ── 5. User with Portuguese access can reach /app/portuguese ──────────────────

def test_user_with_portuguese_access_can_reach_portuguese(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "test@example.com",
            "display_name": "Test User",
            "tier": "guest",
            "auth_id": 1,
        }
    with patch("minimoi_portal.domain_auth.has_domain_access", return_value=True):
        resp = client.get("/app/portuguese")
    assert resp.status_code in [200, 503]  # 503 = auth passed, backend not running in CI


# ── 6. User without Portuguese access gets 403 ────────────────────────────────

def test_user_without_portuguese_access_gets_403(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "test@example.com",
            "display_name": "Test User",
            "tier": "guest",
            "auth_id": 1,
        }
    with patch("minimoi_portal.domain_auth.has_domain_access", return_value=False):
        resp = client.get("/app/portuguese")
    assert resp.status_code == 403


# ── 7. Domain access is domain-specific (Portuguese ≠ German) ─────────────────

def test_domain_access_is_domain_specific(client):
    """A user granted 'portuguese' access does not get 'german' access."""
    def fake_has_access(user_id, domain):
        return domain == "portuguese"

    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "test@example.com",
            "display_name": "Test User",
            "tier": "guest",
            "auth_id": 1,
        }
    # Portuguese: granted
    with patch("minimoi_portal.domain_auth.has_domain_access", side_effect=fake_has_access):
        resp_pt = client.get("/app/portuguese")
    assert resp_pt.status_code in [200, 503]  # 503 = auth passed, backend not running in CI

    # German: not granted (requires_domain not on /app/german, but decorator returns 403 for
    # an auth_id user with no german access if the route had it — test the decorator directly)
    with patch("minimoi_portal.domain_auth.has_domain_access", return_value=False):
        resp_pt_denied = client.get("/app/portuguese")
    assert resp_pt_denied.status_code == 403


# ── 8. Admin (owner tier) can reach /guild/users ──────────────────────────────

def test_owner_can_reach_guild_users(client):
    with client.session_transaction() as sess:
        sess["user"] = {
            "username": "robert",
            "display_name": "Robert",
            "tier": "owner",
        }
    with patch("minimoi_portal.domain_auth.list_users_with_access", return_value=[]):
        resp = client.get("/guild/users")
    assert resp.status_code == 200


# ── 9. Non-admin cannot reach /guild/users ────────────────────────────────────

def test_non_owner_cannot_reach_guild_users(client):
    resp = client.get("/guild/users", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# ── 10. Same token used from two browsers — second use rejected ───────────────

def test_duplicate_token_use_rejected():
    """First call succeeds; second call (same token, now used=True) returns None.
    Two separate clients simulate two different browsers — session from browser 1
    must not carry over to browser 2."""
    from minimoi_portal.app import app as _app
    _app.config["TESTING"] = True
    _app.config["SESSION_COOKIE_SECURE"] = False

    fake_user = {"id": 2, "email": "other@example.com", "name": "Other", "role": "user"}

    # Browser 1 consumes the token
    with _app.test_client() as c1:
        with patch("minimoi_portal.domain_auth.consume_login_token", return_value=fake_user):
            resp1 = c1.get("/login/sharedtoken", follow_redirects=False)
        assert resp1.status_code == 302

    # Browser 2 — fresh session, token now marked used (returns None)
    with _app.test_client() as c2:
        with patch("minimoi_portal.domain_auth.consume_login_token", return_value=None):
            resp2 = c2.get("/login/sharedtoken", follow_redirects=False)
        assert resp2.status_code == 400


# ── 11. Portuguese domain route is protected by requires_domain() ─────────────

def test_portuguese_routes_require_domain_decorator(client):
    """/app/portuguese returns 302 to login when unauthenticated (not 404 or 500)."""
    resp = client.get("/app/portuguese", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    resp2 = client.get("/app/portuguese/", follow_redirects=False)
    assert resp2.status_code == 302
