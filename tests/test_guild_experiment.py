"""Guild Experiment workspace — read-only slice (Spec #155, chunk 1).

Covers §9 items 1-5 and 8, plus the §7 config-validation half of item 7.
Runtime join, surface actions, and the diagnostic page are chunk 2.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

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
        "domains": ["guild", "connecthq"],
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
    assert 'data-domains="guild,connecthq"' in html


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
    monkeypatch.setattr(portal_app._cfg, "CONNECTHQ_RELEASE_LABEL", "revision 4 candidate")
    html = _get(portal_client, projection_file, _projection([_row()]))
    assert "revision 4 candidate" in html
    assert "connecthq-v0.9.0-beta.1" not in html


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
    monkeypatch.setenv("CONNECTHQ_SURFACE_BASE_URL", value)
    return importlib.reload(cfg)


@pytest.fixture(autouse=True)
def _restore_config():
    """Any reload in this module must leave the imported module in its default state."""
    yield
    import minimoi_portal.config as cfg
    importlib.reload(cfg)


def test_defaults_are_the_aws_values():
    import minimoi_portal.config as cfg
    assert cfg.CONNECTHQ_SURFACE_BASE_URL == "/app/connecthq"
    assert cfg.CONNECTHQ_HEALTH_URL == f"{cfg.CONNECTHQ_BACKEND}/api/v1/health"
    assert cfg.CONNECTHQ_RELEASE_LABEL == "revision 4 candidate"
    assert cfg.GUILD_EXPERIMENT_PROJECTION.endswith("data/guild/experiment_projection.json")


def test_surface_paths_are_fixed():
    import minimoi_portal.config as cfg
    assert cfg.CONNECTHQ_SURFACE_PATHS == {
        "launch": "/", "admin": "/admin",
        "billing_workbench": "/workbench", "swagger": "/docs",
    }


@pytest.mark.parametrize("value", ["/app/connecthq", "http://127.0.0.1:8095",
                                   "https://localhost:8095", "http://localhost"])
def test_loopback_origins_and_portal_paths_are_accepted(monkeypatch, value):
    cfg = _reload_config(monkeypatch, value)
    assert cfg.CONNECTHQ_SURFACE_BASE_URL == value.rstrip("/") or value == "/"


@pytest.mark.parametrize("value", ["http://evil.example", "https://minimoi.ai/app/connecthq",
                                   "//evil.example", "ftp://127.0.0.1", "http://127.0.0.1.evil.com"])
def test_non_loopback_base_urls_are_rejected_at_import(monkeypatch, value):
    with pytest.raises(RuntimeError, match="CONNECTHQ_SURFACE_BASE_URL"):
        _reload_config(monkeypatch, value)
