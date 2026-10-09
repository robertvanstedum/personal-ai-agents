"""Guild 1.1: retired pages stay retired, and a saved link leads somewhere supported.

The Workbench "wall", the old Build Queue page and the old Labs page are kept in reserve (git tag, and the release
exclusion script) and are NOT served in Guild 1.1: with MINIMOI_GUILD_RESERVE_PAGES off, their old addresses redirect
to the page that replaced them, by meaning. Linked work opens its Build Log item with a way back to the conversation
that asked, and no page reachable from 1.1 carries a retired label or a retired link. Synthetic data only."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401  (load_portal is a pytest fixture)
from floor_db_helpers import attach, floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
BASE = "/guild-next"
REPO = Path(__file__).resolve().parents[3]
GUILD_UI = REPO / "minimoi_portal" / "guild_ui"
# "Shop floor" is not listed: on the Chat page it is only the saved name of the owner's original conversation (data).
RETIRED_WORDS = ("Workbench", "Open wall", "Back to workbench", "Build Queue")
RETIRED_HREFS = ("/guild/build/bench", "/guild/build/queue", "/guild/labs")


@pytest.fixture
def v11(load_portal, floor_db):
    """The portal as Guild 1.1 serves it: the reserve switch OFF (always so in production)."""
    portal = load_portal(reserve_pages=False)
    attach(portal, floor_db.store())
    return portal


def _new(client, token):
    r = client.post(f"{API}/conversations", json=keyed(), headers=write_headers(token))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["conversation"]["id"]


# ── retired addresses redirect by meaning ─────────────────────────────────────────

@pytest.mark.parametrize("old,new", [("/guild/build/bench", "/guild/board"), ("/guild/build/queue", "/guild/build/log"),
                                     ("/guild/labs", "/guild/experiment")])
def test_a_retired_address_redirects_to_the_page_that_replaced_it(v11, old, new):
    client = v11.owner()
    r = client.get(f"{BASE}{old}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"{BASE}{new}")
    assert "no-store" in r.headers.get("Cache-Control", "")                  # a temporary move, never cached
    landed = client.get(f"{BASE}{old}", follow_redirects=True)
    assert landed.status_code == 200
    body = landed.get_data(as_text=True)
    assert not [w for w in ("Workbench", "The wall", "Open wall", "Build Queue") if w in body], old


def test_the_redirects_keep_the_owner_guard(v11):
    anonymous = v11.client().get(f"{BASE}/guild/build/bench")
    assert anonymous.status_code in (302, 401, 403) and "/guild/board" not in anonymous.headers.get("Location", "")
    guest = v11.guest().get(f"{BASE}/guild/build/bench")
    assert guest.status_code in (302, 401, 403) and "/guild/board" not in guest.headers.get("Location", "")


def test_with_the_reserve_switch_on_the_pages_still_exist_proving_they_are_only_retained(load_portal, floor_db):
    portal = load_portal(reserve_pages=True)
    attach(portal, floor_db.store())
    page = portal.owner().get(f"{BASE}/guild/build/bench")
    assert page.status_code == 200 and "Workbench" in page.get_data(as_text=True)
    assert portal.owner().get(f"{BASE}/guild/build/queue").status_code == 200


# ── nothing retired is reachable from a 1.1 page ──────────────────────────────────

def _get_pages(portal):
    rules = []
    for r in portal.app.url_map.iter_rules():
        path = str(r)
        if (path.startswith(f"{BASE}/guild") and "<" not in path and "GET" in r.methods and "/api/" not in path
                and "/static" not in path and "/ui-assets" not in path):
            rules.append(path)
    return sorted(set(rules) | {f"{BASE}/"})


def test_no_page_reachable_in_1_1_carries_a_retired_label_or_link(v11):
    client = v11.owner()
    inventory, checked = [], 0
    for path in _get_pages(v11):
        r = client.get(path)
        inventory.append((path, r.status_code, r.headers.get("Location", "")))
        if r.status_code != 200:
            assert r.status_code in (302, 401, 403, 404, 503), (path, r.status_code)       # a redirect, a refusal: fine
            continue
        body = r.get_data(as_text=True)
        checked += 1
        visible = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S)
        assert not [w for w in RETIRED_WORDS if w in re.sub(r"<[^>]+>", " ", visible)], (path, [w for w in RETIRED_WORDS if w in visible])
        for h in RETIRED_HREFS:
            at = body.find(h)
            assert at < 0, (path, "links to a retired address", body[max(0, at - 120):at + 60])
    assert checked >= 8, inventory                                              # a real sweep, not an empty one
    # the route inventory is part of the release evidence: print it so a failure or a -s run shows it
    print("\n".join(f"{p}  {s}  {loc}" for p, s, loc in inventory))


def test_the_page_json_carries_no_workbench_entry_point(v11):
    page = v11.owner().get(f"{BASE}/guild/build").get_data(as_text=True)
    assert '"bench"' not in page and "/guild/build/bench" not in page


# ── the item page: a way back that never passes through the Workbench ─────────────

def test_the_item_page_leads_back_to_the_build_log_and_the_asking_conversation(v11):
    client = v11.owner()
    token = v11.csrf(client)
    cid = _new(client, token)
    page = client.get(f"{BASE}/guild/build/items/12?c={cid}").get_data(as_text=True)
    assert "#12 · Build Log — mini-moi</title>" in page and "Floor API" in page
    assert f'href="{BASE}/guild/build?c={cid}" data-back-conversation' in page
    assert f'href="{BASE}/guild/build/log?only=12" data-back-build-log' in page   # the Build Log, filtered to this item
    plain = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.S))
    assert not [w for w in RETIRED_WORDS if w in plain]


@pytest.mark.parametrize("c", ["c-000000000000", "../../etc/passwd", "shop-floor-thread-x", ""])
def test_the_item_page_ignores_a_conversation_that_is_not_the_owners_or_does_not_exist(v11, c):
    page = v11.owner().get(f"{BASE}/guild/build/items/12", query_string={"c": c}).get_data(as_text=True)
    assert "data-back-conversation" not in page and "data-back-build-log" in page


def test_a_missing_item_says_so_and_leads_to_the_build_log(v11):
    r = v11.owner().get(f"{BASE}/guild/build/items/999")
    page = r.get_data(as_text=True)
    assert r.status_code == 404 and "The Build Log has no item with this id" in page
    assert f'href="{BASE}/guild/build/log"' in page and "Build Queue" not in page


# ── linked work: both stored shapes render a link that opens the right place ──────

def test_linked_work_opens_its_build_log_item_with_the_conversation_to_come_back_to(v11):
    client = v11.owner()
    token = v11.csrf(client)
    cid = _new(client, token)
    assert client.post(f"{API}/conversations/{cid}/work-item", json=keyed(ref="12"), headers=write_headers(token)).status_code == 200
    rail = client.get(f"{BASE}/guild/build?c={cid}").get_data(as_text=True)
    link = re.search(r'<a data-linked-link href="([^"]+)"', rail).group(1)
    assert link == f"{BASE}/guild/build/items/12?c={cid}"                      # the stored href is unchanged; ?c= is added when shown
    page = client.get(link).get_data(as_text=True)
    assert "Floor API" in page and "data-back-conversation" in page


def test_a_saved_legacy_style_link_and_a_github_link_both_render_correctly(v11, tmp_path):
    import json
    from minimoi_portal.guild_ui.conversations import conversations_of
    client = v11.owner()
    token = v11.csrf(client)
    cid = _new(client, token)
    store = conversations_of(v11.app.extensions["guild_ui_next"]["services"])
    path = Path(store.dir) / f"{cid}.json"
    conv = json.loads(path.read_text())
    conv["work_item"] = {"kind": "item", "ref": "150", "label": "#150 A saved link", "href": f"{BASE}/guild/build/items/150",
                         "source": "number"}                                     # the shape the owner's saved links already have
    path.write_text(json.dumps(conv))
    rail = client.get(f"{BASE}/guild/build?c={cid}").get_data(as_text=True)
    assert f'href="{BASE}/guild/build/items/150?c={cid}"' in rail and "_blank" not in rail.split("data-linked-link")[1][:200]
    assert client.post(f"{API}/conversations/{cid}/work-item", json=keyed(ref="https://github.com/robertvanstedum/personal-ai-agents/pull/9"),
                       headers=write_headers(token)).status_code == 200
    rail = client.get(f"{BASE}/guild/build?c={cid}").get_data(as_text=True)
    assert 'href="https://github.com/robertvanstedum/personal-ai-agents/pull/9"' in rail and "?c=" not in rail.split("data-linked-link")[1][:200]


# ── the reserve code is excluded from a release tree ──────────────────────────────

def test_no_other_template_or_script_refers_to_the_workbench_assets():
    reserve = {"bench.html", "queue.html", "labs.html", "bench.js"}
    offenders = []
    for path in list((GUILD_UI / "templates").rglob("*.html")) + list((GUILD_UI / "static" / "js").glob("*.js")):
        if path.name in reserve:
            continue
        text = path.read_text()
        if re.search(r"urls\.bench\b|page\.urls\.bench\b|from './bench\.js'|include 'guild_floor/(bench|queue|labs)\.html'", text):
            offenders.append(str(path.relative_to(GUILD_UI)))
    assert not offenders, offenders
    main = (GUILD_UI / "static" / "js" / "main.js").read_text()
    assert "import('./bench.js')" in main and "from './bench.js'" not in main     # a missing file cannot break the module graph


def test_the_exclusion_script_removes_exactly_the_reserve_files_and_nothing_else(tmp_path):
    import shutil
    copy = tmp_path / "guild_ui"
    shutil.copytree(GUILD_UI, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    before = {str(p.relative_to(copy)) for p in copy.rglob("*") if p.is_file()}
    script = REPO / "scripts" / "release" / "exclude_reserve_pages.py"
    run = lambda *a: subprocess.run([sys.executable, str(script), str(copy), *a], capture_output=True, text=True)
    assert run("--check").returncode == 1                                          # present in the dev tree
    assert run().returncode == 0
    after = {str(p.relative_to(copy)) for p in copy.rglob("*") if p.is_file()}
    assert before - after == {"templates/guild_floor/bench.html", "templates/guild_floor/queue.html",
                              "templates/guild_floor/labs.html", "static/js/bench.js"}
    assert after <= before and run("--check").returncode == 0 and run().returncode == 0   # idempotent
    refused = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True)
    assert refused.returncode == 2 and "not a guild_ui directory" in refused.stdout        # it will not run on another folder
