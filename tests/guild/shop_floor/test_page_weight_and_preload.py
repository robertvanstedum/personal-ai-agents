"""Opening a page over the tunnel must be quick (7 Oct 2026 review: a new conversation took 15 to 25 s to become usable).
Two guards: every script module is announced in the page head so the browser fetches them at once, and no image the pages
reference is heavy (a 3 MB banner PNG was one of the causes). Synthetic; no model, no network."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import attach, floor_db, floored  # noqa: F401  (pytest fixtures)

REPO = Path(__file__).resolve().parents[3]
STATIC = REPO / "minimoi_portal" / "static" / "guild"
JS = REPO / "minimoi_portal" / "guild_ui" / "static" / "js"
BASE = "/guild-next"
MAX_IMAGE_BYTES = 600 * 1024


@pytest.fixture
def v11(load_portal, floor_db):
    portal = load_portal(reserve_pages=False)
    attach(portal, floor_db.store())
    return portal


def test_every_script_module_is_preloaded_in_the_head_before_main_runs(v11):
    page = v11.owner().get(f"{BASE}/guild/build").get_data(as_text=True)
    head = page.split("</head>")[0]
    preloaded = re.findall(r'<link rel="modulepreload" href="([^"]+)"', head)
    from minimoi_portal.guild_ui import asset_version
    expected = sorted(f"{BASE}/guild/ui-assets/_v/{asset_version()}/js/{p.name}" for p in JS.glob("*.js") if p.name != "bench.js")
    assert sorted(preloaded) == expected and len(expected) >= 25
    assert head.index('rel="modulepreload"') < head.index('<script type="module"')
    assert "bench.js" not in head                                                    # the retired Workbench script is not announced


@pytest.mark.parametrize("path", ["/guild/build", "/guild/operate", "/guild/board", "/guild/build/log", "/guild/workshop"])
def test_every_guild_page_announces_the_scripts_it_will_need(v11, path):
    head = v11.owner().get(f"{BASE}{path}").get_data(as_text=True).split("</head>")[0]
    assert head.count('rel="modulepreload"') >= 25, path


def test_no_image_a_page_uses_is_heavy():
    used = set()
    for base in (REPO / "minimoi_portal" / "guild_ui", REPO / "minimoi_portal" / "templates"):
        for p in base.rglob("*"):
            if p.suffix in (".html", ".js", ".css", ".py"):
                used.update(re.findall(r"/static/guild/([\w.\-]+\.(?:png|jpg|jpeg|webp|gif))", p.read_text(errors="ignore")))
    assert used, "no image references found"
    heavy = {n: (STATIC / n).stat().st_size for n in used if (STATIC / n).exists() and (STATIC / n).stat().st_size > MAX_IMAGE_BYTES}
    assert not heavy, f"images over {MAX_IMAGE_BYTES // 1024} KB: {heavy}"
    missing = [n for n in used if not (STATIC / n).exists()]
    assert not missing, missing


def test_page_assets_are_addressed_by_a_fingerprint_and_the_browser_may_keep_them_for_good(v11):
    """Every page used to re-fetch about 30 scripts on every visit (Cache-Control: no-store); over the tunnel that was ~2 s."""
    from minimoi_portal.guild_ui import asset_version
    ver = asset_version()
    owner = v11.owner()
    page = owner.get(f"{BASE}/guild/build").get_data(as_text=True)
    assert f'href="{BASE}/guild/ui-assets/_v/{ver}/components.css"' in page and f'src="{BASE}/guild/ui-assets/_v/{ver}/js/main.js"' in page
    kept = owner.get(f"{BASE}/guild/ui-assets/_v/{ver}/js/dom.js")
    assert kept.status_code == 200 and kept.headers["Cache-Control"] == "private, max-age=31536000, immutable"
    assert b"export" in kept.data
    for stale in ("0" * 12, "deadbeef0000"):                                    # an address from an older deploy: fresh, never stored
        got = owner.get(f"{BASE}/guild/ui-assets/_v/{stale}/js/dom.js")
        assert got.status_code == 200 and got.headers["Cache-Control"] == "no-store"
    plain = owner.get(f"{BASE}/guild/ui-assets/js/dom.js")                        # the old address still works and always revalidates
    assert plain.status_code == 200 and plain.headers["Cache-Control"] == "no-store"
    missing = owner.get(f"{BASE}/guild/ui-assets/_v/{ver}/js/nope.js")                # a missing file is never stored
    assert missing.status_code == 404 and "immutable" not in missing.headers["Cache-Control"]
    assert owner.get(f"{BASE}/guild/ui-assets/_v/{ver}/../../../app.py").status_code in (400, 404)


def test_a_versioned_asset_is_still_owner_only(v11):
    from minimoi_portal.guild_ui import asset_version
    got = v11.client().get(f"{BASE}/guild/ui-assets/_v/{asset_version()}/js/dom.js")
    assert got.status_code in (302, 401, 403, 404) and "immutable" not in got.headers.get("Cache-Control", "")


def test_the_fingerprint_changes_when_a_file_changes(tmp_path, monkeypatch):
    import minimoi_portal.guild_ui as g
    (tmp_path / "a.js").write_text("one")
    monkeypatch.setattr(g, "STATIC", tmp_path)
    monkeypatch.setattr(g, "_asset_version", None)
    first = g.asset_version()
    monkeypatch.setattr(g, "_asset_version", None)
    (tmp_path / "a.js").write_text("two")
    assert g.asset_version() != first and len(first) == 12
