"""IoT Connect hosting adapter — portal-side contract (Spec #154, PR #193).

Covers: owner-only access, no global workspace entry (the demo's Guild surface
moved to Experiment in Spec #155),
trusted-identity header boundary (client X-Demo-* / X-Minimoi-* never reach the
backend), idempotent prefix rewriting, no leakage into the Curator /api/*
passthrough, and Compose-project isolation of the production definitions.

The live prefix-routing suite against a running IoT Connect backend lives in
tests/test_portal_iotconnect_prefix_live.py and skips unless one is reachable.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = "spec_154_iot_connect_minimoi_production_hosting_2026-09-04.md"
LEGACY_SPEC = "spec_154_connect_hq_minimoi_production_hosting_2026-09-03.md"


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

@pytest.mark.parametrize("path", ["/app/iotconnect", "/app/iotconnect/", "/app/iotconnect/presentation",
                                  "/app/iotconnect/api/v1/health", "/app/iotconnect/docs"])
def test_signed_out_is_redirected_to_login(portal_client, path):
    _login(portal_client, None)
    r = portal_client.get(path)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


@pytest.mark.parametrize("user", [GUEST, DOMAIN_USER, ADMIN], ids=["guest", "domain-user", "admin"])
def test_non_owner_sessions_are_denied(portal_client, capture_backend, user):
    _login(portal_client, user)
    for path in ("/app/iotconnect", "/app/iotconnect/api/v1/admin/legacy-accounts/available"):
        r = portal_client.get(path)
        assert r.status_code == 302, (user["tier"], path, r.status_code)
        assert "owner_required" in r.headers["Location"]
    assert capture_backend == [], "a non-owner request must never reach the backend"


def test_owner_reaches_backend(portal_client, capture_backend):
    _login(portal_client, OWNER)
    r = portal_client.get("/app/iotconnect/api/v1/health")
    assert r.status_code == 200
    assert len(capture_backend) == 1
    import minimoi_portal.config as cfg
    assert capture_backend[0]["url"] == f"{cfg.IOTCONNECT_BACKEND}/app/iotconnect/api/v1/health", \
        "root-path backends receive the full prefixed path"


def test_owner_mutation_methods_are_proxied(portal_client, capture_backend):
    _login(portal_client, OWNER)
    for method in ("post", "patch", "put", "delete"):
        getattr(portal_client, method)("/app/iotconnect/api/v1/accounts/x", data=b"{}")
    assert [c["method"] for c in capture_backend] == ["POST", "PATCH", "PUT", "DELETE"]


# ── P0-1: not global navigation; Improve is a placeholder again ─────────────

def test_iotconnect_is_not_a_global_workspace():
    from minimoi_portal.workspaces import WORKSPACES, workspace_navigation
    assert all(w["key"] != "iotconnect" for w in WORKSPACES)
    assert all("/app/iotconnect" not in w["path"] for w in workspace_navigation(OWNER))


def test_proxy_nav_does_not_link_iotconnect_for_owner():
    from minimoi_portal.proxy import _portal_nav_html
    html = _portal_nav_html(OWNER, "/app/iotconnect")
    assert 'href="/app/iotconnect"' not in html
    assert 'href="/guild"' in html


def test_guild_improve_is_a_placeholder_again_and_points_at_experiment(portal_client):
    """Spec #155 §7: Experiment owns the demo, so Improve reverts to its stub."""
    _login(portal_client, OWNER)
    r = portal_client.get("/guild/improve")
    assert r.status_code == 200
    html = r.data.decode()
    assert "In Redesign" in html
    assert "Review · Analyze · Improve" in html
    assert 'href="/guild/experiment"' in html
    assert "Reference demos now live under" in html
    assert 'class="demo-card"' not in html
    for gone in ("Prototype Lab", "Reference Demos", "AWS demo", "Launch demo",
                 "iotconnect-v0.9.0-beta.1", f"/guild/build/spec/{SPEC}"):
        assert gone not in html, gone
    assert "/app/iotconnect" not in html


def test_guild_improve_does_not_probe_any_backend(portal_client, monkeypatch):
    """The Improve stub is static: no health check, no card state to compute."""
    import minimoi_portal.app as portal_app

    def _boom(*a, **k):
        raise AssertionError("Improve must not make outbound requests")

    monkeypatch.setattr(portal_app._requests, "get", _boom)
    _login(portal_client, OWNER)
    assert portal_client.get("/guild/improve").status_code == 200
    assert not hasattr(portal_app, "_iotconnect_available")


def test_guild_improve_is_owner_only(portal_client):
    _login(portal_client, ADMIN)
    assert portal_client.get("/guild/improve").status_code == 302


def test_spec_154_is_committed_and_rendered(portal_client):
    assert (REPO / "docs" / "specs" / SPEC).exists()
    legacy = REPO / "docs" / "specs" / LEGACY_SPEC
    assert legacy.exists(), "the superseded spec stays as history"
    assert f"Superseded 2026-09-04 by `{SPEC}`" in legacy.read_text()
    _login(portal_client, OWNER)
    r = portal_client.get(f"/guild/build/spec/{SPEC}")
    assert r.status_code == 200
    assert b"Spec file not found" not in r.data
    assert b"IoT Connect on mini-moi Production Hosting" in r.data


# ── P0-3: trusted identity boundary at the proxy ────────────────────────────

def test_client_demo_and_minimoi_headers_never_reach_backend(portal_client, capture_backend):
    _login(portal_client, OWNER)
    portal_client.get("/app/iotconnect/api/v1/accounts", headers={
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
    r = portal_client.post("/app/iotconnect/api/v1/bill-runs", headers={
        "X-Demo-Role": "BUSINESS_OPS_ADMIN", "X-Minimoi-User-Tier": "owner"})
    assert r.status_code == 302 and capture_backend == []


def test_other_backends_do_not_strip_demo_headers_by_default(portal_client, capture_backend):
    """The X-Demo-* strip is scoped to the IoT Connect boundary (no behaviour
    change for Curator/German/Portuguese/CoS)."""
    _login(portal_client, OWNER)
    portal_client.get("/app/cos/ui", headers={"X-Demo-Role": "x"})
    sent = {k.lower() for k in capture_backend[0]["headers"]}
    assert "x-demo-role" in sent


# ── P0-5: one prefix strategy, idempotent portal rewriting ───────────────────

def test_html_rewrite_is_idempotent(portal_client, capture_backend, monkeypatch):
    import minimoi_portal.proxy as proxy_mod
    html = (b'<html><head><meta name="iotconnect-root-path" content="/app/iotconnect">'
            b'<link href="/app/iotconnect/static/iotconnect/iotconnect.css">'
            b'<link href="/static/plain.css"></head><body>'
            b'<a href="/app/iotconnect/presentation">p</a><a href="/operator">o</a>'
            b'<img src="//cdn.example/x.png"><form action="/app/iotconnect/api/v1/x"></form>'
            b'<script>const API_BASE = "/app/iotconnect/api/v1"; fetch("/api/v1/health"); '
            b'fetch("/app/iotconnect/api/v1/health");</script></body></html>')
    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(content=html, ctype="text/html"))
    _login(portal_client, OWNER)
    out = portal_client.get("/app/iotconnect/").data.decode()
    assert out.count("/app/iotconnect/app/iotconnect") == 0, out
    assert 'href="/app/iotconnect/static/iotconnect/iotconnect.css"' in out
    assert 'href="/app/iotconnect/static/plain.css"' in out
    assert 'href="/app/iotconnect/presentation"' in out
    assert 'href="/app/iotconnect/operator"' in out
    assert 'src="//cdn.example/x.png"' in out
    assert 'fetch("/app/iotconnect/api/v1/health")' in out
    assert out.count('fetch("/app/iotconnect/api/v1/health")') == 2


def test_js_css_and_redirect_rewrites_are_idempotent(portal_client, monkeypatch):
    import minimoi_portal.proxy as proxy_mod
    from minimoi_portal.proxy import _rewrite_js
    js = 'fetch("/app/iotconnect/api/v1/a"); fetch("/api/v1/b"); window.location = "/app/iotconnect/operator";'
    out = _rewrite_js(js, "/app/iotconnect")
    assert "/app/iotconnect/app/iotconnect" not in out
    assert 'fetch("/app/iotconnect/api/v1/b")' in out

    css = b"body{background:url('/app/iotconnect/static/a.png')} h1{background:url('/static/b.png')}"
    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(content=css, ctype="text/css"))
    _login(portal_client, OWNER)
    out = portal_client.get("/app/iotconnect/static/x.css").data.decode()
    assert out.count("/app/iotconnect/static/") == 2 and "/app/iotconnect/app/" not in out

    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(status=302, headers={"Location": "/app/iotconnect/docs"}))
    r = portal_client.get("/app/iotconnect/redirect")
    assert r.headers["Location"] == "/app/iotconnect/docs"
    monkeypatch.setattr(proxy_mod.requests, "request",
                        lambda *a, **k: _FakeResp(status=302, headers={"Location": "/docs"}))
    r = portal_client.get("/app/iotconnect/redirect")
    assert r.headers["Location"] == "/app/iotconnect/docs"


def test_top_level_api_passthrough_still_targets_curator_only(portal_client, capture_backend):
    """No IoT Connect fall-through into the broad /api/* catch-all (Spec §5)."""
    import minimoi_portal.config as cfg
    _login(portal_client, OWNER)
    portal_client.get("/api/v1/health")
    assert capture_backend[0]["url"].startswith(cfg.CURATOR_BACKEND)
    src = (REPO / "minimoi_portal" / "app.py").read_text()
    passthrough = src[src.index('@app.route("/api/<path:path>"'):src.index("_CURATOR_TOP_LEVEL")]
    assert "IOTCONNECT" not in passthrough


# ── P0-4: independent Compose project / deployment isolation ────────────────

def test_iotconnect_has_its_own_compose_project_and_main_compose_has_no_demo_services():
    main = _code(REPO / "docker-compose.prod.yml")
    demo = _code(REPO / "docker-compose.iotconnect.prod.yml")
    assert re.search(r"^name:\s*iotconnect\s*$", demo, re.M)
    assert "iotconnect-edge:\n    external: true" in demo
    assert "MINIMOI_IMAGE_TAG" not in demo
    assert "IOTCONNECT_IMAGE_TAG" in demo
    assert 'name: iotconnect-edge' in main
    assert "IOTCONNECT_BACKEND=http://minimoi-iotconnect:8095" in main
    # the portal reads IOTCONNECT_RELEASE_LABEL; the retired CONNECTHQ_RELEASE
    # was never read by config.py and must not come back.
    assert "IOTCONNECT_RELEASE_LABEL=${IOTCONNECT_RELEASE_LABEL:-iotconnect-v0.9.0-beta.1}" in main
    for svc in ("minimoi-iotconnect-postgres", "minimoi-iotconnect-integrations", "container_name: minimoi-iotconnect\n"):
        assert svc not in main, f"{svc} must not be defined in the mini-moi project"
    # only the app publishes a host port, and only on loopback
    assert demo.count('"127.0.0.1:8095:8095"') == 1
    assert "8096:8096" not in demo and "5432:5432" not in demo


def test_compose_env_names_match_what_the_promoted_app_reads():
    """The hosted env must use the app's real variable names, not retired ones."""
    demo = _code(REPO / "docker-compose.iotconnect.prod.yml")
    for name in ("IOTCONNECT_STORE=postgres", "POSTGRES_DSN=", "FLOWONE_BASE_URL=",
                 "AMDOCS_MIDDLEWARE_BASE_URL=", "IOTCONNECT_INTEGRATIONS_URL=",
                 "IOTCONNECT_APP_URL=", "IOTCONNECT_SEED_TIMESTAMP=",
                 "IOTCONNECT_AUTH_MODE=minimoi_proxy",
                 "IOTCONNECT_ROOT_PATH=/app/iotconnect"):
        assert name in demo, name
    assert "WHAM_V3_STORE" not in demo, "the app reads IOTCONNECT_STORE"
    app_src = "\n".join(
        p.read_text() for p in sorted((REPO / "prototype-lab" / "projects"
                                       / "project-iot-connect" / "app").rglob("*.py")))
    for name in ("IOTCONNECT_STORE", "IOTCONNECT_ROOT_PATH", "IOTCONNECT_AUTH_MODE",
                 "IOTCONNECT_SEED_TIMESTAMP", "POSTGRES_DSN",
                 "FLOWONE_BASE_URL", "AMDOCS_MIDDLEWARE_BASE_URL"):
        assert name in app_src, f"{name} is set in compose but not read by the app"


def test_release_workflow_is_tag_only_and_never_touches_the_minimoi_project():
    wf = _code(REPO / ".github" / "workflows" / "deploy-iotconnect.yml")
    assert "tags: ['iotconnect-v*']" in wf
    assert "branches" not in wf
    assert "docker-compose.prod.yml" not in wf, "the release must not overwrite the mini-moi compose file"
    assert "--remove-orphans" not in wf
    assert "^iotconnect-v[0-9]+" in wf, "manual tag input must be validated"
    assert "APP_DIR: prototype-lab/projects/project-iot-connect" in wf
    assert "$REGISTRY/minimoi/iotconnect:$TAG" in wf
    assert "docker-compose.iotconnect.prod.yml" in wf
    assert "scripts/operations/deploy_iotconnect_ec2.sh" in wf
    script = _code(REPO / "scripts" / "operations" / "deploy_iotconnect_ec2.sh")
    assert "-p iotconnect" in script and "DIR=/opt/minimoi/iotconnect" in script and "$DIR/docker-compose.yml" in script
    assert "--remove-orphans" not in script
    assert "docker-compose.prod.yml" not in script
    assert "get-parameter" in script and "> \"$DIR/.env\"" in script
    assert "/minimoi/production/iotconnect_db_password" in script
    assert "IOTCONNECT_DB_PASSWORD" in script and "IOTCONNECT_IMAGE_TAG" in script
    assert "iotconnect-edge" in script
    assert """'"/app/iotconnect"'""" in script                 # openapi advertises the prefix
    assert "http://127.0.0.1:5001/app/iotconnect" in script    # validates the real entry point


def test_release_pipeline_files_carry_no_retired_connecthq_name():
    """Workflow, compose and deploy script must be free of the retired name."""
    for rel in (".github/workflows/deploy-iotconnect.yml",
                "docker-compose.iotconnect.prod.yml",
                "scripts/operations/deploy_iotconnect_ec2.sh",
                "docker-compose.prod.yml",
                "minimoi_portal/proxy.py"):
        text = (REPO / rel).read_text()
        hits = [l for l in text.splitlines()
                if re.search(r"connect[ _-]?hq", l, re.I)]
        assert hits == [], (rel, hits)


def test_promoted_candidate_tree_is_present_and_digest_pinned():
    """Mirrors the workflow's "Tagged source sanity" step (Spec #154 §8.3)."""
    app_dir = REPO / "prototype-lab" / "projects" / "project-iot-connect"
    for name in ("Dockerfile", "Makefile", "requirements.lock"):
        assert (app_dir / name).is_file(), name
    dockerfile = (app_dir / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.12-slim@sha256:", dockerfile, re.M), \
        "the base image must be pinned by digest"



# ── N3 rename: retired path redirect, one release ───────────────────────────

@pytest.mark.parametrize("legacy,expected", [
    ("/app/connecthq", "/app/iotconnect"),
    ("/app/connecthq/", "/app/iotconnect"),
    ("/app/connecthq/docs?x=1", "/app/iotconnect/docs?x=1"),
    ("/app/connecthq/api/v1/health", "/app/iotconnect/api/v1/health"),
    ("/app/connecthq/operator?a=1&b=2", "/app/iotconnect/operator?a=1&b=2"),
])
def test_legacy_path_permanently_redirects_preserving_query(
        portal_client, capture_backend, legacy, expected):
    _login(portal_client, None)
    r = portal_client.get(legacy)
    assert r.status_code == 301, legacy
    assert r.headers["Location"].endswith(expected), r.headers["Location"]
    assert capture_backend == [], "the legacy path must never proxy"


def test_legacy_redirect_happens_before_authentication_and_never_proxies(
        portal_client, capture_backend):
    """A signed-out visitor is redirected to the current path, which then
    applies owner_required — no backend call is made on the retired route."""
    _login(portal_client, GUEST)
    r = portal_client.get("/app/connecthq/docs")
    assert r.status_code == 301
    assert capture_backend == []


def test_legacy_constant_names_the_retired_path_only():
    import minimoi_portal.config as cfg
    assert cfg.HOSTED_PORTAL_PATH == "/app/iotconnect"
    assert cfg.LEGACY_PORTAL_PATH == "/app/connecthq"
    assert cfg.iotconnect_surfaces()["launch"].startswith("/app/iotconnect")


def test_surface_validator_error_names_the_current_path(monkeypatch):
    import minimoi_portal.config as cfg
    with pytest.raises(RuntimeError) as excinfo:
        cfg._validate_surface_base_url("/app/connecthq")
    assert "/app/iotconnect" in str(excinfo.value)
    assert "IOTCONNECT_SURFACE_BASE_URL" in str(excinfo.value)


# ── N3 rename: one-release environment-variable fallback ────────────────────

def _reload_config(monkeypatch, env):
    import importlib
    import minimoi_portal.config as cfg
    for key in list(os.environ):
        if key.startswith(("IOTCONNECT_", "CONNECTHQ_")):
            monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(cfg)


def test_legacy_env_name_is_honoured_when_the_new_one_is_unset(monkeypatch):
    cfg = _reload_config(monkeypatch, {"CONNECTHQ_BACKEND": "http://legacy:8095"})
    try:
        assert cfg.IOTCONNECT_BACKEND == "http://legacy:8095"
    finally:
        _reload_config(monkeypatch, {})


def test_new_env_name_wins_when_both_are_set(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "CONNECTHQ_BACKEND": "http://legacy:8095",
        "IOTCONNECT_BACKEND": "http://current:8095",
        "CONNECTHQ_RELEASE_LABEL": "legacy label",
        "IOTCONNECT_RELEASE_LABEL": "current label",
    })
    try:
        assert cfg.IOTCONNECT_BACKEND == "http://current:8095"
        assert cfg.IOTCONNECT_RELEASE_LABEL == "current label"
    finally:
        _reload_config(monkeypatch, {})


def test_default_release_label_is_the_untagged_candidate(monkeypatch):
    cfg = _reload_config(monkeypatch, {})
    try:
        assert cfg.IOTCONNECT_RELEASE_LABEL == "revision 7 candidate"
        assert cfg.IOTCONNECT_INITIATIVE_ID == "INIT-2026-0004"
    finally:
        _reload_config(monkeypatch, {})


# ── Codex release review 1 — release-path defects ────────────────────────────
#
# P0: the tag workflow must build the local application image BEFORE the
# verification suite (the promoted Compose file declares `pull_policy: never`,
# so a clean runner has no image) and must push the very image it tested.
# P1: one exact release-tag grammar in the portal, the workflow and the deploy
#     script; a database-password contract; a read-only hosted-mode smoke.

WORKFLOW = REPO / ".github" / "workflows" / "deploy-iotconnect.yml"
DEPLOY_SCRIPT = REPO / "scripts" / "operations" / "deploy_iotconnect_ec2.sh"
DEPLOY_LIB = REPO / "scripts" / "operations" / "lib" / "iotconnect_release_lib.sh"
PROMOTED_COMPOSE = (REPO / "prototype-lab" / "projects" / "project-iot-connect"
                    / "docker-compose.yml")


def _release_steps():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(WORKFLOW.read_text())
    return doc["jobs"]["verify-build-push"]["steps"]


def _promoted_local_image_tag() -> str:
    """The exact local tag the promoted Compose file names for the app service."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PROMOTED_COMPOSE.read_text())
    return doc["services"]["app"]["image"]


def test_release_workflow_builds_the_image_before_it_tests_it():
    steps = _release_steps()
    names = [s.get("name", "") for s in steps]
    build_i = next(i for i, n in enumerate(names) if n.startswith("Build the local application image"))
    test_i = next(i for i, n in enumerate(names) if n.startswith("Standalone verification suite"))
    login_i = next(i for i, n in enumerate(names) if n == "Login to ECR")
    push_i = next(i for i, n in enumerate(names) if n == "Build, push, record digest")
    assert build_i < test_i < login_i < push_i, names


def test_release_workflow_pushes_the_image_it_tested_and_never_rebuilds():
    steps = _release_steps()
    runs = [s.get("run", "") for s in steps]
    builds = [r for r in runs if "docker build" in r]
    assert len(builds) == 1, "exactly one docker build, before the tests"
    assert "docker build" in [s.get("run", "") for s in steps
                              if s.get("name", "").startswith("Build the local application image")][0]
    push = [s for s in steps if s.get("name") == "Build, push, record digest"][0]["run"]
    assert "docker build" not in push, "the push step must not build a second image"
    assert 'docker tag "$LOCAL_TAG" "$IMG"' in push
    assert 'docker push "$IMG"' in push
    assert "RepoDigests" in push, "the pushed digest is still recorded"


def test_the_tested_tag_is_the_tag_the_promoted_compose_file_names():
    """The tested image tag is read from the Compose file, never hard-coded."""
    steps = _release_steps()
    build = [s for s in steps
             if s.get("name", "").startswith("Build the local application image")][0]
    assert build["working-directory"] == "prototype-lab/projects/project-iot-connect"
    run = build["run"]
    assert "docker-compose.yml" in run, "the tag is derived from the Compose file"
    assert 'docker build --platform linux/amd64 -t "$LOCAL_TAG" .' in run
    # …and the tag that derivation yields is the one the test container needs.
    local_tag = _promoted_local_image_tag()
    assert local_tag == "iotconnect-app:0.9.0-beta.1"
    compose_doc = pytest.importorskip("yaml").safe_load(PROMOTED_COMPOSE.read_text())
    assert compose_doc["services"]["integrations"]["pull_policy"] == "never"
    assert compose_doc["services"]["integrations"]["image"] == local_tag
    makefile = (REPO / "prototype-lab" / "projects" / "project-iot-connect" / "Makefile").read_text()
    assert "$(COMPOSE) run --rm --no-deps" in makefile, "make test runs a container from that image"


# ── one release-tag grammar in three places ─────────────────────────────────

def test_one_release_tag_grammar_in_portal_workflow_and_deploy_script():
    from minimoi_portal import config as cfg
    assert cfg.RELEASE_TAG_PATTERN == \
        r"iotconnect-v\d+\.\d+\.\d+(?:-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)?"
    ere = cfg.RELEASE_TAG_ERE
    assert ere == r"^iotconnect-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$"
    # byte-identical text in both shell copies
    assert ere in WORKFLOW.read_text(), "workflow resolve job must use the one grammar"
    assert ere in DEPLOY_SCRIPT.read_text(), "deploy script must use the one grammar"
    assert ere in DEPLOY_LIB.read_text(), "the shared lib must carry the one grammar"


@pytest.mark.parametrize("tag,ok", [
    ("iotconnect-v0.9.0-beta.1", True),
    ("iotconnect-v1.0.0", True),
    ("iotconnect-v0.9.0evil", False),
    ("iotconnect-v0.9.0/../../x", False),
    ("iotconnect-v0.9.0-", False),
    ("iotconnect-v0.9.0-beta.1\n", False),
    ("iotconnect-v0.9.0 ", False),
    ("connecthq-v0.9.0-beta.1", False),
    ("revision 7 candidate", False),
])
def test_release_tag_grammar_python_and_shell_agree(tag, ok):
    import subprocess
    from minimoi_portal import config as cfg
    assert bool(re.fullmatch(cfg.RELEASE_TAG_PATTERN, tag)) is ok
    proc = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_LIB}"; validate_release_tag "$1"', "_", tag],
        capture_output=True)
    assert (proc.returncode == 0) is ok, (tag, proc.returncode, proc.stderr)


# ── database-password contract ───────────────────────────────────────────────

def _validate_db_password(value: str) -> bool:
    import subprocess
    proc = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY_LIB}"; validate_db_password "$1"', "_", value],
        capture_output=True)
    return proc.returncode == 0


@pytest.mark.parametrize("value,ok", [
    ("a" * 40, True),                                   # 40 alphanumeric
    ("Xy3Kq9Zt7Lm2Rb8Wn4Vc6Hd1Jf5Gs0Pa", True),          # base64 with /+= removed
    ("Ab3-_.~!Ab3Ab3Ab3Ab3Ab3Ab3Ab3Ab3", True),         # -_.~! are safe
    ("Ab3$dollarAb3Ab3Ab3Ab3Ab3Ab3Ab3", True),          # $ is safe: .env is single-quoted
    ("a" * 12 + "@" + "a" * 15, False),                 # @ changes the parsed DSN host
    ("a" * 12 + ":" + "a" * 15, False),                 # : is a DSN delimiter
    ("a" * 12 + "/" + "a" * 15, False),
    ("a" * 12 + "?" + "a" * 15, False),
    ("a" * 12 + "#" + "a" * 15, False),
    ("a" * 12 + "%" + "a" * 15, False),
    ("a" * 12 + "'" + "a" * 15, False),                 # breaks the quoted .env line
    ("a" * 12 + "\n" + "a" * 15, False),
    ("a" * 12 + " " + "a" * 15, False),
    ("short10aaa", False),                              # under 24
    ("a" * 129, False),                                 # over 128
    ("", False),
])
def test_db_password_contract(value, ok):
    assert _validate_db_password(value) is ok


def test_deploy_script_enforces_the_password_contract_before_writing_env():
    script = DEPLOY_SCRIPT.read_text()
    assert "iotconnect_release_lib.sh" in script, "the script sources the shared lib"
    validate_at = script.index('validate_db_password "$PW"')
    write_at = script.index('> "$DIR/.env"')
    assert validate_at < write_at, "validate before writing .env"
    assert 'printf "IOTCONNECT_DB_PASSWORD=\'%s\'' in script, \
        "single-quoted values so Compose never interpolates $"
    assert "umask 077" in script
    assert "--env-file $DIR/.env" in script, "explicit env-file, not cwd-dependent lookup"
    assert '"$PW"' in script and "echo $PW" not in script and "echo \"$PW\"" not in script


def test_password_generation_rule_is_documented():
    rule = "openssl rand -base64 48 | tr -d '/+=' | cut -c1-40"
    for path in (DEPLOY_SCRIPT, DEPLOY_LIB, REPO / "docs" / "specs" / SPEC):
        text = path.read_text()
        assert rule in text, path.name
        assert ": @ / ? # %" in text or "`:` `@` `/` `?` `#` `%`" in text, path.name


# ── read-only hosted-mode smoke on EC2 ──────────────────────────────────────

def test_hosted_smoke_uses_the_header_names_the_promoted_app_expects():
    lib = DEPLOY_LIB.read_text()
    deps = (REPO / "prototype-lab" / "projects" / "project-iot-connect"
            / "app" / "dependencies.py").read_text()
    for header in ("X-Minimoi-User-Tier", "X-Minimoi-Username", "X-Minimoi-Auth-Id"):
        assert f'alias="{header}"' in deps, header
        assert header in lib, header
    assert "X-Demo-Role" not in lib, "hosted mode never sends the demo headers"
    # owner reaches data, no identity and a non-owner tier are denied
    assert "X-Minimoi-User-Tier: owner" in lib
    assert "X-Minimoi-User-Tier: guest" in lib
    assert "/api/v1/accounts" in lib and "ACCT-000100" in lib
    assert "/api/v1/admin/activation-batches" in lib
    assert '"servers":' in lib and '"url":"/app/iotconnect"' in lib
    assert lib.count("403") >= 2


def test_hosted_smoke_runs_after_health_and_before_done():
    script = DEPLOY_SCRIPT.read_text()
    health_at = script.index("== wait for health")
    smoke_at = script.index("hosted_smoke http://127.0.0.1:8095")
    done_at = script.index("== done:")
    assert health_at < smoke_at < done_at
    # non-zero exit propagates: the script runs under `set -e`
    assert "set -euo pipefail" in script


def test_hosted_smoke_is_read_only():
    """Every request the smoke makes is a GET; it never resets or mutates data."""
    lib = DEPLOY_LIB.read_text()
    body = lib[lib.index("hosted_smoke()"):]
    assert "-X POST" not in body and "--data" not in body and "-d " not in body
    assert "/demo/reset" not in body


def test_workflow_syncs_the_shared_lib_next_to_the_deploy_script():
    wf = WORKFLOW.read_text()
    assert "scripts/operations/lib/iotconnect_release_lib.sh" in wf
    assert "/opt/minimoi/iotconnect/lib/iotconnect_release_lib.sh" in wf
    assert "mkdir -p /opt/minimoi/iotconnect/lib" in wf
