"""
tests/test_german_archiv_banner.py — German dev-drift fix (2026-08-02):
the Archive page still showed a leftover "Im Aufbau · v1.1" construction
banner even though the feature has been live and in daily use for months.
Only that banner line was removed; the actual Archive content (title,
description, section tabs) must remain intact.
"""


def test_archiv_has_no_construction_banner(german_client):
    resp = german_client.get("/archiv")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "Im Aufbau" not in page
    assert "v1.1" not in page
    assert "tab-in-progress-banner" not in page


def test_archiv_still_renders_real_content_and_tabs(german_client):
    resp = german_client.get("/archiv")
    page = resp.get_data(as_text=True)
    assert '<h2 class="archiv-title">Archiv</h2>' in page
    assert 'data-section="gesprache"' in page
    assert 'data-section="schreiben"' in page
    assert 'data-section="artikel"' in page
