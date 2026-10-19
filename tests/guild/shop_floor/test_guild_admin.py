"""Owner-only Guild Admin: visible drafts, retained Workshop, no adoption claim."""
from floor_helpers import load_portal  # noqa: F401


def test_admin_is_owner_only(load_portal):
    portal = load_portal(next_flag="1")
    path = "/guild-next/guild/admin"
    assert portal.client().get(path).status_code == 302
    assert portal.guest().get(path).status_code == 302
    assert portal.owner().get(path).status_code == 200


def test_admin_links_three_canonical_drafts_and_retains_workshop(load_portal):
    portal = load_portal(next_flag="1")
    html = portal.owner().get("/guild-next/guild/admin").get_data(as_text=True)
    assert 'data-role-adoption' in html
    assert 'Starting role approved.' in html
    for name in ("MASTER_CRAFTSMAN_JOB_DESCRIPTION.md", "MASTER_CRAFTSMAN_PERSONA.md",
                 "MASTER_CRAFTSMAN_WORKING_AGREEMENT_2026-10-12.md"):
        assert name in html
        response = portal.owner().get("/guild-next/guild/docs/read", query_string={
            "ref": "planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/" + name})
        assert response.status_code == 200
        document = response.get_data(as_text=True)
        if name == 'MASTER_CRAFTSMAN_PERSONA.md':
            assert 'Version 1.1' in document
            assert 'Runtime adoption remains pending' in document
        else:
            assert 'Draft' in document
    assert '<h1 id="admin-title" class="page-title">Guild Admin</h1>' in html
    assert 'href="/guild-next/guild/admin" aria-current="true">Admin</a>' in html
    assert 'guild-subnav guild-sections' not in html
    assert 'data-section="workshop"' not in html
    assert 'Workshop Log materials' in html
    assert portal.owner().get('/guild-next/guild/workshop').status_code in (200,302)


def test_missing_role_document_is_truthful(load_portal, monkeypatch):
    from minimoi_portal.guild_ui import references
    monkeypatch.setattr(references, "resolve", lambda _: None)
    portal = load_portal(next_flag="1")
    html = portal.owner().get("/guild-next/guild/admin").get_data(as_text=True)
    assert html.count('unavailable in this deployment') == 3
    assert 'MASTER_CRAFTSMAN_JOB_DESCRIPTION.md' not in html
