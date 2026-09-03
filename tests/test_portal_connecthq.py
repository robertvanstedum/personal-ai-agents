"""Connect HQ hosting adapter — portal-side contract (Spec #154, PR #193).

Covers: owner-only access, Guild Improve placement (no global workspace entry),
trusted-identity header boundary (client X-Demo-* / X-Minimoi-* never reach the
backend), idempotent prefix rewriting, no leakage into the Curator /api/*
passthrough, and Compose-project isolation of the production definitions.

The live prefix-routing suite against a running Connect HQ backend lives in
tests/test_portal_connecthq_prefix_live.py and skips unless one is reachable.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = "spec_154_connect_hq_minimoi_production_hosting_2026-09-03.md"


# ── helpers ──────────────────────────────────────────────────────────────────

def _code(path: Path) -> str:
    """File text with comment lines removed, so prose never satisfies/defeats a check."""
    return "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))


@pytest.fixture(autouse=True)
def _fresh_session(portal_client):
    """The shared session-scoped client keeps cookies; leave it signed out."""
    yield
    with portal_client.session_transaction() as sess:
        sess.pop("user", None)

def _login(client, user):
    with client.session_transaction() as sess:
        if user is None:
            sess.pop("user", None)
        else:
            sess["user"] = user


class _FakeResp:
    def __init__(self, status=200, content=b"ok", ctype="text/plain", headers=None):
        self.status_code = status
        self.content = content
        self.text = content.decode()
        self.headers = {"content-type": ctype, **(headers or {})}


@pytest.fixture
def capture_backend(monkeypatch):
    """Replace the proxy's outbound HTTP call; record what would hit the backend."""
    import minimoi_portal.proxy as proxy_mod
    calls = []

    def fake_request(method, url, headers=None, data=None, allow_redirects=False, timeout=30):
        calls.append({"method": method, "url": url, "headers": dict(headers or {})})
        return _FakeResp()

    monkeypatch.setattr(proxy_mod.requests, "request", fake_request)
    return calls


OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}
ADMIN = {"username": "admin", "tier": "admin", "display_name": "Admin", "auth_id": 2}
GUEST = {"username": "guest_ab12cd34", "tier": "guest", "display_name": "Guest"}
DOMAIN_USER = {"username": "interviewer@example.com", "tier": "guest", "auth_id": 77}


# ── P0-2: owner only ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/app/connecthq", "/app/connecthq/", "/app/connecthq/presentation",
                                  "/app/connecthq/api/v1/health", "/app/connecthq/docs"])
def test_signed_out_is_redirected_to_login(portal_client, path):
    _login(portal_client, None)
    r = portal_client.get(path)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


@pytest.mark.parametrize("user", [GUEST, DOMAIN_USER, ADMIN], ids=["guest", "domain-user", "admin"])
def test_non_owner_sessions_are_denied(portal_client, capture_backend, user):
    _login(portal_client, user)
    for path in ("/app/connecthq", "/app/connecthq/api/v1/admin/legacy-accounts/available"):
        r = portal_client.get(path)
        assert r.status_code == 302, (user["tier"], path, r.status_code)
        assert "owner_required" in r.headers["Location"]
    assert capture_backend == [], "a non-owner request must never reach the backend"


def test_owner_reaches_backend(portal_client, capture_backend):
    _login(portal_client, OWNER)
    r = portal_client.get("/app/connecthq/api/v1/health")
    assert r.status_code == 200
    assert len(capture_backend) == 1
    import minimoi_portal.config as cfg
    assert capture_backend[0]["url"] == f"{cfg.CONNECTHQ_BACKEND}/app/connecthq/api/v1/health", \
        "root-path backends receive the full prefixed path"


def test_owner_mutation_methods_are_proxied(portal_client, capture_backend):
    _login(portal_client, OWNER)
    for method in ("post", "patch", "put", "delete"):
        getattr(portal_client, method)("/app/connecthq/api/v1/accounts/x", data=b"{}")
    assert [c["method"] for c in capture_backend] == ["POST", "PATCH", "PUT", "DELETE"]


# ── P0-1: Guild Improve placement, not global navigation ────────────────────

def test_connecthq_is_not_a_global_workspace():
    from minimoi_portal.workspaces import WORKSPACES, workspace_navigation
    assert all(w["key"] != "connecthq" for w in WORKSPACES)
    assert all("/app/connecthq" not in w["path"] for w in workspace_navigation(OWNER))


def test_proxy_nav_does_not_link_connecthq_for_owner():
    from minimoi_portal.proxy import _portal_nav_html
    html = _portal_nav_html(OWNER, "/app/connecthq")
    assert 'href="/app/connecthq"' not in html
    assert 'href="/guild"' in html


def test_guild_improve_shows_one_connecthq_card(portal_client, monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app, "_connecthq_available", lambda: False)
    _login(portal_client, OWNER)
    r = portal_client.get("/guild/improve")
    assert r.status_code == 200
    html = r.data.decode()
    assert html.count('class="demo-card"') == 1
    assert "Prototype Lab" in html and "Reference Demos" in html
    assert "AWS demo" in html
    assert "connecthq-v0.9.0-beta.1" in html
    assert "Unavailable" in html
    assert 'href="/app/connecthq"' in html and "Launch Connect HQ" in html
    assert f'href="/guild/build/spec/{SPEC}"' in html and "View specification" in html
    assert "In Redesign" not in html


def test_guild_improve_reports_available_when_backend_healthy(portal_client, monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app, "_connecthq_available", lambda: True)
    _login(portal_client, OWNER)
    html = portal_client.get("/guild/improve").data.decode()
    assert "health up" in html and ">Available<" in html


def test_guild_improve_is_owner_only(portal_client):
    _login(portal_client, ADMIN)
    assert portal_client.get("/guild/improve").status_code == 302


def test_spec_154_is_committed_and_rendered(portal_client):
    assert (REPO / "docs" / "specs" / SPEC).exists()
    _login(portal_client, OWNER)
    r = portal_client.get(f"/guild/build/spec/{SPEC}")
    assert r.status_code == 200
    assert b"Spec file not found" not in r.data
    assert b"Connect HQ on mini-moi Production Hosting" in r.data


# ── P0-3: trusted identity boundary at the proxy ────────────────────────────

def test_client_demo_and_minimoi_headers_never_reach_backend(portal_client, capture_backend):
    _login(portal_client, OWNER)
    portal_client.get("/app/connecthq/api/v1/accounts", headers={
        "X-Demo-Role": "BUSINESS_OPS_ADMIN",
        "X-Demo-Account-ID": "spoofed",
        "X-Minimoi-User-Tier": "owner",
        "X-Minimoi-Username": "attacker",
        "X-Request-ID": "keep-me",
    })
    sent = {k.lower(): v for k, v in capture_backend[0]["headers"].items()}
    assert not any(k.startswith("x-demo-") for k in sent), sent
    assert sent["x-minimoi-user-tier"] == "owner"
    assert sent["x-minimoi-username"] == "owner"          # from the session, not the client
    assert sent["x-minimoi-auth-id"] == "1"
    assert sent.get("x-request-id") == "keep-me"          # unrelated headers still forwarded


def test_spoofed_demo_headers_do_not_elevate_non_owner(portal_client, capture_backend):
    _login(portal_client, GUEST)
    r = portal_client.post("/app/connecthq/api/v1/bill-runs", headers={
        "X-Demo-Role": "BUSINESS_OPS_ADMIN", "X-Minimoi-User-Tier": "owner"})
    assert r.status_code == 302 and capture_backend == []


def test_other_backends_do_not_strip_demo_headers_by_default(portal_client, capture_backend):
    """The X-Demo-* strip is scoped to the Connect HQ boundary (no behaviour
    change for Curator/German/Portuguese/CoS)."""
    _login(portal_client, OWNER)
    portal_client.get("/app/cos/ui", headers={"X-Demo-Role": "x"})
    sent = {k.lower() for k in capture_backend[0]["headers"]}
    assert "x-demo-role" in sent


# ── P0-5: one prefix strategy, idempotent portal rewriting ───────────────────

def test_html_rewrite_is_idempotent(portal_client, capture_backend, monkeypatch):
    import minimoi_portal.proxy as proxy_mod
    html = (b'<html><head><meta name="connecthq-root-path" content="/app/connecthq">'
            b'<link href="/app/connecthq/static/nightjar/nightjar.css">'
            b'<link href="/static/plain.css"></head><body>'
            b'<a href="/app/connecthq/presentation">p</a><a href="/operator">o</a>'
            b'<img src="//cdn.example/x.png"><form action="/app/connecthq/api/v1/x"></form>'
            b'<script>const API_BASE = "/app/connecthq/api/v1"; fetch("/api/v1/health"); '
            b'fetch("/app/connecthq/api/v1/health");</script></body></html>')
    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(content=html, ctype="text/html"))
    _login(portal_client, OWNER)
    out = portal_client.get("/app/connecthq/").data.decode()
    assert out.count("/app/connecthq/app/connecthq") == 0, out
    assert 'href="/app/connecthq/static/nightjar/nightjar.css"' in out
    assert 'href="/app/connecthq/static/plain.css"' in out
    assert 'href="/app/connecthq/presentation"' in out
    assert 'href="/app/connecthq/operator"' in out
    assert 'src="//cdn.example/x.png"' in out
    assert 'fetch("/app/connecthq/api/v1/health")' in out
    assert out.count('fetch("/app/connecthq/api/v1/health")') == 2


def test_js_css_and_redirect_rewrites_are_idempotent(portal_client, monkeypatch):
    import minimoi_portal.proxy as proxy_mod
    from minimoi_portal.proxy import _rewrite_js
    js = 'fetch("/app/connecthq/api/v1/a"); fetch("/api/v1/b"); window.location = "/app/connecthq/operator";'
    out = _rewrite_js(js, "/app/connecthq")
    assert "/app/connecthq/app/connecthq" not in out
    assert 'fetch("/app/connecthq/api/v1/b")' in out

    css = b"body{background:url('/app/connecthq/static/a.png')} h1{background:url('/static/b.png')}"
    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(content=css, ctype="text/css"))
    _login(portal_client, OWNER)
    out = portal_client.get("/app/connecthq/static/x.css").data.decode()
    assert out.count("/app/connecthq/static/") == 2 and "/app/connecthq/app/" not in out

    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(status=302, headers={"Location": "/app/connecthq/docs"}))
    r = portal_client.get("/app/connecthq/redirect")
    assert r.headers["Location"] == "/app/connecthq/docs"
    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(status=302, headers={"Location": "/docs"}))
    r = portal_client.get("/app/connecthq/redirect")
    assert r.headers["Location"] == "/app/connecthq/docs"


def test_top_level_api_passthrough_still_targets_curator_only(portal_client, capture_backend):
    """No Connect HQ fall-through into the broad /api/* catch-all (Spec §5)."""
    import minimoi_portal.config as cfg
    _login(portal_client, OWNER)
    portal_client.get("/api/v1/health")
    assert capture_backend[0]["url"].startswith(cfg.CURATOR_BACKEND)
    src = (REPO / "minimoi_portal" / "app.py").read_text()
    passthrough = src[src.index('@app.route("/api/<path:path>"'):src.index("_CURATOR_TOP_LEVEL")]
    assert "CONNECTHQ" not in passthrough


# ── P0-4: independent Compose project / deployment isolation ────────────────

def test_connecthq_has_its_own_compose_project_and_main_compose_has_no_demo_services():
    main = _code(REPO / "docker-compose.prod.yml")
    demo = _code(REPO / "docker-compose.connecthq.prod.yml")
    assert re.search(r"^name:\s*connecthq\s*$", demo, re.M)
    assert "connecthq-edge:\n    external: true" in demo
    assert "MINIMOI_IMAGE_TAG" not in demo
    assert "CONNECTHQ_IMAGE_TAG" in demo
    assert 'name: connecthq-edge' in main
    assert "CONNECTHQ_BACKEND=http://minimoi-connecthq:8095" in main
    for svc in ("minimoi-connecthq-postgres", "minimoi-connecthq-integrations", "container_name: minimoi-connecthq\n"):
        assert svc not in main, f"{svc} must not be defined in the mini-moi project"
    # only the app publishes a host port, and only on loopback
    assert demo.count('"127.0.0.1:8095:8095"') == 1
    assert "8096:8096" not in demo and "5432:5432" not in demo


def test_release_workflow_is_tag_only_and_never_touches_the_minimoi_project():
    wf = _code(REPO / ".github" / "workflows" / "deploy-connecthq.yml")
    assert "tags: ['connecthq-v*']" in wf
    assert "branches" not in wf
    assert "docker-compose.prod.yml" not in wf, "the release must not overwrite the mini-moi compose file"
    assert "--remove-orphans" not in wf
    assert "^connecthq-v[0-9]+" in wf, "manual tag input must be validated"
    script = _code(REPO / "scripts" / "operations" / "deploy_connecthq_ec2.sh")
    assert "-p connecthq" in script and "DIR=/opt/minimoi/connecthq" in script and "$DIR/docker-compose.yml" in script
    assert "--remove-orphans" not in script
    assert "docker-compose.prod.yml" not in script
    assert "get-parameter" in script and "> \"$DIR/.env\"" in script
    assert "http://127.0.0.1:5001/app/connecthq" in script     # validates the real entry point
