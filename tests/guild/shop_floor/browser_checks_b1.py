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
import re
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
    page.click("[data-lights-toggle]")          # Guild 1.1: the lights are a compact strip low on the page
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
    expect(page.locator("[data-needs-badge]")).to_have_attribute("data-stale", "stale")   # Guild 1.1: the one Needs you place
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
    assert page.locator("[data-needs-badge]").get_attribute("data-stale") is None
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
    # Guild 1.1: nothing to continue and nothing urgent, so the rail is one quiet line.
    expect(page.locator("[data-rail-continue]")).to_be_hidden()
    expect(page.locator("[data-floor-urgent]")).to_be_visible()      # #31 needs Robert, so the rail shows it, not the quiet line
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    expect(page.locator("[data-continue]").first).to_have_attribute("data-continue-state", "ok")
    page.wait_for_function("() => [...document.querySelectorAll('[data-continue-link]')].some(a => a.textContent === '#12 Floor API')")
    assert floor.rows("floor_continue")[0]["ref"] == "12"
    go(page, f"{server['url']}/guild-next/guild/build")
    about = page.locator("[data-rail-continue]")                    # "This conversation is about"
    expect(about).to_be_visible()
    expect(about.locator("[data-continue-link]")).to_have_text("#12 Floor API")
    assert not errors, errors
    desk.close()

    phone, ppage = _context(browser, server, **PHONE)     # another session and viewport, same owner
    go(ppage, f"{server['url']}/guild-next/guild/build")
    ppage.click("[data-floor-context] > summary")                 # the context, folded below the chat
    link = ppage.locator('[data-rail-continue] [data-continue-link]')
    expect(link).to_be_visible()
    expect(link).to_have_text("#12 Floor API")
    go(ppage, f"{server['url']}/guild-next/guild/build/queue")
    expect(ppage.locator(".ps-continue [data-continue-link]")).to_have_text("#12 Floor API")
    phone.close()


def _w6(page, server, floor, phone):
    # Guild 1.1: post-its live on the wall (the Workbench), not on the Shop floor.
    go(page, f"{server['url']}/guild-next/guild/build")
    assert page.locator('[data-postits][data-mode="rail"]').count() == 0
    page.locator("[data-open-wall]").first.click() if not phone else page.goto(f"{server['url']}/guild-next/guild/build/bench")
    page.wait_for_selector("body[data-ready=true]")
    wall = page.locator('[data-panel="postits"]')
    if phone:
        page.click('[data-wall-filter="postits"]')                   # one column; the filter narrows it
    wall.locator("[data-postit-input]").fill("Ask about the lock timeout")
    wall.locator("[data-postit-add-btn]").click()
    row = wall.locator('[data-mode="board"] [data-postit]')
    expect(row).to_have_count(1)
    expect(row.locator("[data-postit-author]")).to_have_text("Robert")
    expect(wall.locator("[data-postit-result]").first).to_have_text("Post-it added")
    row.locator("[data-postit-bin]").click()
    expect(wall.locator('[data-mode="board"] [data-postit]')).to_have_count(0)
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
    page.reload()                                                     # the wall keeps the board and bin
    page.wait_for_selector("body[data-ready=true]")
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


def test_the_wall_shows_every_post_it_and_the_floor_shows_none(browser, server, floor):
    """Guild 1.1: no permanent post-it stack on the Shop floor; the wall (the
    Workbench) has room for all of them, with their writers."""
    from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
    store = floor.store()
    for n in range(6):
        store.add_postit(f"note {n}", MASTER_CRAFTSMAN, idempotency_key=f"rail-cap-{n:04d}")
    ctx, page = _context(browser, server)
    go(page, f"{server['url']}/guild-next/guild/build")
    assert page.locator("[data-postit]").count() == 0
    go(page, f"{server['url']}/guild-next/guild/build/bench")
    board = page.locator('[data-panel="postits"] [data-mode="board"]')
    expect(board.locator("[data-postit]")).to_have_count(6)
    expect(board.locator("[data-postit-author]").first).to_have_text("Master Craftsman")
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
    go(page, f"{server['url']}/guild-next/guild/build/bench")      # Guild 1.1: post-its are on the wall
    wall = page.locator('[data-panel="postits"]')
    wall.locator("[data-postit-input]").fill("private post-it")
    wall.locator("[data-postit-add-btn]").click()
    expect(wall.locator("[data-postit-result]").first).to_have_text(OFF_TEXT)
    go(page, f"{server['url']}/guild-next/guild/build")
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


def test_a_long_markdown_reply_renders_as_structure_live_and_after_a_reload(browser, server, floor):
    """Robert, September 29: MC's Markdown replies must read like OpenClaw's,
    Codex's or Grok's, not one run-on block with ** showing. The reply goes
    through the real /mc/turns route (a stand-in runtime), so the HTML is the
    server's sanitised render, live and after a reload."""
    import minimoi_portal.guild_ui.mc.openclaw as oc
    from minimoi_portal.guild_ui.mc import CachedHealth
    services = server["app"].extensions["guild_ui_next"]["services"]
    reply = ("Here is where things stand:\n## Build queue\n**Three items** need you:\n1. **#12** waits on review\n"
             "   - the relay change\n   - the portal change\n2. **#14** is in build\n### Next\n- run `verify.sh`\n"
             "<img src=x onerror=\"window.__pwned=1\">")

    class Resp:
        def __init__(self, status, text, headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    def post(url, data=None, headers=None, timeout=None):
        return Resp(200, json.dumps({"id": "chatcmpl_md", "object": "chat.completion", "model": "openclaw/mc-agent",
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                                                  "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
                    {"X-MC-Correlation-Id": (headers or {}).get("X-MC-Correlation-Id")})
    saved = (services.mc, services.mc_health, services.mc_turns)
    backend = oc.OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 40, http_get=lambda *a, **k: Resp(200, "{}"),
                                         http_post=post)
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    try:
        ctx, page = _context(browser, server)
        errors = _errors(page)
        go(page, f"{server['url']}/guild-next/guild/build")
        page.fill("#mc-input", "Where do things stand?")
        page.click("[data-mc-send]")
        answered = page.locator('[data-mc-thread] [data-kind="note"]').last
        expect(answered).to_contain_text("Three items", timeout=10000)
        body = answered.locator(".msg-md")
        expect(body.locator("h2")).to_have_text("Build queue")
        expect(body.locator("ol > li")).to_have_count(2)
        expect(body.locator("ol > li").first.locator("ul > li")).to_have_count(2)
        expect(body.locator("strong").first).to_have_text("Three items")
        expect(body.locator("code")).to_have_text("verify.sh")
        assert "**" not in body.inner_text() and body.locator("img").count() == 0
        assert page.evaluate("window.__pwned === undefined")
        page.reload()
        page.wait_for_selector("body[data-ready=true]")
        again = page.locator('[data-mc-thread] [data-kind="note"]').last.locator(".msg-md")
        expect(again.locator("h2")).to_have_text("Build queue")
        assert "**" not in again.inner_text()
        assert not errors, errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


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
    page.route("**/api/v1/notes?limit=20*", lambda route: route.fulfill(
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
    mark = page.locator('[data-rail-continue] [data-zone-fresh]')
    expect(mark).to_contain_text("Stale · last good read")
    expect(page.locator("[data-notes-line]")).to_have_attribute("data-stale", "stale")
    expect(page.locator('[data-rail-continue] [data-continue-link]')).to_have_text("#12 Floor API")
    poll(page)
    expect(mark).to_contain_text("Unknown · no good read since")
    page.unroute(FLOOR_API)
    poll(page)
    expect(page.locator("[data-zone-fresh]")).to_have_count(0)
    assert page.locator("[data-rail-continue] [data-continue]").get_attribute("data-stale") is None
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
    a, b, c, d, e, f = order()                                  # six boxes since Continue joined the wall
    handle(a).drag_to(box(b))                                   # down one: now after b
    assert order() == [b, a, c, d, e, f]
    handle(b).drag_to(box(d))                                   # down further: lands after d
    assert order() == [a, c, d, b, e, f]
    handle(d).drag_to(box(a))                                   # up: lands before a
    assert order() == [d, a, c, b, e, f]
    box(e).locator('[data-act="focus"]').click()                # e in focus, pinned on top
    assert order()[0] == e
    handle(e).drag_to(box(c))                                   # dragging it moves it and ends the pin
    assert order()[0] != e and order().index(e) == order().index(c) + 1
    expect(box(e).locator('[data-act="focus"]')).to_have_text("☆ Focus")
    page.reload()
    assert order().index(e) == order().index(c) + 1             # the arrangement is kept
    assert not errors, errors
    ctx.close()


# ── Guild 1.1 dev, slice 1: the Shop floor layout (Robert, September 29) ──────

def _box(page, sel):
    return page.locator(sel).first.bounding_box()


def test_slice1_desktop_history_chat_and_context_with_the_hero_in_the_chat_header(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    history, rail = page.locator("[data-floor-history]"), page.locator("#main")
    expect(history).to_be_visible()
    expect(history.locator(".fh-row")).to_have_count(1)                          # one real thread, no fake rows
    expect(rail).to_be_visible()
    expect(rail.locator("[data-build-card]")).to_have_count(0)                   # the hero left the rail (Guild 1.1 slice 1)
    _hero_in_header(page)
    h, c, r = _box(page, "[data-floor-history]"), _box(page, "[data-mc-thread]"), _box(page, "#main")
    assert h["x"] < c["x"] < r["x"]                                              # history | chat | rail
    assert c["width"] <= 730                                                     # the text column is capped
    # Needs you: exactly one place, the chat header's badge, which opens the wall.
    badge = page.locator("[data-needs-badge]")
    expect(badge).to_be_visible()
    expect(badge.locator("[data-reminder-count]")).to_have_text("1")
    assert badge.get_attribute("href").endswith("/guild/build/bench")
    visible_needs = page.locator("text=/Needs you/").filter(visible=True)
    assert visible_needs.count() == 1
    expect(rail.locator("[data-floor-urgent]")).to_contain_text("#31 Blocked thing")     # the single most urgent action
    expect(rail.locator("[data-rail-quiet]")).to_be_hidden()
    # The status strip sits low, under the chat.
    assert _box(page, "[data-zone='status']")["y"] > _box(page, "[data-mc-composer]")["y"]
    # The rail collapses by hand, and that is remembered.
    page.click("[data-rail-toggle]")
    expect(rail).to_be_hidden()
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("#main")).to_be_hidden()
    page.click("[data-rail-toggle]")
    expect(page.locator("#main")).to_be_visible()
    assert not errors, errors
    ctx.close()


def test_slice1_narrower_screens_rail_toggle_and_history_drawer(browser, server, floor):
    ctx, page = _context(browser, server, 1100, 800)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("#main")).to_be_hidden()                                 # below 1200 px: a toggle
    expect(page.locator("[data-floor-history]")).to_be_visible()
    page.click("[data-rail-toggle]")
    expect(page.locator("#main")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#main")).to_be_hidden()
    ctx.close()
    ctx, page = _context(browser, server, 860, 800)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-floor-history]")).to_be_hidden()                  # below 900 px: a drawer
    page.click("[data-history-toggle]")
    expect(page.locator("[data-floor-history]")).to_be_visible()
    expect(page.locator("[data-history-toggle]")).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(page.locator("[data-floor-history]")).to_be_hidden()
    ctx.close()


def test_slice1_quiet_rail_when_nothing_needs_attention(browser, server, floor):
    write_queue(server["queue"], [i for i in QUEUE_ITEMS if i.get("status") != "blocked"])
    try:
        ctx, page = _context(browser, server, 1440, 900)
        go(page, f"{server['url']}/guild-next/guild/build")
        expect(page.locator("[data-needs-badge]")).to_be_hidden()                  # hidden at zero
        expect(page.locator("[data-rail-quiet]")).to_have_text("Nothing needs you right now · Open wall")
        expect(page.locator("[data-floor-urgent]")).to_be_hidden()
        expect(page.locator("[data-rail-continue]")).to_be_hidden()
        ctx.close()
    finally:
        write_queue(server["queue"], QUEUE_ITEMS)


def test_slice1_a_failed_answer_shows_one_line_above_the_composer_until_the_next_answer(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    replies = []
    page.route("**/api/v1/mc/turns", lambda route: route.fulfill(status=200, content_type="application/json",
                                                                 body=json.dumps(replies.pop(0))))
    go(page, f"{server['url']}/guild-next/guild/build")
    _mc_turns_on(page)
    blocker = page.locator("[data-mc-blocker]")
    expect(blocker).to_be_hidden()
    replies.append({"status": "unavailable", "failure_class": "key_refused", "mc_state": "unavailable",
                    "message": "Master Craftsman is unavailable · its model key was refused. Your note is kept.",
                    "mc_header": "Master Craftsman is unavailable · its model key was refused"})
    page.fill("#mc-input", "Are you there?")
    page.click("[data-mc-send]")
    expect(blocker).to_be_visible()
    expect(blocker).to_contain_text("Last answer failed · Master Craftsman is unavailable · its model key was refused")
    b, comp = _box(page, "[data-mc-blocker]"), _box(page, "[data-mc-composer]")
    assert b["y"] + b["height"] <= comp["y"] + 1                                 # directly above the composer
    replies.append({"status": "answered", "mc_state": "live", "message": "Master Craftsman answered",
                    "mc_header": "Master Craftsman is live · your messages are kept as notes",
                    "reply_note": {"id": 7001, "request_id": "mc-x", "author_kind": "agent", "author_label": "Master Craftsman",
                                   "who": "master_craftsman", "text": "Here.", "html": "<p>Here.</p>",
                                   "created_at": "2026-09-29T09:00:00+00:00", "context": {}}})
    page.fill("#mc-input", "Now?")
    page.click("[data-mc-send]")
    expect(page.locator('[data-note="7001"]')).to_contain_text("Here.")
    expect(blocker).to_be_hidden()
    assert not errors, errors
    ctx.close()


def test_slice1_phone_chat_first_with_the_composer_and_newest_note_in_view(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    # A stand-in visualViewport we can shrink, the way the on-screen keyboard does.
    ctx.add_init_script("""
      (() => {
        const vv = new EventTarget();
        let kb = 0;
        Object.defineProperty(vv, 'height', { get: () => window.innerHeight - kb });
        Object.defineProperty(vv, 'width', { get: () => window.innerWidth });
        vv.offsetTop = 0; vv.scale = 1;
        Object.defineProperty(window, 'visualViewport', { get: () => vv });
        window.__kb = (px) => { kb = px; vv.dispatchEvent(new Event('resize')); };
      })();
    """)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    for n in range(8):
        page.fill("#mc-input", f"phone note {n}")
        page.click("[data-mc-send]")
        expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(n + 1)
    hero, chat, context = _box(page, "[data-chat-hero]"), _box(page, "[data-mc-thread]"), _box(page, "[data-floor-context]")
    assert hero["y"] < chat["y"] < context["y"]                                   # hero header, chat, then context
    assert hero["height"] <= 90 and hero["width"] >= 380                          # compact, edge to edge
    expect(page.locator("[data-build-card], [data-build-strip]")).to_have_count(0)
    assert page.locator("[data-floor-context]").get_attribute("open") is None     # context folded below
    comp = _box(page, "[data-mc-composer]")
    assert comp["y"] + comp["height"] <= 844                                      # composer in the first view
    newest = page.locator('[data-mc-thread] [data-kind="note"]').last
    expect(newest).to_be_in_viewport()
    nb = newest.bounding_box()
    assert nb["y"] + nb["height"] <= comp["y"] + 2, (nb, comp)                      # not hidden under the composer
    # The keyboard opens: the composer stays above it.
    page.evaluate("window.__kb(300)")
    page.wait_for_function("document.body.dataset.keyboard === 'open'")
    comp = _box(page, "[data-mc-composer]")
    assert comp["y"] + comp["height"] <= 844 - 300 + 2, comp
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert not errors, errors
    ctx.close()


def test_slice1_the_wall_filters_and_carries_the_same_conversation(browser, server, floor):
    from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN
    floor.store().add_postit("Check the lock", MASTER_CRAFTSMAN, idempotency_key="wall-f-0001")
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build/bench")
    expect(page.locator("[data-bench] > [data-panel]")).to_have_count(6)
    page.click('[data-wall-filter="postits"]')
    expect(page.locator("[data-bench] > [data-panel]:visible")).to_have_count(1)
    expect(page.locator('[data-panel="postits"]')).to_be_visible()
    page.click('[data-wall-filter="needs"]')
    expect(page.locator('[data-panel="needs"]')).to_be_visible()
    expect(page.locator('[data-panel="postits"]')).to_be_hidden()
    page.click('[data-wall-filter="all"]')
    expect(page.locator("[data-bench] > [data-panel]:visible")).to_have_count(6)
    page.click("[data-mc-pill]")                                                  # MC docked/floating on the wall
    expect(page.locator("[data-mc-current]")).to_contain_text("Current conversation · Shop floor thread")
    ctx.close()
    phone, ppage = _context(browser, server, **PHONE)
    go(ppage, f"{server['url']}/guild-next/guild/build/bench")
    expect(ppage.locator('[data-panel="needs"]')).to_be_visible()                # the wall opens as one column
    boxes = [ppage.locator(f'[data-panel="{p}"]').bounding_box() for p in ("needs", "continue")]
    assert abs(boxes[0]["x"] - boxes[1]["x"]) < 2 and boxes[1]["y"] > boxes[0]["y"]
    phone.close()


def test_slice1_navigation_reaches_every_guild_page_and_the_truthful_labs_page(browser, server, fresh_queue):
    # Guild 1.1 slice 1 (spec §3): Chat · Board · Build Log · Rooms · More ▾.
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    nav = page.locator(".guild-subnav")
    expect(nav.locator("> a")).to_have_text(["Chat", "Board", "Build Log", "Rooms"])
    for label, where in (("Board", "/guild-next/guild/board"), ("Build Log", "/guild-next/guild/build/log"),
                         ("Rooms", "/guild-next/guild/rooms"), ("Chat", "/guild-next/guild/build")):
        with page.expect_navigation():
            nav.locator("> a", has_text=label).click()
        page.wait_for_selector("body[data-ready=true]")
        assert page.url.split("?")[0].endswith(where), (label, page.url)
        expect(page.locator(".guild-subnav > [aria-current='page']")).to_have_text(label)
        if label == "Rooms":
            expect(page.locator("[data-later]")).to_contain_text("Coming in a later slice")
    _more_menu(page)
    with page.expect_navigation():
        page.click("[data-more='design-studio']")
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("#planning-studio")).to_contain_text("planning-studio/")
    expect(page.locator(".page-meta")).to_contain_text("neither is served on dev yet")
    assert not errors, errors
    ctx.close()


# ── Slice 1, review fix round (#263 review F1-F8, and the coordinator's polish) ──

@pytest.mark.parametrize("width,height", [(390, 844), (860, 800)], ids=["phone", "tablet"])
def test_fix_the_history_drawer_closes_by_button_and_by_tapping_outside(browser, server, floor, width, height):
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 640, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    drawer, toggle = page.locator("[data-floor-history]"), page.locator("[data-history-toggle]")
    toggle.click()
    expect(drawer).to_be_visible()
    expect(page.locator("[data-history-close]")).to_be_focused()                  # focus moves into the drawer
    tb = toggle.bounding_box()
    hit = page.evaluate(f"document.elementFromPoint({tb['x'] + tb['width'] / 2}, {tb['y'] + tb['height'] / 2}).closest('[data-history-toggle]') !== null")
    assert hit, "the drawer (or its scrim) covers the ☰ button"
    page.locator("[data-history-close]").click()                                  # a close button
    expect(drawer).to_be_hidden()
    expect(toggle).to_be_focused()                                                # focus goes back to ☰
    toggle.click()
    expect(drawer).to_be_visible()
    page.mouse.click(width - 10, height - 200)                                    # a tap outside, on the scrim
    expect(drawer).to_be_hidden()
    expect(page.locator("[data-floor-scrim]")).to_be_hidden()
    ctx.close()


def test_fix_a_failed_poll_never_leaves_a_live_looking_zero_or_the_quiet_line(browser, server, floor):
    write_queue(server["queue"], [i for i in QUEUE_ITEMS if i.get("status") != "blocked"])
    try:
        ctx, page = _context(browser, server, 1440, 900)
        go(page, f"{server['url']}/guild-next/guild/build")
        badge, quiet = page.locator("[data-needs-badge]"), page.locator("[data-rail-quiet]")
        expect(badge).to_be_hidden()
        expect(quiet).to_be_visible()
        page.route(FLOOR_API, lambda route: route.fulfill(status=503, content_type="application/json",
                                                            body='{"error": "unavailable", "message": "down"}'))
        poll(page)
        expect(page.locator("[data-stale-banner]")).to_be_visible()
        expect(badge).to_be_visible()                                              # a stale badge, not a live zero
        expect(badge.locator("[data-reminder-count]")).to_have_text("?")
        expect(badge).to_have_attribute("data-fresh", "stale")
        assert page.evaluate("getComputedStyle(document.querySelector('[data-needs-badge]')).borderTopStyle") == "dashed"
        expect(quiet).to_be_hidden()                                               # no "right now" on a failed read
        expect(page.locator("[data-rail-wall]")).to_be_visible()
        page.unroute(FLOOR_API)
        poll(page)
        expect(badge).to_be_hidden()
        expect(quiet).to_be_visible()
        ctx.close()
    finally:
        write_queue(server["queue"], QUEUE_ITEMS)


def test_fix_the_history_count_follows_what_is_kept(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    count = page.locator("[data-fh-count]")
    expect(count).to_have_text("0 kept")
    page.fill("#mc-input", "one")
    page.click("[data-mc-send]")
    expect(count).to_have_text("1 kept")
    page.fill("#mc-input", "two")
    page.click("[data-mc-send]")
    expect(count).to_have_text("2 kept")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-fh-count]")).to_have_text("2 kept")
    ctx.close()


def test_fix_the_blocker_line_follows_the_live_server_state(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    live = page.request.get(f"{server['url']}/guild-next/api/v1/floor").json()
    states = [dict(live, blockers=[{"kind": "mc_down", "text": "Master Craftsman is unavailable · not answering"}], observed_at="2026-09-29T10:00:01+00:00"),
              dict(live, blockers=[], observed_at="2026-09-29T10:00:02+00:00")]
    page.route(FLOOR_API, lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(states.pop(0))))
    blocker = page.locator("[data-mc-blocker]")
    poll(page)
    expect(blocker).to_have_text("Master Craftsman is unavailable · not answering")
    poll(page)
    expect(blocker).to_be_hidden()                                                  # MC recovered
    page.unroute(FLOOR_API)
    page.route("**/api/v1/mc/turns", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "status": "timeout_uncertain", "failure_class": "deadline", "mc_state": "unavailable",
        "message": "No answer within the deadline", "mc_header": "No answer within the deadline"})))
    _mc_turns_on(page)
    page.fill("#mc-input", "still there?")
    page.click("[data-mc-send]")
    expect(blocker).to_have_text("Last answer failed · No answer within the deadline")   # not the old "MC down"
    ctx.close()


def test_fix_the_rail_toggle_moves_focus_in_and_back(browser, server, floor):
    ctx, page = _context(browser, server, 1100, 800)
    go(page, f"{server['url']}/guild-next/guild/build")
    toggle = page.locator("[data-rail-toggle]")
    toggle.click()
    expect(page.locator("#main")).to_be_visible()
    assert page.evaluate("document.querySelector('#main').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(page.locator("#main")).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.click()
    page.mouse.click(40, 700)                                                       # a tap outside closes the overlay too
    expect(page.locator("#main")).to_be_hidden()
    ctx.close()


def test_fix_rotating_a_phone_to_landscape_keeps_the_context_reachable(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    assert page.locator("[data-floor-context]").get_attribute("open") is None
    page.set_viewport_size({"width": 844, "height": 390})
    expect(page.locator("[data-floor-context]")).to_have_attribute("open", "")
    page.click("[data-rail-toggle]")
    expect(page.locator("[data-floor-urgent]")).to_be_visible()
    ctx.close()


def test_fix_polish_bubbles_folded_help_and_no_platform_card_in_the_chat(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-mc-thread] [data-briefing]")).to_have_count(0)       # the chat holds only conversation
    expect(page.locator("#main [data-briefing-text]")).to_contain_text("needs you")
    lines = page.locator("[data-mc-off-lines]")
    expect(lines).to_be_hidden()
    expect(page.locator("[data-mc-record]")).to_be_visible()
    expect(page.locator("[data-mc-invite]")).to_be_visible()
    page.click("[data-mc-info]")
    expect(lines).to_be_visible()
    expect(page.locator("[data-mc-info]")).to_have_attribute("aria-expanded", "true")
    page.click("[data-mc-info]")
    expect(lines).to_be_hidden()
    page.route("**/api/v1/mc/turns", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({
        "status": "answered", "mc_state": "live", "message": "Master Craftsman answered",
        "mc_header": "Master Craftsman is live · your messages are kept as notes",
        "reply_note": {"id": 7101, "request_id": "mc-b", "author_kind": "agent", "author_label": "Master Craftsman",
                       "who": "master_craftsman", "text": "Left side.", "html": "<p>Left side.</p>",
                       "created_at": "2026-09-29T09:00:00+00:00", "context": {}}})))
    _mc_turns_on(page)
    page.fill("#mc-input", "Right side?")
    page.click("[data-mc-send]")
    mine = page.locator('[data-mc-thread] [data-author-kind="owner"]').last
    theirs = page.locator('[data-note="7101"]')
    expect(theirs).to_be_visible()
    t, m, a = _box(page, "[data-mc-thread]"), mine.bounding_box(), theirs.bounding_box()
    assert abs((m["x"] + m["width"]) - (t["x"] + t["width"])) < 12 and m["x"] > t["x"] + 40     # mine: right
    assert abs(a["x"] - t["x"]) < 4                                                              # theirs: left
    assert m["width"] < t["width"] * 0.85
    assert page.evaluate("getComputedStyle(document.querySelector('[data-author-kind=owner]')).backgroundColor") != \
        page.evaluate("getComputedStyle(document.querySelector('[data-note=\"7101\"]')).backgroundColor")
    assert not errors, errors
    ctx.close()


def test_fix_phone_the_type_bar_covers_neither_the_composer_nor_the_newest_note(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    for n in range(6):
        page.fill("#mc-input", f"note {n}")
        page.click("[data-mc-send]")
        expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(n + 1)
    page.wait_for_timeout(200)
    # On the floor the Type bar sits inside the composer's block, under the form: it covers neither.
    typebar, form, dock = _box(page, ".mc-phone-bar"), _box(page, "[data-mc-composer]"), _box(page, "[data-mc-dock-bottom]")
    assert form["y"] + form["height"] <= typebar["y"] + 1, (form, typebar)
    assert typebar["y"] + typebar["height"] <= 844 + 1
    newest = page.locator('[data-mc-thread] [data-kind="note"]').last.bounding_box()
    assert newest["y"] + newest["height"] <= dock["y"] + 2
    signout = page.locator(".portal-nav-signout")
    if signout.count() and signout.is_visible():
        assert signout.bounding_box()["height"] < 24                                 # "Sign out" on one line
    assert page.evaluate("getComputedStyle(document.querySelector('.guild-subnav')).overflowX") == "auto"
    ctx.close()


# ── Guild 1.1 dev, slice 2: conversations ─────────────────────────────────────

def _menu(page, cid, action, navigates=True):
    row = page.locator(f'[data-conv="{cid}"]')
    row.locator("[data-conv-menu]").click()
    if not navigates:
        row.locator(f'[data-conv-act="{action}"]').click()
        return
    with page.expect_navigation():
        row.locator(f'[data-conv-act="{action}"]').click()
    page.wait_for_selector("body[data-ready=true]")


def _nav_click(page, selector):
    with page.expect_navigation():
        page.click(selector)
    page.wait_for_selector("body[data-ready=true]")


def test_slice2_conversations_new_rename_pin_remove_archive_restore(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    rows = page.locator("[data-conv-list] [data-conv]")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Shop floor thread")
    page.fill("#mc-input", "A note on the old thread")
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(1)
    # New: a fresh, empty conversation opens.
    _nav_click(page, "[data-conv-new]")
    assert "?c=c-" in page.url
    new_id = page.url.split("?c=")[1]
    expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(0)
    expect(page.locator("[data-conv-current-title]")).to_have_text("New conversation")
    page.fill("#mc-input", "Plan the **Rooms** review")
    page.click("[data-mc-send]")
    expect(page.locator("[data-conv-current-title]")).to_have_text("Plan the Rooms review")    # titled by the first message
    expect(page.locator(f'[data-conv="{new_id}"] [data-conv-title]')).to_have_text("Plan the Rooms review")
    # Rename.
    _menu(page, new_id, "rename", navigates=False)
    field = page.locator("[data-conv-rename]")
    field.fill("Rooms review")
    with page.expect_navigation():
        field.press("Enter")
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator(f'[data-conv="{new_id}"] [data-conv-title]')).to_have_text("Rooms review")
    # Pin the old thread: it goes on top.
    _menu(page, "shop-floor-thread", "pin")
    expect(rows.first).to_have_attribute("data-conv", "shop-floor-thread")
    expect(rows.first).to_have_attribute("data-pinned", "true")
    # Switching shows only that conversation's notes.
    _nav_click(page, '[data-conv="shop-floor-thread"] [data-conv-link]')
    expect(page.locator("[data-mc-thread]")).to_contain_text("A note on the old thread")
    expect(page.locator("[data-mc-thread]")).not_to_contain_text("Rooms")
    # Remove from list, then find it in the Archive and restore it.
    _menu(page, new_id, "archive")
    expect(page.locator(f'[data-conv="{new_id}"]')).to_have_count(0)
    _nav_click(page, '[data-conv-view="archived"]')
    expect(page.locator(f'[data-conv="{new_id}"]')).to_have_count(1)
    _menu(page, new_id, "restore")
    assert f"?c={new_id}" in page.url
    expect(page.locator("[data-mc-thread]")).to_contain_text("Plan the Rooms review")    # nothing was erased
    assert not errors, errors
    ctx.close()


def test_slice2_phone_the_drawer_holds_the_same_actions(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/build")
    page.click("[data-history-toggle]")
    drawer = page.locator("[data-floor-history]")
    expect(drawer.locator("[data-conv-new]")).to_be_visible()
    _nav_click(page, "[data-floor-history] [data-conv-new]")
    cid = page.url.split("?c=")[1]
    page.click("[data-history-toggle]")
    _menu(page, cid, "pin")
    page.click("[data-history-toggle]")
    expect(page.locator("[data-conv-list] [data-conv]").first).to_have_attribute("data-conv", cid)
    menu_btn = page.locator(f'[data-conv="{cid}"] [data-conv-menu]')
    assert menu_btn.bounding_box()["width"] >= 28 and menu_btn.bounding_box()["height"] >= 28
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()


# ── Slice 4a: the local Workshop (read only, model free) ─────────────────────

SHOTS = os.environ.get("GUILD_SHOTS")     # a folder: save the Workshop screenshots there


@pytest.fixture
def workshop(server, floor, tmp_path):
    from minimoi_portal.workshop.observer import Observation, health_event
    from minimoi_portal.workshop.record import Workshop, iso, now
    saved = {k: os.environ.get(k) for k in ("MINIMOI_WORKSHOPS_DIR", "MINIMOI_WORKSHOP_ID")}
    os.environ["MINIMOI_WORKSHOPS_DIR"] = str(tmp_path / "workshops")
    os.environ["MINIMOI_WORKSHOP_ID"] = "mac"
    w = Workshop(str(tmp_path / "workshops"), "mac")
    obs = Observation(observed_at=iso(now()), memory_free_pct=46.0, swap_used_gb=1.2, disk_free_gb=80.0, load_1m=2.1,
                      clients=[{"kind": "codex", "pid": 11, "elapsed": "05:00", "counted": True},
                               {"kind": "codex-desktop", "pid": 12, "elapsed": "09:00", "counted": False}],
                      clients_known=True)
    w.append(health_event(obs, "mac"))
    w.append({"workshop": "mac", "actor": "claude-code", "kind": "started", "item": "queue:12", "stage": "build",
              "text": "Slice 4a: tests and screenshots", "next_actor": "codex"})
    w.append({"workshop": "mac", "actor": "claude-code", "kind": "needs_you", "item": "queue:12",
              "text": "Choose the refresh cadence", "next_actor": "robert"})
    w.append({"workshop": "mac", "actor": "claude-code", "kind": "next", "item": "spec:streaming",
              "text": "Streaming S1 after the reviews"})
    yield {"workshop": w, "Observation": Observation, "health_event": health_event, "iso": iso, "now": now}
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _visible_order(page, selectors):
    return [page.locator(s).first.bounding_box()["y"] for s in selectors]


def test_slice4a_the_workshop_opens_from_a_queue_item_and_refreshes_honestly(browser, server, workshop):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    model_calls = _requests(page, "/mc/turns")
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    with page.expect_navigation():
        page.click("[data-open-workshop]")
    page.wait_for_selector("body[data-ready=true]")
    assert "/guild-next/guild/workshop?item=12" in page.url
    expect(page.locator(".guild-subnav [data-more='workshop']")).to_have_attribute("aria-current", "page")
    expect(page.locator("[data-ws-admission]")).to_have_attribute("data-verdict", "tight")
    expect(page.locator("[data-ws-runs]")).to_contain_text("1 agent session running on this Mac (outside Docker)")
    expect(page.locator("[data-ws-background]")).to_contain_text("Codex desktop (ChatGPT app)")
    expect(page.locator("[data-ws-limits]")).to_contain_text("agent sessions tight at 1, blocked at 3")
    expect(page.locator("[data-ws-recovery-list]")).to_contain_text("prefer one at a time")
    expect(page.locator("[data-ws-next]")).to_contain_text("robert")
    expect(page.locator("[data-ws-needs]")).to_contain_text("Choose the refresh cadence")
    expect(page.locator("[data-ws-scope]")).to_contain_text("Approved for build")
    expect(page.locator("[data-ws-more]")).to_have_attribute("open", "")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/1-desktop-workshop-item-12.png", full_page=True)
    # A check-in: the host is quiet now; the refresh shows it (files only).
    w = workshop
    w["workshop"].append(w["health_event"](w["Observation"](observed_at=w["iso"](w["now"]()), memory_free_pct=50.0,
                                                           swap_used_gb=1.0, disk_free_gb=80.0, clients=[],
                                                           clients_known=True), "mac"))
    with page.expect_response(lambda r: "/api/v1/workshop" in r.url):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("[data-ws-admission]")).to_have_attribute("data-verdict", "ok")
    expect(page.locator("[data-ws-runs]")).to_have_text("No agent session running on this Mac (outside Docker)")
    expect(page.locator("[data-ws-background]")).to_have_text("")
    expect(page.locator("[data-ws-recovery-list] li")).to_have_count(0)            # ok: nothing to recover
    # A failed refresh is unknown, never "nothing running".
    page.route("**/api/v1/workshop*", lambda route: route.fulfill(status=503, body="{}", content_type="application/json"))
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("[data-ws-admission]")).to_have_attribute("data-verdict", "unknown")
    expect(page.locator("[data-ws-verdict]")).to_have_text("Host unknown")
    expect(page.locator("[data-ws-runs]")).to_have_text("Running agents unknown (refresh failed)")
    expect(page.locator("[data-ws-age]")).to_contain_text("last good read")
    expect(page.locator("[data-ws-age]")).not_to_contain_text("just now")
    expect(page.locator("[data-ws-headroom]")).to_have_attribute("data-stale", "true")
    expect(page.locator("[data-ws-headroom]")).to_contain_text("(last good read")
    expect(page.locator("[data-ws-recovery-list]")).to_have_text(re.compile("^The refresh failed: reload the page"))
    expect(page.locator("[data-ws-refreshed]")).to_contain_text("(last good read; the refresh failed)")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/2-desktop-refresh-failed-unknown.png")
    assert not model_calls, model_calls
    assert not [e for e in errors if "status of 503" not in e], errors      # the 503 is the one we injected
    ctx.close()


def test_slice4a_phone_shows_now_needs_you_budget_then_the_chat(browser, server, workshop):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    errors = _errors(page)
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/workshop?item=12")
    expect(page.locator(".phone-summary")).to_have_count(0)                  # the Workshop leads with Now
    ys = _visible_order(page, ["[data-ws-now]", "[data-ws-needs]", "[data-ws-budget]", "[data-ws-more]", ".mc-panel"])
    assert ys == sorted(ys), ys
    expect(page.locator("[data-ws-more]")).not_to_have_attribute("open", "")   # scope, queue and recovery folded
    expect(page.locator("[data-ws-scope]")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/3-phone-workshop-first-screen.png")
        page.screenshot(path=f"{SHOTS}/4-phone-workshop-full.png", full_page=True)
    page.click("[data-ws-more] summary")
    expect(page.locator("[data-ws-scope]")).to_be_visible()
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
    go(page, f"{server['url']}/guild-next/guild/build/bench")          # slice 1: post-its live on the wall
    add = page.locator("[data-postits] [data-postit-add]").first
    add.locator("[data-postit-input]").fill("a post-it while unknown")
    add.locator("[data-postit-add-btn]").click()
    expect(page.locator("[data-postits]").first).to_contain_text(UNKNOWN_TEXT)
    page.wait_for_timeout(300)
    assert sent == [] and floor.count("floor_messages") == 0 and floor.count("floor_postits") == 0
    go(page, f"{server['url']}/guild-next/guild/build")
    page.fill("[data-mc-input]", "a note while unknown")
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


def test_242_repairing_a_damaged_list_keeps_the_valid_entries(browser, server, floor):
    ctx, first = _context(browser, server)
    go(first, f"{server['url']}/guild-next/guild/build")
    first.evaluate(f"""localStorage.setItem('{OFF_TABS}', JSON.stringify({{
        tother: new Date().toISOString(), tbad: 'not a time' }}))""")
    first.click("[data-mc-record]")                                    # this tab goes off: it writes the list
    tabs = json.loads(first.evaluate(f"localStorage.getItem('{OFF_TABS}')"))
    assert "tother" in tabs and "tbad" not in tabs and len(tabs) == 2  # the other tab's entry survived the repair
    ctx.close()


# ── Streaming S1: Master Craftsman streams on the Shop floor ──────────────────
# The real OpenClaw adapter over a scripted relay (test_mc_streaming.StreamRuntime):
# no network, no model call.

@pytest.fixture
def streaming_mc(server, floor):
    from minimoi_portal.guild_ui.mc import CachedHealth
    from test_mc_streaming import StreamRuntime
    from test_mc_turns import _openclaw
    services = server["app"].extensions["guild_ui_next"]["services"]
    saved = (services.mc, services.mc_health, services.mc_turns, services.mc_stream)
    runtime = StreamRuntime()
    backend = _openclaw(runtime)
    services.mc, services.mc_health, services.mc_turns, services.mc_stream = backend, CachedHealth(backend), True, True
    from minimoi_portal.guild_ui.api import _MC_INFLIGHT
    from minimoi_portal.guild_ui.mc.streaming import DISPATCHED
    DISPATCHED._items.clear()
    _MC_INFLIGHT.clear()
    yield {"runtime": runtime, "services": services}
    runtime.gate.set()
    services.mc, services.mc_health, services.mc_turns, services.mc_stream = saved


def _script(*items):
    from test_mc_streaming import nd
    out = []
    for item in items:
        if isinstance(item, (int, float)):
            out.append(("sleep", item))
        elif isinstance(item, dict) or item == "WAIT":
            out.append(nd(item) if isinstance(item, dict) else item)
        else:
            out.append(nd({"t": "delta", "text": item}))
    return out


FINISH = [{"t": "finish", "reason": "stop"}, {"t": "usage", "prompt_tokens": 50, "completion_tokens": 7}]


def _send(page, server, text="Where do things stand?"):
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("body")).to_have_attribute("data-mc-stream", "true")
    page.fill("#mc-input", text)
    page.click("[data-mc-send]")
    return page.locator("[data-mc-thread] [data-mc-stream-line]")


def test_s1_a_streamed_reply_works_writes_and_ends_with_its_footer(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script(0.4, "## Plan\n", 0.5, "- **one**\n", 0.8, "- two", 0.3, *FINISH)
    ctx, page = _context(browser, server)
    errors = _errors(page)
    line = _send(page, server)
    expect(line).to_have_count(1)
    expect(line.locator('[data-slot="state"]')).to_have_text("Working…")
    expect(line).to_have_attribute("aria-busy", "true")
    expect(line).to_contain_text("one")                                   # the text grows in place
    expect(line.locator("[data-mc-stop]")).to_be_visible()
    expect(line.locator("h2")).to_have_text("Plan")                       # the server's safe render, while writing
    reply = page.locator('[data-mc-thread] [data-kind="note"]').last
    expect(reply).to_contain_text("two", timeout=8000)
    expect(line).to_have_count(0)                                         # replaced by done's kept text
    expect(reply.locator(".msg-md h2")).to_have_text("Plan")
    expect(reply.locator(".msg-md strong")).to_have_text("one")
    expect(reply.locator("[data-turn-foot]")).to_contain_text("Done in")
    expect(reply.locator("[data-turn-foot]")).to_contain_text("7 output tokens")
    assert reply.inner_text().count("two") == 1
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/s1-desktop-streamed-reply.png")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    again = page.locator('[data-mc-thread] [data-kind="note"]').last
    expect(again.locator(".msg-md h2")).to_have_text("Plan")              # kept: it is there after a reload
    assert not errors, errors
    ctx.close()


def test_s1_stop_ends_it_and_says_what_it_cannot_save(browser, server, streaming_mc):
    runtime = streaming_mc["runtime"]
    runtime.script = _script("A partial ", "WAIT", "answer.", *FINISH)
    ctx, page = _context(browser, server)
    errors = _errors(page)
    line = _send(page, server)
    expect(line).to_contain_text("A partial")
    expect(line.locator('[data-slot="stopnote"]')).to_contain_text("may still be billed")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/s1-desktop-writing-with-stop.png")
    line.locator("[data-mc-stop]").click()
    expect(line.locator('[data-slot="state"]')).to_have_text("Stopped")
    expect(line).to_contain_text("A partial")                             # shown once, never kept
    expect(line).not_to_have_attribute("aria-busy", "true")
    platform = page.locator('[data-mc-thread] [data-kind="platform"]').last
    expect(platform).to_contain_text("you may still be billed")
    assert any(p["url"].endswith("/turns/stop") for p in runtime.posts)
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    assert "A partial" not in page.locator("[data-mc-thread]").inner_text()
    assert not errors, errors
    ctx.close()


def test_s1_a_failure_mid_stream_is_interrupted_and_honest(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script("Half an ", 0.3, {"t": "error", "class": "idle"})
    ctx, page = _context(browser, server)
    line = _send(page, server)
    expect(line.locator('[data-slot="state"]')).to_have_text("(interrupted)")
    expect(line).to_contain_text("Half an")
    expect(page.locator('[data-mc-thread] [data-kind="platform"]').last).to_contain_text("it may still have run")
    ctx.close()


def test_s1_off_the_record_mid_stream_still_keeps_the_answer_and_says_so(browser, server, streaming_mc):
    runtime = streaming_mc["runtime"]
    runtime.script = _script("Sent ", "WAIT", "on the record.", *FINISH)
    ctx, page = _context(browser, server)
    line = _send(page, server)
    expect(line).to_contain_text("Sent")
    page.click("[data-mc-record]")                                       # off the record, mid-stream
    runtime.gate.set()
    reply = page.locator('[data-mc-thread] [data-kind="note"]').last
    expect(reply).to_contain_text("on the record.")
    expect(page.locator('[data-mc-thread] [data-kind="platform"]').last).to_contain_text(
        "asked for on the record, so it is kept")
    ctx.close()


def test_s1_the_page_falls_back_only_on_a_declared_refusal(browser, server, streaming_mc):
    services = streaming_mc["services"]
    ctx, page = _context(browser, server)
    bodies = []
    page.on("request", lambda r: bodies.append((r.url.rsplit("/api/v1", 1)[-1], r.post_data))
            if "/api/v1/mc/turns" in r.url else None)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("body")).to_have_attribute("data-mc-stream", "true")
    services.mc_stream = False                                            # switched off after the page loaded
    page.fill("#mc-input", "Fall back, please")
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-kind="note"]').last).to_contain_text("ok")
    assert [b[0] for b in bodies] == ["/mc/turns/stream", "/mc/turns"]
    first, second = (json.loads(b[1]) for b in bodies)
    assert first["request_id"] == second["request_id"]                   # the same turn: nothing was sent twice
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("body")).to_have_attribute("data-mc-stream", "false")
    ctx.close()


def test_s1_unsafe_markdown_never_executes_while_streaming_or_after(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script(
        "Look: <img src=x onerror=\"window.__pwned=1\"> ", 0.3, "[click](javascript:window.__pwned=2) ", 0.3,
        "<script>window.__pwned=3</script> **safe**", 0.3, *FINISH)
    ctx, page = _context(browser, server)
    line = _send(page, server)
    expect(line).to_contain_text("Look:")
    reply = page.locator('[data-mc-thread] [data-kind="note"]').last
    expect(reply.locator(".msg-md strong")).to_have_text("safe", timeout=8000)
    thread = page.locator("[data-mc-thread]")
    assert thread.locator("img").count() == 0 and thread.locator("script").count() == 0
    assert all("javascript" not in (a.get_attribute("href") or "") for a in thread.locator("a").all())
    assert page.evaluate("window.__pwned === undefined")
    ctx.close()


def test_s1_reduced_motion_has_no_animation_and_the_text_still_appears(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script("Calm ", 0.4, "text.", 0.2, *FINISH)
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    line = _send(page, server)
    expect(line).to_contain_text("Calm")
    assert line.evaluate("el => getComputedStyle(el).animationName") == "none"
    expect(page.locator('[data-mc-thread] [data-kind="note"]').last).to_contain_text("Calm text.")
    ctx.close()


def test_s1_phone_shows_the_stream_and_stop(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script("A reply ", "WAIT", "on a phone.", *FINISH)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    line = _send(page, server)
    expect(line.locator("[data-mc-stop]")).to_be_visible()
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/s1-phone-writing-with-stop.png")
    streaming_mc["runtime"].gate.set()
    expect(page.locator('[data-mc-thread] [data-kind="note"]').last).to_contain_text("on a phone.")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()


# ── Guild 1.1 slice 1 (spec §3): the Guild home, the Guild bar, the Chat hero ─

DOORS = [("Build", "/guild-next/guild/build/log"), ("Operate", "/guild-next/guild/operate"),
         ("Improve", "/guild/improve"), ("Experiment", "/guild-next/guild/labs")]


def _no_page_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def _hero_in_header(page):
    """The Chat header carries the hero: photo, portrait, name, state, above the thread."""
    hero = page.locator("[data-chat-hero]")
    expect(hero).to_be_visible()
    expect(hero.locator(".hero-name")).to_contain_text("Master Craftsman")
    expect(hero.locator("[data-conv-current-title]")).to_be_visible()
    expect(hero.locator("[data-mc-header]")).to_contain_text("Master Craftsman")
    expect(hero.locator(".hero-portrait")).to_be_visible()
    assert "guild-chat-hero.jpg" in hero.evaluate("e => getComputedStyle(e).backgroundImage")
    assert "guild-mc-portrait.jpg" in page.locator(".hero-portrait").evaluate("e => getComputedStyle(e).backgroundImage")
    # Both images really load (same origin, allowed by the CSP).
    for name in ("guild-chat-hero.jpg", "guild-mc-portrait.jpg"):
        assert page.evaluate("""(n) => new Promise((ok) => { const i = new Image(); i.onload = () => ok(i.naturalWidth > 0);
                                i.onerror = () => ok(false); i.src = '/static/guild/' + n; })""", name), name
    hb, tb = hero.bounding_box(), page.locator("[data-mc-thread]").bounding_box()
    assert hb["y"] + hb["height"] <= tb["y"] + 1                                  # the header sits above the thread
    expect(page.locator("[data-hero-choice], [data-hero-option], .hero-opt")).to_have_count(0)   # no chooser


def _more_menu(page):
    """More ▾ opens a menu with the Workshop, Guild home, Docs, Operate, Labs and Design Studio."""
    menu = page.locator("[data-subnav-menu]")
    expect(menu).to_be_hidden()
    page.click("[data-subnav-more] > summary")
    expect(menu).to_be_visible()
    for key in ("workshop", "home", "docs", "operate", "labs", "design-studio"):
        expect(menu.locator(f"[data-more='{key}']")).to_be_visible()
    expect(menu.locator("[data-more='docs'] a")).to_have_attribute(
        "href", "https://github.com/robertvanstedum/personal-ai-agents/tree/main/docs")
    box, vw = menu.bounding_box(), page.viewport_size["width"]
    assert box["x"] >= 0 and box["x"] + box["width"] <= vw + 1, box              # the whole menu is on screen
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    page.click("[data-subnav-more] > summary")
    expect(menu).to_be_visible()
    page.mouse.click(5, page.viewport_size["height"] - 5)                          # a click outside closes it
    expect(menu).to_be_hidden()
    page.click("[data-subnav-more] > summary")
    expect(menu).to_be_visible()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_s1_the_guild_home_is_paired_with_curator(browser, server, fresh_queue, width, height):
    ctx, page = _context(browser, server, width, height)
    errors = _errors(page)
    page.goto(f"{server['url']}/guild-next/")
    page.wait_for_load_state("load")
    assert page.url.rstrip("/").endswith("/guild-next")                          # no redirect to the floor
    expect(page.locator(".gl-title")).to_have_text("Guild")
    drawers = page.locator(".gl-drawer")
    expect(drawers).to_have_count(4)
    expect(page.locator(".gl-tab")).to_have_text([d[0] for d in DOORS])
    for i, (_label, href) in enumerate(DOORS):
        assert drawers.nth(i).get_attribute("href") == href
    # Every painting loads, in Curator's 210:136 rectangle (no squares).
    for i in range(4):
        img = drawers.nth(i).locator("img")
        page.wait_for_function("(e) => e.complete && e.naturalWidth > 0", arg=img.element_handle())
        b = img.bounding_box()
        assert abs(b["width"] / b["height"] - 210 / 136) < 0.03, b
    boxes = [drawers.nth(i).bounding_box() for i in range(4)]
    if width >= 1200:
        assert len({round(b["y"]) for b in boxes}) == 1                          # one row of four
        row = boxes[-1]["x"] + boxes[-1]["width"] - boxes[0]["x"]
        assert 1000 <= row <= 1140, row                                          # about 1,100 px at 1440
    else:
        assert len({round(b["x"]) for b in boxes}) == 1                          # one column on a phone
        assert all(boxes[i + 1]["y"] > boxes[i]["y"] for i in range(3))
        assert boxes[0]["width"] >= width - 40
    expect(page.locator("text=/tunnel/i")).to_have_count(0)
    expect(page.locator(".guild-subnav > a")).to_have_text(["Chat", "Board", "Build Log", "Rooms"])
    assert _no_page_overflow(page)
    _more_menu(page)
    expect(page.locator("[data-more='home']")).to_have_attribute("aria-current", "page")
    page.keyboard.press("Escape")
    with page.expect_navigation():
        drawers.nth(0).click()                                                    # Build → the Build Log (slice 2)
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild-next/guild/build/log")
    assert not errors, errors
    ctx.close()


def test_s1_the_guild_home_goes_two_up_at_tablet_width(browser, server, fresh_queue):
    ctx, page = _context(browser, server, 860, 900)
    page.goto(f"{server['url']}/guild-next/")
    boxes = [page.locator(".gl-drawer").nth(i).bounding_box() for i in range(4)]
    assert round(boxes[0]["y"]) == round(boxes[1]["y"]) < round(boxes[2]["y"]) == round(boxes[3]["y"])
    assert _no_page_overflow(page)
    ctx.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_s1_the_guild_bar_and_no_page_overflow_anywhere(browser, server, floor, width, height):
    ctx, page = _context(browser, server, width, height)
    errors = _errors(page)
    for path in ("/guild-next/", "/guild-next/guild/build", "/guild-next/guild/build/bench",
                 "/guild-next/guild/build/log", "/guild-next/guild/rooms", "/guild-next/guild/build/queue",
                 "/guild-next/guild/workshop", "/guild-next/guild/operate", "/guild-next/guild/labs"):
        page.goto(f"{server['url']}{path}")
        page.wait_for_load_state("load")
        tabs = page.locator(".guild-subnav > a")
        expect(tabs).to_have_text(["Chat", "Board", "Build Log", "Rooms"])
        for i in range(4):
            expect(tabs.nth(i)).to_be_in_viewport()                               # all four tabs fit, phone included
        expect(page.locator("[data-subnav-more] > summary")).to_be_in_viewport()
        assert _no_page_overflow(page), path
    page.goto(f"{server['url']}/guild-next/guild/build")
    page.wait_for_selector("body[data-ready=true]")
    _more_menu(page)
    assert not errors, errors
    ctx.close()


def test_s1_phone_chat_has_the_hero_in_its_header(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _hero_in_header(page)
    hero = _box(page, "[data-chat-hero]")
    assert hero["y"] < 140 and hero["height"] <= 90                                # right under the bars, compact
    expect(page.locator("[data-history-toggle]")).to_be_visible()                 # ☰ still in the header
    comp = _box(page, "[data-mc-composer]")
    assert comp["y"] + comp["height"] <= 844
    assert _no_page_overflow(page)
    assert not errors, errors
    ctx.close()


def _select_in(page, selector):
    page.evaluate("""(sel) => { const n = document.querySelector(sel); const r = document.createRange();
                    r.selectNodeContents(n); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
                    document.dispatchEvent(new Event('selectionchange')); }""", selector)


def test_s1_selection_actions_on_the_record_only_and_rooms_disabled(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    posts = _requests(page, "/api/v1/postits")
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(_hero_chip(page)).to_have_text("On the record")
    page.fill("#mc-input", "Check the queue lock")
    page.click("[data-mc-send]")
    note = page.locator('[data-mc-thread] [data-kind="note"][data-note]').last
    expect(note).to_contain_text("Check the queue lock")
    bar = page.locator("[data-sel-bar]")
    expect(bar).to_be_hidden()
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note]:last-child .msg-text')
    expect(bar).to_be_visible()
    expect(bar.locator("[data-sel-room]")).to_be_disabled()                        # Rooms come in slice 4
    bar.locator("[data-sel-pin]").click()
    expect(bar.locator("[data-sel-result]")).to_have_text("Pinned to the Board.")
    assert [m for m, _u in posts if m == "POST"] == ["POST"]
    board = [p["text"] for p in floor.store().list_postits().data["postits"]]
    assert board == ["Check the queue lock"], board
    # Clearing the selection hides the bar.
    page.evaluate("window.getSelection().removeAllRanges()")
    page.wait_for_timeout(500)
    expect(bar).to_be_hidden()
    # Off the record: the selection is cleared and the bar never shows.
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note]:last-child .msg-text')
    expect(bar).to_be_visible()
    page.click("[data-mc-record]")
    expect(bar).to_be_hidden()
    assert page.evaluate("window.getSelection().toString()") == ""
    expect(_hero_chip(page)).to_have_text("Off the record · not kept")
    assert "grayscale" in page.locator("[data-chat-hero]").evaluate("e => getComputedStyle(e).filter")
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note] .msg-text')
    page.wait_for_timeout(300)
    expect(bar).to_be_hidden()
    assert [m for m, _u in posts if m == "POST"] == ["POST"]                       # nothing more was sent
    page.click("[data-mc-record]")                                                 # back on the record
    expect(_hero_chip(page)).to_have_text("On the record")
    assert not errors, errors
    ctx.close()


def _hero_chip(page):
    return page.locator("[data-record-chip]")


def test_s1_a_selection_longer_than_a_post_it_is_refused_not_cut(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    posts = _requests(page, "/api/v1/postits")
    go(page, f"{server['url']}/guild-next/guild/build")
    long_text = "x" * 30 + " " + "y" * 300                     # over the post-it cap of 280 (slice 3)
    page.fill("#mc-input", long_text)
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-kind="note"][data-note]').last).to_contain_text("yyyy")
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note]:last-child .msg-text')
    page.locator("[data-sel-pin]").click()
    expect(page.locator("[data-sel-result]")).to_contain_text("Too long for a post-it")
    assert not [m for m, _u in posts if m == "POST"]
    ctx.close()


def test_s1_switching_back_on_the_record_clears_a_selection_made_off_it(browser, server, floor):
    """Review of PR #284, F4: switching mode clears the selection in both directions."""
    ctx, page = _context(browser, server, 1440, 900)
    posts = _requests(page, "/api/v1/postits")
    go(page, f"{server['url']}/guild-next/guild/build")
    page.fill("#mc-input", "Kept before going off")
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-kind="note"][data-note]').last).to_contain_text("Kept before")
    page.click("[data-mc-record]")                                                 # off the record
    expect(_hero_chip(page)).to_have_text("Off the record · not kept")
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note] .msg-text')
    assert page.evaluate("window.getSelection().toString()") != ""
    expect(page.locator("[data-sel-bar]")).to_be_hidden()
    page.click("[data-mc-record]")                                                 # back on the record
    expect(_hero_chip(page)).to_have_text("On the record")
    assert page.evaluate("window.getSelection().toString()") == ""                 # the off-record selection is gone
    page.wait_for_timeout(300)
    expect(page.locator("[data-sel-bar]")).to_be_hidden()
    assert not [m for m, _u in posts if m == "POST"]
    ctx.close()


def test_s1_a_selection_across_two_notes_offers_no_actions(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    notes = page.locator('[data-mc-thread] [data-kind="note"][data-note]')
    for n, text in enumerate(("First kept note", "Second kept note")):
        page.fill("#mc-input", text)
        page.click("[data-mc-send]")
        expect(notes).to_have_count(n + 1)
    page.evaluate("""() => { const ns = document.querySelectorAll('[data-mc-thread] [data-kind="note"][data-note] .msg-text');
                    const r = document.createRange(); r.setStart(ns[0], 0);
                    r.setEnd(ns[1], ns[1].childNodes.length); const s = getSelection(); s.removeAllRanges(); s.addRange(r);
                    document.dispatchEvent(new Event('selectionchange')); }""")
    assert "First kept note" in page.evaluate("getSelection().toString()")
    page.wait_for_timeout(500)
    expect(page.locator("[data-sel-bar]")).to_be_hidden()
    ctx.close()


# ── Guild 1.1 slice 2 (spec §4, §11): the Build Log ──────────────────────────

BL_ITEMS = [
    {"id": 7, "spec_title": "Queue lock hardening", "status": "spec_ready", "priority": "high",
     "notes": "Robert's go to start", "owner_rank": 2, "last_transition_at": "2026-09-20T10:00:00+00:00"},
    {"id": 12, "spec_title": "Floor API", "status": "in_build", "summary": "json api", "owner_rank": 1,
     "priority": "high", "notes": "Wire the stream", "last_transition_at": "2026-09-21T10:00:00+00:00"},
    {"id": 31, "spec_title": "Blocked thing", "status": "blocked", "blocked_reason": "waiting on Robert",
     "last_transition_at": "2026-09-22T10:00:00+00:00"},
    {"id": 40, "spec_title": "Done thing", "status": "done", "last_transition_at": "2026-09-01T10:00:00+00:00"},
    {"id": 41, "spec_title": "Design the Workshop jobs view", "status": "design", "priority": "normal",
     "last_transition_at": "2026-09-28T10:00:00+00:00"},
    {"id": 42, "spec_title": "Board photo notes", "status": "idea", "last_transition_at": "2026-09-27T10:00:00+00:00"},
    {"id": 43, "spec_title": "Tips popover redesign", "status": "deferred", "priority": "low",
     "last_transition_at": "2026-09-02T10:00:00+00:00"},
]


@pytest.fixture
def build_log(server, floor):
    write_queue(server["queue"], BL_ITEMS)
    journal = server["queue"].parent / qs.JOURNAL_NAME
    if journal.exists():
        journal.unlink()
    yield server["queue"]
    write_queue(server["queue"])
    if journal.exists():
        journal.unlink()


def _bl_ids(page):
    return [int(x) for x in page.locator("[data-bl-row]").evaluate_all("rs => rs.map(r => r.dataset.blRow)")]


def test_s2_desktop_views_filters_sort_search_and_count(browser, server, build_log):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build/log")
    expect(page.locator(".guild-subnav > [aria-current='page']")).to_have_text("Build Log")
    # My next three: the ranks pinned on top in rank order, then work in progress.
    assert _bl_ids(page) == [12, 7, 41]
    expect(page.locator("[data-bl-row].bl-pinned")).to_have_count(2)
    expect(page.locator("[data-bl-count]")).to_contain_text("3 of 3 · 7 total")
    for view, ids in (("ready", [7]), ("trouble", [31]), ("roadmap", None), ("all", None)):
        page.click(f'[data-bl-view="{view}"]')
        if ids is not None:
            assert _bl_ids(page) == ids, view
    assert sorted(_bl_ids(page)) == [7, 12, 31, 40, 41, 42, 43]                 # All includes done
    expect(page.locator('[data-bl-view="roadmap"] [data-bl-view-n]')).to_have_text("2")
    # Search and a column filter narrow it; the count says so; Clear restores.
    page.fill("[data-bl-search]", "board")
    assert _bl_ids(page) == [42]
    expect(page.locator("[data-bl-count]")).to_contain_text("1 of 7")
    page.click("[data-bl-clear]")
    page.select_option('[data-bl-filter="priority"]', "high")
    assert sorted(_bl_ids(page)) == [7, 12]
    page.click("[data-bl-clear]")
    # Sort by updated, newest first, with pinning off.
    page.click("[data-bl-vopts] > summary")
    page.uncheck("[data-bl-pin]")
    page.keyboard.press("Escape")                                                  # View options closes
    expect(page.locator(".bl-vo")).to_be_hidden()
    page.click('[data-bl-sort="updated"]')
    assert _bl_ids(page)[:2] == [41, 42]
    expect(page.locator('th[data-col="updated"]')).to_have_attribute("aria-sort", "descending")
    # View options: hide a column, comfortable density; remembered on reload.
    page.click("[data-bl-vopts] > summary")
    page.uncheck('[data-bl-col="author"]')
    expect(page.locator('th[data-col="author"]')).to_have_count(0)
    page.click('[data-bl-density="comfortable"]')
    expect(page.locator("[data-bl-matrix]")).to_have_class(re.compile("comfortable"))
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator('th[data-col="author"]')).to_have_count(0)
    expect(page.locator('[data-bl-view="all"]')).to_have_attribute("aria-pressed", "true")
    assert _no_page_overflow(page)
    assert not errors, errors
    ctx.close()


def test_s2_drawer_rank_shift_and_rework_with_history(browser, server, build_log):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    model_calls = _requests(page, "/mc/turns")
    go(page, f"{server['url']}/guild-next/guild/build/log")
    page.click('[data-bl-view="all"]')
    page.click('[data-bl-open="41"]')
    drawer = page.locator("[data-bl-drawer]")
    expect(drawer).to_be_visible()
    expect(drawer.locator(".bl-d-title")).to_have_text("Design the Workshop jobs view")
    expect(drawer.locator("[data-bl-author]")).to_have_text("blank: no author line in the spec")
    # Rank 1: #12 moves to 2, #7 to 3; one write, a receipt.
    drawer.locator('[data-bl-rank="1"]').click()
    expect(drawer.locator("[data-bl-rank-result]")).to_contain_text("Rank saved · verified · receipt")
    page.click('[data-bl-view="next"]')
    assert _bl_ids(page)[:3] == [41, 12, 7]
    ranks = {i["id"]: i.get("owner_rank") for i in json.loads(build_log.read_text())}
    assert (ranks[41], ranks[12], ranks[7]) == (1, 2, 3)
    # Rework needs a reason; with one it saves and the history shows it.
    status = drawer.locator("[data-bl-status-select]")
    status.select_option("rework")
    drawer.locator("[data-bl-reason]").fill("")
    drawer.locator("[data-bl-save]").click()
    expect(drawer.locator("[data-bl-status-result]")).to_contain_text("need a reason")
    drawer.locator("[data-bl-reason]").fill("Review found the job card hides its last contact")
    drawer.locator("[data-bl-save]").click()
    expect(drawer.locator("[data-bl-status-result]")).to_contain_text("Saved · verified")
    expect(drawer.locator("[data-bl-trouble]")).to_contain_text("Review found the job card")
    expect(drawer.locator("[data-bl-trouble]")).to_contain_text("In trouble from design")
    expect(drawer.locator("[data-bl-history] li")).to_have_count(2)
    expect(drawer.locator("[data-bl-history] li").first).to_contain_text("design → rework")
    expect(drawer.locator("[data-bl-history] li").last).to_contain_text("Rank none → 1")
    expect(drawer.locator("[data-bl-back]")).to_have_text("Suggest: back to design")
    page.click('[data-bl-view="trouble"]')
    assert sorted(_bl_ids(page)) == [31, 41]
    # Leaving trouble is an explicit Save.
    drawer.locator("[data-bl-back]").click()
    drawer.locator("[data-bl-save]").click()
    expect(drawer.locator("[data-bl-status-result]")).to_contain_text("Saved · verified")
    expect(drawer.locator("[data-bl-trouble]")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(drawer).to_be_hidden()
    assert not model_calls
    assert not errors, errors
    ctx.close()


def test_s2_a_stale_ranking_in_one_tab_gets_a_conflict_and_the_current_order(browser, server, build_log):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build/log")
    other = ctx.new_page()
    go(other, f"{server['url']}/guild-next/guild/build/log")
    other.click('[data-bl-view="all"]')
    other.click('[data-bl-open="42"]')
    other.locator('[data-bl-rank="1"]').click()
    expect(other.locator("[data-bl-rank-result]")).to_contain_text("Rank saved")
    page.click('[data-bl-view="all"]')
    page.click('[data-bl-open="43"]')
    page.locator('[data-bl-rank="1"]').click()                                     # this tab's digest is stale
    expect(page.locator("[data-bl-rank-result]")).to_contain_text("The ranking changed since you opened it")
    ranks = {i["id"]: i.get("owner_rank") for i in json.loads(build_log.read_text())}
    assert ranks[42] == 1 and ranks.get(43) is None                                # never overwritten
    page.click('[data-bl-view="next"]')
    assert _bl_ids(page)[0] == 42                                                  # the current order is shown
    ctx.close()


def test_s2_new_item_opens_in_the_drawer(browser, server, build_log):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build/log")
    page.click("[data-bl-new-open]")
    page.fill("[data-bl-new-title]", "Rooms file preview")
    page.select_option("[data-bl-new-status]", "design")
    page.click("[data-bl-new-add]")
    expect(page.locator("[data-bl-new-result]")).to_contain_text("#44 added · verified · receipt")
    expect(page.locator("[data-bl-drawer] .bl-d-title")).to_have_text("Rooms file preview")
    expect(page.locator("[data-bl-drawer] [data-bl-history] li")).to_contain_text(["Created as design"])
    assert json.loads(build_log.read_text())[-1]["id"] == 44
    ctx.close()


def test_s2_off_the_record_the_build_log_writes_nothing(browser, server, build_log):
    ctx, page = _context(browser, server, 1440, 900)
    posts = _requests(page, "/api/v1/queue/items")
    go(page, f"{server['url']}/guild-next/guild/build/log")
    page.click("[data-mc-pill]")
    page.click("[data-mc-record]")
    page.click("[data-mc-min]")                                                   # back to the pill
    page.click('[data-bl-open="12"]')
    page.locator('[data-bl-rank="3"]').click()
    expect(page.locator("[data-bl-rank-result]")).to_contain_text("Off the record")
    assert not [m for m, _u in posts if m == "POST"]
    page.click("[data-mc-pill]")
    page.click("[data-mc-record]")
    ctx.close()


def test_s2_phone_contained_scroll_default_columns_and_drawer(browser, server, build_log):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build/log")
    heads = page.locator("[data-bl-head] th").evaluate_all("ths => ths.map(t => t.dataset.col)")
    assert heads == ["rank", "title", "status", "notes"]                          # the phone's default columns
    assert _no_page_overflow(page)
    wrap = page.locator("[data-bl-wrap]")
    assert wrap.evaluate("w => w.scrollWidth > w.clientWidth")                    # the table scrolls in its box
    assert wrap.bounding_box()["width"] <= 390
    page.click('[data-bl-view="all"]')
    page.click('[data-bl-open="31"]')
    drawer = page.locator("[data-bl-drawer]")
    expect(drawer).to_be_visible()
    box = drawer.bounding_box()
    assert box["x"] <= 1 and box["width"] >= 388                                   # full screen on a phone
    expect(drawer.locator("[data-bl-trouble]")).to_contain_text("waiting on Robert")
    drawer.locator("[data-bl-close]").click()
    expect(drawer).to_be_hidden()
    assert _no_page_overflow(page)
    assert not errors, errors
    ctx.close()


# ── Guild 1.1 slice 3 (spec §5, §11): the Board and the Media library ────────

@pytest.fixture
def board_media(server, floor, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    services = server["app"].extensions["guild_ui_next"]["services"]
    saved = services.media_dir
    services.media_dir = str(media)
    yield floor, media
    services.media_dir = saved


def _bd_ids(page, sel="[data-bd-note]"):
    return [int(x) for x in page.locator(sel).evaluate_all("ns => ns.map(n => n.dataset.bdNote)")]


def _jpeg_file(tmp_path):
    from board_media_helpers import image_bytes
    path = tmp_path / "lake.jpg"
    path.write_bytes(image_bytes(size=(320, 240), color=(60, 120, 180)))
    return path


def test_s3_desktop_notes_done_order_trash_and_empty(browser, server, board_media):
    from minimoi_portal.guild_ui.stores import Author
    floor, _media = board_media
    robert = Author("robert", "owner", "Robert")
    store = floor.store()
    ids = [store.add_postit(t, robert, idempotency_key=f"s3-seed-{i:04d}").value["id"]
           for i, t in enumerate(("first", "second", "third"))]
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/board")
    expect(page.locator(".guild-subnav > [aria-current='page']")).to_have_text("Board")
    assert _bd_ids(page) == ids[::-1]                                              # newest on top
    # + Note ▾ → Note, with a label and a linked work item.
    page.click("[data-bd-add] > summary")
    page.click('[data-bd-open="note"]')
    page.fill("[data-bd-note-text]", "Decide how long Done notes stay")
    page.select_option("[data-bd-note-label]", "decide")
    page.fill("[data-bd-note-item]", "12")
    page.click("[data-bd-note-add]")
    expect(page.locator("[data-bd-add-result]")).to_have_text("Post-it added")
    new = page.locator("[data-bd-note]").first
    expect(new).to_have_attribute("data-label", "decide")
    expect(new.locator("[data-bd-item]")).to_have_text("#12")
    expect(new.locator(".bd-linkline")).to_contain_text("Floor API")
    new_id = _bd_ids(page)[0]
    # Order: › button, Alt+Arrow, and drag.
    page.locator(f'[data-bd-move="next"][data-id="{new_id}"]').click()
    expect(page.locator("[data-bd-note]").nth(1)).to_have_attribute("data-bd-note", str(new_id))
    page.locator(f'[data-bd-note="{new_id}"]').focus()
    page.keyboard.press("Alt+ArrowLeft")
    expect(page.locator("[data-bd-note]").first).to_have_attribute("data-bd-note", str(new_id))
    page.locator(f'[data-bd-note="{ids[0]}"]').drag_to(page.locator(f'[data-bd-note="{new_id}"]'),
                                                         target_position={"x": 5, "y": 40})
    expect(page.locator("[data-bd-note]").first).to_have_attribute("data-bd-note", str(ids[0]))
    order = _bd_ids(page)
    assert [p["id"] for p in store.board().data["active"]] == order                 # the server has the order
    # Done leaves Active and shows under Done; Undone brings it back.
    page.locator(f'[data-bd-done="{ids[1]}"]').click()
    expect(page.locator(f'[data-bd-note="{ids[1]}"]')).to_have_count(0)
    page.select_option("[data-bd-show]", "done")
    expect(page.locator(f'[data-bd-note="{ids[1]}"]')).to_be_visible()
    page.locator(f'[data-bd-undone="{ids[1]}"]').click()
    expect(page.locator(f'[data-bd-note="{ids[1]}"]')).to_have_count(0)
    # Discard → Trash (with its count) → Restore → Discard again → Empty, confirmed.
    page.select_option("[data-bd-show]", "active")
    page.locator(f'[data-bd-bin="{ids[2]}"]').click()
    expect(page.locator('[data-bd-show] option[value="trash"]')).to_have_text("Trash (1)")
    page.select_option("[data-bd-show]", "trash")
    page.locator(f'[data-bd-restore="{ids[2]}"]').click()
    expect(page.locator('[data-bd-show] option[value="trash"]')).to_have_text("Trash (0)")
    page.select_option("[data-bd-show]", "active")
    page.locator(f'[data-bd-bin="{ids[2]}"]').click()
    page.select_option("[data-bd-show]", "trash")
    page.click("[data-bd-empty]")
    expect(page.locator("[data-bd-confirm]")).to_be_visible()
    page.click("[data-bd-confirm-no]")
    expect(page.locator(f'[data-bd-note="{ids[2]}"]')).to_be_visible()             # cancel deletes nothing
    page.click("[data-bd-empty]")
    page.click("[data-bd-confirm-yes]")
    expect(page.locator("[data-bd-result]")).to_contain_text("Trash emptied · receipt t-")
    expect(page.locator("[data-bd-note]")).to_have_count(0)
    assert _no_page_overflow(page)
    assert not errors, errors
    ctx.close()


def test_s3_photo_upload_place_and_library_purge_refused_while_used(browser, server, board_media, tmp_path):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    uploads = _requests(page, "/api/v1/media")
    go(page, f"{server['url']}/guild-next/guild/board")
    page.click("[data-bd-add] > summary")
    page.click('[data-bd-open="photo"]')
    expect(page.locator("[data-bd-pick]")).to_contain_text("no photos yet")
    page.set_input_files("[data-bd-photo-file]", str(_jpeg_file(tmp_path)))
    expect(page.locator("[data-bd-photo-result]")).to_contain_text("Added to your library")
    page.fill("[data-bd-photo-caption]", "Lake ride, Saturday")
    page.click("[data-bd-photo-add]")
    photo = page.locator(".bd-photo").first
    expect(photo).to_contain_text("Lake ride, Saturday")
    page.wait_for_function("(e) => e.complete && e.naturalWidth > 0", arg=photo.locator("img").element_handle())
    assert [m for m, _u in uploads if m == "POST"] == ["POST"]
    # The library: trash the image, then a permanent delete is refused and says where it is used.
    go(page, f"{server['url']}/guild-next/guild/media")
    item = page.locator("[data-md-asset]").first
    expect(item).to_contain_text("Used in 1 place")
    item.locator("[data-md-trash]").click()
    expect(page.locator("[data-md-result]")).to_contain_text("Moved to the library Trash")
    page.select_option("[data-md-show]", "trash")
    page.locator("[data-md-purge]").first.click()
    page.click("[data-md-confirm-yes]")
    expect(page.locator("[data-md-result]")).to_contain_text("Still used")
    expect(page.locator("[data-md-where]").first).to_contain_text("Board note #")
    # On the Board the photo is still shown (served while in the library Trash).
    go(page, f"{server['url']}/guild-next/guild/board")
    page.wait_for_function("(e) => e.complete && e.naturalWidth > 0", arg=page.locator(".bd-photo img").first.element_handle())
    assert not [e for e in errors if "409" not in e], errors                       # the refused purge is a 409 by design
    ctx.close()


def test_s3_off_the_record_the_board_writes_nothing(browser, server, board_media, tmp_path):
    from minimoi_portal.guild_ui.stores import Author
    floor, _media = board_media
    pid = floor.store().add_postit("stay", Author("robert", "owner", "Robert"), idempotency_key="s3-off-0001").value["id"]
    ctx, page = _context(browser, server, 1440, 900)
    writes = _writes(page)
    go(page, f"{server['url']}/guild-next/guild/board")
    page.click("[data-mc-pill]")
    page.click("[data-mc-record]")
    page.click("[data-mc-min]")
    page.locator(f'[data-bd-done="{pid}"]').click()
    expect(page.locator("[data-bd-result]")).to_contain_text("Off the record")
    page.click("[data-bd-add] > summary")
    page.click('[data-bd-open="photo"]')
    page.set_input_files("[data-bd-photo-file]", str(_jpeg_file(tmp_path)))
    expect(page.locator("[data-bd-photo-result]")).to_contain_text("Off the record")
    assert not [w for w in writes if w.startswith(("/postits", "/media"))]
    page.click("[data-mc-pill]")
    page.click("[data-mc-record]")
    ctx.close()


@pytest.mark.parametrize("path", ["/guild-next/guild/board", "/guild-next/guild/media"])
def test_s3_phone_board_and_library(browser, server, board_media, path):
    from minimoi_portal.guild_ui.stores import Author
    floor, _media = board_media
    store = floor.store()
    for i in range(3):
        store.add_postit(f"phone note {i} with a little more text to wrap", Author("robert", "owner", "Robert"),
                         idempotency_key=f"s3-ph-{path[-5:]}-{i:04d}", label="remember")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    go(page, f"{server['url']}{path}")
    assert _no_page_overflow(page)
    if path.endswith("board"):
        boxes = [page.locator("[data-bd-note]").nth(i).bounding_box() for i in range(3)]
        assert max(b["x"] for b in boxes) - min(b["x"] for b in boxes) < 4          # one column on a phone (tilted notes)
        assert page.locator("[data-bd-done]").first.bounding_box()["height"] >= 43   # touch targets
    assert not errors, errors
    ctx.close()
