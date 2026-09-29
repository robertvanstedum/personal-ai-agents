"""Browser checks for /guild-next (Playwright, installed Chrome, headless). Run explicitly:

    venv/bin/python3 -m pytest tests/guild/shop_floor/browser_checks_b1.py -q -p no:cacheprovider

Serves a fresh copy of the staging portal (MINIMOI_GUILD_NEXT=1) on a loopback
port with a temporary queue folder. A test-only route on that copy signs the
browser in as the owner; no credential is used. Nothing leaves the machine.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from domains.guild import queue_store as qs

from floor_helpers import OWNER, QUEUE_ITEMS, REPO, write_queue
from floor_db_helpers import SqliteFloor

OFF_TEXT = "Off the record · nothing is kept. Save, notes and post-its are paused."


@pytest.fixture(scope="module")
def server():
    import core.get_secret as secrets_module
    import minimoi_portal.config as portal_config
    saved = (secrets_module.get_secret, portal_config.GUILD_QUEUE_PATH, portal_config.BASE_URL,
             qs._running_in_container, os.environ.get("MINIMOI_GUILD_NEXT"))
    secrets_module.get_secret = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no secrets in tests"))
    qs._running_in_container = lambda: False
    folder = Path(tempfile.mkdtemp()) / "guild"
    queue = write_queue(folder / "build_queue.json")
    portal_config.GUILD_QUEUE_PATH = str(queue)
    portal_config.BASE_URL = "https://dev.minimoi.ai"
    os.environ["MINIMOI_GUILD_NEXT"] = "1"
    try:
        spec = importlib.util.spec_from_file_location("portal_app_browser_b1", REPO / "minimoi_portal" / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        (secrets_module.get_secret, portal_config.GUILD_QUEUE_PATH, portal_config.BASE_URL,
         qs._running_in_container, flag) = saved
        if flag is None:
            os.environ.pop("MINIMOI_GUILD_NEXT", None)
        else:
            os.environ["MINIMOI_GUILD_NEXT"] = flag
    qs._running_in_container = lambda: False  # the store checks this on every Save
    app = module.app
    assert module.GUILD_MOUNTS["guild_next"] == "on"

    def test_sign_in():
        from flask import session
        session["user"] = dict(OWNER)
        return '<!DOCTYPE html><link rel="icon" href="data:,"><title>signed in</title>ok'  # no favicon request
    app.add_url_rule("/__b1_test_sign_in", "b1_test_sign_in", test_sign_in)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{port}", "queue": queue, "app": app}
    srv.shutdown()
    qs._running_in_container = saved[3]


@pytest.fixture
def fresh_queue(server):
    write_queue(server["queue"])
    journal = server["queue"].parent / qs.JOURNAL_NAME
    if journal.exists():
        journal.unlink()
    return server["queue"]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def _context(browser, server, width=1280, height=800):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    return ctx, page


def go(page, url):
    page.goto(url)
    page.wait_for_selector("body[data-ready=true]")


def _errors(page):
    """Console errors, except the portal bar's inline styles, which the floor's
    strict CSP (style-src 'self') blocks by design (INTEGRATION_MAP §3.5); the
    bar is styled by components.css instead. Script violations still count."""
    errors = []

    def on_console(message):
        if message.type != "error":
            return
        if "Content Security Policy directive 'style-src 'self''" in message.text:
            return
        errors.append(message.text)
    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def test_desktop_floor_lights_briefing_and_explain_card(browser, server, fresh_queue):
    ctx, page = _context(browser, server)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-mc-header]")).to_contain_text("Master Craftsman is off")
    expect(page.locator("[data-light]")).to_have_count(5)
    expect(page.locator('[data-light="systems"] [data-light-word]')).to_have_text("Unknown")
    expect(page.locator("[data-briefing-label]")).to_have_text("Guild platform · rules · no model")
    expect(page.locator("[data-briefing-text]")).to_contain_text("1 needs you (Decide #31)")
    page.click('[data-ask="usage"]')
    card = page.locator('[data-explain="usage"]')
    expect(card).to_contain_text("Guild platform · rules · no model")
    expect(card).to_contain_text("Usage watch arrives in B2")
    expect(page.locator("text=Hold to talk")).to_have_count(0)
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("width", [390, 360])
def test_phone_type_is_reachable(browser, server, fresh_queue, width):
    ctx, page = _context(browser, server, width=width, height=780)
    errors = _errors(page)
    for path in ("/guild-next/guild/build", "/guild-next/guild/build/queue"):
        go(page, f"{server['url']}{path}")
        type_btn = page.locator("[data-mc-type]")
        expect(type_btn).to_be_visible()
        expect(type_btn).to_be_in_viewport()
        type_btn.click()
        field = page.locator("#mc-input")
        expect(field).to_be_focused()
        expect(field).to_be_in_viewport()
        field.fill("a typed line")
        expect(page.locator("text=Hold to talk")).to_have_count(0)
        expect(page.locator("[data-mc-send]")).to_be_enabled()
    width_ok = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert width_ok
    assert not errors, errors
    ctx.close()


def test_prototype_storage_is_never_read_and_old_keys_are_removed(browser, server, fresh_queue):
    ctx, page = _context(browser, server)
    page.goto(f"{server['url']}/__b1_test_sign_in")
    page.evaluate("""() => {
      localStorage.setItem('guild.guild-proto.bench.v1', JSON.stringify({layout_version: 1, order: ['postits'], folded: ['needs'], focus: 'postits'}));
      localStorage.setItem('guild.guild-next.bench.v1', '{"stale": true}');
      localStorage.setItem('guild.guild-next.conversation.v1', '[]');
    }""")
    go(page, f"{server['url']}/guild-next/guild/build/bench")
    keys = page.evaluate("Object.keys(localStorage)")
    assert "guild.guild-next.bench.v1" not in keys and "guild.guild-next.conversation.v1" not in keys
    assert "guild.guild-proto.bench.v1" in keys  # untouched, and not read
    first = page.locator("[data-bench] > [data-panel]").first
    expect(first).to_have_attribute("data-panel", "needs")
    expect(page.locator('[data-panel="needs"]')).to_have_attribute("data-folded", "false")
    page.click('[data-panel="motion"] [data-act="fold"]')
    stored = json.loads(page.evaluate("localStorage.getItem('guild.guild-next.bench.v2')"))
    assert stored["folded"] == ["motion"]
    assert not [k for k in page.evaluate("Object.keys(localStorage)") if k.startswith("guild.guild-next.") and not k.endswith("bench.v2")]
    ctx.close()


def test_save_shows_a_verified_receipt_that_matches_the_journal(browser, server, fresh_queue):
    ctx, page = _context(browser, server)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build/queue")
    form = page.locator('[data-queue-card][data-item-id="12"] [data-status-form]')
    form.locator("[data-status-select]").select_option("done")
    form.locator("[data-save]").click()
    result = form.locator("[data-save-result]")
    expect(result).to_contain_text("Saved · verified · receipt q-")
    expect(result).to_have_attribute("data-kind", "ok")
    receipt = result.inner_text().rsplit(" ", 1)[-1]
    journal = [json.loads(l) for l in (fresh_queue.parent / qs.JOURNAL_NAME).read_text().splitlines() if l.strip()]
    assert [l["receipt_id"] for l in journal if l.get("kind") == "completed"] == [receipt]
    assert next(i for i in qs.QueueStore(str(fresh_queue)).read_items() if i["id"] == 12)["status"] == "done"
    expect(page.locator("[data-queue-moved]")).to_contain_text("#12 moved to done")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Saved · verified")
    assert not errors, errors
    ctx.close()


def test_conflict_shows_the_current_state(browser, server, fresh_queue):
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    store = qs.QueueStore(str(fresh_queue))
    item = next(i for i in store.read_items() if i["id"] == 12)
    assert store.save_status(12, "blocked", expect_item_digest=qs.item_digest(item), note="changed elsewhere",
                             principal="robert", via="legacy").result == "saved"
    form = page.locator("[data-status-form]")
    form.locator("[data-status-select]").select_option("done")
    form.locator("[data-save]").click()
    expect(form.locator("[data-save-result]")).to_contain_text("#12 changed since you opened it")
    expect(form.locator("[data-status-select]")).to_have_value("blocked")
    expect(page.locator("[data-effective-status]")).to_have_text("blocked")
    assert next(i for i in store.read_items() if i["id"] == 12)["status"] == "blocked"
    ctx.close()


def test_off_the_record_refuses_save_with_platform_text(browser, server, fresh_queue):
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    before = fresh_queue.read_bytes()
    page.click("[data-mc-pill]")  # off the floor, the conversation opens from its pill
    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-refusal]")).to_have_text(OFF_TEXT)
    form = page.locator("[data-status-form]")
    form.locator("[data-status-select]").select_option("done")
    form.locator("[data-save]").click()
    expect(form.locator("[data-save-result]")).to_have_text(OFF_TEXT)
    assert fresh_queue.read_bytes() == before
    assert not (fresh_queue.parent / qs.JOURNAL_NAME).exists()
    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Back on the record")
    expect(page.locator("[data-mc-refusal]")).to_be_hidden()
    ctx.close()


def test_a_broken_queue_reads_unknown_in_the_browser(browser, server, fresh_queue):
    fresh_queue.write_text("[{")
    try:
        ctx, page = _context(browser, server)
        go(page, f"{server['url']}/guild-next/guild/build")
        expect(page.locator('[data-light="build_queue"] [data-light-word]')).to_have_text("Unknown")
        expect(page.locator("[data-needs-line]")).to_have_text("Needs you · unknown — read failed")
        expect(page.locator("[data-briefing-text]")).to_contain_text("needs you unknown")
        ctx.close()
    finally:
        write_queue(fresh_queue, QUEUE_ITEMS)


FLOOR_API = "**/guild-next/api/v1/floor"


def poll(page):
    """Run one floor poll now, the way returning to the tab does."""
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def _page_errors(page):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def test_a_failed_poll_turns_the_floor_stale_then_unknown_and_recovers(browser, server, fresh_queue):
    """Review F1: a failed poll never leaves the last values looking current."""
    ctx, page = _context(browser, server)
    errors = _page_errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    queue = page.locator('[data-light="build_queue"]')
    expect(queue).to_have_attribute("data-light-state", "red")
    expect(queue.locator("[data-light-src]")).to_have_text("live")
    page.route(FLOOR_API, lambda route: route.fulfill(status=503, content_type="application/json",
                                                        body='{"error": "unavailable", "message": "down"}'))
    poll(page)
    # one miss: stale, with a word, its own shape and the time since the last good read
    expect(queue).to_have_attribute("data-light-state", "stale")
    expect(queue.locator("[data-light-word]")).to_have_text("Stale · was Problem")
    expect(queue.locator("svg.lshape")).to_have_attribute("data-shape", "stale")
    expect(queue.locator("[data-light-src]")).to_contain_text("stale · last good read")
    expect(queue.locator("[data-light-src]")).to_contain_text("ago)")
    expect(page.locator('[data-lc="build_queue"] [data-lc-word]')).to_have_text("Stale")
    expect(page.locator("[data-stale-banner]")).to_contain_text("the server answered 503")
    expect(page.locator("[data-needs-line]")).to_contain_text("stale, last good read")
    expect(page.locator("[data-briefing-text]")).to_contain_text("Stale, last good read")
    expect(page.locator('[data-zone="needs"]')).to_have_attribute("data-stale", "stale")
    for light in page.locator("[data-light]").all():
        expect(light.locator("[data-light-src]")).not_to_have_text("live")
    # two misses (here a network error): grey unknown, never "live"
    page.unroute(FLOOR_API)
    page.route(FLOOR_API, lambda route: route.abort())
    poll(page)
    expect(queue).to_have_attribute("data-light-state", "unknown")
    expect(queue.locator("[data-light-word]")).to_have_text("Unknown")
    expect(queue.locator("svg.lshape")).to_have_attribute("data-shape", "ring")
    expect(queue.locator("[data-light-src]")).to_contain_text("unknown · no good read since")
    expect(page.locator("[data-needs-line]")).to_contain_text("Needs you · unknown — no good read since")
    expect(page.locator("[data-reminder-count]")).to_have_text("?")
    expect(page.locator("[data-briefing-text]")).to_contain_text("Floor unknown")
    expect(page.locator("[data-stale-banner]")).to_contain_text("could not be reached")
    assert page.evaluate("document.body.dataset.freshness") == "unknown"
    # the server answers again (304 on the old tag): live values come back
    page.unroute(FLOOR_API)
    poll(page)
    expect(queue).to_have_attribute("data-light-state", "red")
    expect(queue.locator("[data-light-src]")).to_have_text("live")
    expect(queue.locator("svg.lshape")).to_have_attribute("data-shape", "square")
    expect(page.locator("[data-stale-banner]")).to_be_hidden()
    expect(page.locator("[data-reminder-count]")).to_have_text("1")
    assert page.locator('[data-zone="needs"]').get_attribute("data-stale") is None
    assert not errors, errors
    ctx.close()


def test_three_minutes_without_a_good_read_is_unknown(browser, server, fresh_queue):
    """The time rule alone: no poll fails (they never answer), only the page's
    clock moves: stale after 90 s, grey unknown after 3 minutes."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page()
    page.clock.install()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    queue = page.locator('[data-light="build_queue"]')
    page.route(FLOOR_API, lambda route: None)   # a hung server: no answer, so no miss is counted
    page.clock.fast_forward("01:40")
    expect(queue).to_have_attribute("data-light-state", "stale")
    expect(queue.locator("[data-light-src]")).to_contain_text("1 min ago")
    page.clock.fast_forward("01:25")
    expect(queue).to_have_attribute("data-light-state", "unknown")
    expect(queue.locator("[data-light-src]")).to_contain_text("3 min ago")
    ctx.close()


def test_a_401_greys_the_floor_as_signed_out(browser, server, fresh_queue):
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build")
    page.route(FLOOR_API, lambda route: route.fulfill(status=401, content_type="application/json",
                                                        body='{"error": "not_signed_in", "message": "Sign in first."}'))
    poll(page)
    queue = page.locator('[data-light="build_queue"]')
    expect(queue).to_have_attribute("data-light-state", "unknown")
    expect(queue.locator("[data-light-word]")).to_have_text("Signed out")
    expect(queue.locator("[data-light-src]")).to_contain_text("signed out · last good read")
    expect(page.locator("[data-needs-line]")).to_have_text("Needs you · unknown — signed out")
    expect(page.locator("[data-stale-banner]")).to_contain_text("Signed out")
    ctx.close()


def test_one_idempotency_key_per_change_and_a_retry_reuses_it(browser, server, fresh_queue):
    """Review F9: the key belongs to the opened form and its change, not the click."""
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    form = page.locator("[data-status-form]")
    opened = form.get_attribute("data-idem-key")
    assert opened and len(opened) >= 8
    form.locator("[data-status-select]").select_option("done")
    key = form.get_attribute("data-idem-key")
    assert key != opened
    sent = []
    status_api = "**/guild-next/api/v1/queue/items/12/status"

    def lose_the_answer(route):
        sent.append(json.loads(route.request.post_data)["idempotency_key"])
        route.abort()
    page.route(status_api, lose_the_answer)
    form.locator("[data-save]").click()
    expect(form.locator("[data-save-result]")).to_contain_text("could not be reached")
    page.unroute(status_api)
    page.on("request", lambda r: sent.append(json.loads(r.post_data)["idempotency_key"])
            if r.url.endswith("/queue/items/12/status") else None)
    form.locator("[data-save]").click()
    expect(form.locator("[data-save-result]")).to_contain_text("Saved · verified · receipt q-")
    assert sent == [key, key]
    journal = [json.loads(l) for l in (fresh_queue.parent / qs.JOURNAL_NAME).read_text().splitlines() if l.strip()]
    assert len([l for l in journal if l.get("kind") == "intent"]) == 1
    assert form.get_attribute("data-idem-key") != key   # the next change gets a new key
    ctx.close()


# ── Deliverable (c): Continue, post-its with the bin, notes (W4, W6, W7) ──────

PHONE = {"width": 390, "height": 780}


@pytest.fixture
def floor(server, fresh_queue, tmp_path):
    """A fresh floor database behind the running portal."""
    db = SqliteFloor(tmp_path / "floor")
    server["app"].extensions["guild_ui_next"]["services"].floor = db.store()
    return db


def _requests(page, fragment):
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url)) if fragment in r.url else None)
    return seen


def test_w4_opening_an_item_sets_continue_and_the_phone_shows_it(browser, server, floor):
    desk, page = _context(browser, server)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator('[data-zone="continue"] [data-continue]')).to_have_text("Nothing to continue — open a queue item")
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    expect(page.locator("[data-continue]").first).to_have_attribute("data-continue-state", "ok")
    page.wait_for_function("() => [...document.querySelectorAll('[data-continue-link]')].some(a => a.textContent === '#12 Floor API')")
    assert floor.rows("floor_continue")[0]["ref"] == "12"
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator('[data-zone="continue"] [data-continue-link]')).to_have_text("#12 Floor API")
    assert not errors, errors
    desk.close()

    phone, ppage = _context(browser, server, **PHONE)     # another session and viewport, same owner
    go(ppage, f"{server['url']}/guild-next/guild/build")
    ppage.click("[data-sheet-toggle]")
    link = ppage.locator('[data-zone="continue"] [data-continue-link]')
    expect(link).to_be_visible()
    expect(link).to_have_text("#12 Floor API")
    go(ppage, f"{server['url']}/guild-next/guild/build/queue")
    expect(ppage.locator(".ps-continue [data-continue-link]")).to_have_text("#12 Floor API")
    phone.close()


def _w6(page, server, floor, phone):
    go(page, f"{server['url']}/guild-next/guild/build")
    if phone:
        page.click("[data-sheet-toggle]")
    rail = page.locator('[data-postits][data-mode="rail"]')
    rail.locator("[data-postit-input]").fill("Ask about the lock timeout")
    rail.locator("[data-postit-add-btn]").click()
    row = rail.locator("[data-postit]")
    expect(row).to_have_count(1)
    expect(row.locator("[data-postit-author]")).to_have_text("Robert")
    expect(rail.locator("[data-postit-result]")).to_have_text("Post-it added")
    row.locator("[data-postit-bin]").click()
    expect(rail.locator("[data-postit]")).to_have_count(0)
    expect(rail.locator("[data-postits-bin-link]")).to_have_text("Bin (1) →")
    rail.locator("[data-postits-bin-link]").click()
    page.wait_for_selector("body[data-ready=true]")
    binned = page.locator('[data-postits][data-mode="bin"] [data-bin-item]')
    expect(binned).to_have_count(1)
    expect(binned).to_be_visible()
    expect(binned).to_contain_text("Ask about the lock timeout")
    expect(binned).to_contain_text("binned")
    binned.locator("[data-postit-restore]").click()
    expect(page.locator('[data-postits][data-mode="bin"] [data-bin-item]')).to_have_count(0)
    board = page.locator('[data-postits][data-mode="board"] [data-postit]')
    expect(board).to_have_count(1)
    expect(board).to_be_visible()
    rows = floor.rows("floor_postits")
    assert len(rows) == 1 and rows[0]["binned_at"] is None and rows[0]["restored_at"]


def test_w6_post_it_add_remove_restore_on_desktop(browser, server, floor):
    ctx, page = _context(browser, server)
    errors = _errors(page)
    before = server["queue"].read_bytes()
    _w6(page, server, floor, phone=False)
    assert server["queue"].read_bytes() == before                     # nothing else triggered
    assert not (server["queue"].parent / qs.JOURNAL_NAME).exists()
    assert floor.count("floor_messages") == 0
    go(page, f"{server['url']}/guild-next/guild/build/bench")         # the desktop bench shows the board and bin
    expect(page.locator('[data-panel="postits"] [data-mode="board"] [data-postit]')).to_have_count(1)
    expect(page.locator('[data-panel="postits"] [data-mode="bin"]')).to_contain_text("The bin is empty")
    assert not errors, errors
    ctx.close()


def test_the_first_post_it_can_be_added_on_an_empty_bench(browser, server, floor):
    """Robert's walkthrough, W6: with no post-its and an empty bin, the bench
    must still show the Add box (it used to fold the whole panel away)."""
    ctx, page = _context(browser, server)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build/bench")
    panel = page.locator('[data-panel="postits"]')
    expect(panel.locator("[data-postit-input]")).to_be_visible()
    expect(panel.locator("[data-panel-state]")).not_to_have_text("nothing to show")
    expect(panel).to_contain_text("No post-its on the board")
    panel.locator("[data-postit-input]").fill("First one, from the bench")
    panel.locator("[data-postit-add-btn]").click()
    expect(panel.locator('[data-mode="board"] [data-postit]')).to_have_count(1)
    assert len(floor.rows("floor_postits")) == 1
    assert not errors, errors
    ctx.close()


def test_w6_post_it_add_remove_restore_on_the_phone(browser, server, floor):
    ctx, page = _context(browser, server, **PHONE)
    errors = _errors(page)
    _w6(page, server, floor, phone=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert not errors, errors
    ctx.close()


def test_the_rail_shows_four_and_links_the_rest(browser, server, floor):
    from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
    store = floor.store()
    for n in range(6):
        store.add_postit(f"note {n}", MASTER_CRAFTSMAN, idempotency_key=f"rail-cap-{n:04d}")
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build")
    rail = page.locator('[data-postits][data-mode="rail"]')
    expect(rail.locator("[data-postit]")).to_have_count(4)
    expect(rail.locator("[data-postit-author]").first).to_have_text("Master Craftsman")
    expect(rail.locator("[data-postits-more]")).to_have_text("2 more →")
    ctx.close()


def test_w7_notes_on_the_record_and_nothing_sent_off_the_record(browser, server, floor):
    ctx, page = _context(browser, server)
    errors = _errors(page)
    sent = _requests(page, "/api/v1/notes")
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-mc-header]")).to_have_text("Master Craftsman is off · your messages are kept as notes")
    page.fill("#mc-input", "Kept: check the EC2 queue checksum")
    page.click("[data-mc-send]")
    note = page.locator('[data-mc-thread] [data-kind="note"]')
    expect(note).to_have_count(1)
    expect(note).to_contain_text("Kept: check the EC2 queue checksum")
    expect(note).to_contain_text("kept as a note")
    assert floor.count("floor_messages") == 1 and len(sent) == 1

    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-refusal]")).to_have_text(OFF_TEXT)
    page.fill("#mc-input", "private: not for the record")
    page.click("[data-mc-send]")
    off = page.locator("[data-off-record-line]")
    expect(off).to_have_count(1)
    expect(off).to_contain_text("not sent, not kept")
    rail = page.locator('[data-postits][data-mode="rail"]')
    rail.locator("[data-postit-input]").fill("private post-it")
    rail.locator("[data-postit-add-btn]").click()
    expect(rail.locator("[data-postit-result]")).to_have_text(OFF_TEXT)
    assert len(sent) == 1                                          # nothing sent while off
    assert floor.count("floor_messages") == 1 and floor.count("floor_postits") == 0
    assert "private" not in floor.all_text()

    page.click("[data-mc-record]")
    expect(page.locator("[data-off-record-line]")).to_have_count(0)
    expect(page.locator("[data-mc-thread]")).to_contain_text("Back on the record")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(1)
    assert "private" not in page.content()
    assert not errors, errors
    ctx.close()


def _mc_turns_on(page):
    page.evaluate("document.body.dataset.mcTurns = 'true'")


def test_mc_waiting_line_sits_where_the_reply_goes_and_is_replaced_in_place(browser, server, floor):
    """Robert, 2026-09-29 (after his own OpenClaw chat): no "Asking Master
    Craftsman" platform entry. Under the note, MC's mark, "Waiting for a
    response…" and a seconds counter; the reply (with "Done in Ns") or the
    honest failure line replaces it in place. The note shows first."""
    ctx, page = _context(browser, server)
    errors = _errors(page)
    held = []
    page.route("**/api/v1/mc/turns", lambda route: held.append(route))   # the turn waits until released
    go(page, f"{server['url']}/guild-next/guild/build")
    _mc_turns_on(page)
    thread = page.locator("[data-mc-thread]")
    page.fill("#mc-input", "What is stuck on the board?")
    page.click("[data-mc-send]")
    note = thread.locator('[data-kind="note"]')
    expect(note).to_have_count(1)                                   # shown while MC is still working
    waiting = thread.locator("[data-mc-waiting]")
    expect(waiting).to_have_count(1)
    expect(waiting).to_contain_text("Waiting for a response…")
    expect(waiting.locator(".mc-mark")).to_have_text("MC")
    expect(waiting.locator('[data-slot="elapsed"]')).to_have_attribute("aria-hidden", "true")
    expect(waiting.locator('[data-slot="elapsed"]')).to_have_text("1s", timeout=3000)   # the counter ticks
    assert thread.locator("li").last.get_attribute("data-kind") == "mc-waiting"        # right under the note
    assert "Asking Master Craftsman" not in thread.inner_text()
    platform_before = thread.locator('[data-kind="platform"]').count()
    assert len(held) == 1
    reply = {"id": 9001, "request_id": "mc-reply-1", "author_kind": "agent", "author_label": "Master Craftsman",
             "who": "master_craftsman", "text": "Item 12 is waiting on review.", "created_at": "2026-09-29T09:00:00+00:00",
             "context": {}, "turn": {"duration_ms": 1234, "done_text": "Done in 1.2s", "usage": None,
                                      "output_tokens": None, "tokens_text": None}}      # tokens not recorded yet
    held[0].fulfill(status=200, content_type="application/json", body=json.dumps({
        "status": "answered", "reply_note": reply, "message": "Master Craftsman answered · kept on the record",
        "mc_state": "live", "mc_header": "Master Craftsman is live · your messages are kept as notes"}))
    expect(waiting).to_have_count(0)
    answered = thread.locator('[data-note="9001"]')
    expect(answered).to_contain_text("Item 12 is waiting on review.")
    expect(answered.locator("[data-turn-done]")).to_have_text("Done in 1.2s")
    expect(answered.locator("[data-turn-usage]")).to_have_text("")
    # usage-record U3: the page asks the notes list again; the gateway's record has landed by then.
    later = dict(reply, turn={**reply["turn"], "output_tokens": 96, "tokens_text": "96 output tokens"})
    page.route("**/api/v1/notes?limit=20", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({"notes": [later], "more": False})))
    expect(answered.locator("[data-turn-foot]")).to_have_text("Done in 1.2s · 96 output tokens", timeout=8000)
    assert thread.locator("li").last.get_attribute("data-note") == "9001"             # in the waiting line's place
    assert thread.locator('[data-kind="platform"]').count() == platform_before        # no platform line added

    # An honest failure replaces the waiting line with its platform line.
    page.fill("#mc-input", "And the bin?")
    page.click("[data-mc-send]")
    expect(waiting).to_have_count(1)
    held[1].fulfill(status=200, content_type="application/json", body=json.dumps({
        "status": "unavailable", "failure_class": "key_refused", "mc_state": "unavailable",
        "message": "Master Craftsman is unavailable · its model key was refused. Your note is kept; Master Craftsman did not answer.",
        "mc_header": "Master Craftsman is unavailable · its model key was refused"}))
    expect(waiting).to_have_count(0)
    last = thread.locator("li").last
    expect(last).to_have_attribute("data-kind", "platform")
    expect(last).to_contain_text("its model key was refused")
    assert not errors, errors
    ctx.close()


def test_a_failed_poll_marks_the_floor_zones_with_the_floors_freshness(browser, server, floor):
    """(c)'s zones follow (b)'s freshness rules: stale after one failed poll,
    unknown after two, and live again on the next good read."""
    floor.store().set_continue("robert", kind="item", ref="12", label="#12 Floor API", idempotency_key="fresh-cont-01")
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build")
    page.route(FLOOR_API, lambda route: route.abort())
    poll(page)
    mark = page.locator('[data-zone="continue"] [data-zone-fresh]')
    expect(mark).to_contain_text("Stale · last good read")
    expect(page.locator('[data-postits][data-mode="rail"]')).to_have_attribute("data-stale", "stale")
    expect(page.locator("[data-notes-line]")).to_have_attribute("data-stale", "stale")
    expect(page.locator('[data-zone="continue"] [data-continue-link]')).to_have_text("#12 Floor API")
    poll(page)
    expect(mark).to_contain_text("Unknown · no good read since")
    page.unroute(FLOOR_API)
    poll(page)
    expect(page.locator("[data-zone-fresh]")).to_have_count(0)
    assert page.locator('[data-postits][data-mode="rail"]').get_attribute("data-stale") is None
    ctx.close()


# ── Review B1c fixes: off the record across pages, lists kept current ─────────

def test_off_the_record_survives_navigation_and_nothing_is_written(browser, server, floor):
    ctx, page = _context(browser, server)
    errors = _errors(page)
    writes = []
    page.on("request", lambda r: writes.append((r.method, r.url))
            if r.method != "GET" and ("/api/v1/continue" in r.url or "/api/v1/notes" in r.url
                                      or "/api/v1/postits" in r.url) else None)
    go(page, f"{server['url']}/guild-next/guild/build")
    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-record]")).to_have_text("Back on the record")
    go(page, f"{server['url']}/guild-next/guild/build/items/12")        # a new page load, still off
    expect(page.locator("[data-mc-record]")).to_have_text("Back on the record")
    expect(page.locator("[data-mc-refusal]")).to_have_text(OFF_TEXT)
    page.wait_for_timeout(500)
    go(page, f"{server['url']}/guild-next/guild/build/items/7")
    page.wait_for_timeout(500)
    assert writes == [] and floor.count("floor_continue") == 0 and floor.count("floor_messages") == 0
    stored = page.evaluate("sessionStorage.getItem('guild.guild-next.record_mode')")
    assert json.loads(stored)["off"] is True and set(json.loads(stored)) <= {"off", "since"}   # a flag, no content
    page.click("[data-mc-pill]")
    page.click("[data-mc-record]")                                       # back on the record, by choice
    go(page, f"{server['url']}/guild-next/guild/build/items/7")
    page.wait_for_function("() => document.querySelector('[data-continue-link]')?.textContent === '#7 Queue lock hardening'")
    assert floor.rows("floor_continue")[0]["ref"] == "7"
    assert not errors, errors
    ctx.close()


def test_an_unreadable_record_mode_makes_no_automatic_write(browser, server, floor):
    ctx, page = _context(browser, server)
    sent = _requests(page, "/api/v1/continue")
    page.evaluate("sessionStorage.setItem('guild.guild-next.record_mode', 'garbled')")
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    expect(page.locator("[data-mc-thread]")).to_contain_text("does not know whether you are on the record")
    page.wait_for_timeout(500)
    assert sent == [] and floor.count("floor_continue") == 0
    ctx.close()


def test_the_board_and_bin_follow_a_change_made_elsewhere(browser, server, floor):
    from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN, Author
    store = floor.store()
    kept = store.add_postit("stays", MASTER_CRAFTSMAN, idempotency_key="elsewhere-01").value
    moved = store.add_postit("binned elsewhere", MASTER_CRAFTSMAN, idempotency_key="elsewhere-02").value
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build/postits")
    board = page.locator('[data-postits][data-mode="board"] [data-postit]')
    expect(board).to_have_count(2)
    store.bin_postit(moved["id"], Author("robert_phone", "owner", "Robert"), idempotency_key="elsewhere-03")
    poll(page)                                                           # the floor poll (or returning to the tab)
    expect(board).to_have_count(1)
    expect(board).to_contain_text("stays")
    expect(page.locator('[data-postits][data-mode="bin"] [data-bin-item]')).to_contain_text("binned elsewhere")

    # A list that cannot be re-read stays on screen, marked stale, never as current.
    page.route("**/guild-next/api/v1/postits**", lambda route: route.abort())
    store.restore_postit(moved["id"], Author("robert_phone", "owner", "Robert"), idempotency_key="elsewhere-04")
    poll(page)
    expect(page.locator('[data-postits][data-mode="board"]')).to_have_attribute("data-list-stale", "true")
    expect(page.locator('[data-postits][data-mode="board"] [data-list-fresh]')).to_contain_text("Stale")
    expect(board).to_have_count(1)
    page.unroute("**/guild-next/api/v1/postits**")
    page.evaluate("window.dispatchEvent(new Event('focus'))")           # focus re-reads the lists
    expect(board).to_have_count(2)
    expect(page.locator("[data-list-fresh]")).to_have_count(0)
    assert kept["id"] != moved["id"]
    ctx.close()


def test_a_tab_opened_from_an_off_record_tab_writes_nothing_by_itself(browser, server, floor):
    """Re-check residual: a fresh tab (e.g. a middle-click) starts with empty
    sessionStorage; while another tab is off the record it must not assume
    "on the record" and write Continue."""
    ctx, first = _context(browser, server)
    go(first, f"{server['url']}/guild-next/guild/build")
    first.click("[data-mc-record]")                                   # tab 1 goes off the record
    second = ctx.new_page()                                           # same browser, fresh tab
    sent = _requests(second, "/api/v1/continue")
    go(second, f"{server['url']}/guild-next/guild/build/items/12")
    expect(second.locator("[data-mc-thread]")).to_contain_text("does not know whether you are on the record")
    second.wait_for_timeout(500)
    assert sent == [] and floor.count("floor_continue") == 0
    second.click("[data-mc-pill]")
    confirm = second.locator("[data-mc-record-confirm]")
    expect(confirm).to_be_visible()
    confirm.click()                                                   # Robert chooses: on the record here
    expect(confirm).to_be_hidden()
    go(second, f"{server['url']}/guild-next/guild/build/items/7")
    second.wait_for_function("() => document.querySelector('[data-continue-link]')?.textContent === '#7 Queue lock hardening'")
    assert floor.rows("floor_continue")[0]["ref"] == "7"

    first.click("[data-mc-record]")                                   # tab 1 back on the record
    third = ctx.new_page()                                            # now a fresh tab is simply on the record
    go(third, f"{server['url']}/guild-next/guild/build/items/12")
    third.wait_for_function("() => document.querySelector('[data-continue-link]')?.textContent === '#12 Floor API'")
    expect(third.locator("[data-mc-record-confirm]")).to_be_hidden()
    ctx.close()



def test_dragging_a_box_moves_it_up_or_down_and_releases_focus(browser, server, fresh_queue):
    """Robert's walkthrough: dragging did nothing. Dropping on a box below put
    yours back before it (no move), and a box in focus stayed pinned on top."""
    ctx, page = _context(browser, server)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build/bench")
    order = lambda: page.eval_on_selector_all("[data-bench] > [data-panel]", "els => els.map(e => e.dataset.panel)")
    handle = lambda pid: page.locator(f'[data-panel="{pid}"] [data-drag-handle]')
    box = lambda pid: page.locator(f'[data-panel="{pid}"]')
    a, b, c, d, e = order()
    handle(a).drag_to(box(b))                                   # down one: now after b
    assert order() == [b, a, c, d, e]
    handle(b).drag_to(box(d))                                   # down further: lands after d
    assert order() == [a, c, d, b, e]
    handle(d).drag_to(box(a))                                   # up: lands before a
    assert order() == [d, a, c, b, e]
    box(e).locator('[data-act="focus"]').click()                # e in focus, pinned on top
    assert order()[0] == e
    handle(e).drag_to(box(c))                                   # dragging it moves it and ends the pin
    assert order()[0] != e and order().index(e) == order().index(c) + 1
    expect(box(e).locator('[data-act="focus"]')).to_have_text("☆ Focus")
    page.reload()
    assert order().index(e) == order().index(c) + 1             # the arrangement is kept
    assert not errors, errors
    ctx.close()


# ── #242: the off-the-record tab list is fail-safe ────────────────────────────

UNKNOWN_TEXT = "Not sent: this tab does not know whether you are on the record."
OFF_TABS = "guild.guild-next.off_tabs"


def _writes(page):
    seen = []
    page.on("request", lambda r: seen.append(r.url.split("/api/v1")[-1])
            if r.method != "GET" and "/api/v1/" in r.url else None)
    return seen


def _unknown_tab_sends_nothing(page, server, floor):
    """On a tab whose mode is unknown: it says so, and a note and a post-it stay here."""
    sent = _writes(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-mc-context]")).to_contain_text("record mode unknown")
    expect(page.locator("body")).to_have_attribute("data-record-known", "false")
    page.fill("[data-mc-input]", "a note while unknown")
    page.click("[data-mc-send]")
    expect(page.locator("[data-mc-refusal]")).to_contain_text(UNKNOWN_TEXT)
    add = page.locator("[data-postits] [data-postit-add]").first
    add.locator("[data-postit-input]").fill("a post-it while unknown")
    add.locator("[data-postit-add-btn]").click()
    expect(page.locator("[data-postits]").first).to_contain_text(UNKNOWN_TEXT)
    page.wait_for_timeout(300)
    assert sent == [] and floor.count("floor_messages") == 0 and floor.count("floor_postits") == 0
    page.click("[data-mc-record-confirm]")                            # Robert chooses: on the record
    expect(page.locator("[data-mc-context]")).to_contain_text("on the record")
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(1)
    assert sent == ["/notes"]


@pytest.mark.parametrize("stored", ["{garbled", '{"t1": 5}', '{"t1": "not a time"}', '["t1"]'],
                         ids=["corrupt-json", "not-a-time", "bad-time", "not-an-object"])
def test_242_a_damaged_off_tab_list_makes_a_fresh_tab_unknown(browser, server, floor, stored):
    ctx, first = _context(browser, server)
    go(first, f"{server['url']}/guild-next/guild/build")
    first.evaluate(f"localStorage.setItem('{OFF_TABS}', {json.dumps(stored)})")
    fresh = ctx.new_page()
    _unknown_tab_sends_nothing(fresh, server, floor)
    ctx.close()


def test_242_unreadable_browser_storage_makes_a_fresh_tab_unknown(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script("""Object.defineProperty(window, 'localStorage', {
        configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });""")
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    _unknown_tab_sends_nothing(page, server, floor)
    ctx.close()


def _tab_id(page):
    return page.evaluate("sessionStorage.getItem('guild.guild-next.tab_id')")


def test_242_a_duplicated_tab_takes_its_own_id_and_is_still_counted(browser, server, floor):
    ctx, first = _context(browser, server)
    go(first, f"{server['url']}/guild-next/guild/build")
    first_id = _tab_id(first)
    go(first, f"{server['url']}/guild-next/guild/build/bench")
    assert _tab_id(first) == first_id                                  # moving between pages keeps the id
    first.click("[data-mc-pill]")
    first.click("[data-mc-record]")                                    # off the record
    with ctx.expect_page() as opened:
        first.evaluate("window.open(location.href)")                   # a copy of this tab's sessionStorage
    dup = opened.value
    dup.wait_for_selector("body[data-ready=true]")
    assert dup.evaluate("JSON.parse(sessionStorage.getItem('guild.guild-next.record_mode')).off") is True
    dup_id = _tab_id(dup)
    assert dup_id and dup_id != first_id
    first.click("[data-mc-record]")                                    # the original goes back on the record
    tabs = json.loads(first.evaluate(f"localStorage.getItem('{OFF_TABS}')"))
    assert list(tabs) == [dup_id]                                      # the copy is still counted
    fresh = ctx.new_page()
    go(fresh, f"{server['url']}/guild-next/guild/build")
    expect(fresh.locator("[data-mc-context]")).to_contain_text("record mode unknown")
    ctx.close()


def test_242_a_closed_off_tab_stops_counting(browser, server, floor):
    ctx, first = _context(browser, server)
    go(first, f"{server['url']}/guild-next/guild/build")
    first.click("[data-mc-record]")
    keeper = ctx.new_page()                                            # keeps the browser context's storage open
    keeper.goto(f"{server['url']}/__b1_test_sign_in")
    first.close(run_before_unload=True)
    keeper.wait_for_timeout(200)
    assert json.loads(keeper.evaluate(f"localStorage.getItem('{OFF_TABS}')")) == {}
    fresh = ctx.new_page()
    go(fresh, f"{server['url']}/guild-next/guild/build/items/12")
    expect(fresh.locator("[data-mc-context]")).to_contain_text("on the record")
    expect(fresh.locator("[data-mc-record-confirm]")).to_be_hidden()
    fresh.wait_for_function("() => document.querySelector('[data-continue-link]')?.textContent === '#12 Floor API'")
    ctx.close()


@pytest.mark.parametrize("minutes,known", [(11, True), (5, False)], ids=["crashed-11-min-ago", "live-5-min-ago"])
def test_242_a_crashed_off_tab_stops_counting_after_ten_minutes(browser, server, floor, minutes, known):
    ctx, first = _context(browser, server)
    go(first, f"{server['url']}/guild-next/guild/build")
    first.evaluate(f"""localStorage.setItem('{OFF_TABS}',
        JSON.stringify({{tgone: new Date(Date.now() - {minutes} * 60000).toISOString()}}))""")
    fresh = ctx.new_page()
    go(fresh, f"{server['url']}/guild-next/guild/build")
    expect(fresh.locator("body")).to_have_attribute("data-record-known", "true" if known else "false")
    ctx.close()
