"""Guild Experiment workspace — read-only slice, G1 (Spec #155).

Covers §9 items 1-9: access, landing and subnav, projection rendering and
degradation, the runtime join (health probe, four surfaces, diagnostic page),
surface safety, stage truthfulness, and the guarantee that the rest of Guild
is untouched.
"""
from __future__ import annotations

import importlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

OWNER = {"username": "owner", "tier": "owner", "display_name": "Robert", "auth_id": 1}
ADMIN = {"username": "admin", "tier": "admin", "display_name": "Admin", "auth_id": 2}
GUEST = {"username": "guest_ab12cd34", "tier": "guest", "display_name": "Guest"}


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


def _projection(rows, **extra):
    payload = {
        "generated": True,
        "source": "planning-studio",
        "generated_at": "2026-09-03T00:00:00Z",
        "placeholder_ids": ["INIT-2026-0004"],
        "rows": rows,
    }
    payload.update(extra)
    return payload


def _row(**overrides):
    row = {
        "initiative_id": "INIT-2026-0004",
        "title": "IoT Connect",
        "summary": "Enterprise IoT connectivity management.",
        "experiment_stage": "built",
        "scope": "reference demo",
        "updated_at": "2026-09-03",
        "next_step": "Promote, tag, and host after Spec #154 gates",
        "domains": ["guild", "iotconnect"],
        "planning_url": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def projection_file(tmp_path, monkeypatch):
    """Point the loader at a temporary projection; returns a writer callable."""
    import minimoi_portal.app as portal_app

    path = tmp_path / "experiment_projection.json"

    def write(payload):
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    monkeypatch.setattr(portal_app._cfg, "GUILD_EXPERIMENT_PROJECTION", str(path))
    return write


def _get(client, projection_file, payload):
    if payload is not None:
        projection_file(payload)
    _login(client, OWNER)
    r = client.get("/guild/experiment")
    assert r.status_code == 200
    return r.data.decode()


# ── §9.1 access ──────────────────────────────────────────────────────────────

def test_signed_out_is_redirected_to_login(portal_client):
    _login(portal_client, None)
    r = portal_client.get("/guild/experiment")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


@pytest.mark.parametrize("user", [GUEST, ADMIN], ids=["guest", "admin"])
def test_non_owner_is_denied(portal_client, user):
    _login(portal_client, user)
    r = portal_client.get("/guild/experiment")
    assert r.status_code == 302 and "owner_required" in r.headers["Location"]


@pytest.mark.parametrize("path", ["/guild/experiment", "/guild/experiment/"])
def test_owner_gets_the_page(portal_client, path):
    _login(portal_client, OWNER)
    r = portal_client.get(path)
    assert r.status_code == 200
    assert "Guild · Experiment" in r.data.decode()


def test_page_is_titled_prototype_lab_with_a_one_line_intro(portal_client):
    """Visual review, 2026-09-03: shorter page, Prototype Lab name."""
    _login(portal_client, OWNER)
    html = portal_client.get("/guild/experiment").data.decode()
    assert "<title>Prototype Lab — Guild</title>" in html
    assert '<h1 class="lab-title">Prototype Lab</h1>' in html
    assert '<p class="lab-lede">A place to experiment.</p>' in html
    assert "Efforts of any size" not in html
    assert "Guild · Experiment" in html


# ── §9.2 landing ─────────────────────────────────────────────────────────────

def test_landing_has_four_cards_and_experiment_links_to_the_page(portal_client):
    _login(portal_client, OWNER)
    html = portal_client.get("/guild").data.decode()
    assert html.count('class="guild-card"') == 4
    assert 'href="/guild/experiment" class="guild-card"' in html
    assert "Ideas · Tinkering · Reference demos" in html
    assert "/static/guild/guild-experiment.jpg" in html
    for href in ('href="/guild/build/queue"', 'href="/guild/operate"', 'href="/guild/improve"'):
        assert href in html, href


# ── §9.3 subnav ──────────────────────────────────────────────────────────────

def test_subnav_gains_experiment_and_keeps_the_others(portal_client):
    _login(portal_client, OWNER)
    html = portal_client.get("/guild/experiment").data.decode()
    for label in ("Build", "Operate", "Improve", "Experiment"):
        assert f">{label}</a>" in html, label
    assert 'href="/guild/experiment" class="subnav-link subnav-active"' in html

    build = portal_client.get("/guild/build/queue").data.decode()
    assert 'href="/guild/build/queue" class="subnav-link subnav-active"' in build
    assert 'href="/guild/experiment" class="subnav-link"' in build


# ── §9.4 projection rendering, ordering, staleness, placeholders ─────────────

def test_shipped_projection_renders_both_rows_with_all_fields(portal_client):
    _login(portal_client, OWNER)
    html = portal_client.get("/guild/experiment").data.decode()
    assert html.count('data-initiative="') == 2
    for fragment in ("INIT-2026-0004", "IoT Connect", "reference demo", "built",
                     "2026-09-03", "Promote, tag, and host after Spec #154 gates",
                     "INIT-2026-0005", "Fiber order-to-cash demonstration", "idea",
                     "G3/G3A after standalone beta acceptance"):
        assert fragment in html, fragment
    assert 'data-scope="reference demo"' in html
    assert 'data-domains="guild,iotconnect"' in html


def test_rows_are_ordered_by_updated_at_descending(portal_client, projection_file):
    html = _get(portal_client, projection_file, _projection([
        _row(initiative_id="INIT-A", title="Older", updated_at="2026-08-01"),
        _row(initiative_id="INIT-B", title="Newer", updated_at="2026-09-01"),
    ]))
    assert html.index("INIT-B") < html.index("INIT-A")


def test_stale_indicator_after_thirty_days(portal_client, projection_file):
    fresh = datetime.now(timezone.utc).date().isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=45)).date().isoformat()
    html = _get(portal_client, projection_file, _projection([
        _row(initiative_id="INIT-OLD", title="Dormant", updated_at=old),
        _row(initiative_id="INIT-NEW", title="Active", updated_at=fresh),
    ]))
    assert html.count('class="xp-chip stale"') == 1
    assert html.index("INIT-NEW") < html.index("INIT-OLD")


def test_placeholder_ids_are_visibly_marked(portal_client, projection_file):
    html = _get(portal_client, projection_file, _projection([
        _row(),
        _row(initiative_id="INIT-2026-9999", title="Real record"),
    ]))
    assert html.count(">placeholder<") == 1
    marked = html[html.index("INIT-2026-0004"):html.index("INIT-2026-9999")]
    assert ">placeholder<" in marked


def test_release_label_comes_from_configuration(portal_client, projection_file, monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_RELEASE_LABEL", "revision 7 candidate")
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert "revision 7 candidate" in html
    assert "iotconnect-v0.9.0-beta.1" not in html


def test_url_like_fields_in_the_projection_are_not_rendered(portal_client, projection_file):
    """Surfaces come only from configuration (spec §6); the file cannot inject one."""
    html = _get(portal_client, projection_file, _projection([
        _row(launch_url="http://evil.example/pwn", health_url="http://evil.example/health"),
    ]))
    assert "evil.example" not in html


# ── §9.8 stage truthfulness ─────────────────────────────────────────────────

def test_built_row_sits_in_the_matrix_never_in_operational(portal_client, projection_file):
    html = _get(portal_client, projection_file, _projection([_row(experiment_stage="built")]))
    assert "No hosted demo yet. The local candidate is in the working matrix." in html
    operational_block = html[html.index("Operational"):html.index("Working matrix")]
    assert "INIT-2026-0004" not in operational_block
    assert "INIT-2026-0004" in html[html.index("Working matrix"):]


def test_only_operational_rows_render_in_the_operational_section(portal_client, projection_file):
    html = _get(portal_client, projection_file, _projection([
        _row(initiative_id="INIT-OPS", title="Hosted demo", experiment_stage="operational"),
        _row(initiative_id="INIT-BUILT", title="Local candidate", experiment_stage="built"),
    ]))
    operational_block = html[html.index("Operational"):html.index("Working matrix")]
    assert "INIT-OPS" in operational_block and "INIT-BUILT" not in operational_block
    assert "No hosted demo yet" not in html


# ── §9.5 degradation ────────────────────────────────────────────────────────

def test_malformed_row_is_skipped_with_a_warning(portal_client, projection_file):
    html = _get(portal_client, projection_file, _projection([
        _row(),
        {"initiative_id": "INIT-BAD", "title": "No stage"},
        _row(initiative_id="INIT-BADSTAGE", title="Bad stage", experiment_stage="shipped"),
    ]))
    assert html.count('data-initiative="') == 1
    assert "were skipped" in html
    assert "INIT-BAD" not in html.replace("INIT-BADSTAGE", "")
    assert "unknown stage" in html


def test_missing_file_renders_unavailable_with_200(portal_client, monkeypatch, tmp_path):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "GUILD_EXPERIMENT_PROJECTION", str(tmp_path / "gone.json"))
    _login(portal_client, OWNER)
    r = portal_client.get("/guild/experiment")
    assert r.status_code == 200
    assert "Working register unavailable." in r.data.decode()


def test_invalid_file_still_renders_and_keeps_generated_at(portal_client, projection_file):
    html = _get(portal_client, projection_file,
                {"generated": True, "generated_at": "2026-09-01T10:00:00Z", "rows": "not-a-list"})
    assert "Working register unavailable." in html
    assert "2026-09-01T10:00:00Z" in html


def test_unparseable_json_renders_unavailable(portal_client, projection_file):
    projection_file("{ not json")
    _login(portal_client, OWNER)
    r = portal_client.get("/guild/experiment")
    assert r.status_code == 200 and "Working register unavailable." in r.data.decode()


# ── §9.7 (config half): base URL validation at import ───────────────────────

def _reload_config(monkeypatch, value):
    import minimoi_portal.config as cfg
    monkeypatch.setenv("IOTCONNECT_SURFACE_BASE_URL", value)
    return importlib.reload(cfg)


@pytest.fixture(autouse=True)
def _restore_config():
    """Any reload in this module must leave the imported module in its default state.

    The surface variables are cleared first: an autouse fixture can outlive
    monkeypatch's env teardown, and reloading with a rejected base URL still
    set would raise here instead of in the test that set it.
    """
    yield
    import minimoi_portal.config as cfg
    for var in ("IOTCONNECT_SURFACE_BASE_URL", "IOTCONNECT_HEALTH_URL"):
        os.environ.pop(var, None)
    importlib.reload(cfg)


def test_defaults_are_the_aws_values():
    import minimoi_portal.config as cfg
    assert cfg.IOTCONNECT_SURFACE_BASE_URL == "/app/iotconnect"
    assert cfg.IOTCONNECT_HEALTH_URL == f"{cfg.IOTCONNECT_BACKEND}/api/v1/health"
    assert cfg.IOTCONNECT_RELEASE_LABEL == "revision 7 candidate"
    assert cfg.GUILD_EXPERIMENT_PROJECTION.endswith("data/guild/experiment_projection.json")


def test_surface_paths_are_fixed():
    import minimoi_portal.config as cfg
    assert cfg.IOTCONNECT_SURFACE_PATHS == {
        "launch": "/", "admin": "/admin",
        "billing_workbench": "/workbench", "swagger": "/docs",
    }


@pytest.mark.parametrize("value,expected", [
    ("/app/iotconnect", "/app/iotconnect"),
    ("/app/iotconnect/", "/app/iotconnect"),
    ("http://127.0.0.1:8095", "http://127.0.0.1:8095"),
    ("http://127.0.0.1:8095/", "http://127.0.0.1:8095"),
    ("https://localhost", "https://localhost"),
    ("http://localhost:5001", "http://localhost:5001"),
    ("https://localhost:8095", "https://localhost:8095"),
])
def test_hosted_path_and_loopback_origins_are_accepted(value, expected):
    from minimoi_portal.config import _validate_surface_base_url
    assert _validate_surface_base_url(value) == expected


@pytest.mark.parametrize("value", [
    # not the one reviewed hosted path
    "/other", "/app", "/app/iotconnect/admin", "/",
    # traversal, query, fragment on the hosted path
    "/app/iotconnect/../admin", "/app/iotconnect?x=1", "/app/iotconnect#fragment",
    "/guild/../admin?next=https://evil.example#x",
    # scheme-relative and non-loopback origins
    "//evil.example/app/iotconnect", "//evil.example",
    "http://evil.example", "https://minimoi.ai/app/iotconnect",
    "http://example.com:8095", "http://127.0.0.1.evil.com", "ftp://127.0.0.1",
    # credentials, invalid ports, and anything after the origin
    "http://user:pw@127.0.0.1:8095", "http://127.0.0.1:0",
    "http://127.0.0.1:65536", "http://127.0.0.1:abc",
    "http://127.0.0.1:8095/path", "http://127.0.0.1:8095?x=1",
    "http://127.0.0.1:8095#x", "",
])
def test_everything_outside_the_allow_list_is_rejected(value):
    from minimoi_portal.config import _validate_surface_base_url
    with pytest.raises(RuntimeError, match="IOTCONNECT_SURFACE_BASE_URL"):
        _validate_surface_base_url(value)


def test_hosted_portal_path_is_a_single_named_constant():
    """The hosted path is one named constant, not a scattered literal."""
    import minimoi_portal.config as cfg
    assert cfg.HOSTED_PORTAL_PATH == "/app/iotconnect"
    assert cfg.IOTCONNECT_SURFACE_BASE_URL == cfg.HOSTED_PORTAL_PATH


def test_a_rejected_base_url_fails_at_import(monkeypatch):
    """The allow-list is enforced at module import, not only when called."""
    with pytest.raises(RuntimeError, match="IOTCONNECT_SURFACE_BASE_URL"):
        _reload_config(monkeypatch, "/guild/../admin?next=https://evil.example#x")


# ── §9.6 runtime join: health probe, surfaces, diagnostic page ──────────────

class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _health_probe(monkeypatch):
    """Deterministic probe: unhealthy by default, cache cleared around each test.

    Tests that need a healthy demo call `probe.healthy()`. Nothing here reaches
    the network, and the module-level cache never leaks between tests.
    """
    import minimoi_portal.app as portal_app

    class Probe:
        def __init__(self):
            self.calls = []
            self._result = ConnectionError("connection refused")

        def __call__(self, url, timeout=None, **kwargs):
            self.calls.append({"url": url, "timeout": timeout})
            if isinstance(self._result, Exception):
                raise self._result
            return self._result

        def healthy(self):
            self._result = _Response(200)
            return self

        def status(self, code):
            self._result = _Response(code)
            return self

        def raises(self, exc):
            self._result = exc
            return self

    probe = Probe()
    portal_app._experiment_health_cache.update({"checked": 0.0, "result": None})
    monkeypatch.setattr(portal_app._requests, "get", probe)
    yield probe
    portal_app._experiment_health_cache.update({"checked": 0.0, "result": None})


@pytest.fixture
def loopback_surfaces(monkeypatch):
    """Point the surface configuration at the standalone Mac demo."""
    import minimoi_portal.app as portal_app

    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_SURFACE_BASE_URL", "http://127.0.0.1:8095")
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_HEALTH_URL", "http://127.0.0.1:8095/api/v1/health")
    return "http://127.0.0.1:8095"


def _hrefs(html):
    import re
    return re.findall(r'<a class="xp-action[^"]*"\s+href="([^"]+)"', html)


def test_healthy_probe_builds_all_four_surfaces_from_configuration(
        portal_client, projection_file, _health_probe, loopback_surfaces):
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert "Launch demo" in html and "Demo unavailable" not in html
    for label in ("Admin workbench", "Billing workbench", "Swagger"):
        assert label in html, label
    hrefs = _hrefs(html)
    assert hrefs == [f"{loopback_surfaces}/", f"{loopback_surfaces}/admin",
                     f"{loopback_surfaces}/workbench", f"{loopback_surfaces}/docs"]
    assert "/guild/experiment/unavailable/" not in html
    assert 'class="xp-runtime up"' in html and ">available" in html


def test_healthy_loopback_surfaces_open_in_a_new_tab_safely(
        portal_client, projection_file, _health_probe, loopback_surfaces):
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert html.count('target="_blank" rel="noopener"') == 4


def test_relative_base_url_produces_relative_hrefs_in_the_same_tab(
        portal_client, projection_file, _health_probe):
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert _hrefs(html) == ["/app/iotconnect/", "/app/iotconnect/admin",
                            "/app/iotconnect/workbench", "/app/iotconnect/docs"]
    assert 'target="_blank"' not in html


def test_unhealthy_sends_every_action_to_the_diagnostic_route(
        portal_client, projection_file, _health_probe, loopback_surfaces):
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert "Demo unavailable" in html and "Launch demo" not in html
    diagnostic = "/guild/experiment/unavailable/INIT-2026-0004"
    assert _hrefs(html) == [diagnostic] * 4
    assert loopback_surfaces not in html
    assert 'class="xp-runtime down"' in html and ">unavailable" in html


def test_unhealthy_action_group_is_still_fully_visible(
        portal_client, projection_file, _health_probe):
    html = _get(portal_client, projection_file, _projection([_row()]))
    for label in ("Demo unavailable", "Admin workbench", "Billing workbench", "Swagger"):
        assert label in html, label


def test_diagnostic_page_returns_503_with_the_failed_check(
        portal_client, projection_file, _health_probe, loopback_surfaces):
    _health_probe.raises(ConnectionError("connection refused"))
    projection_file(_projection([_row()]))
    _login(portal_client, OWNER)
    r = portal_client.get("/guild/experiment/unavailable/INIT-2026-0004")
    assert r.status_code == 503
    html = r.data.decode()
    assert "IoT Connect is not running." in html
    assert "http://127.0.0.1:8095/api/v1/health" in html
    assert "connection refused" in html
    assert "href=\"/guild/experiment\"" in html
    import minimoi_portal.app as portal_app
    assert portal_app._experiment_health_cache["result"]["checked_at"] in html


def test_diagnostic_page_reports_a_non_200_health_response(
        portal_client, projection_file, _health_probe):
    _health_probe.status(502)
    projection_file(_projection([_row()]))
    _login(portal_client, OWNER)
    html = portal_client.get("/guild/experiment/unavailable/INIT-2026-0004").data.decode()
    assert "HTTP 502" in html


def test_diagnostic_page_404s_for_an_unknown_initiative(portal_client, projection_file):
    projection_file(_projection([_row()]))
    _login(portal_client, OWNER)
    r = portal_client.get("/guild/experiment/unavailable/INIT-NOPE")
    assert r.status_code == 404
    assert "Unknown experiment." in r.data.decode()


def test_diagnostic_page_is_owner_only(portal_client, projection_file):
    projection_file(_projection([_row()]))
    _login(portal_client, GUEST)
    r = portal_client.get("/guild/experiment/unavailable/INIT-2026-0004")
    assert r.status_code == 302 and "owner_required" in r.headers["Location"]


def test_probe_timeout_is_respected_and_the_page_still_renders(
        portal_client, projection_file, _health_probe):
    import requests as real_requests

    def slow(url, timeout=None, **kwargs):
        _health_probe.calls.append({"url": url, "timeout": timeout})
        raise real_requests.exceptions.ReadTimeout("timed out")

    import minimoi_portal.app as portal_app
    portal_app._requests.get = slow  # monkeypatch fixture restores the original
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert _health_probe.calls[0]["timeout"] == 1.5
    assert "Demo unavailable" in html
    assert 'class="xp-runtime down"' in html


def test_probe_result_is_cached_for_thirty_seconds(
        portal_client, projection_file, _health_probe, monkeypatch):
    import minimoi_portal.app as portal_app

    clock = {"now": 1000.0}
    monkeypatch.setattr(portal_app.time, "time", lambda: clock["now"])
    _health_probe.healthy()
    projection_file(_projection([_row()]))
    _login(portal_client, OWNER)

    portal_client.get("/guild/experiment")
    clock["now"] += 10
    portal_client.get("/guild/experiment")
    assert len(_health_probe.calls) == 1

    clock["now"] += 25  # past the 30 s window
    portal_client.get("/guild/experiment")
    assert len(_health_probe.calls) == 2


def test_runtime_join_is_by_initiative_id_not_domain_tag(
        portal_client, projection_file, _health_probe, monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_INITIATIVE_ID", "INIT-JOINED")
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([
        _row(initiative_id="INIT-JOINED", title="Joined", domains=["guild"]),
        _row(initiative_id="INIT-OTHER", title="Not joined", domains=["guild", "iotconnect"]),
    ]))
    joined = html[html.index("INIT-JOINED"):html.index("INIT-OTHER")]
    other = html[html.index("INIT-OTHER"):]
    assert "Launch demo" in joined and "revision 7 candidate" in joined
    assert "Launch demo" not in other and "revision 7 candidate" not in other


def test_operational_rows_use_the_same_action_markup(
        portal_client, projection_file, _health_probe, loopback_surfaces, monkeypatch):
    """The joined initiative, once operational, needs no separate markup."""
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_INITIATIVE_ID", "INIT-OPS")
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([
        _row(initiative_id="INIT-OPS", title="Hosted demo", experiment_stage="operational"),
    ]))
    operational_block = html[html.index("Operational"):html.index("Working matrix")]
    assert "Launch demo" in operational_block
    assert f"{loopback_surfaces}/workbench" in operational_block


def test_unrelated_operational_row_is_not_joined_to_iot_connect(
        portal_client, projection_file, _health_probe, loopback_surfaces, monkeypatch):
    """Stage never joins: only the configured initiative gets the runtime.

    An unrelated prototype promoted to Operational must not inherit the IoT
    Connect release label, health result, or any of the four surface links.
    """
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_INITIATIVE_ID", "INIT-JOINED")
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([
        _row(initiative_id="INIT-JOINED", title="Joined demo",
             experiment_stage="operational"),
        _row(initiative_id="INIT-OTHER", title="Unrelated promotion",
             experiment_stage="operational", domains=["guild", "iotconnect"]),
    ]))
    joined = html[html.index("INIT-JOINED"):html.index("INIT-OTHER")]
    other = html[html.index("INIT-OTHER"):html.index("Working matrix")]

    # The unrelated operational row carries no runtime at all.
    assert "revision 7 candidate" not in other
    assert 'class="xp-runtime' not in other and "checked" not in other
    for label in ("Launch demo", "Admin workbench", "Billing workbench", "Swagger"):
        assert label not in other
    assert loopback_surfaces not in other
    assert "/guild/experiment/unavailable" not in other
    assert "&mdash;" in other or "—" in other

    # ...and the configured initiative still does.
    assert "revision 7 candidate" in joined
    assert "Launch demo" in joined and f"{loopback_surfaces}/workbench" in joined


def test_join_is_stage_independent_at_the_predicate(monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_INITIATIVE_ID", "INIT-IOTCONNECT")
    assert portal_app._experiment_is_joined(
        {"initiative_id": "INIT-IOTCONNECT", "experiment_stage": "built"}) is True
    assert portal_app._experiment_is_joined(
        {"initiative_id": "INIT-OTHER", "experiment_stage": "operational"}) is False


# ── §9.7 safety: surfaces come only from configuration ──────────────────────

def test_projection_cannot_override_the_surfaces(
        portal_client, projection_file, _health_probe, loopback_surfaces):
    _health_probe.healthy()
    html = _get(portal_client, projection_file, _projection([
        _row(launch_url="http://evil.example/pwn",
             surfaces={"launch": "http://evil.example/", "admin": "http://evil.example/admin"},
             admin_url="http://evil.example/admin", health_url="http://evil.example/health"),
    ]))
    assert "evil.example" not in html
    assert _hrefs(html) == [f"{loopback_surfaces}/", f"{loopback_surfaces}/admin",
                            f"{loopback_surfaces}/workbench", f"{loopback_surfaces}/docs"]


def test_surface_join_is_safe_for_every_configured_base(monkeypatch):
    import minimoi_portal.config as cfg
    for base in ("/app/iotconnect", "http://127.0.0.1:8095", "http://localhost"):
        module = _reload_config(monkeypatch, base)
        surfaces = module.iotconnect_surfaces()
        assert surfaces["launch"] == f"{base}/"
        assert surfaces["swagger"] == f"{base}/docs"
        assert "//" not in surfaces["admin"].replace("http://", "").replace("https://", "")
    importlib.reload(cfg)


# ── §4.2 health URL derivation ──────────────────────────────────────────────

def test_health_url_is_derived_from_a_loopback_base(monkeypatch):
    monkeypatch.delenv("IOTCONNECT_HEALTH_URL", raising=False)
    cfg = _reload_config(monkeypatch, "http://127.0.0.1:8095")
    assert cfg.IOTCONNECT_HEALTH_URL == "http://127.0.0.1:8095/api/v1/health"


def test_health_url_falls_back_to_the_backend_for_a_relative_base(monkeypatch):
    monkeypatch.delenv("IOTCONNECT_HEALTH_URL", raising=False)
    cfg = _reload_config(monkeypatch, "/app/iotconnect")
    assert cfg.IOTCONNECT_HEALTH_URL == f"{cfg.IOTCONNECT_BACKEND}/api/v1/health"


def test_explicit_health_url_wins(monkeypatch):
    monkeypatch.setenv("IOTCONNECT_HEALTH_URL", "http://127.0.0.1:9999/health")
    cfg = _reload_config(monkeypatch, "http://127.0.0.1:8095")
    assert cfg.IOTCONNECT_HEALTH_URL == "http://127.0.0.1:9999/health"


# ── §5.2 filters and the configured stale threshold ─────────────────────────

def _filtered(client, projection_file, payload, query):
    projection_file(payload)
    _login(client, OWNER)
    r = client.get(f"/guild/experiment?{query}")
    assert r.status_code == 200
    return r.data.decode()


@pytest.fixture
def three_rows():
    return _projection([
        _row(initiative_id="INIT-A", title="Alpha", experiment_stage="built",
             scope="reference demo", domains=["guild", "iotconnect"], updated_at="2026-09-03"),
        _row(initiative_id="INIT-B", title="Bravo", experiment_stage="idea",
             scope="integration", domains=["cos"], updated_at="2026-09-02"),
        _row(initiative_id="INIT-C", title="Charlie", experiment_stage="idea",
             scope="tool", domains=["guild"], updated_at="2026-09-01"),
    ])


@pytest.mark.parametrize("query,kept,dropped", [
    ("stage=idea", ["INIT-B", "INIT-C"], ["INIT-A"]),
    ("stage=IDEA", ["INIT-B", "INIT-C"], ["INIT-A"]),
    ("scope=reference+demo", ["INIT-A"], ["INIT-B", "INIT-C"]),
    ("domain=guild", ["INIT-A", "INIT-C"], ["INIT-B"]),
    ("stage=idea&domain=guild", ["INIT-C"], ["INIT-A", "INIT-B"]),
])
def test_matrix_filters_by_stage_scope_and_domain(
        portal_client, projection_file, three_rows, query, kept, dropped):
    html = _filtered(portal_client, projection_file, three_rows, query)
    body = html[html.index("Working matrix"):]
    for row_id in kept:
        assert f'data-initiative="{row_id}"' in body, row_id
    for row_id in dropped:
        assert f'data-initiative="{row_id}"' not in body, row_id


def test_unknown_filter_values_are_ignored(portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "stage=shipped&domain=nope")
    assert html.count('data-initiative="') == 3


def test_no_filter_chips_remain(portal_client, projection_file, three_rows):
    """Visual review, 2026-09-03: chips replaced by a spreadsheet-style table."""
    html = _filtered(portal_client, projection_file, three_rows, "stage=idea")
    assert 'class="xp-filter"' not in html
    assert 'class="xp-filter active"' not in html
    assert "data-filter" not in html


def test_every_column_is_sortable_and_has_a_filter_input(
        portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "")
    matrix = html[html.index("Working matrix"):]
    header = matrix[matrix.index("<thead"):matrix.index("</thead>")]
    assert header.count("<th data-sort=") == 8
    assert 'data-sort="date"' in header
    assert header.count('class="xp-filter-input"') == 8
    for column in range(8):
        assert f'data-column="{column}"' in header, column
    assert ">clear</a>" in header
    assert 'src="/static/guild-experiment.js"' in html


def test_table_is_marked_for_the_grid_script(portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "")
    assert '<table class="xp-table" data-grid>' in html


def test_ordering_survives_filtering(portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "stage=idea")
    assert html.index("INIT-B") < html.index("INIT-C")


# ── PR #196 review: a deep-linked filter must be visible and clearable ──────

def test_deep_linked_stage_prefills_its_column_and_offers_a_clean_clear_link(
        portal_client, projection_file, three_rows):
    """Codex, PR #196: ?stage=idea dropped rows with no visible reason."""
    html = _filtered(portal_client, projection_file, three_rows, "stage=idea")
    matrix = html[html.index("Working matrix"):]
    header = matrix[matrix.index("<thead"):matrix.index("</thead>")]
    assert 'value="idea"' in header
    assert header.count('value=""') == 7  # every other column stays empty
    assert '<a class="xp-clear" href="/guild/experiment">clear</a>' in header
    assert 'data-active-query="1"' in matrix
    assert "xp-grid-ready" in matrix  # the filter row is visible before the script runs
    assert 'data-initiative="INIT-A"' not in matrix


def test_deep_linked_scope_prefills_its_column(portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "scope=reference+demo")
    matrix = html[html.index("Working matrix"):]
    assert 'value="reference demo"' in matrix
    assert 'data-initiative="INIT-A"' in matrix
    assert 'data-initiative="INIT-B"' not in matrix


def test_deep_linked_domain_is_named_in_a_notice_with_a_show_all_link(
        portal_client, projection_file, three_rows):
    """domain has no column, so the filter is explained above the table."""
    html = _filtered(portal_client, projection_file, three_rows, "domain=guild")
    assert "Filtered by domain: guild" in html
    assert '<a href="/guild/experiment">show all</a>' in html
    matrix = html[html.index("Working matrix"):]
    assert 'data-initiative="INIT-B"' not in matrix
    for row_id in ("INIT-A", "INIT-C"):
        assert f'data-initiative="{row_id}"' in matrix, row_id


def test_a_filter_combination_that_matches_nothing_explains_itself(
        portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "scope=reference+demo&domain=cos")
    assert "No rows match the active filter." in html
    assert "Filtered by domain: cos" in html
    assert '<a class="xp-clear" href="/guild/experiment">clear</a>' in html
    assert 'data-initiative="' not in html[html.index("Working matrix"):]


def test_an_unfiltered_page_carries_no_notice_and_empty_inputs(
        portal_client, projection_file, three_rows):
    html = _filtered(portal_client, projection_file, three_rows, "")
    assert "Filtered by domain:" not in html
    assert "data-active-query" not in html
    matrix = html[html.index("Working matrix"):]
    header = matrix[matrix.index("<thead"):matrix.index("</thead>")]
    assert header.count('value=""') == 8


def test_clearing_a_deep_linked_view_reloads_the_clean_url():
    """Clearing the inputs cannot restore server-dropped rows; the script navigates."""
    script = (Path(__file__).resolve().parent.parent
              / "minimoi_portal" / "static" / "guild-experiment.js").read_text()
    assert "window.location.assign(clear.href)" in script
    assert "window.location.search" in script


def test_the_grid_script_parses():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available in this environment")
    script = (Path(__file__).resolve().parent.parent
              / "minimoi_portal" / "static" / "guild-experiment.js")
    out = subprocess.run([node, "--check", str(script)], capture_output=True, timeout=30)
    assert out.returncode == 0, out.stderr.decode()


def test_stale_threshold_comes_from_configuration(portal_client, projection_file, monkeypatch):
    import minimoi_portal.app as portal_app
    old = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    payload = _projection([_row(initiative_id="INIT-OLD", updated_at=old)])

    assert 'class="xp-chip stale"' not in _get(portal_client, projection_file, payload)

    monkeypatch.setattr(portal_app._cfg, "GUILD_EXPERIMENT_STALE_DAYS", 5)
    assert 'class="xp-chip stale"' in _get(portal_client, projection_file, payload)


# ── §9.6 property: nothing links to the backend while it is down ────────────

def _all_hrefs(html):
    return re.findall(r'href="([^"]*)"', html)


def test_no_href_reaches_the_backend_while_it_is_unhealthy(
        portal_client, projection_file, _health_probe, loopback_surfaces, three_rows):
    """The whole-page property behind the unavailable-launch contract (§5.2).

    Not just the action group: with the probe failing, *no* link anywhere on
    the workspace page or on the diagnostic page may address the configured
    base URL, so a click can never send the browser to a dead backend.
    """
    projection_file(three_rows)
    _login(portal_client, OWNER)
    pages = [portal_client.get("/guild/experiment"),
             portal_client.get("/guild/experiment?stage=built"),
             portal_client.get("/guild/experiment/unavailable/INIT-A")]
    assert [p.status_code for p in pages] == [200, 200, 503]
    for page in pages:
        for href in _all_hrefs(page.data.decode()):
            assert loopback_surfaces not in href, href
    # On the workspace itself the base URL is absent entirely, not merely
    # unlinked. The diagnostic page prints the health URL as text on purpose.
    for page in pages[:2]:
        assert loopback_surfaces not in page.data.decode()


# ── §9.2 / §9.9 the rest of Guild is untouched ──────────────────────────────

def _git_show(rev_path):
    """File content at a revision, or None when git cannot answer (CI checkout)."""
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(["git", "show", rev_path], cwd=repo, capture_output=True, timeout=15)
    except Exception:
        return None
    return out.stdout.decode() if out.returncode == 0 else None


def _card_blocks(html):
    """The three original landing cards, keyed by their HTML section comment."""
    return {name: block.strip() for name, block in
            re.findall(r"<!-- (Build|Operate|Improve) -->\n(.*?)</a>", html, re.S)}


def test_the_three_original_landing_cards_are_still_present():
    """§9.2 guard, relaxed after G1 shipped: the landing keeps its three original
    cards and their links; wording may change deliberately (PR #197 renamed the
    Improve kicker), so the byte-identical comparison against origin/main is gone."""
    current = (Path(__file__).resolve().parent.parent
               / "minimoi_portal" / "templates" / "guild" / "guild_landing.html").read_text()
    blocks = _card_blocks(current)
    assert set(blocks) == {"Build", "Operate", "Improve"}
    assert 'href="/guild/build/queue"' in blocks["Build"]
    assert 'href="/guild/operate"' in blocks["Operate"]
    assert 'href="/guild/improve"' in blocks["Improve"]


def test_improve_card_no_longer_claims_experiment():
    """Experiment belongs to the fourth card; Improve reads review → analyze."""
    current = (Path(__file__).resolve().parent.parent
               / "minimoi_portal" / "templates" / "guild" / "guild_landing.html").read_text()
    improve = _card_blocks(current)["Improve"]
    assert "Review · Analyze" in improve
    assert "review → analyze → improve" in improve
    assert "Experiment" not in improve and "experiment" not in improve


@pytest.mark.parametrize("suite", ["tests/test_guild.py", "tests/test_portal.py",
                                   "tests/test_cos_guild_visual_polish.py"])
def test_existing_suites_are_not_modified_by_this_branch(suite):
    """§9.9: these pass unchanged — so the branch must not have edited them."""
    baseline = _git_show(f"origin/main:{suite}")
    if baseline is None:
        pytest.skip("origin/main not available in this checkout")
    current = (Path(__file__).resolve().parent.parent / suite).read_text()
    assert current == baseline, f"{suite} was modified; §9.9 requires it to pass unchanged"


def test_placeholder_illustration_is_shipped():
    """§5.1: art is non-blocking, but the card must not render a broken image."""
    art = (Path(__file__).resolve().parent.parent
           / "minimoi_portal" / "static" / "guild" / "guild-experiment.jpg")
    assert art.is_file() and art.stat().st_size > 0


# ── N3: runtime stage promotion (tagged release + healthy → Operational) ─────

def _stage_of(portal_client, projection_file, _health_probe, monkeypatch,
              label, healthy=True, stage="built"):
    """Render the page with a given release label and health, return the row's section."""
    import minimoi_portal.app as portal_app
    if healthy:
        _health_probe.healthy()
    else:
        _health_probe.status(503)
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_RELEASE_LABEL", label)
    html = _get(portal_client, projection_file,
                _projection([_row(experiment_stage=stage)]))
    head = html.index("INIT-2026-0004")
    # the Operational section precedes the working matrix in the template
    operational_at = html.index("Operational")
    matrix_at = html.index("Working", operational_at)
    return "operational" if head < matrix_at else "working"


def test_tagged_release_and_healthy_demo_promotes_the_row_to_operational(
        portal_client, projection_file, _health_probe, monkeypatch):
    assert _stage_of(portal_client, projection_file, _health_probe, monkeypatch,
                     "iotconnect-v0.9.0-beta.1") == "operational"


def test_candidate_label_never_promotes_even_when_healthy(
        portal_client, projection_file, _health_probe, monkeypatch):
    assert _stage_of(portal_client, projection_file, _health_probe, monkeypatch,
                     "revision 7 candidate") == "working"


def test_tagged_release_does_not_promote_when_health_fails(
        portal_client, projection_file, _health_probe, monkeypatch):
    assert _stage_of(portal_client, projection_file, _health_probe, monkeypatch,
                     "iotconnect-v0.9.0-beta.1", healthy=False) == "working"


@pytest.mark.parametrize("label,expected", [
    ("iotconnect-v0.9.0-beta.1", True),
    ("iotconnect-v1.0.0", True),
    ("iotconnect-v0.9.0", True),
    ("revision 7 candidate", False),
    ("iotconnect-v0.9", False),
    ("connecthq-v0.9.0-beta.1", False),
    ("sha256:deadbeef", False),
    ("", False),
    (None, False),
    # Codex release review 1, P1 — the predicate is a full match, not a prefix
    # match, and the prerelease grammar is exact.
    ("iotconnect-v0.9.0evil", False),
    ("iotconnect-v0.9.0/../../x", False),
    ("iotconnect-v0.9.0-", False),
    ("iotconnect-v0.9.0-beta.1\n", False),
    ("iotconnect-v0.9.0 ", False),
    (" iotconnect-v0.9.0", False),
    ("iotconnect-v0.9.0-beta.", False),
    ("iotconnect-v0.9.0-beta_1", False),
    ("iotconnect-v0.9.0.1", False),
    ("iotconnect-v0.9.0-rc.2", True),
])
def test_release_label_tag_predicate(label, expected):
    import minimoi_portal.app as portal_app
    assert portal_app._experiment_release_is_tagged(label) is expected


def test_promotion_applies_only_to_the_joined_row(monkeypatch):
    import minimoi_portal.app as portal_app
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_INITIATIVE_ID", "INIT-JOINED")
    monkeypatch.setattr(portal_app._cfg, "IOTCONNECT_RELEASE_LABEL", "iotconnect-v0.9.0-beta.1")
    rows = [
        {"initiative_id": "INIT-JOINED", "experiment_stage": "built"},
        {"initiative_id": "INIT-OTHER", "experiment_stage": "built"},
    ]
    portal_app._experiment_join_runtime(rows, {"ok": True})
    assert rows[0]["experiment_stage"] == "operational"
    assert rows[1]["experiment_stage"] == "built", "an unjoined row is never promoted"


def test_projection_file_still_records_the_candidate_as_built():
    """Promotion is runtime-derived; the tracked projection stays truthful."""
    import json as _json
    data = _json.loads((REPO / "data" / "guild" / "experiment_projection.json")
                       .read_text(encoding="utf-8"))
    row = next(r for r in data["rows"] if r["initiative_id"] == "INIT-2026-0004")
    assert row["experiment_stage"] == "built"
