"""Live prefix-routing contract for Connect HQ behind the portal (Spec #154 §5, §10.3).

Runs the real portal proxy (Flask test client, in-process) against a REAL
Connect HQ backend started with CONNECTHQ_ROOT_PATH=/app/connecthq, e.g.

    cd prototype-lab/projects/project-connect-hq
    COMPOSE_PROJECT_NAME=connecthq-test CONNECTHQ_APP_PORT=38095 \
      CONNECTHQ_INTEGRATIONS_PORT=38096 CONNECTHQ_DB_PORT=35432 \
      CONNECTHQ_ROOT_PATH=/app/connecthq make up
    CONNECTHQ_BACKEND=http://127.0.0.1:38095 pytest tests/test_portal_connecthq_prefix_live.py

Skips entirely when no backend is reachable. Mutation rows additionally
require the backend in CONNECTHQ_AUTH_MODE=minimoi_proxy (the portal strips
X-Demo-* headers, so in local_demo mode those rows report 403 and are marked
xfail rather than silently passing).
"""
from __future__ import annotations

import json
import os
import re

import pytest
import requests

BACKEND = os.environ.get("CONNECTHQ_BACKEND", "")
PREFIX = "/app/connecthq"
OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}


def _backend_up() -> bool:
    if not BACKEND:
        return False
    try:
        return requests.get(f"{BACKEND}/api/v1/health", timeout=2).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _backend_up(), reason="no live Connect HQ backend (set CONNECTHQ_BACKEND)")


@pytest.fixture(scope="module")
def owner(portal_client):
    import minimoi_portal.config as cfg
    cfg.CONNECTHQ_BACKEND = BACKEND
    with portal_client.session_transaction() as sess:
        sess["user"] = OWNER
    yield portal_client
    with portal_client.session_transaction() as sess:
        sess.pop("user", None)


@pytest.fixture(scope="module")
def backend_calls(request):
    """Every backend URL the proxy actually called during this module."""
    import minimoi_portal.proxy as proxy_mod
    calls = []
    real = proxy_mod.requests.request

    def spy(method, url, **kw):
        calls.append((method, url))
        return real(method, url, **kw)

    proxy_mod.requests.request = spy
    yield calls
    proxy_mod.requests.request = real


_UNPREFIXED = re.compile(r"""(?:href|src|action)=["'](?!//)(/(?!app/connecthq)[^"']*)""")


# Links the PORTAL itself injects (nav bar) are portal-level and correctly unprefixed.
_PORTAL_OWN = ("/dashboard", "/app/curator", "/app/german", "/app/portuguese", "/app/cos",
               "/guild", "/logout", "/login", "/account", "/static/portal", "/static/tour")


def _assert_prefixed(html: str, where: str):
    bad = [m.group(1) for m in _UNPREFIXED.finditer(html)
           if not m.group(1).startswith(PREFIX) and not m.group(1).startswith(_PORTAL_OWN)]
    assert not bad, f"{where}: unprefixed absolute URLs {bad[:5]}"
    assert f"{PREFIX}{PREFIX}" not in html, f"{where}: double prefix"


# ── launch page, assets, presentation ────────────────────────────────────────

def test_launch_page(owner, backend_calls):
    r = owner.get(f"{PREFIX}/")
    assert r.status_code == 200
    html = r.data.decode()
    _assert_prefixed(html, "launch page")
    assert re.search(r'<meta[^>]*name="connecthq-root-path"[^>]*content="/app/connecthq"', html) or \
           re.search(r'<meta[^>]*content="/app/connecthq"[^>]*name="connecthq-root-path"', html)
    assert "Connect HQ" in html


@pytest.mark.parametrize("asset", [
    "static/nightjar/nightjar.css", "static/nightjar/nightjar.js", "static/api-client.js",
    "static/styles.css",
])
def test_css_and_js_assets(owner, asset):
    r = owner.get(f"{PREFIX}/{asset}")
    assert r.status_code == 200, asset
    body = r.data.decode()
    assert f"{PREFIX}{PREFIX}" not in body
    if asset.endswith(".js"):
        assert 'fetch("/api' not in body and "fetch('/api" not in body


def test_presentation_and_slides(owner):
    r = owner.get(f"{PREFIX}/presentation")
    assert r.status_code == 200
    html = r.data.decode()
    _assert_prefixed(html, "presentation")
    slides = re.findall(r"""src=["'](/app/connecthq/[^"']+\.(?:png|svg|jpg|webp))""", html)
    assert slides, "presentation lists no slide images"
    for s in slides[:3]:
        assert owner.get(s).status_code == 200, s


# ── customer and operator navigation ─────────────────────────────────────────

@pytest.mark.parametrize("page", ["operator", "admin", "portal", "operator/accounts", "operator/inventory"])
def test_navigation_pages(owner, page):
    r = owner.get(f"{PREFIX}/{page}")
    assert r.status_code in (200, 302), page
    if r.status_code == 302:
        assert r.headers["Location"].startswith(PREFIX)
    else:
        _assert_prefixed(r.data.decode(), page)


# ── API reads, Swagger, OpenAPI ───────────────────────────────────────────────

def test_get_api_call(owner):
    r = owner.get(f"{PREFIX}/api/v1/health")
    assert r.status_code == 200 and r.is_json
    r = owner.get(f"{PREFIX}/api/v1/accounts")
    assert r.status_code in (200, 403)


def test_swagger_ui_and_openapi(owner):
    r = owner.get(f"{PREFIX}/docs")
    assert r.status_code == 200
    html = r.data.decode()
    assert "openapi.json" in html and f"{PREFIX}{PREFIX}" not in html
    r = owner.get(f"{PREFIX}/openapi.json")
    assert r.status_code == 200
    spec = json.loads(r.data)
    assert spec.get("servers") == [{"url": PREFIX}], spec.get("servers")


# ── mutations (require hosted identity mode in the backend) ──────────────────

def _hosted_mode_ok(owner) -> bool:
    r = owner.get(f"{PREFIX}/api/v1/admin/legacy-accounts/available")
    return r.status_code == 200


def test_admin_mutation_and_workflow(owner):
    if not _hosted_mode_ok(owner):
        pytest.xfail("backend not in CONNECTHQ_AUTH_MODE=minimoi_proxy: portal strips X-Demo-*, admin calls 403 (expected until candidate revision 4)")
    accounts = owner.get(f"{PREFIX}/api/v1/accounts").get_json()
    assert accounts, "seeded accounts expected"
    acct = accounts[0]["account_id"] if isinstance(accounts, list) else next(iter(accounts.values()))[0]["account_id"]
    # summarized-billing change (admin PATCH)
    r = owner.patch(f"{PREFIX}/api/v1/accounts/{acct}/configuration",
                    data=json.dumps({"billing_mode": "SUMMARIZED"}), content_type="application/json")
    assert r.status_code in (200, 409, 422), r.data[:200]
    # bill run + evidence
    r = owner.post(f"{PREFIX}/api/v1/bill-runs", data=json.dumps({"account_id": acct}),
                   content_type="application/json")
    assert r.status_code in (200, 201, 409), r.data[:200]
    if r.status_code in (200, 201):
        run_id = r.get_json()["bill_run_id"]
        assert owner.get(f"{PREFIX}/api/v1/bill-runs/{run_id}/charges").status_code == 200
        assert owner.get(f"{PREFIX}/api/v1/bill-runs/{run_id}/reconciliation").status_code == 200


# ── isolation: nothing escaped to another mini-moi backend ───────────────────

def test_no_request_reached_an_unrelated_backend(backend_calls):
    assert backend_calls, "spy recorded no calls"
    foreign = [u for _, u in backend_calls if not u.startswith(BACKEND)]
    assert not foreign, foreign
