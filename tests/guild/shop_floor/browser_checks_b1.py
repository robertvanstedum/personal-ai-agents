"""Browser checks for /guild-next (Playwright, installed Chrome, headless). Run explicitly:

    venv/bin/python3 -m pytest tests/guild/shop_floor/browser_checks_b1.py -q -p no:cacheprovider

Serves a fresh copy of the staging portal (MINIMOI_GUILD_NEXT=1) on a loopback
port with a temporary queue folder. A test-only route on that copy signs the
browser in as the owner; no credential is used. Nothing leaves the machine.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import socket
import tempfile
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from domains.guild import queue_store as qs

from floor_helpers import OWNER, QUEUE_ITEMS, REPO, write_queue
from floor_db_helpers import SqliteFloor

from minimoi_portal.guild_ui.security import OFF_RECORD_TEXT as OFF_TEXT       # Private, the one wording the app uses


def _private_on(page):
    """Private shows as a pressed lock and a compact chip; the explanation is on demand (5 Oct)."""
    expect(page.locator("[data-mc-record]")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("[data-private-chip]")).to_be_visible()
    page.click("[data-private-why]")
    expect(page.locator("[data-private-explain]")).to_have_text(OFF_TEXT)


@pytest.fixture(scope="module")
def server():
    import core.get_secret as secrets_module
    import minimoi_portal.config as portal_config
    saved = (secrets_module.get_secret, portal_config.GUILD_QUEUE_PATH, portal_config.BASE_URL,
             qs._running_in_container, os.environ.get("MINIMOI_GUILD_NEXT"))
    saved_backend = os.environ.get("RECORDS_BACKEND")
    os.environ["RECORDS_BACKEND"] = "http://127.0.0.1:18880"           # Rooms (slice 4): loopback, as local hosts need (F7)
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
        if saved_backend is None:
            os.environ.pop("RECORDS_BACKEND", None)
        else:
            os.environ["RECORDS_BACKEND"] = saved_backend
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
    # Rooms (slice 4): every page that loads Rooms reaches a real Records app
    # on a temporary folder through the bridge (tests that need their own use
    # the rooms fixture).
    from minimoi_portal import records_bridge as RB
    from rooms_helpers import RecordsTransport, records_app
    saved_rb = (RB._TRANSPORT, RB.LOCAL_HOSTS)
    RB._TRANSPORT = RecordsTransport(records_app(Path(tempfile.mkdtemp()) / "records"))
    RB.LOCAL_HOSTS = {f"127.0.0.1:{port}"}
    # The retired Workbench, Build Queue and Labs pages are served only with this switch (off in Guild 1.1). The suite's
    # older tests of those pages run with it on; the exclusion journeys switch it off for their own duration.
    saved_reserve = os.environ.get("MINIMOI_GUILD_RESERVE_PAGES")
    os.environ["MINIMOI_GUILD_RESERVE_PAGES"] = "1"
    yield {"url": f"http://127.0.0.1:{port}", "queue": queue, "app": app}
    if saved_reserve is None:
        os.environ.pop("MINIMOI_GUILD_RESERVE_PAGES", None)
    else:
        os.environ["MINIMOI_GUILD_RESERVE_PAGES"] = saved_reserve
    RB._TRANSPORT, RB.LOCAL_HOSTS = saved_rb
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
    expect(page.locator("[data-briefing-text]")).to_have_count(0)           # the chat no longer prints the briefing paragraph (5 Oct)
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
        if path.endswith("/build"):
            expect(type_btn).to_have_count(0)                                          # on Chat the Ask box is already on screen
        else:
            expect(type_btn).to_be_visible()
            expect(type_btn).to_have_text("Ask Master Craftsman")
            expect(type_btn).to_be_in_viewport()
            type_btn.click()
        field = page.locator("#mc-input")
        if not path.endswith("/build"):
            expect(field).to_be_focused()                                              # the button put the cursor in the box
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
    _private_on(page)
    form = page.locator("[data-status-form]")
    form.locator("[data-status-select]").select_option("done")
    form.locator("[data-save]").click()
    expect(form.locator("[data-save-result]")).to_have_text(OFF_TEXT)
    assert fresh_queue.read_bytes() == before
    assert not (fresh_queue.parent / qs.JOURNAL_NAME).exists()
    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-thread]")).to_contain_text("Back on the record")
    expect(page.locator("[data-private-chip]")).to_be_hidden()
    ctx.close()


def test_a_broken_queue_reads_unknown_in_the_browser(browser, server, fresh_queue):
    fresh_queue.write_text("[{")
    try:
        ctx, page = _context(browser, server)
        go(page, f"{server['url']}/guild-next/guild/build")
        expect(page.locator('[data-light="build_queue"] [data-light-word]')).to_have_text("Unknown")
        expect(page.locator("[data-needs-summary]")).to_have_text("Needs you · unknown — read failed")   # the only line: an honest unknown, never a zero
        expect(page.locator("text=/Nothing needs you/")).to_have_count(0)
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
    expect(page.locator("[data-stale-banner]")).to_contain_text("last good read")           # the banner and the lights carry staleness (5 Oct)
    expect(page.locator("text=/Nothing needs you/")).to_have_count(0)
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
    expect(page.locator("[data-stale-banner]")).to_contain_text("no good read since")
    expect(page.locator("[data-stale-banner]")).to_contain_text("could not be reached")
    assert page.evaluate("document.body.dataset.freshness") == "unknown"
    # the server answers again (304 on the old tag): live values come back
    page.unroute(FLOOR_API)
    poll(page)
    expect(queue).to_have_attribute("data-light-state", "red")
    expect(queue.locator("[data-light-src]")).to_have_text("live")
    expect(queue.locator("svg.lshape")).to_have_attribute("data-shape", "square")
    expect(page.locator("[data-stale-banner]")).to_be_hidden()
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
    # 5 Oct: the rail shows only Linked work and Files; no "Last opened", no "Most urgent".
    expect(page.locator("[data-rail-continue]")).to_have_count(0)
    expect(page.locator("[data-floor-urgent]")).to_have_count(0)
    go(page, f"{server['url']}/guild-next/guild/build/items/12")
    expect(page.locator("[data-continue]").first).to_have_attribute("data-continue-state", "ok")
    page.wait_for_function("() => [...document.querySelectorAll('[data-continue-link]')].some(a => a.textContent === '#12 Floor API')")
    assert floor.rows("floor_continue")[0]["ref"] == "12"
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-rail-continue]")).to_have_count(0)    # 5 Oct: Continue no longer appears in the chat rail
    expect(page.locator("[data-linked-empty]")).to_be_visible()      # and opening an item never links it to a conversation
    assert not errors, errors
    desk.close()

    phone, ppage = _context(browser, server, **PHONE)     # another session and viewport, same owner
    go(ppage, f"{server['url']}/guild-next/guild/build")
    ppage.click("[data-floor-context] > summary")                 # the context, folded below the chat: no Continue line (5 Oct)
    expect(ppage.locator("[data-rail-continue]")).to_have_count(0)
    go(ppage, f"{server['url']}/guild-next/guild/build/queue")
    expect(ppage.locator(".ps-continue [data-continue-link]")).to_have_text("#12 Floor API")
    phone.close()


def _w6(page, server, floor, phone):
    # Guild 1.1: post-its live on the wall (the Workbench), not on the Shop floor.
    go(page, f"{server['url']}/guild-next/guild/build")
    assert page.locator('[data-postits][data-mode="rail"]').count() == 0
    page.goto(f"{server['url']}/guild-next/guild/build/bench")          # the Chat has no Wall link any more (5 Oct)
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
    _private_on(page)
    page.fill("#mc-input", "scratch text 4812 not for the record")
    page.click("[data-mc-send]")
    expect(page.locator("[data-mc-refusal]")).to_contain_text("not switched on")          # Master Craftsman is off here: nothing is sent
    expect(page.locator("[data-off-record-line]")).to_have_count(0)
    expect(page.locator("#mc-input")).to_have_value("scratch text 4812 not for the record")   # and the draft is kept
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
    assert "scratch text 4812" not in page.content()                          # what was typed in Private is gone, not kept
    assert not errors, errors
    ctx.close()


def _mc_turns_on(page):
    page.evaluate("document.body.dataset.mcTurns = 'true'")


def test_b19_private_gets_an_answer_and_nothing_is_kept_and_leaving_private_clears_it(browser, server, floor):
    """Robert, 7 Oct: "there is no point in me writing if I don't get a response". Private asks the model and shows the
    answer; MiniMoi keeps no note, and the whole private thread is gone when Private ends or the page reloads."""
    import minimoi_portal.guild_ui.mc.openclaw as oc
    from minimoi_portal.guild_ui.mc import CachedHealth
    services = server["app"].extensions["guild_ui_next"]["services"]
    sent_users, sent_text = [], []

    class Resp:
        def __init__(self, status, text, headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    def post(url, data=None, headers=None, timeout=None):
        body = json.loads(data)
        sent_users.append(body["user"]); sent_text.append(body["messages"][0]["content"])
        return Resp(200, json.dumps({"id": "chatcmpl_pv", "object": "chat.completion", "model": "openclaw/mc-chat",
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "Let's start with **the role**."},
                                                  "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
                    {"X-MC-Correlation-Id": (headers or {}).get("X-MC-Correlation-Id")})
    saved = (services.mc, services.mc_health, services.mc_turns)
    backend = oc.OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 40, http_get=lambda *a, **k: Resp(200, "{}"), http_post=post)
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    try:
        ctx, page = _context(browser, server)
        errors = _errors(page)
        notes = _requests(page, "/api/v1/notes")
        private = _requests(page, "/api/v1/mc/private")
        go(page, f"{server['url']}/guild-next/guild/build")
        before = floor.count("floor_messages")
        page.click("[data-mc-record]")
        expect(page.locator("[data-private-chip]")).to_contain_text("Private · not kept by MiniMoi")
        page.fill("#mc-input", "I want to discuss my wife's job interview")
        page.click("[data-mc-send]")
        answer = page.locator('[data-kind="private-answer"]')
        expect(answer).to_have_count(1, timeout=10000)
        expect(answer.locator("strong")).to_have_text("the role")
        expect(answer).to_contain_text("Private · not kept by MiniMoi")
        expect(answer.locator("[data-slot='elapsed']")).to_have_text(re.compile(r"^\d+s$"))
        expect(page.locator('[data-kind="off-record"]')).to_contain_text("I want to discuss my wife's job interview")
        page.fill("#mc-input", "and what should she ask them?")
        page.click("[data-mc-send]")
        expect(page.locator('[data-kind="private-answer"]')).to_have_count(2, timeout=10000)
        assert len(sent_users) == 2 and sent_users[0] == sent_users[1]                      # one sitting, one backend session
        assert len(notes) == 0 and len(private) == 2                                        # no note write; two private asks
        assert floor.count("floor_messages") == before and "wife" not in floor.all_text()   # nothing kept
        page.click("[data-mc-record]")                                                      # leaving Private
        expect(page.locator("[data-off-record-line]")).to_have_count(0)
        expect(page.locator("[data-mc-thread]")).not_to_contain_text("wife")
        page.click("[data-mc-record]")                                                      # a new Private sitting is a new session
        page.fill("#mc-input", "a fresh start")
        page.click("[data-mc-send]")
        expect(page.locator('[data-kind="private-answer"]')).to_have_count(1, timeout=10000)
        assert sent_users[2] != sent_users[0]
        page.reload()
        page.wait_for_selector("body[data-ready=true]")
        assert "wife" not in page.content() and floor.count("floor_messages") == before
        assert not errors, errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def _private_backend(server, reply="Let's start with the role."):
    import minimoi_portal.guild_ui.mc.openclaw as oc
    from minimoi_portal.guild_ui.mc import CachedHealth
    services = server["app"].extensions["guild_ui_next"]["services"]
    users = []

    class Resp:
        def __init__(self, status, text, headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    def post(url, data=None, headers=None, timeout=None):
        body = json.loads(data); users.append(body["user"])
        return Resp(200, json.dumps({"id": "c", "object": "chat.completion", "model": "openclaw/mc-chat",
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
                    {"X-MC-Correlation-Id": (headers or {}).get("X-MC-Correlation-Id")})
    saved = (services.mc, services.mc_health, services.mc_turns)
    backend = oc.OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 40, http_get=lambda *a, **k: Resp(200, "{}"), http_post=post)
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    return services, saved, users


def test_b20_every_way_out_of_private_forgets_the_thread_and_a_late_answer_is_never_shown(browser, server, floor):
    """Codex review, 7 Oct: the page's lifetime must match the wording. Exit chip, lock, Confirm, leaving the page, a page restored from the
    back/forward cache, and exit and re-enter while an answer is still on its way all end the sitting."""
    services, saved, users = _private_backend(server)
    try:
        ctx, page = _context(browser, server)
        errors = _errors(page)
        go(page, f"{server['url']}/guild-next/guild/build")

        def ask(text):
            page.fill("#mc-input", text); page.click("[data-mc-send]")

        page.click("[data-mc-record]")                                                       # in
        ask("first private question")
        expect(page.locator('[data-kind="private-answer"]')).to_have_count(1, timeout=10000)
        page.fill("#mc-input", "a private draft")
        page.click("[data-private-exit]")                                                    # out by the chip's Exit
        expect(page.locator("[data-off-record-line]")).to_have_count(0)
        expect(page.locator("#mc-input")).to_have_value("")                                  # the private draft went too

        page.click("[data-mc-record]")                                                       # in again: a new sitting
        ask("second private question")
        expect(page.locator('[data-kind="private-answer"]')).to_have_count(1, timeout=10000)
        assert users[0] != users[1]
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}))")     # leaving the page
        expect(page.locator("[data-off-record-line]")).to_have_count(0)
        ask("third after leaving")                                                           # still in Private mode, but a fresh sitting
        expect(page.locator('[data-kind="private-answer"]')).to_have_count(1, timeout=10000)
        assert users[2] != users[1]
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))")    # restored from the cache
        expect(page.locator("[data-off-record-line]")).to_have_count(0)

        held = []
        page.route("**/api/v1/mc/private", lambda route: held.append(route))                # the next answer is held on its way
        ask("a question whose answer arrives too late")
        expect(page.locator("[data-mc-waiting]")).to_have_count(1)
        page.click("[data-private-exit]")                                                    # out ...
        page.click("[data-mc-record]")                                                       # ... and in again, before the answer
        assert len(held) == 1
        held[0].continue_()                                                                  # the old sitting's answer now arrives
        page.wait_for_timeout(1500)
        expect(page.locator('[data-kind="private-answer"]')).to_have_count(0)               # and is dropped
        expect(page.locator("[data-mc-thread]")).not_to_contain_text("arrives too late")
        assert "answered" not in page.locator("[data-mc-announcer]").inner_text()           # and it is not announced to a screen reader either
        assert not errors, errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


# ── The topic Workshop (W1) ──────────────────────────────────────────────────
_WT_JS = """async ([path, body, method]) => {
  const p = JSON.parse(document.getElementById('guild-page').textContent);
  const init = {method, credentials: 'same-origin', headers: method === 'GET' ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': p.csrf_token, 'X-Record-Mode': 'on_record'}};
  if (method !== 'GET') init.body = JSON.stringify({idempotency_key: 'k' + Math.random().toString(36).slice(2, 14), ...body});
  const r = await fetch(p.urls.api + path, init); return await r.json();
}"""
_WT_UP = """async ([path, b64, title]) => {
  const p = JSON.parse(document.getElementById('guild-page').textContent);
  const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0)); const fd = new FormData();
  fd.append('file', new Blob([bin], {type: 'image/png'}), 'x.png'); fd.append('title', title);
  const r = await fetch(p.urls.api + path, {method: 'POST', credentials: 'same-origin', body: fd, headers: {'X-CSRF-Token': p.csrf_token, 'X-Record-Mode': 'on_record', 'Idempotency-Key': 'up' + Math.random().toString(36).slice(2, 14)}});
  return await r.json();
}"""
_DOC = "# Topic records\n\nPut away keeps the card.\n\nNothing is deleted by default.\n\n- Queued is not received\n- Returned is not approved"


def _wt(page, path, body=None, method="POST"):
    return page.evaluate(_WT_JS, [path, body or {}, method])


def _png_b64():
    import base64
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (60, 120), (190, 170, 130)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _seed_topic(page, server, title):
    go(page, f"{server['url']}/guild-next/guild/build")
    tid = _wt(page, "/topics/create", {"title": title, "summary": "sample"})["topic"]["id"]
    ids = {}
    ids["note"] = _wt(page, f"/topics/{tid}/items", {"kind": "note", "title": "What should stay visible?", "text": "Current work first."})["item"]["id"]
    ids["doc"] = _wt(page, f"/topics/{tid}/items", {"kind": "document", "title": "Spec · topic records", "text": _DOC})["item"]["id"]
    ids["req"] = _wt(page, f"/topics/{tid}/items", {"kind": "request", "title": "Review the Files layout", "text": "Please review revision B.", "request": {"to": "Codex", "included": "revision B"}})["item"]["id"]
    ids["design"] = page.evaluate(_WT_UP, [f"/topics/{tid}/items/design", _png_b64(), "Files panel · phone"])["item"]["id"]
    return tid, ids


def _wt_open(page, server, tid, view="cards", item=None):
    q = f"?topic={tid}&view={view}" + (f"&item_id={item}" if item else "")
    go(page, f"{server['url']}/guild-next/guild/workshop{q}")
    page.wait_for_selector("[data-wt-board], .wt-reader, [data-wt-views] button")


def test_w1_the_workshop_starts_a_topic_and_the_old_host_page_moved_to_operate(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/workshop")
    expect(page.locator("[data-wt-body]")).to_contain_text("Start a topic")
    page.fill("input[aria-label='Name for the topic']", "Guild workshop trial")
    page.click("text=Start topic")
    page.wait_for_selector("[data-wt-board]")
    expect(page.locator("[data-wt-title]")).to_have_text("Guild workshop trial")
    assert "topic=t-" in page.url and "view=split" in page.url                                   # a wide window starts as chat | topic
    expect(page.locator("[data-wt-board] .wt-empty")).to_contain_text("Nothing on the desk yet")
    expect(page.locator("[data-wt-views] button")).to_have_text(["Chat", "Cards", "Split"])
    go(page, f"{server['url']}/guild-next/guild/operate/build-host")
    expect(page.locator("h1.page-title")).to_have_text("Build host")
    expect(page.locator(".guild-subnav .subnav-active")).to_contain_text("Build host")
    assert not errors, errors
    ctx.close()


def test_w2_every_card_opens_in_the_same_work_area_and_back_keeps_arrangement(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W2 cards")
    _wt_open(page, server, tid)
    cards = page.locator(".wt-card")
    expect(cards).to_have_count(4)
    titles = cards.locator(".open").all_inner_texts()
    cards.first.locator(".more").click()
    page.get_by_role("menuitem", name="Move later").click()                                   # arrangement is saved on the server
    expect(cards.nth(1).locator(".open")).to_have_text(titles[0])                              # it re-draws once the server has saved the order
    moved = cards.locator(".open").all_inner_texts()
    assert moved != titles
    for name in ("What should stay visible?", "Review the Files layout", "Spec · topic records", "Files panel · phone"):
        page.get_by_role("button", name=name, exact=True).click()
        expect(page.locator(".wt-reader")).to_have_count(1)
        assert page.locator("dialog[open]").count() == 0                                         # never a different kind of window
        expect(page.locator("#wt-item-title")).to_have_text(name)
        page.keyboard.press("Escape")                                                            # back to the cards
        expect(page.locator("[data-wt-board]")).to_be_visible()
        assert page.locator(".wt-card .open").all_inner_texts() == moved                         # the arrangement is exactly as left
    page.reload(); page.wait_for_selector("[data-wt-board]")
    assert page.locator(".wt-card .open").all_inner_texts() == moved                             # and it survived a reload
    expect(page.locator("[data-wt-board] .wt-steps li[aria-current=step]")).to_have_text("Queued")
    page.locator(".wt-card").filter(has_text="What should stay visible?").locator(".more").click()
    page.get_by_role("menuitem", name="Put away").click()
    expect(cards).to_have_count(3)
    page.get_by_role("button", name="Previous (1)").click()
    page.get_by_role("button", name="Bring back").click()
    expect(cards).to_have_count(4)
    assert not errors, errors
    ctx.close()


def test_w3_comments_are_anchored_to_a_paragraph_and_a_revision_with_reply_resolve_and_disposition(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W3 comments")
    _wt_open(page, server, tid, item=ids["doc"])
    expect(page.locator(".wt-doc [data-block]")).to_have_count(4)
    page.locator("[data-wt-cmode]").click()
    page.locator(".wt-doc [data-block='2']").click()
    page.fill("textarea[aria-label^='Comment on paragraph 3']", "Say what deleted means here.")
    page.get_by_role("button", name="Add comment").click()
    cmt = page.locator(".wt-cmt[data-cid]").first
    expect(cmt).to_contain_text("paragraph 3"); expect(cmt).to_contain_text("version A")
    expect(page.locator(".wt-doc [data-block='2'].marked")).to_have_count(1)
    cmt.get_by_role("button", name="Reply").click()
    page.fill("dialog textarea", "Deleted means removed from disk; it never is.")
    page.get_by_role("button", name="Add reply").click()
    expect(page.locator(".wt-cmt .reply")).to_contain_text("never is")
    page.locator(".wt-cmt [data-disp='accepted']").click()
    expect(page.locator(".wt-cmt")).to_contain_text("Accepted · ")
    page.locator(".wt-cmt").get_by_role("button", name="Resolve").click()
    expect(page.locator(".wt-cmt .wt-badge.ok").first).to_be_visible()
    # editing keeps the first version exactly as it was (no window, no ceremony), and the comment stays on the first
    page.locator("[data-wt-edit]").click()
    page.fill("textarea[data-wt-editor]", "# Topic records\n\nPut away keeps the card.\n\nNothing is removed from disk.")
    page.locator("[data-wt-save]").click()
    expect(page.locator("#wt-item-title")).to_have_text("Spec · topic records")
    expect(page.locator(".wt-reader .wt-eyebrow")).to_contain_text("version B")
    expect(page.locator(".wt-cmt")).to_have_count(0)                                              # nothing is open on version B
    page.select_option("select[aria-label='Version']", "A")
    expect(page.locator(".wt-cmt[data-cid]").first).to_contain_text("Say what deleted means")
    assert not errors, errors
    ctx.close()


def test_w4_a_design_is_an_image_and_a_comment_points_at_a_place_in_it(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W4 design")
    _wt_open(page, server, tid, item=ids["design"])
    img = page.locator(".wt-img img")
    expect(img).to_be_visible()
    assert img.evaluate("i => i.naturalWidth") == 60
    page.locator("[data-wt-cmode]").click()
    box = img.bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5)
    page.fill("textarea[aria-label='Comment on this point']", "This name is cut off")
    page.get_by_role("button", name="Add comment").click()
    expect(page.locator(".wt-pin")).to_have_count(1)
    expect(page.locator(".wt-cmt").first).to_contain_text("a point 30% across")
    assert not errors, errors
    ctx.close()


def _wt_mc(server):
    import minimoi_portal.guild_ui.mc.openclaw as oc
    from minimoi_portal.guild_ui.mc import CachedHealth
    services = server["app"].extensions["guild_ui_next"]["services"]
    sent = []

    class Resp:
        def __init__(self, status, text, headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    def post(url, data=None, headers=None, timeout=None):
        body = json.loads(data); sent.append(body["messages"][0]["content"])
        return Resp(200, json.dumps({"id": "c", "object": "chat.completion", "model": "openclaw/mc-agent",
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": "Noted."}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
                    {"X-MC-Correlation-Id": (headers or {}).get("X-MC-Correlation-Id")})
    saved = (services.mc, services.mc_health, services.mc_turns)
    backend = oc.OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 40, http_get=lambda *a, **k: Resp(200, "{}"), http_post=post)
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    return services, saved, sent


def test_w5_ask_master_craftsman_about_this_selects_context_and_sends_nothing_until_send(browser, server, floor):
    services, saved, sent = _wt_mc(server)
    try:
        ctx, page = _context(browser, server, 1440, 900)
        errors = _errors(page)
        tid, ids = _seed_topic(page, server, "W5 ask")
        _wt_open(page, server, tid)
        page.locator(".wt-card").filter(has_text="Spec · topic records").locator(".more").click()
        page.get_by_role("menuitem", name="Ask Master Craftsman about this").click()
        chip = page.locator("[data-wt-ctx]")
        expect(chip).to_be_visible(); expect(chip).to_contain_text("Document: Spec · topic records · version A")
        assert page.locator("body").get_attribute("data-mc-mode") == "full"                         # Chat view: the conversation fills the page
        assert sent == []                                                                          # choosing context sent nothing
        page.fill("[data-mc-input]", "What does the spec say about archiving?")
        assert sent == []                                                                          # typing sent nothing
        page.click("[data-mc-send]")
        expect(page.locator("[data-mc-thread]")).to_contain_text("Noted.", timeout=10000)
        assert len(sent) == 1 and "What does the spec say about archiving?" in sent[0] and "BEGIN ITEM 1" in sent[0] and "Put away keeps the card." in sent[0]
        expect(page.locator("[data-wt-ctx]")).to_have_count(0)                                      # the item is not attached to later messages
        expect(page.locator("[data-mc-thread]")).to_contain_text("Spec · topic records")           # the report under the message names what was included
        page.fill("[data-mc-input]", "And one more thing")
        page.click("[data-mc-send]")
        for _ in range(100):
            if len(sent) >= 2:
                break
            page.wait_for_timeout(100)
        assert len(sent) == 2 and "And one more thing" in sent[1] and "BEGIN ITEM" not in sent[1]
        assert not errors, errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_w6_chat_cards_split_one_conversation_and_state_kept_across_views_and_resizes(browser, server, floor):
    services, saved, sent = _wt_mc(server)
    try:
        ctx, page = _context(browser, server, 1440, 900)
        errors = _errors(page)
        tid, ids = _seed_topic(page, server, "W6 views")
        _wt_open(page, server, tid)
        mode = lambda: page.locator("body").get_attribute("data-mc-mode")
        assert mode() == "pill"                                                                    # Cards: the chat is folded away
        page.locator("[data-view='split']").click()
        assert mode() == "docked"
        chat, work = page.locator("[data-mc-panel]").bounding_box(), page.locator("[data-wt-body]").bounding_box()
        assert chat["x"] + chat["width"] <= work["x"] + 2 and chat["width"] >= 380 and work["width"] >= 640    # two columns, MC left
        page.fill("[data-mc-input]", "a draft I have not sent")
        page.get_by_role("button", name="Spec · topic records", exact=True).click()
        expect(page.locator(".wt-reader")).to_have_count(1)
        expect(page.locator("[data-mc-input]")).to_have_value("a draft I have not sent")            # opening an item does not touch the draft
        page.set_viewport_size({"width": 900, "height": 900})                                        # narrow while a draft and an item are open
        expect(page.locator("[data-wt-narrow]")).to_be_visible()
        assert mode() == "pill" and page.locator(".wt-reader").count() == 1
        assert page.locator("[data-view='split']").is_disabled()
        page.locator("[data-view='chat']").click()
        assert mode() == "full"
        expect(page.locator("[data-mc-input]")).to_have_value("a draft I have not sent")            # the same conversation, same draft
        page.set_viewport_size({"width": 1440, "height": 900})
        page.locator("[data-view='cards']").click()
        expect(page.locator(".wt-reader")).to_have_count(1)                                          # the item is recoverable after visiting Chat
        assert sent == []                                                                            # switching views sent nothing
        assert not errors, errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_w7_on_a_phone_chat_and_cards_are_single_full_width_views_with_clear_back(browser, server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page(); page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W7 phone")
    _wt_open(page, server, tid)
    assert page.locator("[data-view='split']").is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.get_by_role("button", name="Spec · topic records", exact=True).click()
    expect(page.locator(".wt-reader")).to_be_visible()
    assert page.locator(".wt-bottom").is_visible() and page.locator(".wt-bottom button").count() == 2
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.locator(".wt-bottom").get_by_role("button", name="Ask Master Craftsman ›").click()
    expect(page.locator("[data-wt-ctx]")).to_be_visible()
    assert page.locator("body").get_attribute("data-mc-mode") == "full"
    page.locator("[data-view='cards']").click()
    expect(page.locator(".wt-reader")).to_have_count(1)                                              # the document is still open
    page.go_back()                                                                                   # the phone's Back closes the item
    expect(page.locator("[data-wt-board]")).to_be_visible()
    assert not errors, errors
    ctx.close()


def test_w8_the_record_shows_who_wrote_what_and_marks_inbox_entries_as_claimed(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W8 record")
    topics = server["app"].extensions["guild_ui_next"]["services"]
    from minimoi_portal.guild_ui.topics import topics_of
    store = topics_of(topics)
    store.append_journal(tid, "robert", author={"kind": "agent", "name": "Codex", "model": "gpt-5.5"}, via="inbox", kind="disagreement",
                         text="Ask MC belongs in the card menu.", refs=[{"type": "file", "ref": "UI_AND_MORNING_REVIEW_codex.md"}])
    _wt_open(page, server, tid)
    expect(page.locator("details.wt-journal")).not_to_have_attribute("open", "")                      # the Record is one quiet line until opened
    page.locator("details.wt-journal > summary").click()
    expect(page.locator(".wt-journal li")).to_contain_text("disagreement")
    expect(page.locator(".wt-journal li")).to_contain_text("Codex (gpt-5.5)")
    expect(page.locator(".wt-journal li .wt-badge")).to_have_text("claimed")
    page.locator("details.wt-j-add > summary").click()
    page.select_option("select[aria-label='Kind of entry']", "decision")
    page.fill("textarea[aria-label='Your entry']", "Go with F.")
    page.get_by_role("button", name="Add entry").click()
    expect(page.locator(".wt-journal li").first).to_contain_text("decision")
    expect(page.locator(".wt-journal li").first).to_contain_text("Go with F.")
    expect(page.locator(".wt-journal li").first.locator(".wt-badge")).to_have_count(0)               # the owner's own entry carries no 'claimed' label
    assert not errors, errors
    ctx.close()


def test_w9_waiting_contributions_are_counted_and_join_the_record_only_when_imported(browser, server):
    ctx, page = _context(browser, server, 390, 844)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W9 inbox")
    from minimoi_portal.guild_ui.topics import topics_of
    store = topics_of(server["app"].extensions["guild_ui_next"]["services"])
    store.put_inbox(tid, "codex", kind="decision", text="Use version F.", model="gpt-5.5")
    store.put_inbox(tid, "grok", kind="finding", text="The card menu is hard to reach.")
    _wt_open(page, server, tid)
    expect(page.locator("details.wt-journal > summary")).to_contain_text("2 waiting")                  # visible even while the Record is closed
    page.locator("details.wt-journal > summary").click()
    expect(page.locator(".wt-inbox")).to_contain_text("2 contributions waiting from codex (1), grok (1)")
    expect(page.locator(".wt-journal li")).to_have_count(1)                                          # looking imported nothing ("Nothing recorded yet")
    assert store.inbox_waiting(tid, "robert") == {"codex": 1, "grok": 1}
    page.get_by_role("button", name="Import contributions").click()
    expect(page.locator(".wt-inbox")).to_have_count(0)
    expect(page.locator(".wt-journal li")).to_have_count(2)
    expect(page.locator("[data-wt-import-note]")).to_contain_text("Imported 2 from the inbox")           # it says what came in and where it went
    expect(page.locator("[data-wt-import-note]")).to_contain_text("a proposal from codex")
    expect(page.locator("[data-wt-import-note]")).to_contain_text("a finding from grok")
    expect(page.locator(".wt-journal li.fresh")).to_have_count(2)                                      # and the new rows are marked
    expect(page.locator(".wt-journal")).to_contain_text("proposal · not a decision")
    expect(page.locator(".wt-journal")).to_contain_text("only the owner decides")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    ctx.close()


def test_w10_a_topic_with_no_conversation_is_linked_by_an_explicit_step_and_never_by_looking(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W10 no link")
    from pathlib import Path
    import json as _json
    from minimoi_portal.guild_ui.topics import topics_of
    store = topics_of(server["app"].extensions["guild_ui_next"]["services"])
    f = Path(store.dir) / tid / "topic.json"
    doc = _json.loads(f.read_text()); doc.pop("conversation_id", None); f.write_text(_json.dumps(doc))
    reads = page.evaluate("async (u) => { const r = await fetch(u, {credentials:'same-origin'}); return (await r.json()).conversation_missing }", f"{server['url']}/guild-next/api/v1/topics/{tid}")
    assert reads is True and "conversation_id" not in _json.loads(f.read_text())                      # a GET alone left it unlinked
    _wt_open(page, server, tid)                                                                      # the page then links it with its own guarded POST
    expect(page.locator("[data-wt-board]")).to_be_visible()
    assert _json.loads(f.read_text()).get("conversation_id")
    assert not errors, errors
    ctx.close()


def test_w11_an_opened_item_has_a_visible_back_to_cards_control_on_phone_and_desktop(browser, server):
    for width, height in ((390, 844), (1440, 900)):
        ctx, page = _context(browser, server, width, height)
        errors = _errors(page)
        tid, ids = _seed_topic(page, server, f"W11 back {width}")
        for kind in ("doc", "note", "req", "design"):
            _wt_open(page, server, tid)
            page.locator(f".wt-card[data-id='{ids[kind]}'] .open").first.click()
            back = page.locator("[data-wt-back]")
            expect(back).to_be_visible()
            assert back.bounding_box()["height"] >= (43 if width < 701 else 28) and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            expect(page.locator(".wt-crumb")).to_contain_text("W11 back")
            back.click()                                                                              # the real control, not Escape or the browser's Back
            expect(page.locator("[data-wt-board]")).to_be_visible()
            expect(page.locator(".wt-reader")).to_have_count(0)
            assert page.evaluate("document.activeElement && document.activeElement.closest('.wt-card') && document.activeElement.closest('.wt-card').dataset.id") == ids[kind]   # focus returns to the card
        # a direct link to an item has no board history before it: the control still returns to the board
        _wt_open(page, server, tid, item=ids["doc"])
        expect(page.locator(".wt-reader")).to_be_visible()
        page.locator("[data-wt-back]").click()
        expect(page.locator("[data-wt-board]")).to_be_visible()
        assert not errors, errors
        ctx.close()


def test_w12_in_chat_view_the_conversation_starts_under_the_heading_and_its_composer_is_on_screen(browser, server, floor):
    """Robert, 8 Oct: the chat placement was too low. The panel must start right under the heading (not a screen below) and
    the Ask box must be visible without scrolling, at laptop and phone sizes."""
    for width, height in ((1470, 745), (1440, 900), (1920, 1080), (390, 844)):
        ctx, page = _context(browser, server, width, height)
        errors = _errors(page)
        tid, ids = _seed_topic(page, server, f"W12 chat {width}")
        go(page, f"{server['url']}/guild-next/guild/workshop?topic={tid}&view=chat")
        page.wait_for_selector("body[data-mc-mode='full']")
        page.wait_for_timeout(400)
        m = page.evaluate("""() => { const p = document.querySelector('.mc-panel[data-mode=full]').getBoundingClientRect();
          const h = document.querySelector('.wt-top').getBoundingClientRect(); const ta = document.querySelector('.mc-panel[data-mode=full] textarea, .mc-panel[data-mode=full] input[type=text]');
          const c = ta ? ta.getBoundingClientRect() : null;
          return { panelTop: p.top + scrollY, headBottom: h.bottom + scrollY, composerBottom: c ? c.bottom : null, vh: innerHeight, scrollable: document.documentElement.scrollHeight - innerHeight } }""")
        assert m["panelTop"] - m["headBottom"] <= 40, m                                              # right under the heading
        assert m["composerBottom"] is not None and m["composerBottom"] <= m["vh"], m                   # the Ask box is on screen without scrolling
        assert not errors, errors
        ctx.close()


def test_w13_the_work_starts_high_and_the_chat_panel_has_the_page_margin_on_desktop(browser, server, floor):
    """Robert, 8 Oct: six rows of chrome before the work, big navigation buttons, and the Split chat panel bleeding to the left edge.
    The cards start within a fixed distance of the top of the page, the page chrome is one slim row, and the docked panel is inset
    with rounded corners (its dark header never touches the left edge)."""
    for width, height in ((1470, 745), (1440, 900)):
        ctx, page = _context(browser, server, width, height)
        errors = _errors(page)
        tid, ids = _seed_topic(page, server, f"W13 high {width}")
        for view in ("cards", "split"):
            go(page, f"{server['url']}/guild-next/guild/workshop?topic={tid}&view={view}")
            page.wait_for_selector("[data-wt-board]")
            page.wait_for_timeout(300)
            m = page.evaluate("""() => { const r = (s) => { const e = document.querySelector(s); return e ? e.getBoundingClientRect() : null };
              const board = r('[data-wt-board]'), top = r('.wt-top'), views = r('.wt-views'), panel = r('.mc-panel[data-mode=docked]'), nav = r('.guild-subnav');
              return { board: board && board.top + scrollY, topH: top && top.height, navBottom: nav && nav.bottom + scrollY, viewsH: views && views.height,
                       panelLeft: panel && panel.left, panelTop: panel && panel.top + scrollY, radius: panel && getComputedStyle(document.querySelector('.mc-panel[data-mode=docked]')).borderTopLeftRadius,
                       overflow: panel && getComputedStyle(document.querySelector('.mc-panel[data-mode=docked]')).overflow } }""")
            assert m["topH"] <= 60 and m["viewsH"] <= 36, m                                             # one slim header row, quiet view control
            assert m["board"] - m["navBottom"] <= 80, m                                                  # the cards start right under the navigation
            if view == "split":
                assert m["panelLeft"] >= 12 and m["radius"] != "0px" and m["overflow"] == "hidden", m    # inset, rounded, clipped: no bleed
                assert m["panelTop"] - m["navBottom"] <= 24, m
        assert not errors, errors
        ctx.close()


def test_w14_the_columns_are_sized_by_dragging_and_the_chat_can_be_moved_or_closed(browser, server, floor):
    """Robert, 8 Oct: size the columns dynamically, not with fixed view buttons; chat prominent while talking, topic while reviewing;
    a close, minimise or move control on the chat box."""
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W14 columns")
    go(page, f"{server['url']}/guild-next/guild/workshop?topic={tid}")
    page.wait_for_selector("[data-wt-board]")
    assert page.locator("body").get_attribute("data-mc-mode") == "docked"                              # a wide window starts as chat | topic
    div = page.locator("[data-wt-divider]")
    expect(div).to_be_visible()
    width = lambda sel: page.evaluate("(s) => document.querySelector(s).getBoundingClientRect().width", sel)
    chat0, topic0 = width(".mc-panel"), width(".gu-main")
    box = div.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 80)
    page.mouse.down(); page.mouse.move(box["x"] + 330, box["y"] + 80, steps=6); page.mouse.up()       # drag it right: the chat grows
    chat1, topic1 = width(".mc-panel"), width(".gu-main")
    assert chat1 > chat0 + 200 and topic1 < topic0 - 200, (chat0, chat1, topic0, topic1)
    page.reload(); page.wait_for_selector("[data-wt-board]")
    assert abs(width(".mc-panel") - chat1) < 8                                                         # the width is remembered
    div.focus(); page.keyboard.press("ArrowLeft"); page.keyboard.press("ArrowLeft")
    assert width(".mc-panel") < chat1 - 20                                                             # the keyboard moves it too
    page.keyboard.press("Enter")
    assert abs(width(".mc-panel") / (width(".mc-panel") + width(".gu-main")) - 0.42) < 0.05           # Enter resets it
    # move: the chat swaps sides
    left0 = page.evaluate("document.querySelector('.mc-panel').getBoundingClientRect().left")
    page.locator("[data-wt-swap]").click()
    left1 = page.evaluate("document.querySelector('.mc-panel').getBoundingClientRect().left")
    assert left1 > left0 + 400, (left0, left1)
    page.locator("[data-wt-swap]").click()
    # dragging all the way left folds the chat; all the way right gives it the whole page
    box = div.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 80); page.mouse.down(); page.mouse.move(8, box["y"] + 80, steps=8); page.mouse.up()
    assert page.locator("body").get_attribute("data-mc-mode") == "pill"
    page.locator("[data-view='split']").click()
    page.locator("[data-wt-close]").click()                                                            # the close control folds the chat; the Chat button brings it back
    assert page.locator("body").get_attribute("data-mc-mode") == "pill"
    expect(page.locator("[data-wt-board]")).to_be_visible()
    assert not errors, errors
    ctx.close()


def test_w15_editing_happens_in_the_page_in_a_large_box_and_keeps_the_earlier_version(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W15 edit")
    _wt_open(page, server, tid, item=ids["doc"])
    page.locator("[data-wt-edit]").click()
    ta = page.locator("textarea[data-wt-editor]")
    expect(ta).to_be_visible()
    expect(page.locator("dialog[open]")).to_have_count(0)                                              # no small window
    assert ta.bounding_box()["height"] >= 300 and ta.bounding_box()["width"] >= 500
    ta.fill("# Edited\n\nA new paragraph.")
    page.locator(".wt-crumb [data-wt-back]").click()                                                    # leaving with unsaved text is refused, not lost
    expect(page.locator("textarea[data-wt-editor]")).to_have_value("# Edited\n\nA new paragraph.")
    page.keyboard.press("Escape")                                                                      # Escape does not discard an edit either
    expect(page.locator("textarea[data-wt-editor]")).to_be_visible()
    page.locator("[data-wt-save]").click()
    expect(page.locator(".wt-reader .wt-eyebrow")).to_contain_text("version B")
    expect(page.locator(".wt-doc")).to_contain_text("A new paragraph.")
    page.select_option("select[aria-label='Version']", "A")
    expect(page.locator(".wt-doc")).to_contain_text("Nothing is deleted by default")           # the earlier text is still there
    expect(page.locator(".wt-doc")).not_to_contain_text("A new paragraph.")
    page.locator("[data-wt-edit]").click(); page.locator("[data-wt-cancel]").click()
    expect(page.locator("textarea[data-wt-editor]")).to_have_count(0)
    assert not errors, errors
    ctx.close()


def test_w16_a_card_menu_always_opens_inside_the_window(browser, server):
    for width, height in ((390, 844), (1440, 900), (1000, 500)):
        ctx, page = _context(browser, server, width, height)
        errors = _errors(page)
        tid, ids = _seed_topic(page, server, f"W16 menu {width}")
        _wt_open(page, server, tid)
        for card in page.locator(".wt-card").all():
            btn = card.locator(".more")
            btn.scroll_into_view_if_needed()
            page.evaluate("(el) => { const r = el.getBoundingClientRect(); window.scrollBy(0, r.top - (innerHeight - 56)); }", btn.element_handle())   # the button sits at the bottom edge
            tops = page.evaluate("(el) => el.getBoundingClientRect().bottom", btn.element_handle())
            btn.click()
            m = page.locator(".wt-menu").bounding_box()
            assert m and m["y"] >= 0 and m["y"] + m["height"] <= height + 1 and m["x"] >= 0 and m["x"] + m["width"] <= width + 1, (width, height, m)
            page.keyboard.press("Escape")
        assert not errors, errors
        ctx.close()


def test_w17_buttons_are_small_and_quiet_on_a_desktop(browser, server):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    tid, ids = _seed_topic(page, server, "W17 buttons")
    _wt_open(page, server, tid, item=ids["doc"])
    page.locator("[data-wt-cmode]").click(); page.locator(".wt-doc [data-block='1']").click()
    page.fill("textarea[aria-label^='Comment on paragraph']", "Check this."); page.get_by_role("button", name="Add comment").click()
    expect(page.locator(".wt-cmt[data-cid]")).to_have_count(1)
    big = page.evaluate("""() => [...document.querySelectorAll('.wt button, .wt-views button')].filter((b) => b.offsetParent).map((b) => [b.textContent.trim().slice(0, 24), Math.round(b.getBoundingClientRect().height)]).filter(([, h]) => h > 36)""")
    assert big == [], big                                                                                # nothing on the page is a big button
    small = page.evaluate("""() => [...document.querySelectorAll('.wt-cmt .acts button')].map((b) => Math.round(b.getBoundingClientRect().height))""")
    assert small and max(small) <= 26, small                                                            # the comment actions are quiet chips
    assert not errors, errors
    ctx.close()


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
    expect(page.locator("[data-stale-banner]")).to_contain_text("last good read")    # the rail shows no Continue line any more (5 Oct)
    expect(page.locator("[data-rail-continue]")).to_have_count(0)
    poll(page)
    expect(page.locator("[data-stale-banner]")).to_contain_text("no good read since")
    page.unroute(FLOOR_API)
    poll(page)
    expect(page.locator("[data-stale-banner]")).to_be_hidden()
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
    expect(page.locator("[data-mc-record]")).to_have_attribute("aria-pressed", "true")
    go(page, f"{server['url']}/guild-next/guild/build/items/12")        # a new page load, still off
    page.click("[data-mc-pill]")                                         # off the floor the chat opens from its pill
    _private_on(page)
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
    expect(history.locator('.fh-row[data-conv="shop-floor-thread"]')).to_have_count(1)   # the real thread; every row is a real conversation (area chats join it)
    expect(rail).to_be_visible()
    expect(rail.locator("[data-build-card]")).to_have_count(0)                   # the hero left the rail (Guild 1.1 slice 1)
    _hero_in_header(page)
    h, c, r = _box(page, "[data-floor-history]"), _box(page, "[data-mc-thread]"), _box(page, "#main")
    assert h["x"] < c["x"] < r["x"]                                              # history | chat | rail
    assert c["width"] <= 730                                                     # the text column is capped
    # 5 Oct: no Needs-you badge, no urgent line, no quiet line in the Chat. The rail is Linked work and Files.
    expect(page.locator("[data-needs-badge], [data-floor-urgent], [data-rail-quiet], [data-open-wall]")).to_have_count(0)
    expect(rail.locator("[data-linked-work]")).to_be_visible()
    expect(rail.locator("[data-conv-files-sec]")).to_be_visible()
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
        expect(page.locator("[data-rail-quiet]")).to_have_count(0)                  # no quiet line, no Open wall (5 Oct)
        expect(page.locator("[data-open-wall]")).to_have_count(0)
        expect(page.locator("[data-floor-urgent]")).to_have_count(0)
        expect(page.locator("[data-rail-continue]")).to_have_count(0)
        expect(page.locator("[data-linked-empty]")).to_be_visible()
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
    assert hero["height"] <= 200 and hero["width"] >= 380                         # compact (banner + header), edge to edge
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
    expect(page.locator("[data-mc-panel]")).to_be_visible()                        # the chat panel opens on the wall
    ctx.close()
    phone, ppage = _context(browser, server, **PHONE)
    go(ppage, f"{server['url']}/guild-next/guild/build/bench")
    expect(ppage.locator('[data-panel="needs"]')).to_be_visible()                # the wall opens as one column
    boxes = [ppage.locator(f'[data-panel="{p}"]').bounding_box() for p in ("needs", "continue")]
    assert abs(boxes[0]["x"] - boxes[1]["x"]) < 2 and boxes[1]["y"] > boxes[0]["y"]
    phone.close()


def test_slice1_navigation_reaches_every_guild_page_and_the_truthful_labs_page(browser, server, fresh_queue):
    # Build is Chat · Board · Build Log · Workshop · Rooms, with no More menu (5 Oct).
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    nav = page.locator(".guild-subnav")
    expect(nav.locator("> a")).to_have_text(["Chat", "Board", "Build Log", "Workshop", "Rooms"])
    expect(page.locator("[data-subnav-more]")).to_have_count(0)
    for label, where in (("Board", "/guild-next/guild/board"), ("Build Log", "/guild-next/guild/build/log"),
                         ("Workshop", "/guild-next/guild/workshop"),
                         ("Rooms", "/guild-next/guild/rooms"), ("Chat", "/guild-next/guild/build")):
        with page.expect_navigation():
            nav.locator("> a", has_text=label).click()
        page.wait_for_selector("body[data-ready=true]")
        assert page.url.split("?")[0].endswith(where), (label, page.url)
        expect(page.locator(".guild-subnav > [aria-current='page']")).to_have_text(label)
        if label == "Rooms":
            expect(page.locator("[data-rm-composer]")).to_be_visible()        # the real page since slice 4
    assert not [e for e in errors if "401" not in e], errors      # Rooms before Records' sign-in: 401 by design
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
        banner = page.locator("[data-stale-banner]")
        expect(banner).to_be_hidden()
        expect(page.locator("text=/Nothing needs you/").filter(visible=True)).to_have_count(0)          # the quiet line is retired: no "right now" claim at all
        page.route(FLOOR_API, lambda route: route.fulfill(status=503, content_type="application/json",
                                                            body='{"error": "unavailable", "message": "down"}'))
        poll(page)
        expect(banner).to_be_visible()                                             # a failed read says so ...
        expect(page.locator('[data-light="build_queue"]')).to_have_attribute("data-light-state", "stale")   # ... on the lights, not a live value
        expect(page.locator("text=/Nothing needs you/").filter(visible=True)).to_have_count(0)
        page.unroute(FLOOR_API)
        poll(page)
        expect(banner).to_be_hidden()
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
    expect(page.locator("[data-linked-work]")).to_be_visible()                     # the rail (Linked work, Files) stays reachable
    ctx.close()


def test_fix_polish_bubbles_folded_help_and_no_platform_card_in_the_chat(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-mc-thread] [data-briefing]")).to_have_count(0)       # the chat holds only conversation
    expect(page.locator("#main [data-briefing-text]")).to_have_count(0)               # the generic status paragraph is retired (5 Oct)
    expect(page.locator("[data-mc-off-lines]")).to_have_count(0)                      # so is the folded help
    expect(page.locator("[data-mc-record]")).to_be_visible()
    expect(page.locator("[data-mc-invite]")).to_be_visible()
    page.click("[data-mc-invite]")                                                    # grey, clickable, one answer
    expect(page.locator("[data-invite-soon]")).to_have_text("Coming soon.")
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
    # On the floor there is no Type bar any more (the Ask box is already there); the composer stays inside the screen.
    form, dock = _box(page, "[data-mc-composer]"), _box(page, "[data-mc-dock-bottom]")
    assert page.locator(".mc-phone-bar").count() == 0
    assert form["y"] + form["height"] <= 844 + 1
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
    # Area chats (one a day per area) now sit in the list too, so the test no longer assumes it holds exactly one row.
    expect(page.locator('[data-conv="shop-floor-thread"]')).to_have_count(1)
    expect(page.locator('[data-conv="shop-floor-thread"]')).to_contain_text("Shop floor thread")
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
    go(page, f"{server['url']}/guild-next/guild/workshop?item=12")             # the old address: it now lands on Operate → Build host
    assert "/guild-next/guild/operate/build-host?item=12" in page.url
    expect(page.locator(".guild-subnav .subnav-active")).to_contain_text("Build host")
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
    go(page, f"{server['url']}/guild-next/guild/operate/build-host?item=12")
    expect(page.locator(".phone-summary")).to_have_count(0)
    # 5 Oct: the Workshop leads with the disk card, folds the host observations, then the chat. Opening the fold shows
    # Now, Needs you, Budget and the rest in that order, still above the chat.
    expect(page.locator("[data-ws-op='disk']")).to_be_visible()
    expect(page.locator("[data-ws-now]")).to_be_hidden()
    ys0 = _visible_order(page, ["[data-ws-op='disk']", "details.card > summary", ".mc-panel"])
    assert ys0 == sorted(ys0), ys0
    _open_host_observations(page)
    ys = _visible_order(page, ["[data-ws-now]", "[data-ws-needs]", "[data-ws-budget]", "[data-ws-more]", ".mc-panel"])
    assert ys == sorted(ys), ys
    expect(page.locator("[data-ws-more]")).not_to_have_attribute("open", "")      # scope, queue and recovery stay folded on a phone
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
    expect(line.locator('[data-slot="state"]')).to_have_text("Writing…")      # from the first text; "…" until then
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
    expect(line.locator("[data-mc-stop]")).to_be_visible()                  # the live line carries only Stop; the billing note comes with the stop
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
    expect(page.locator('[data-mc-thread] [data-kind="platform"]').last).to_contain_text("may already have run commands or changed files")
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

DOORS = [("Build", "/guild-next/guild/build"), ("Operate", "/guild-next/guild/operate"),
         ("Improve", "/guild-next/guild/improve"), ("Prototype Lab", "/guild-next/guild/experiment")]


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
    expect(hero.locator("img.hero-art")).to_have_attribute("src", "/static/guild/guild-workshop-team.webp")     # the workshop banner
    assert "guild-mc-portrait.jpg" in page.locator(".hero-portrait").evaluate("e => getComputedStyle(e).backgroundImage")
    # Both images really load (same origin, allowed by the CSP).
    for name in ("guild-workshop-team.webp", "guild-mc-portrait.jpg"):
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
    expect(page.locator(".guild-subnav > a")).to_have_text(["Chat", "Board", "Build Log", "Workshop", "Rooms"])
    expect(page.locator("[data-subnav-more]")).to_have_count(0)
    assert _no_page_overflow(page)
    with page.expect_navigation():
        drawers.nth(0).click()                                                    # Build → the Chat
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.split("?")[0].endswith("/guild-next/guild/build")
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
    BUILD_PAGES = {"/guild-next/guild/build", "/guild-next/guild/build/bench", "/guild-next/guild/build/log",
                   "/guild-next/guild/rooms", "/guild-next/guild/build/queue", "/guild-next/guild/workshop"}
    ctx, page = _context(browser, server, width, height)
    errors = _errors(page)
    for path in ("/guild-next/", "/guild-next/guild/build", "/guild-next/guild/build/bench",
                 "/guild-next/guild/build/log", "/guild-next/guild/rooms", "/guild-next/guild/build/queue",
                 "/guild-next/guild/workshop", "/guild-next/guild/operate", "/guild-next/guild/labs"):
        page.goto(f"{server['url']}{path}")
        page.wait_for_load_state("load")
        if path in BUILD_PAGES:
            tabs = page.locator(".guild-subnav > a")
            expect(tabs).to_have_text(["Chat", "Board", "Build Log", "Workshop", "Rooms"])
            for i in range(5):
                expect(tabs.nth(i)).to_be_in_viewport()                           # all five tabs fit, phone included
            expect(page.locator("[data-subnav-more]")).to_have_count(0)
        assert _no_page_overflow(page), path
    page.goto(f"{server['url']}/guild-next/guild/build")
    page.wait_for_selector("body[data-ready=true]")
    # Rooms asks Records first; before Records' own sign-in that is a 401 by design.
    assert not [e for e in errors if "401" not in e], errors
    ctx.close()


def test_s1_phone_chat_has_the_hero_in_its_header(browser, server, floor):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _hero_in_header(page)
    hero = _box(page, "[data-chat-hero]")
    assert hero["y"] < 200 and hero["height"] <= 200                               # under the bars: a 40 px banner and the header
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
    expect(_hero_chip(page)).to_be_hidden()                                        # on the record: no Private chip
    page.fill("#mc-input", "Check the queue lock")
    page.click("[data-mc-send]")
    note = page.locator('[data-mc-thread] [data-kind="note"][data-note]').last
    expect(note).to_contain_text("Check the queue lock")
    bar = page.locator("[data-sel-bar]")
    expect(bar).to_be_hidden()
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note]:last-child .msg-text')
    expect(bar).to_be_visible()
    expect(bar.locator("[data-sel-room]")).to_be_enabled()                         # Take to a Room (slice 4)
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
    expect(_hero_chip(page)).to_be_visible()                                       # Private: the compact chip
    assert "grayscale" in page.locator("[data-chat-hero]").evaluate("e => getComputedStyle(e).filter")
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note] .msg-text')
    page.wait_for_timeout(300)
    expect(bar).to_be_hidden()
    assert [m for m, _u in posts if m == "POST"] == ["POST"]                       # nothing more was sent
    page.click("[data-mc-record]")                                                 # back on the record
    expect(_hero_chip(page)).to_be_hidden()                                        # on the record: no chip
    assert not errors, errors
    ctx.close()


def _hero_chip(page):
    return page.locator("[data-private-chip]")


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
    expect(_hero_chip(page)).to_be_visible()
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note] .msg-text')
    assert page.evaluate("window.getSelection().toString()") != ""
    expect(page.locator("[data-sel-bar]")).to_be_hidden()
    page.click("[data-mc-record]")                                                 # back on the record
    expect(_hero_chip(page)).to_be_hidden()
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
    # The default view is Ranked (5 Oct): only ranked items, pinned in rank order. Work in progress is its own view.
    assert _bl_ids(page) == [12, 7]
    expect(page.locator("[data-bl-row].bl-pinned")).to_have_count(2)
    expect(page.locator("[data-bl-count]")).to_contain_text("2 of 2 · 7 total")
    for view, ids in (("progress", [12, 41]), ("ready", [7]), ("trouble", [31]), ("roadmap", None), ("all", None)):
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
    _open_bl_columns(page)
    page.uncheck("[data-bl-pin]")
    page.keyboard.press("Escape")                                                  # View options closes
    expect(page.locator(".bl-vo")).to_be_hidden()
    page.click('[data-bl-sort="updated"]')
    assert _bl_ids(page)[:2] == [41, 42]
    expect(page.locator('th[data-col="updated"]')).to_have_attribute("aria-sort", "descending")
    # View options: hide a column; remembered on reload.
    _open_bl_columns(page)
    page.uncheck('[data-bl-col="author"]')
    expect(page.locator('th[data-col="author"]')).to_have_count(0)
    # (The Compact / Comfortable density switch was removed from the page in the 5 Oct redesign.)
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator('th[data-col="author"]')).to_have_count(0)
    expect(page.locator('[data-bl-view="all"]')).to_have_attribute("aria-pressed", "true")
    assert _no_page_overflow(page)
    assert not errors, errors
    ctx.close()


def _open_bl_columns(page):
    """Ranks and columns sit in a disclosure inside the Build Log actions menu (5 Oct)."""
    if not page.locator("[data-bl-vopts]").evaluate("e => e.open"):
        page.click("[data-bl-vopts] > summary")
    nested = page.locator(".bl-vo details", has_text="Table columns and ranks").first
    if not nested.evaluate("e => e.open"):
        nested.locator("> summary").click()


def _set_rank(page, value):
    """The drawer's rank is a number field now (5 Oct): type it and leave the field. The result shows at the top of the page."""
    field = page.locator("[data-bl-drawer] [data-bl-inline-rank]")
    field.fill(str(value))
    field.dispatch_event("change")


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
    # Rank 1 for #41: one write and a receipt; the others keep their ranks.
    _set_rank(page, 1)
    expect(page.locator("[data-bl-rank-result]")).to_contain_text("Rank saved · verified · receipt")
    page.click('[data-bl-view="next"]')
    assert _bl_ids(page)[:3] == [41, 12, 7]
    ranks = {i["id"]: i.get("owner_rank") for i in json.loads(build_log.read_text())}
    # Ranks can be shared (5 Oct): giving #41 rank 1 does not push the others down.
    assert (ranks[41], ranks[12], ranks[7]) == (1, 1, 2)
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
    _set_rank(other, 1)
    expect(other.locator("[data-bl-rank-result]")).to_contain_text("Rank saved")
    page.click('[data-bl-view="all"]')
    page.click('[data-bl-open="43"]')
    _set_rank(page, 1)                                                             # this tab's digest is stale
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
    _set_rank(page, 3)
    expect(page.locator("[data-bl-rank-result]")).to_contain_text("Private")
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


def _bd_discard(page, note_id):
    """Discard lives in the note's small ⋯ menu (5 Oct)."""
    page.locator(f'[data-bd-note="{note_id}"] .bd-edit > summary').click()
    page.locator(f'[data-bd-bin="{note_id}"]').click()


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
    page.locator("[data-bd-note-form] details > summary").click()                     # label, link and image sit under "More"
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
    _bd_discard(page, ids[2])
    expect(page.locator('[data-bd-show] option[value="trash"]')).to_have_text("Trash (1)")
    page.select_option("[data-bd-show]", "trash")
    page.locator(f'[data-bd-restore="{ids[2]}"]').click()
    expect(page.locator('[data-bd-show] option[value="trash"]')).to_have_text("Trash (0)")
    page.select_option("[data-bd-show]", "active")
    _bd_discard(page, ids[2])
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
    expect(page.locator("[data-bd-result]")).to_contain_text("Private")
    page.click("[data-bd-add] > summary")
    page.click('[data-bd-open="photo"]')
    page.set_input_files("[data-bd-photo-file]", str(_jpeg_file(tmp_path)))
    expect(page.locator("[data-bd-photo-result]")).to_contain_text("Private")
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



# ── Guild 1.1 slice 4 (spec §6, §11): Rooms on Records ────────────────────────

@pytest.fixture
def rooms(server, floor, tmp_path):
    """The real Records app on a temporary folder behind the portal's bridge."""
    from minimoi_portal import records_bridge as RB
    from rooms_helpers import RecordsTransport, owner_key, records_app
    app = records_app(tmp_path / "records")
    transport = RecordsTransport(app)
    saved = (RB._TRANSPORT, RB.LOCAL_HOSTS)
    RB._TRANSPORT = transport
    RB.LOCAL_HOSTS = {server["url"].split("//", 1)[1]}
    assert server["app"].extensions["records_bridge"]["state"] == "on"
    yield {"app": app, "transport": transport, "owner_key": owner_key(app)}
    RB._TRANSPORT, RB.LOCAL_HOSTS = saved


def _records_fetch(page, path, body=None, key=None):
    return page.evaluate("""async ([path, body, key]) => {
        const opts = { method: body ? 'POST' : 'GET', credentials: 'same-origin',
          headers: body ? { 'Content-Type': 'application/json', 'Idempotency-Key': key } : {} };
        if (body) opts.body = JSON.stringify(body);
        const r = await fetch('/app/records/api' + path, opts);
        return { status: r.status, body: await r.json().catch(() => null) };
    }""", [path, body, key])


def _agent_post(rooms, room_id, text):
    """An agent posting with its own Records key (what roomctl sends), straight to Records."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    client = rooms["app"].test_client(use_cookies=False)
    base = "http://minimoi-records:18880"
    owner = {"Authorization": f"Bearer {rooms['owner_key']}", "Content-Type": "application/json"}
    client.post("/api/v1/principals", base_url=base, headers={**owner, "Idempotency-Key": "p-codex-01"},
                data=_json.dumps({"id": "codex", "label": "Codex"}))
    client.post(f"/api/v1/rooms/{room_id}/members", base_url=base, headers={**owner, "Idempotency-Key": "m-codex-01"},
                data=_json.dumps({"actor": "codex", "role": "contributor"}))
    issued = client.post("/api/v1/platform/credentials", base_url=base, headers={**owner, "Idempotency-Key": "c-codex-01"},
                         data=_json.dumps({"principal": "codex", "label": "Codex CLI", "grants": {room_id: ["read", "post"]},
                                           "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}))
    token = issued.get_json()["access_token"]
    r = client.post(f"/api/v1/rooms/{room_id}/events", base_url=base,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Idempotency-Key": "agent-msg-01"},
                    data=_json.dumps({"body": text, "kind": "message"}))
    assert r.status_code == 201, r.data


def _rooms_ready(page, server, rooms, title="Screen review"):
    r = _records_fetch(page, "/login", {"token": rooms["owner_key"]}, "login-0001")
    assert r["status"] == 200
    r = _records_fetch(page, "/v1/rooms", {"title": title, "purpose": "Review the Rooms slice", "mode": "meeting",
                                           "recording_acknowledged": True}, f"room-{title[:6]}-0001")
    assert r["status"] == 201
    return r["body"]["result"]["id"]


def test_s4_desktop_signin_draft_chat_files_agent_and_pause(browser, server, rooms, tmp_path):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/rooms")
    expect(page.locator(".guild-subnav > [aria-current='page']")).to_have_text("Rooms")
    # No Records sign-in yet: say so, and keep the draft.
    expect(page.locator("[data-rm-signin]")).to_be_visible()
    expect(page.locator("[data-rm-signin-link]")).to_have_attribute("href", "/app/records/")
    page.fill("[data-rm-input]", "A draft written before signing in")
    room_id = _rooms_ready(page, server, rooms)
    go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}")
    expect(page.locator("[data-rm-signin]")).to_be_hidden()
    expect(page.locator("[data-rm-title]")).to_have_text("Screen review")
    expect(page.locator("[data-rm-composer]")).to_be_in_viewport()
    # The draft typed before signing in followed into the room (#288 review F3).
    expect(page.locator("[data-rm-input]")).to_have_value("A draft written before signing in")
    # Send a message (the draft typed in this room before is kept until sent).
    page.fill("[data-rm-input]", "Attaching the review pack once.")
    page.click("[data-rm-send]")
    expect(page.locator(".rm-msg .rm-body").last).to_have_text("Attaching the review pack once.")
    expect(page.locator("[data-rm-input]")).to_have_value("")
    # Attach a file: it appears in the thread and in the Files drawer, with a preview.
    notes = tmp_path / "review-notes.md"
    notes.write_text("# Review notes\np.18 fixed columns\n")
    page.set_input_files("[data-rm-attach]", str(notes))
    expect(page.locator("[data-rm-open-file]").first).to_contain_text("review-notes.md")
    page.click("[data-rm-files-toggle]")
    expect(page.locator("[data-rm-files]")).to_be_visible()
    page.locator("[data-rm-files] [data-rm-open-file]").first.click()
    expect(page.locator("[data-rm-preview] pre")).to_contain_text("p.18 fixed columns")
    page.click("[data-rm-file-details] summary")
    page.fill("[data-rm-save-path]", "docs/design/review-notes.md")
    page.click("[data-rm-save] button[type=submit]")
    expect(page.locator("[data-rm-status]")).to_contain_text("Project home recorded: docs/design/review-notes.md · v1. Nothing was written")
    page.click("[data-rm-files-back]")
    expect(page.locator("[data-rm-files] .rm-flist")).to_be_visible()
    page.click("[data-rm-files-close]")
    # An agent's message (its own Records key) is attributed and labelled.
    _agent_post(rooms, room_id, "Codex: p.12 still says kept.")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    agent = page.locator('.rm-msg[data-actor="codex"]')
    expect(agent).to_contain_text("Codex: p.12 still says kept.")
    expect(agent.locator('[data-tag="agent"]')).to_have_text("agent")
    expect(page.locator("[data-rm-mic]")).to_be_disabled()
    expect(page.locator("[data-rm-mic]")).to_contain_text("dictation · later")
    # Pause: the room says so and writes are refused.
    page.click("[data-rm-details] > summary")
    page.fill("[data-rm-state-note]", "Lunch break")
    page.click("[data-rm-set-state='paused']")
    expect(page.locator("[data-rm-state]")).to_have_text("Paused")
    expect(page.locator("[data-rm-send]")).to_be_disabled()
    expect(page.locator("[data-rm-composer]")).to_be_in_viewport()
    refused = _records_fetch(page, f"/v1/rooms/{room_id}/events", {"body": "x", "kind": "message"}, "paused-0001")
    assert refused["status"] == 409
    assert _no_page_overflow(page)
    assert not [e for e in errors if "401" not in e and "409" not in e], errors     # 401 before sign-in, 409 by design
    ctx.close()


def test_s4_phone_composer_always_visible_and_the_room_list(browser, server, rooms):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/rooms")
    room_id = _rooms_ready(page, server, rooms, title="Phone room")
    go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}")
    for n in range(12):
        _records_fetch(page, f"/v1/rooms/{room_id}/events", {"body": f"message {n} " + "words " * 20, "kind": "message"},
                       f"phone-msg-{n:04d}")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator(".rm-msg")).to_have_count(12)
    comp = page.locator("[data-rm-composer]")
    expect(comp).to_be_in_viewport()
    box = comp.bounding_box()
    assert box["y"] + box["height"] <= 844 and box["x"] >= 0 and box["x"] + box["width"] <= 390
    expect(page.locator("[data-rm-input]")).to_be_visible()
    page.click("[data-rm-list-open]")
    expect(page.locator("[data-rm-list] [data-rm-room]")).to_have_count(1)
    page.click("[data-rm-list-close]")
    assert _no_page_overflow(page)
    ctx.close()


def test_s4_take_to_a_room_shares_the_stored_note_by_id(browser, server, rooms):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/rooms")
    room_id = _rooms_ready(page, server, rooms, title="Take room")
    go(page, f"{server['url']}/guild-next/guild/build")
    page.fill("#mc-input", "Decide the Rooms header art")
    page.click("[data-mc-send]")
    note = page.locator('[data-mc-thread] [data-kind="note"][data-note]').last
    expect(note).to_contain_text("Decide the Rooms header art")
    note_id = note.get_attribute("data-note")
    _select_in(page, '[data-mc-thread] [data-kind="note"][data-note]:last-child .msg-text')
    with page.expect_navigation():
        page.click("[data-sel-room]")
    page.wait_for_selector("body[data-ready=true]")
    assert f"take={note_id}" in page.url
    take = page.locator("[data-rm-take]")
    expect(take).to_be_visible()
    expect(page.locator("[data-rm-take-text]")).to_have_text("Decide the Rooms header art")
    page.click("[data-rm-take-send]")
    expect(page.locator("[data-rm-take-result]")).to_have_text("Shared into this room.")
    shared = page.locator('.rm-msg [data-tag="chat"]')
    expect(shared).to_have_text("from Guild Chat")
    expect(page.locator(".rm-msg .rm-body").last).to_contain_text(f"From Guild Chat · note #{note_id}")
    assert "take=" not in page.url
    ctx.close()


def test_s4_a_prepared_take_is_discarded_when_going_off_the_record_and_cancel_sticks(browser, server, rooms):
    """#288 review F2 and F8: switching mode discards a prepared Take, which is
    then never sent; Cancel drops it from the address so a reload cannot
    bring it back."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/rooms")
    room_id = _rooms_ready(page, server, rooms, title="Off room")
    go(page, f"{server['url']}/guild-next/guild/build")
    page.fill("#mc-input", "Keep this for the room")
    page.click("[data-mc-send]")
    note = page.locator('[data-mc-thread] [data-kind="note"][data-note]').last
    expect(note).to_contain_text("Keep this for the room")
    note_id = note.get_attribute("data-note")
    go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}&take={note_id}")
    expect(page.locator("[data-rm-take]")).to_be_visible()
    before = len(_records_fetch(page, f"/v1/rooms/{room_id}", None, None)["body"]["events"])
    assert page.locator("[data-mc-record]").count() == 1
    page.evaluate("document.querySelector('[data-mc-record]').click()")         # off the record
    expect(page.locator("[data-rm-take]")).to_be_hidden()
    expect(page.locator("[data-rm-status]")).to_contain_text("discarded")
    assert "take=" not in page.url
    page.evaluate("document.querySelector('[data-rm-take-send]').click()")      # a stale click does nothing
    page.wait_for_timeout(300)
    assert len(_records_fetch(page, f"/v1/rooms/{room_id}", None, None)["body"]["events"]) == before
    page.evaluate("document.querySelector('[data-mc-record]').click()")         # back on the record
    # Cancel sticks across a reload.
    go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}&take={note_id}")
    expect(page.locator("[data-rm-take]")).to_be_visible()
    page.click("[data-rm-take-cancel]")
    assert "take=" not in page.url
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-rm-take]")).to_be_hidden()
    ctx.close()


def test_s4_a_take_in_a_tab_with_unknown_mode_waits_for_confirm_and_off_drops_it(browser, server, rooms):
    """#288 re-check R1 and R2: in a tab whose record mode is unknown, the Take
    box says so and offers Confirm on the record; nothing is fetched before.
    Choosing Off instead drops the Take for good."""
    ctx, first = _context(browser, server, 1440, 900)
    go(first, f"{server['url']}/guild-next/guild/rooms")
    room_id = _rooms_ready(first, server, rooms, title="Unknown room")
    go(first, f"{server['url']}/guild-next/guild/build")
    first.fill("#mc-input", "Share me once confirmed")
    first.click("[data-mc-send]")
    note = first.locator('[data-mc-thread] [data-kind="note"][data-note]').last
    expect(note).to_contain_text("Share me once confirmed")
    note_id = note.get_attribute("data-note")
    first.evaluate(f"localStorage.setItem('{OFF_TABS}', '{{garbled')")
    fresh = ctx.new_page()
    shares = []
    fresh.on("request", lambda r: shares.append(r.url) if "/share" in r.url else None)
    go(fresh, f"{server['url']}/guild-next/guild/rooms?room={room_id}&take={note_id}")
    expect(fresh.locator("body")).to_have_attribute("data-record-known", "false")
    expect(fresh.locator("[data-rm-take]")).to_be_visible()
    expect(fresh.locator("[data-rm-take-meta]")).to_contain_text("Confirm on the record")
    expect(fresh.locator("[data-rm-take-send]")).to_be_disabled()
    fresh.wait_for_timeout(300)
    assert shares == []
    fresh.click("[data-rm-take-confirm]")
    expect(fresh.locator("[data-rm-take-text]")).to_have_text("Share me once confirmed")
    expect(fresh.locator("[data-rm-take-confirm]")).to_be_hidden()
    assert len(shares) == 1
    fresh.click("[data-rm-take-send]")
    expect(fresh.locator("[data-rm-take-result]")).to_have_text("Shared into this room.")
    # Another unknown tab chooses Off: the Take is dropped and never prepared.
    first.evaluate(f"localStorage.setItem('{OFF_TABS}', '{{garbled')")
    other = ctx.new_page()
    go(other, f"{server['url']}/guild-next/guild/rooms?room={room_id}&take={note_id}")
    expect(other.locator("[data-rm-take]")).to_be_visible()
    other.evaluate("document.querySelector('[data-mc-record]').click()")
    expect(other.locator("[data-rm-take]")).to_be_hidden()
    assert "take=" not in other.url
    ctx.close()


def _end_records_session(ctx, page):
    """Clear Records' session cookie and make sure it stays cleared. The room page polls, and Records re-sets its cookie on
    every answer, so a poll that was already in flight can put the cookie back a moment after a single clear; the test then
    sent its message with a live session (the old flake, about one run in four). Wait for the page to be quiet, clear,
    and check again until the cookie is really gone."""
    for _ in range(8):
        page.wait_for_load_state("networkidle")
        ctx.clear_cookies(name="minimoi_room_poc")
        page.wait_for_timeout(250)
        if not any(c["name"] == "minimoi_room_poc" for c in ctx.cookies()):
            return
    raise AssertionError("the Records session cookie kept coming back")


def test_s4_an_expired_records_login_says_sign_in_and_keeps_the_draft(browser, server, rooms):
    ctx, page = _context(browser, server, 1440, 900)
    writes = []
    page.on("request", lambda r: writes.append(r.url) if r.method == "POST" and "/app/records/api/v1/rooms/" in r.url else None)
    go(page, f"{server['url']}/guild-next/guild/rooms")
    room_id = _rooms_ready(page, server, rooms, title="Expiry room")
    go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}")
    expect(page.locator("[data-rm-title]")).to_have_text("Expiry room")
    page.fill("[data-rm-input]", "Half-written thought about the art")
    _end_records_session(ctx, page)                                  # Records' own session ends
    page.click("[data-rm-send]")
    expect(page.locator("[data-rm-signin]")).to_be_visible()
    expect(page.locator("[data-rm-input]")).to_have_value("Half-written thought about the art")
    expect(page.locator("[data-rm-send]")).to_be_disabled()
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-rm-signin]")).to_be_visible()
    # Signing in again brings the room back with the draft.
    _records_fetch(page, "/login", {"token": rooms["owner_key"]}, "login-0002")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-rm-input]")).to_have_value("Half-written thought about the art")
    assert len(writes) == 1                                           # the one refused attempt; nothing else sent
    ctx.close()


# ── Guild 1.1 slice 5 (spec §7, §11): the Workshop restyled ───────────────────

@pytest.fixture
def usage_store(tmp_path):
    """A usage store whose last gateway record is three days and two hours old."""
    folder = tmp_path / "usage"
    folder.mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = folder / f"usage-{month}.jsonl"
    path.write_text(json.dumps({"emitter": "gateway", "actor": "mc", "cost_usd": 0.4}) + "\n")
    old = (datetime.now(timezone.utc) - timedelta(days=3, hours=2)).timestamp()
    os.utime(path, (old, old))
    saved = os.environ.get("MINIMOI_USAGE_DIR")
    os.environ["MINIMOI_USAGE_DIR"] = str(folder)
    yield folder
    if saved is None:
        os.environ.pop("MINIMOI_USAGE_DIR", None)
    else:
        os.environ["MINIMOI_USAGE_DIR"] = saved


def _open_host_observations(page):
    """Host observations and the ops strip sit behind a disclosure on the Workshop (Codex, 5 Oct)."""
    page.locator("details.card > summary", has_text="Host observations").click()
    expect(page.locator("[data-ws-top]")).to_be_visible()


def test_s5_desktop_ops_strip_job_cards_and_controls_not_available(browser, server, workshop, usage_store):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    writes = _writes(page)
    go(page, f"{server['url']}/guild-next/guild/operate/build-host")
    expect(page.locator(".guild-subnav .subnav-active")).to_contain_text("Build host")
    _open_host_observations(page)
    expect(page.locator("[data-ws-top] .ws-title")).to_have_text("Host observations")
    for key, text in (("memory", "Memory free 46.0%"), ("swap", "Swap used 1.2 GB"), ("disk", "Disk free 80.0 GB"),
                      ("load", "Load (1 min) 2.1")):
        chip = page.locator(f"[data-ws-op='{key}']")
        expect(chip).to_have_attribute("data-state", "measured")
        expect(chip).to_contain_text(text)
        expect(chip.locator(".sx-at")).to_be_visible()                            # with its time
    expect(page.locator("[data-ws-op='production']")).to_have_text("Production host · not measured")
    expect(page.locator("[data-ws-op='production']")).to_have_attribute("data-state", "not_measured")
    expect(page.locator("[data-ws-ops-table]")).to_be_hidden()
    page.click("[data-ws-ops] > summary")
    expect(page.locator("[data-ws-ops-table]")).to_be_visible()
    expect(page.locator("[data-ws-op-row='memory'] td").nth(1)).to_contain_text("workshop.py observe")
    expect(page.locator("[data-ws-op-row='production'] [data-ws-op-fresh]")).to_have_text("—")
    card = page.locator("[data-ws-job='queue:12']")
    expect(card).to_have_attribute("data-state", "needs you")
    expect(card.locator("[data-ws-evidence] li")).to_have_count(2)
    expect(card.locator(".ws-ref")).to_have_attribute("href", "/guild-next/guild/build/log?item=12")
    # No fake buttons: the page says once that start, stop and comment are not available yet (Codex, 5 Oct).
    expect(page.locator("[data-ws-control]")).to_have_count(0)
    expect(page.locator("[data-ws-controls-note]")).to_contain_text("not available yet")
    # Spend keeps its own time: the usage file's, not the host reading's.
    expect(page.locator("[data-ws-op='spend']")).to_contain_text("Model spend $0.40")
    expect(page.locator("[data-ws-op='spend'] .sx-at")).to_contain_text("last record")
    w, now, iso = workshop["workshop"], workshop["now"], workshop["iso"]
    # The cards follow the refresh (review F2): a new event changes a pill, a new item adds a card.
    w.append({"workshop": "mac", "actor": "robert", "kind": "decision", "item": "queue:12", "text": "Every minute"})
    w.append({"workshop": "mac", "actor": "codex", "kind": "review", "item": "queue:31", "text": "Reviewing #289"})
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(card.locator("[data-ws-pill]")).to_have_text("running")
    expect(page.locator("[data-ws-job='queue:31'] [data-ws-pill]")).to_have_text("in review")
    expect(page.locator("[data-ws-job='queue:31'] .ws-ref")).to_have_attribute("href", "/guild-next/guild/build/log?item=31")
    expect(page.locator("[data-ws-jobs-stale]")).to_have_count(0)
    # A stale host reading shows on the cards too, in words.
    old = iso(now() - timedelta(hours=2))
    stale = workshop["Observation"](observed_at=old, memory_free_pct=46.0, swap_used_gb=1.2, disk_free_gb=80.0,
                                    load_1m=2.1, clients=[], clients_known=True)
    w.append({**workshop["health_event"](stale, "mac"), "at": iso(now())})
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("[data-ws-jobs-stale]")).to_have_text("The host reading is stale, so these job states may be out of date.")
    expect(card.locator("[data-ws-stale-pill]")).to_be_visible()
    expect(card.locator("[data-ws-stale-pill]")).to_have_text("stale record")
    expect(page.locator("[data-ws-op='memory']")).to_contain_text("Memory free 46.0% · stale")
    # A failed refresh (review F3): each figure keeps its value, marked stale, with its own last good time and a live age.
    page.route("**/api/v1/workshop*", lambda route: route.fulfill(status=503, body="{}", content_type="application/json"))
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("[data-ws-op='memory']")).to_have_attribute("data-state", "stale")
    expect(page.locator("[data-ws-op='memory'] .sx-at")).to_contain_text("last good read")
    expect(page.locator("[data-ws-op='spend']")).to_have_attribute("data-state", "stale")
    expect(page.locator("[data-ws-op='spend']")).to_contain_text("Model spend $0.40 in")
    host_at = page.locator("[data-ws-op='memory'] .sx-at").text_content()
    spend_at = page.locator("[data-ws-op='spend'] .sx-at").text_content()
    assert "last good read" in spend_at and spend_at != host_at, (spend_at, host_at)
    row = page.locator("[data-ws-op-row='memory']")
    expect(row).to_have_attribute("data-state", "stale")
    expect(row.locator("[data-ws-op-value]")).to_have_text("46.0% (stale)")
    expect(row.locator("[data-ws-op-fresh]")).to_contain_text("last good read")
    expect(row.locator("[data-ws-op-fresh]")).to_contain_text("2 h ago")
    assert row.locator("td").first.evaluate("e => getComputedStyle(e).fontStyle") == "italic"
    expect(page.locator("[data-ws-op-row='spend'] [data-ws-op-value]")).to_have_text("$0.40 (stale)")
    expect(page.locator("[data-ws-jobs-stale]")).to_contain_text("may be out of date (the refresh failed; last good read")
    expect(page.locator("[data-ws-job='queue:31'] [data-ws-stale-pill]")).to_be_visible()
    assert _no_page_overflow(page)
    assert not writes, writes                                                       # read only
    assert not [e for e in errors if "status of 503" not in e], errors
    ctx.close()


def test_s5_phone_workshop_in_the_shell_without_overflow(browser, server, workshop):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    errors = _errors(page)
    page.goto(f"{server['url']}/__b1_test_sign_in")
    page.goto(f"{server['url']}/guild-next/guild/operate")                         # Operate, then its Build host tab
    page.wait_for_load_state("load")
    with page.expect_navigation():
        page.click(".guild-subnav a:has-text('Build host')")
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild-next/guild/operate/build-host")
    _open_host_observations(page)
    expect(page.locator("[data-ws-op='memory']")).to_be_in_viewport()
    cards = page.locator(".ws-job")
    expect(cards).to_have_count(2)
    boxes = [cards.nth(i).bounding_box() for i in range(2)]
    assert abs(boxes[0]["x"] - boxes[1]["x"]) < 2 and boxes[1]["y"] > boxes[0]["y"]   # one column
    assert boxes[0]["x"] >= 0 and boxes[0]["x"] + boxes[0]["width"] <= 390
    expect(page.locator("[data-ws-control]")).to_have_count(0)
    expect(page.locator("[data-ws-controls-note]")).to_be_visible()
    page.click("[data-ws-ops] > summary")
    expect(page.locator("[data-ws-ops-table]")).to_be_visible()
    assert _no_page_overflow(page)
    assert not errors, errors
    ctx.close()


# ── Rooms R1 (docs/specs/minimoi-connected-work/ROOMS_R1.md §6) ──────────────
# New conversation → Invite MC → inline Prove (draft kept) → one question →
# one attributed reply; @CoS gets a visible next action; Stop fences a reply
# mid-stream; End → Continue conversation. The real worker runs in a thread
# against the test Records app with a scripted relay: no model, no spend.

@pytest.fixture
def rooms_r1(rooms, tmp_path):
    import importlib.util
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rooms_worker"))
    from rooms_helpers import RECORDS_DIR
    from worker_helpers import FakeRelay, start_worker
    from services.rooms_worker import worker as worker_module
    spec = importlib.util.spec_from_file_location("records_poc_manage_for_browser", RECORDS_DIR / "manage.py")
    manage = importlib.util.module_from_spec(spec); spec.loader.exec_module(manage)
    out = tmp_path / "outbox"
    manage.provision_rooms(rooms["app"].extensions["records_store"], "mc", "Master Craftsman", out)

    class Relay(FakeRelay):
        def stream(self, messages, user, correlation, on_open=None, **_):
            self.calls.append({"messages": messages, "correlation": correlation})
            if "slowly" in messages[-1]["content"]:
                self.wait_for_stop(20)
                return {"outcome": "stopped", "text": "half a thought", "usage": None, "detail": "stopped"}
            return {"outcome": "done", "text": "Start with one honest meeting.", "usage": None, "detail": "done"}
    relay = Relay()
    saved = worker_module.HEARTBEAT_S
    worker_module.HEARTBEAT_S = 0.2
    running = start_worker(rooms["app"], out, relay, tmp_path / "journal")
    yield {**rooms, "relay": relay}
    running.stop()
    worker_module.HEARTBEAT_S = saved


def _r1_shot(page, name):
    """Opt-in review screenshots (R1_SHOTS=<folder>); sample data only."""
    folder = os.environ.get("R1_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=False)


def _rm_session_menu(page):
    """Pause and End sit under the ⋯ menu in the room's side panel (Codex, 5 Oct); open it if it is closed."""
    menu = page.locator(".rm-session-menu")
    if not menu.evaluate("e => e.open"):
        menu.locator("> summary").click()


def test_r1_desktop_new_conversation_prove_inline_reply_stop_end_continue(browser, server, rooms_r1):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/rooms")
    assert _records_fetch(page, "/login", {"token": rooms_r1["owner_key"]}, "login-r1-0001")["status"] == 200
    go(page, f"{server['url']}/guild-next/guild/rooms")
    # New conversation, with MC (not yet proven) as a teammate.
    page.click("[data-rm-new]")
    expect(page.locator("[data-rm-new-dialog]")).to_be_visible()
    page.fill("[data-rm-new-title]", "Planning Rooms")
    page.fill("[data-rm-new-purpose]", "Decide the first slice together")
    expect(page.locator("[data-rm-new-card='mc']")).to_be_checked()
    with page.expect_navigation():
        page.click("[data-rm-new-create]")
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-rm-title]")).to_have_text("Planning Rooms")
    mc = page.locator("[data-person='mc']")
    expect(mc).to_have_attribute("data-rsvp", "invited")
    expect(mc).to_have_attribute("title", re.compile("not yet proven"))
    # Writing to an unproven MC gives the next action, not silence.
    page.fill("[data-rm-input]", "Hello MC")
    page.click("[data-rm-send]")
    expect(page.locator("[data-rm-turns]")).to_contain_text("not yet proven. Use Invite → Prove first.")
    # Prove inline from Invite; the draft is kept and the room stays open.
    page.fill("[data-rm-input]", "my unsent draft")
    page.click("[data-rm-invite]")
    card = page.locator("[data-rm-card='mc']")
    expect(card).to_contain_text("Not yet proven — Prove first (one turn, needs your OK)")
    _r1_shot(page, "r1-1-invite-prove-first")
    page.click("[data-rm-prove='mc']")
    expect(page.locator("[data-rm-prove='mc']")).to_have_text("Confirm: use one paid turn")
    page.click("[data-rm-prove='mc']")
    expect(page.locator("[data-rm-invite-status]")).to_contain_text("is proven", timeout=20000)
    page.click("[data-rm-dialog-close]")
    expect(page.locator("[data-rm-input]")).to_have_value("my unsent draft")
    expect(page.locator("[data-rm-title]")).to_have_text("Planning Rooms")
    expect(mc).to_have_attribute("data-rsvp", "accepted", timeout=15000)
    # One question, one attributed reply, no refresh.
    page.fill("[data-rm-input]", "What should we build first?")
    page.click("[data-rm-send]")
    reply = page.locator('.rm-msg[data-actor="mc"]')
    expect(reply).to_contain_text("Start with one honest meeting.", timeout=20000)
    expect(reply.locator('[data-tag="agent"]')).to_have_text("agent")
    _r1_shot(page, "r1-2-mc-reply")
    # @CoS: the message is kept, nothing is sent to anyone, and the next action is shown.
    page.fill("[data-rm-input]", "@CoS can you check the calendar?")
    page.click("[data-rm-send]")
    expect(page.locator("[data-rm-turns]")).to_contain_text(
        "Not sent to CoS: not available in Rooms yet. Master Craftsman can answer — say @MC or leave it unaddressed.")
    _r1_shot(page, "r1-3-not-sent-to-cos")
    expect(page.locator(".rm-msg .rm-body").last).to_have_text("@CoS can you check the calendar?")
    # Stop mid-reply: the reply is stopped and nothing late is posted.
    replies = reply.count()
    page.fill("[data-rm-input]", "Think about this slowly")
    page.click("[data-rm-send]")
    expect(page.locator("[data-rm-turns]")).to_contain_text("Master Craftsman is answering…", timeout=15000)
    page.click("[data-rm-stop]")
    expect(page.locator("[data-rm-state]")).to_have_text("Paused")
    expect(page.locator("[data-rm-turns]")).to_contain_text("Reply stopped.", timeout=15000)
    _r1_shot(page, "r1-4-reply-stopped")
    assert reply.count() == replies
    # Resume, End with a note, then Continue conversation.
    _rm_session_menu(page)
    page.click("[data-rm-pause]")
    page.fill("[data-rm-act-note]", "Back from the break")
    page.click("[data-rm-act-confirm]")
    expect(page.locator("[data-rm-state]")).to_be_hidden()
    _rm_session_menu(page)
    page.click("[data-rm-end]")
    page.fill("[data-rm-act-note]", "Decided: build R1 first")
    page.click("[data-rm-act-confirm]")
    expect(page.locator("[data-rm-closed]")).to_be_visible()
    expect(page.locator("[data-rm-input]")).to_be_disabled()
    _r1_shot(page, "r1-5-closed-continue")
    with page.expect_navigation():
        page.click("[data-rm-continue]")
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-rm-title]")).to_have_text("Planning Rooms (continued)")
    expect(page.locator("[data-person='mc']")).to_have_attribute("data-rsvp", "accepted", timeout=15000)
    assert _no_page_overflow(page)
    assert not [e for e in errors if "401" not in e and "409" not in e], errors
    ctx.close()


def test_r1_phone_controls_status_and_composer_fit(browser, server, rooms_r1):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    go(page, f"{server['url']}/guild-next/guild/rooms")
    room_id = _rooms_ready(page, server, rooms_r1, title="Phone meeting")
    store = rooms_r1["app"].extensions["records_store"]
    with store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00' WHERE principal='mc'")
    assert _records_fetch(page, f"/v1/rooms/{room_id}/invite", {"actor": "mc"}, "phone-invite-1")["status"] == 201
    go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}")
    page.click("[data-rm-panel-toggle]")                                          # people and controls are a drawer on a phone (5 Oct)
    expect(page.locator("[data-person='mc']")).to_have_attribute("data-rsvp", "accepted", timeout=15000)
    expect(page.locator("[data-rm-controls]")).to_be_visible()
    page.click("[data-rm-panel-close]")
    expect(page.locator("[data-rm-controls]")).to_be_hidden()
    page.fill("[data-rm-input]", "Quick question from the phone")
    page.click("[data-rm-send]")
    expect(page.locator('.rm-msg[data-actor="mc"]')).to_contain_text("Start with one honest meeting.", timeout=20000)
    comp = page.locator("[data-rm-composer]")
    expect(comp).to_be_in_viewport()
    box = comp.bounding_box()
    assert box["y"] + box["height"] <= 844 and box["x"] >= 0 and box["x"] + box["width"] <= 390
    _r1_shot(page, "r1-6-phone")
    assert _no_page_overflow(page)
    ctx.close()



# ── Rooms R2 (docs/specs/minimoi-connected-work/ROOMS_R2.md §6) ──────────────

def test_r2_claude_code_in_a_room_and_codex_still_not_available(browser, server, rooms, tmp_path):
    import importlib.util
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rooms_worker"))
    from rooms_helpers import RECORDS_DIR
    from worker_helpers import FakeRelay, start_worker
    spec = importlib.util.spec_from_file_location("records_poc_manage_for_r2_browser", RECORDS_DIR / "manage.py")
    manage = importlib.util.module_from_spec(spec); spec.loader.exec_module(manage)
    store = rooms["app"].extensions["records_store"]
    manage.provision_rooms(store, "mc", "Master Craftsman", tmp_path / "mc-out")
    manage.provision_rooms(store, "claude-code", "Claude Code", tmp_path / "cc-out", worker="rooms-connector-mac",
                           card={"host": "this Mac", "connector": "rooms-connector"})
    with store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00'")

    class Relay(FakeRelay):
        def stream(self, messages, user, correlation, on_open=None, **_):
            return {"outcome": "done", "text": "Claude Code here: start with the meeting itself.", "usage": None, "detail": "done"}
    mc = start_worker(rooms["app"], tmp_path / "mc-out", FakeRelay(), tmp_path / "j-mc")
    cc = start_worker(rooms["app"], tmp_path / "cc-out", Relay(), tmp_path / "j-cc", teammate="claude-code",
                      worker="rooms-connector-mac", agent_id="claude-code", runtime="Claude Code CLI (fake)")
    try:
        ctx, page = _context(browser, server, 1440, 900)
        go(page, f"{server['url']}/guild-next/guild/rooms")
        room_id = _rooms_ready(page, server, rooms, title="Team room")
        go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}")
        page.click("[data-rm-invite]")
        expect(page.locator("[data-rm-card='claude-code']")).to_contain_text("Proven")
        page.click("[data-rm-invite-card='claude-code']")
        page.click("[data-rm-invite-card='mc']")
        page.click("[data-rm-dialog-close]")
        expect(page.locator("[data-person='claude-code']")).to_have_attribute("data-rsvp", "accepted", timeout=15000)
        page.fill("[data-rm-input]", "@Claude what should we build first?")
        page.click("[data-rm-send]")
        reply = page.locator('.rm-msg[data-actor="claude-code"]')
        expect(reply).to_contain_text("Claude Code here", timeout=20000)
        expect(reply.locator('[data-tag="agent"]')).to_have_text("agent")
        assert page.locator('.rm-msg[data-actor="mc"]').count() == 0          # addressed: MC stays quiet
        page.fill("[data-rm-input]", "@Codex can you review it?")
        page.click("[data-rm-send]")
        expect(page.locator("[data-rm-turns]")).to_contain_text("Not sent to Codex: not available in Rooms yet.")
        _r1_shot(page, "r2-1-claude-code-in-a-room")
        ctx.close()
    finally:
        mc.stop(); cc.stop()


# ── Rooms R3a (docs/specs/minimoi-connected-work/ROOMS_R3.md §2) ─────────────

def test_r3a_everyone_round_answers_in_order_with_a_round_line(browser, server, rooms, tmp_path):
    import importlib.util
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rooms_worker"))
    from rooms_helpers import RECORDS_DIR
    from worker_helpers import FakeRelay, start_worker
    spec = importlib.util.spec_from_file_location("records_poc_manage_for_r3a_browser", RECORDS_DIR / "manage.py")
    manage = importlib.util.module_from_spec(spec); spec.loader.exec_module(manage)
    store = rooms["app"].extensions["records_store"]
    manage.provision_rooms(store, "mc", "Master Craftsman", tmp_path / "mc-out")
    manage.provision_rooms(store, "claude-code", "Claude Code", tmp_path / "cc-out", worker="rooms-connector-mac")
    with store.connect() as d:
        d.execute("UPDATE teammates SET proven_at='2026-10-01T00:00:00+00:00'")

    class MC(FakeRelay):
        def stream(self, messages, user, correlation, on_open=None, **_):
            return {"outcome": "done", "text": "MC first: the meeting.", "usage": None, "detail": "done"}

    class CC(FakeRelay):
        def stream(self, messages, user, correlation, on_open=None, **_):
            saw = "MC first" in messages[1]["content"]
            return {"outcome": "done", "text": "Claude second, having read MC." if saw else "Claude without MC", "usage": None, "detail": "done"}
    mc = start_worker(rooms["app"], tmp_path / "mc-out", MC(), tmp_path / "j-mc")
    cc = start_worker(rooms["app"], tmp_path / "cc-out", CC(), tmp_path / "j-cc", teammate="claude-code",
                      worker="rooms-connector-mac", agent_id="claude-code", runtime="Claude Code CLI (fake)")
    try:
        ctx, page = _context(browser, server, 1440, 900)
        go(page, f"{server['url']}/guild-next/guild/rooms")
        room_id = _rooms_ready(page, server, rooms, title="Round room")
        for who in ("mc", "claude-code"):
            assert _records_fetch(page, f"/v1/rooms/{room_id}/invite", {"actor": who}, f"inv-{who}")["status"] == 201
        go(page, f"{server['url']}/guild-next/guild/rooms?room={room_id}")
        expect(page.locator("[data-person='claude-code']")).to_have_attribute("data-rsvp", "accepted", timeout=15000)
        expect(page.locator("[data-person='mc']")).to_have_attribute("data-rsvp", "accepted", timeout=15000)
        page.fill("[data-rm-input]", "@ev")
        expect(page.locator("[data-rm-mention='everyone']")).to_be_visible()
        page.click("[data-rm-mention='everyone']")
        page.type("[data-rm-input]", "what should we build first?")
        page.click("[data-rm-send]")
        expect(page.locator('.rm-msg[data-actor="claude-code"]')).to_contain_text("Claude second, having read MC.", timeout=25000)
        actors = page.locator(".rm-msg").evaluate_all("els => els.map(e => e.dataset.actor)")
        assert actors.index("mc") < actors.index("claude-code")
        expect(page.locator("[data-rm-turns]")).to_contain_text("Round: Master Craftsman answered · Claude Code answered", timeout=10000)
        _r1_shot(page, "r3a-1-everyone-round")
        ctx.close()
    finally:
        mc.stop(); cc.stop()


# ── Build refinement, 5 Oct 2026: Linked work, kept images, Private, Invite, a denser Board, Prototype Lab ──

def test_b5_linked_work_invite_and_private_in_the_chat(browser, server, board_media):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    expect(page.locator("[data-linked-empty]")).to_be_visible()                      # empty until Robert attaches something
    expect(page.locator("[data-rail-continue], [data-floor-urgent], [data-open-wall]")).to_have_count(0)
    page.fill("[data-linked-input]", "https://example.com/not-this-repo")
    page.press("[data-linked-input]", "Enter")
    expect(page.locator("[data-linked-error]")).to_contain_text("Nothing was changed")
    page.fill("[data-linked-input]", "12")
    page.press("[data-linked-input]", "Enter")
    expect(page.locator("[data-linked-link]")).to_have_text("#12 Floor API")
    expect(page.locator("[data-linked-meta]")).to_have_text("Build Log item")
    page.reload()
    expect(page.locator("[data-linked-link]")).to_have_text("#12 Floor API")           # saved on the conversation
    page.click("[data-linked-remove]")
    expect(page.locator("[data-linked-empty]")).to_be_visible()
    page.reload()
    expect(page.locator("[data-linked-empty]")).to_be_visible()
    page.click("[data-mc-invite]")                                                    # grey, clickable, one answer
    expect(page.locator("[data-invite-soon]")).to_have_text("Coming soon.")
    page.click("[data-mc-record]")                                                    # Private: a compact chip, attach off
    expect(page.locator("[data-private-chip]")).to_be_visible()
    expect(page.locator("[data-mc-attach]")).to_be_disabled()
    page.click("[data-private-why]")
    expect(page.locator("[data-private-explain]")).to_contain_text("MiniMoi keeps no notes")
    page.click("[data-private-exit]")
    expect(page.locator("[data-private-chip]")).to_be_hidden()
    expect(page.locator("[data-mc-attach]")).to_be_enabled()
    assert not [e for e in errors if "422" not in e], errors                           # the refused link is the one expected 422
    ctx.close()


def test_b5_an_image_is_kept_as_a_picture_and_unreadable_files_are_refused(browser, server, board_media, tmp_path):
    from board_media_helpers import image_bytes
    png = tmp_path / "plan.png"
    png.write_bytes(image_bytes("PNG"))
    sheet = tmp_path / "budget.xlsx"
    sheet.write_bytes(b"PK\x03\x04 not really a sheet")
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")                                                      # a fresh conversation: no leftovers
    page.set_input_files("[data-mc-file]", [str(png), str(sheet)])
    expect(page.locator('.att[data-state="ok"]')).to_contain_text("Master Craftsman can’t see images yet")
    expect(page.locator('.att[data-state="failed"]')).to_contain_text(".xlsx files can’t be read yet")
    expect(page.locator("[data-conv-files] .fc-file")).to_have_count(1)
    page.reload()                                                                     # kept: it is still listed
    expect(page.locator("[data-conv-files] .fc-file")).to_have_count(1)
    assert page.evaluate("""async () => { const i = document.querySelector('[data-conv-files] img');
        await new Promise(r => i.complete ? r() : (i.onload = r)); return i.naturalWidth > 0; }""")
    page.click("[data-file-remove]")                                                         # leave nothing behind for the next test
    expect(page.locator("[data-conv-files] .fc-file")).to_have_count(0)
    assert not errors, errors
    ctx.close()


def test_b7_documents_are_read_listed_kept_and_removed(browser, server, board_media, tmp_path):
    from doc_fixtures import docx_bytes, pdf_bytes
    files = {"notes.txt": b"Launch is 14 November.\nBudget 4200 EUR.", "plan.pdf": pdf_bytes(["Delivery Friday"]),
             "memo.docx": docx_bytes(["Heading", "Body"]), "scan.pdf": pdf_bytes(["", ""])}
    paths = []
    for name, raw in files.items():
        f = tmp_path / name
        f.write_bytes(raw)
        paths.append(str(f))
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")                                                      # a fresh conversation: no leftovers
    page.set_input_files("[data-mc-file]", paths)
    ok = page.locator('.att[data-state="ok"]')
    expect(ok).to_have_count(3)
    expect(ok.first.locator(".att-status")).to_have_text("")                                # a plain chip: just the name
    expect(page.locator('.att[data-state="failed"]')).to_contain_text("no text layer")     # a scan: said, not invented
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(3)
    expect(page.locator("[data-conv-files] .fc-doc").first).not_to_contain_text("characters kept")
    page.reload()
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(3)                      # kept as text, listed after a reload
    page.locator('[data-doc-remove]').first.click()
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(2)
    page.reload()
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(2)
    assert not [e for e in errors if "422" not in e], errors                                 # the scan is the one expected 422
    ctx.close()


def test_b15_a_refused_or_finished_action_is_shown_not_only_read_out(browser, server, board_media, tmp_path):
    from doc_fixtures import docx_bytes
    f = tmp_path / "toast.docx"
    f.write_bytes(docx_bytes(["Synthetic"]))
    ctx, page = _context(browser, server, 1280, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    page.set_input_files("[data-mc-file]", [str(f)])
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    page.click("[data-mc-record]")                                                        # Private
    page.locator("[data-doc-remove]").first.click()
    toast = page.locator("[data-toast]")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("Private")                                              # the refusal can be seen
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(1)                    # and nothing was removed
    page.click("[data-private-exit]")
    page.locator("[data-doc-remove]").first.click()
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(0)
    expect(toast).to_contain_text("Removed")                                              # the success can be seen too
    assert not errors, errors
    ctx.close()


def test_b14_the_ask_box_grows_edits_in_the_middle_and_sends_with_enter_on_a_computer(browser, server, jobs_mc):
    jobs_mc["runtime"].script = _script("Noted.", *FINISH)
    ctx, page = _context(browser, server, 1280, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    box = page.locator("#mc-input")
    one = box.bounding_box()["height"]
    box.click()
    page.keyboard.type("first line")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("second line")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("third line")
    assert box.bounding_box()["height"] > one + 20                                       # it grew
    assert box.input_value() == "first line\nsecond line\nthird line"                    # Shift+Enter did not send
    page.evaluate("(() => { const b = document.getElementById('mc-input'); b.focus(); b.setSelectionRange(6, 6); })()")
    page.keyboard.type("LONG ")                                                          # a change in the middle of the text
    assert box.input_value().startswith("first LONG line")
    page.keyboard.press("Enter")                                                         # Enter sends on a computer
    note = _thread_note(page, "first LONG line")
    expect(note).to_have_count(1, timeout=8000)
    assert page.evaluate("getComputedStyle(document.querySelector('[data-mc-thread] [data-author-kind=owner] .msg-text')).whiteSpace") == "pre-wrap"
    assert "\n" in note.locator(".msg-text").inner_text() or note.locator(".msg-text").evaluate("e => e.textContent.includes('\\n')")
    expect(box).to_have_value("")
    assert box.bounding_box()["height"] <= one + 2                                       # and shrank back
    assert not errors, errors
    ctx.close()


def test_b14_on_a_phone_enter_is_a_new_line_and_only_the_send_button_sends(browser, server, jobs_mc):
    jobs_mc["runtime"].script = _script("Noted.", *FINISH)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    _fresh_conversation(page, server)
    box = page.locator("#mc-input")
    box.click()
    page.keyboard.type("line one")
    page.keyboard.press("Enter")
    page.keyboard.type("line two")
    assert box.input_value() == "line one\nline two"                                      # Enter did not send
    assert page.locator('[data-mc-thread] [data-author-kind="owner"]').count() == 0
    page.click("[data-mc-send]")
    expect(page.locator('[data-mc-thread] [data-author-kind="owner"]')).to_have_count(1, timeout=8000)
    assert not errors, errors
    ctx.close()


def test_b17_a_second_visit_fetches_no_script_or_style_from_the_server(browser, server):
    """Pages used to re-fetch about 30 scripts on every visit (no-store). Versioned assets are kept by the browser."""
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    first = page.evaluate("performance.getEntriesByType('resource').filter(r => r.name.includes('/ui-assets/_v/')).length")
    assert first >= 25
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/ui-assets/" in r.url else None)
    go(page, f"{server['url']}/guild-next/guild/build/log")                                 # another page: shared scripts come from the browser's own store
    page.wait_for_selector("body[data-ready=true]")
    shared = page.evaluate("performance.getEntriesByType('resource').filter(r => r.name.includes('/ui-assets/_v/') && r.transferSize === 0).length")
    assert shared >= 20, shared
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    assert page.evaluate("performance.getEntriesByType('resource').filter(r => r.name.includes('/ui-assets/_v/') && r.transferSize > 0 && !r.name.includes('build_log')).length") == 0
    assert not errors, errors
    ctx.close()


def test_b18_while_it_works_only_the_time_shows_and_the_answer_keeps_how_long_it_took(browser, server, jobs_mc):
    jobs_mc["runtime"].script = [("sleep", 2.2), *_script("Noted.", *FINISH)]
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    page.fill("#mc-input", "Say noted")
    page.click("[data-mc-send]")
    clock = page.locator("[data-mc-stream-line] [data-slot='elapsed'], [data-mc-waiting] [data-slot='elapsed']").first
    expect(clock).to_be_visible(timeout=4000)                                              # the seconds are on screen
    expect(clock).to_have_text(re.compile(r"^\d+s$"))
    visible = page.evaluate("[...document.querySelectorAll('[data-mc-stream-line], [data-mc-waiting]')].map(e => e.innerText).join('|')")
    assert "Waiting for a response" not in visible and "Still working" not in visible    # no wording about what for
    answer = _thread_note(page, "Noted.")
    expect(answer).to_have_count(1, timeout=10000)
    expect(answer.locator("[data-turn-time]")).to_have_text(re.compile(r"^\d+s$"))        # and it keeps how long it took
    assert not errors, errors
    ctx.close()


def test_b16_on_a_phone_the_fold_is_named_for_what_is_in_it_files_come_before_the_picture_and_the_page_clears_the_toolbar(browser, server, jobs_mc):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(f"{server['url']}/__b1_test_sign_in")
    errors = _errors(page)
    _fresh_conversation(page, server)
    expect(page.locator(".floor-context-sum")).to_have_text("Files and linked work")
    page.click(".floor-context-sum")
    art = page.locator(".fc-art").bounding_box()["y"]
    assert page.locator("[data-linked-work]").bounding_box()["y"] < art                  # linked work above the picture
    assert page.locator("[data-conv-files-sec]").bounding_box()["y"] < art               # files above the picture
    assert page.locator(".fc-art").is_visible()                                          # the picture is still there
    assert float(page.evaluate("parseFloat(getComputedStyle(document.body).paddingBottom)")) >= 24   # room under the last thing
    page.set_viewport_size({"width": 1440, "height": 900})
    assert page.locator(".fc-art").bounding_box()["y"] < page.locator("[data-linked-work]").bounding_box()["y"]   # on a computer the picture still leads
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("path", ["/guild/build", "/guild/operate", "/guild/board", "/guild/build/log", "/guild/workshop", "/guild/rooms",
                                  "/guild/build/items/12", "/guild/media", "/guild/experiment", "/guild/improve"])
def test_b13_no_field_on_a_phone_is_small_enough_to_make_the_iphone_zoom_in(browser, server, path):
    """iOS Safari zooms the whole page when a field with text under 16 px gets focus; the page then looks wider than the screen and
    the buttons beside the field slide off its edge (Robert's Operate screenshot, 7 Oct). Every text field on a phone is at least 16 px."""
    ctx, page = _context(browser, server, 390, 844)
    go(page, f"{server['url']}/guild-next{path}")
    small = page.evaluate("""() => Array.from(document.querySelectorAll('input, textarea, select')).filter((e) => {
        const t = (e.getAttribute('type') || 'text').toLowerCase();
        if (['hidden', 'checkbox', 'radio', 'file', 'button', 'submit', 'range', 'color'].includes(t)) return false;
        return parseFloat(getComputedStyle(e).fontSize) < 16; })
      .map((e) => (e.getAttribute('data-mc-input') !== null ? 'mc-input' : e.id || e.name || e.className || e.tagName) + ' ' + getComputedStyle(e).fontSize)""")
    assert not small, small
    ctx.close()


@pytest.mark.parametrize("width", [360, 390, 600, 700, 820, 900, 1024, 1280, 1440])
def test_b12_file_names_stay_readable_and_their_buttons_fit_at_every_width(browser, server, board_media, tmp_path, width):
    f = tmp_path / "a-fairly-long-résumé-file-name-for-the-width-check.docx"
    from doc_fixtures import docx_bytes
    f.write_bytes(docx_bytes(["Résumé", "Synthetic"]))
    ctx, page = _context(browser, server, width, 800)
    errors = _errors(page)
    _fresh_conversation(page, server)
    page.set_input_files("[data-mc-file]", [str(f)])
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    if width <= 640:
        page.locator("details.floor-context > summary").click()
    else:
        toggle = page.locator("[data-rail-toggle]")
        if toggle.count() and toggle.get_attribute("aria-expanded") == "false":              # at mid widths the rail starts folded
            toggle.click()
    row = page.locator("[data-conv-files] .fc-doc")
    expect(row).to_have_count(1)
    expect(row.locator(".fc-file-name")).to_be_visible()
    name = row.locator(".fc-file-name").bounding_box()
    acts = row.locator(".fc-file-acts").bounding_box()
    assert name["width"] >= 110, ("the name is squeezed", name)                                 # never one letter per line
    assert name["height"] <= 110, ("the name wraps into a tall column", name)
    for button in ("[data-doc-download]", "[data-doc-remove]"):
        box = row.locator(button).bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (button, box, width)     # both buttons are on screen
        assert box["y"] >= acts["y"] - 1 and box["height"] >= 24, (button, box)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_b10_the_original_can_be_downloaded_from_the_files_list_and_goes_with_remove(browser, server, board_media, tmp_path, width, height):
    from doc_fixtures import docx_bytes
    raw = docx_bytes(["Résumé A", "Role: Analyst"])
    f = tmp_path / "resume-a.docx"
    f.write_bytes(raw)
    ctx, page = _context(browser, server, width, height)
    errors = _errors(page)
    _fresh_conversation(page, server)
    page.set_input_files("[data-mc-file]", [str(f)])
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    row = page.locator("[data-conv-files] .fc-doc")
    expect(row).to_have_count(1)
    link = row.locator("[data-doc-download]")
    expect(link).to_have_count(1)                                                               # shown at once, not only after a reload
    href = link.get_attribute("href")
    got = ctx.request.get(f"{server['url']}{href}" if href.startswith("/") else href)
    assert got.status == 200 and got.body() == raw and "attachment" in got.headers["content-disposition"]
    page.reload()
    link = page.locator("[data-conv-files] .fc-doc [data-doc-download]")
    expect(link).to_have_count(1)                                                               # and the same after a reload
    assert link.get_attribute("href") == href
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if width <= 640:                                                                            # on a phone the files live in the Context panel
        page.locator("details.floor-context > summary").click()
        expect(page.locator("details.floor-context")).to_have_attribute("open", "")
        box = page.locator("[data-conv-files] .fc-doc [data-doc-download]").bounding_box()
        assert box["height"] >= 40 and box["width"] >= 40, box                                  # a phone-sized target, on screen
    page.locator("[data-doc-remove]").first.click()
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(0)
    assert ctx.request.get(f"{server['url']}{href}" if href.startswith("/") else href).status == 404
    assert not [e for e in errors if "404" not in e], errors                                    # the 404 above is the expected one
    ctx.close()


# ── Master Craftsman jobs (step 3): a job card in the thread, driven by a fake relay speaking the v2 job protocol ──

@pytest.fixture
def jobs_mc(server, floor):
    from fake_job_relay import FakeJobRelay
    from minimoi_portal.guild_ui.jobs import Ticker
    from minimoi_portal.guild_ui.jobs_wiring import attach_jobs
    from minimoi_portal.guild_ui.mc import CachedHealth
    from test_mc_streaming import StreamRuntime
    from test_mc_turns import _openclaw

    class Runtime(StreamRuntime):
        def __init__(self):
            super().__init__()
            self.jobs = FakeJobRelay()

        def get(self, url, **kw):
            return self.jobs.get(url, **kw) if "/jobs" in url else super().get(url, **kw)

        def post(self, url, data=None, headers=None, stream=False, **kw):
            return self.jobs.post(url, data=data, headers=headers, **kw) if "/jobs" in url else super().post(url, data=data, headers=headers, stream=stream, **kw)

    services = server["app"].extensions["guild_ui_next"]["services"]
    saved = (services.mc, services.mc_health, services.mc_turns, services.mc_stream)
    runtime = Runtime()
    backend = _openclaw(runtime)
    services.mc, services.mc_health, services.mc_turns, services.mc_stream = backend, CachedHealth(backend), True, True
    from minimoi_portal.guild_ui.api import _MC_INFLIGHT
    from minimoi_portal.guild_ui.mc.streaming import DISPATCHED
    DISPATCHED._items.clear()
    _MC_INFLIGHT.clear()
    manager = attach_jobs(server["app"], "guild_ui_next", services, environ={"MINIMOI_GUILD_JOBS": "1", "MINIMOI_GUILD_JOBS_TICKER": "0"})
    ticker = Ticker(manager, interval_s=0.25)
    ticker.start()
    yield {"runtime": runtime, "services": services, "manager": manager}
    ticker.stop()
    ticker.join(2)
    server["app"].extensions["guild_ui_next"].pop("jobs", None)
    runtime.gate.set()
    services.mc, services.mc_health, services.mc_turns, services.mc_stream = saved


def _say(page, text):
    page.fill("[data-mc-input]", text)
    page.click("[data-mc-send]")


def _job_id(jobs_mc, nth=1):
    for _ in range(100):
        started = jobs_mc["runtime"].jobs.starts
        if len(started) >= nth:
            return started[nth - 1]["job_id"]
        time.sleep(0.1)
    raise AssertionError("the job never reached the relay")


def test_b11_run_this_as_a_job_shows_a_live_card_finishes_in_the_thread_and_survives_a_reload(browser, server, jobs_mc):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    _say(page, "Compare my new resume to my old resume. Run this as a job.")
    card = page.locator("[data-job]")
    expect(card).to_have_count(1)
    expect(card.locator("[data-job-state]")).to_have_text(re.compile(r"Job (waiting|starting|running)"))
    jid = _job_id(jobs_mc)
    expect(card.locator("[data-job-state]")).to_have_text("Job running", timeout=8000)
    expect(card.locator("[data-job-stop]")).to_be_visible()
    expect(card.locator("[data-job-message]")).to_contain_text("Running in the background")
    assert [p for p in jobs_mc["runtime"].posts if p["url"].endswith("/chat/completions")] == []           # no chat turn was sent
    jobs_mc["runtime"].jobs.progress(jid, 30, tools=[{"name": "read", "target": "docs/resume-old.md", "ok": True}])
    expect(card.locator("[data-job-audit-summary]")).to_contain_text("1 tool call", timeout=8000)
    page.reload()                                                                                          # a reload mid-job
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-job]")).to_have_count(1)
    expect(page.locator("[data-job] [data-job-state]")).to_have_text("Job running", timeout=8000)
    jobs_mc["runtime"].jobs.finish(jid, "The new resume leads with results; the old one lists duties.", tools=[
        {"name": "read", "target": "docs/resume-old.md", "ok": True}, {"name": "process", "target": "", "ok": True},
        {"name": "read", "target": "docs/resume-new.md", "ok": True}])
    expect(page.locator("[data-job] [data-job-state]")).to_have_text("Job done", timeout=8000)
    expect(page.locator("[data-job] [data-job-stop]")).to_be_hidden()
    result = page.locator('[data-mc-thread] [data-kind="note"]').filter(has_text="leads with results")
    expect(result).to_have_count(1)                                                                        # the result is a note in the thread
    expect(result.locator(".msg-label")).to_have_text("Master Craftsman")
    page.locator("[data-job] summary").click()
    expect(page.locator("[data-job-tools] li")).to_have_count(3)
    assert page.locator("[data-job-tools] li").all_inner_texts() == [                                      # plain words, no bare process/empty lines
        "read: docs/resume-old.md", "checked on a background command", "read: docs/resume-new.md"]
    expect(page.locator("[data-job-audit-note]")).to_contain_text("not a complete record")
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("[data-job]")).to_have_count(1)
    expect(page.locator('[data-mc-thread] [data-kind="note"]').filter(has_text="leads with results")).to_have_count(1)   # not twice
    order = page.evaluate("""() => Array.from(document.querySelectorAll('[data-mc-thread] > li')).map((li) =>
      li.dataset.job ? 'job' : /leads with results/.test(li.textContent) ? 'result' : /Compare my new resume/.test(li.textContent) ? 'ask' : 'other')""")
    assert order.index("ask") < order.index("job") < order.index("result"), order                          # in the order they happened
    assert not errors, errors
    ctx.close()


def test_b11_a_call_into_a_forbidden_area_is_marked_in_the_list_and_said_in_the_end(browser, server, jobs_mc):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    _say(page, "Look around. Run this as a job.")
    jid = _job_id(jobs_mc)
    expect(page.locator("[data-job] [data-job-state]")).to_have_text("Job running", timeout=8000)
    jobs_mc["runtime"].jobs.finish(jid, "I looked.", tools=[{"name": "read", "target": "docs/ok.md", "ok": True},
                                                            {"name": "read", "target": "/home/x/.ssh/id_rsa", "ok": True, "flag": "touches_restricted_area"}])
    card = page.locator("[data-job]")
    expect(card.locator("[data-job-state]")).to_have_text("Job done", timeout=8000)
    expect(card).to_have_attribute("data-flagged", "true")
    expect(card.locator("[data-job-message]")).to_contain_text("1 tool call touched an area the job rules forbid")
    card.locator("summary").click()
    expect(card.locator('[data-job-tools] li[data-flag]')).to_have_count(1)
    expect(card.locator('[data-job-tools] li[data-flag]')).to_contain_text("outside its allowed area")
    assert not errors, errors
    ctx.close()


def test_b11_the_conversation_lane_proposes_a_job_and_the_server_starts_it_without_showing_the_marker(browser, server, jobs_mc):
    jobs_mc["runtime"].script = _script("JOB: Compare the resumes\n", "I'll run that in the background.", *FINISH)
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    ask = "Compare my new resume to my old resume and write the differences into a file."
    _say(page, ask)
    card = page.locator("[data-job]")
    expect(card).to_have_count(1, timeout=8000)
    expect(_thread_note(page, "I'll run that in the background.")).to_have_count(1, timeout=8000)
    expect(card.locator("[data-job-state]")).to_have_text("Job running", timeout=8000)
    assert "JOB:" not in page.locator("[data-mc-thread]").inner_text()                                    # the marker was never shown, nor kept
    chats = [p for p in jobs_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    assert len(chats) == 1 and ask in chats[0]["body"]["messages"][0]["content"]                         # the model was asked once, with the owner's words
    jid = _job_id(jobs_mc)
    assert jobs_mc["runtime"].jobs.starts[0]["task"] == ask                                              # and the job's task is those same words, not the model's
    jobs_mc["runtime"].jobs.finish(jid, "The new one adds results; the old one lists duties.")
    expect(card.locator("[data-job-state]")).to_have_text("Job done", timeout=8000)
    expect(_thread_note(page, "adds results")).to_have_count(1)
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    assert "JOB:" not in page.locator("[data-mc-thread]").inner_text() and page.locator("[data-job]").count() == 1
    assert not errors, errors
    ctx.close()


def test_b11_stop_button_and_typing_stop_both_stop_a_job_and_say_what_that_means(browser, server, jobs_mc):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    _say(page, "Do a long analysis. Run this as a job.")
    jid = _job_id(jobs_mc)
    card = page.locator("[data-job]")
    expect(card.locator("[data-job-state]")).to_have_text("Job running", timeout=8000)
    card.locator("[data-job-stop]").click()
    expect(card.locator("[data-job-state]")).to_have_text("Job stopped", timeout=8000)
    expect(card.locator("[data-job-message]")).to_contain_text("the relay confirmed it cancelled the run")
    expect(card.locator("[data-job-message]")).to_contain_text("may still be running")
    assert jobs_mc["runtime"].jobs.stops == [jid]
    _say(page, "Another one. Run this as a job.")
    expect(page.locator("[data-job]")).to_have_count(2)
    jid2 = _job_id(jobs_mc, 2)
    expect(page.locator("[data-job]").last.locator("[data-job-state]")).to_have_text("Job running", timeout=8000)
    _say(page, "stop")
    expect(page.locator("[data-job]").last.locator("[data-job-state]")).to_have_text("Job stopped", timeout=8000)
    assert jobs_mc["runtime"].jobs.stops == [jid, jid2]
    assert [p for p in jobs_mc["runtime"].posts if p["url"].endswith("/chat/completions")] == []
    assert not errors, errors
    ctx.close()


def test_b11_a_stop_the_relay_only_acknowledged_is_shown_as_requested_not_stopped(browser, server, jobs_mc):
    jobs_mc["runtime"].jobs.stop_mode = "requested"
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    _say(page, "Slow work. Run this as a job.")
    _job_id(jobs_mc)
    card = page.locator("[data-job]")
    expect(card.locator("[data-job-state]")).to_have_text("Job running", timeout=8000)
    card.locator("[data-job-stop]").click()
    expect(card.locator("[data-job-message]")).to_contain_text("has not confirmed", timeout=8000)
    expect(card.locator("[data-job-state]")).to_have_text("Job running")
    expect(card.locator("[data-job-stop]")).to_be_disabled()
    assert not errors, errors
    ctx.close()


def test_b11_a_failed_and_an_unknown_job_say_so_and_post_no_answer(browser, server, jobs_mc):
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    _fresh_conversation(page, server)
    _say(page, "Try something. Run this as a job.")
    jid = _job_id(jobs_mc)
    expect(page.locator("[data-job] [data-job-state]")).to_have_text("Job running", timeout=8000)
    jobs_mc["runtime"].jobs.fail(jid, "agent_crashed")
    expect(page.locator("[data-job] [data-job-state]")).to_have_text("Job failed", timeout=8000)
    expect(page.locator("[data-job] [data-job-message]")).to_contain_text("Nothing was run again")
    assert page.locator('[data-mc-thread] [data-author-kind="agent"]').count() == 0
    assert not errors, errors
    ctx.close()


def test_b11_the_job_card_fits_a_phone_and_its_controls_are_phone_sized(browser, server, jobs_mc):
    ctx, page = _context(browser, server, 390, 844)
    errors = _errors(page)
    _fresh_conversation(page, server)
    _say(page, "Compare my new resume to my old resume with a long and wordy title that keeps going. Run this as a job.")
    jid = _job_id(jobs_mc)
    card = page.locator("[data-job]")
    expect(card.locator("[data-job-state]")).to_have_text("Job running", timeout=8000)
    jobs_mc["runtime"].jobs.progress(jid, 10, tools=[{"name": "read", "target": "_working/some/very/long/path/to/a/file/with/a/long-name.md", "ok": True}])
    expect(card.locator("[data-job-audit-summary]")).to_contain_text("1 tool call", timeout=8000)
    box = card.locator("[data-job-stop]").bounding_box()
    assert box and box["height"] >= 40, box
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "the page scrolls sideways"
    card_box = card.bounding_box()
    assert card_box["x"] >= 0 and card_box["x"] + card_box["width"] <= 390 + 1, card_box
    assert not errors, errors
    ctx.close()


def test_b11_with_jobs_off_nothing_changes_and_the_page_never_asks_for_jobs(browser, server, streaming_mc):
    streaming_mc["runtime"].script = _script("Plain answer.", *FINISH)
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/v1/" in r.url and "/jobs" in r.url else None)
    _fresh_conversation(page, server)
    _say(page, "Run this as a job.")                                                                      # an ordinary message when jobs are off
    expect(_thread_note(page, "Plain answer.")).to_have_count(1, timeout=8000)
    assert page.locator("[data-job]").count() == 0 and asked == []
    assert not errors, errors
    ctx.close()


def test_b7_a_message_carries_its_files_and_the_page_says_exactly_what_was_sent(browser, server, streaming_mc, board_media, tmp_path):
    from board_media_helpers import image_bytes
    streaming_mc["runtime"].script = _script("The launch is on 14 November.", *FINISH)
    notes = tmp_path / "plan.txt"
    notes.write_text("Launch is 14 November.\nBudget 4200 EUR.")
    png = tmp_path / "board.png"
    png.write_bytes(image_bytes("PNG"))
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")                                                      # a fresh conversation: no leftovers
    page.set_input_files("[data-mc-file]", [str(notes), str(png)])
    expect(page.locator('.att[data-state="ok"]')).to_have_count(2)
    page.fill("#mc-input", "When do we launch?")
    page.click("[data-mc-send]")
    reply = page.locator('[data-mc-thread] [data-kind="note"]').last
    expect(reply).to_contain_text("14 November", timeout=8000)
    expect(page.locator(".att")).to_have_count(0)                                           # the tray went with the message
    sent = page.locator("[data-note-files]").last
    expect(sent).to_contain_text("plan.txt")
    expect(sent).not_to_contain_text("sent in full")
    expect(sent).to_contain_text("board.png · not sent (Master Craftsman can't see images yet)")
    [post] = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    content = post["body"]["messages"][0]["content"]
    assert "\nWhen do we launch?\n\n[Files the owner attached" in content                     # what the page said is what left
    assert "Launch is 14 November.\nBudget 4200 EUR." in content and "PNG" not in content
    page.reload()                                                                           # the same line, from the saved report
    again = page.locator("[data-note-files]").last
    expect(again).to_contain_text("plan.txt")
    expect(again).not_to_contain_text("sent in full")
    expect(again).to_contain_text("board.png · not sent")
    page.click("[data-file-remove]")                                                        # leave no picture behind for the next test
    expect(page.locator("[data-conv-files] [data-file]")).to_have_count(0)
    assert not errors, errors
    ctx.close()


def test_b7_private_takes_no_files_and_a_dropped_file_is_refused(browser, server, board_media, tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("do not keep this")
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")                                                      # a fresh conversation: no leftovers
    page.click("[data-mc-record]")
    expect(page.locator("[data-mc-attach]")).to_be_disabled()
    page.set_input_files("[data-mc-file]", [str(f)])                                        # even if one gets in, it is refused
    expect(page.locator('.att[data-state="failed"]')).to_contain_text("you are Private")
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(0)
    assert not errors, errors
    ctx.close()


# ── Revision 6: the tray lifecycle (Codex review of Revision 5) and honest failure lines ──
# The upload answer is held by the test, so each race is deterministic: upload starts, the user acts, the answer arrives.

def _hold_documents(page):
    held = []
    page.route("**/api/v1/conversations/*/documents", lambda route: held.append(route))
    return held


def _wait_held(page, held, n=1):
    for _ in range(100):
        if len(held) >= n:
            return
        page.wait_for_timeout(100)
    raise AssertionError("the upload never started")


def _thread_note(page, text):
    return page.locator('[data-mc-thread] [data-kind="note"]').filter(has_text=text)


def test_b8_a_file_dismissed_while_it_is_being_read_is_never_sent(browser, server, streaming_mc, board_media, tmp_path):
    streaming_mc["runtime"].script = _script("Fine.", *FINISH)
    f = tmp_path / "late.txt"
    f.write_text("LATE-DISMISSED-TEXT")
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")
    held = _hold_documents(page)
    page.set_input_files("[data-mc-file]", [str(f)])
    _wait_held(page, held)
    expect(page.locator('.att[data-state="working"]')).to_have_count(1)
    page.click(".att-x")                                                          # dismissed before the server answers
    expect(page.locator(".att")).to_have_count(0)
    held[0].continue_()                                                           # ... then the answer arrives late
    expect(page.locator("[data-conv-files] .fc-doc")).to_have_count(1)           # the server kept the text with the conversation
    expect(page.locator(".att")).to_have_count(0)                                 # but it did not come back into the tray
    page.fill("#mc-input", "Hello, a plain message")
    page.click("[data-mc-send]")
    expect(_thread_note(page, "Fine.")).to_have_count(1, timeout=8000)
    [post] = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    content = post["body"]["messages"][0]["content"]
    assert "LATE-DISMISSED-TEXT" not in content and "BEGIN FILE" not in content   # what left carries no file
    expect(page.locator("[data-note-files]")).to_have_count(0)
    assert not errors, errors
    ctx.close()


def test_b8_send_waits_for_a_file_still_being_read_and_never_carries_it_into_a_later_message(browser, server, streaming_mc, board_media, tmp_path):
    streaming_mc["runtime"].script = _script("Noted.", *FINISH)
    f = tmp_path / "slow.txt"
    f.write_text("SLOW-FILE-TEXT")
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")
    held = _hold_documents(page)
    page.set_input_files("[data-mc-file]", [str(f)])
    _wait_held(page, held)
    page.fill("#mc-input", "What does the file say?")
    page.click("[data-mc-send]")                                                  # the file is still being read
    expect(page.locator("[data-mc-refusal]")).to_contain_text("still being read")
    assert page.input_value("#mc-input") == "What does the file say?"             # the draft is kept
    assert streaming_mc["runtime"].posts == []                                    # nothing was sent without the file
    held[0].continue_()
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    page.click("[data-mc-send]")                                                  # now it goes, with its file
    expect(_thread_note(page, "Noted.")).to_have_count(1, timeout=8000)
    posts = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    assert len(posts) == 1 and "SLOW-FILE-TEXT" in posts[0]["body"]["messages"][0]["content"]
    expect(page.locator("[data-note-files]").last).to_contain_text("slow.txt")
    page.fill("#mc-input", "And a separate follow-up")                            # the file is not carried into this one
    page.click("[data-mc-send]")
    expect(_thread_note(page, "Noted.")).to_have_count(2, timeout=8000)
    posts = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    assert len(posts) == 2 and "SLOW-FILE-TEXT" not in posts[1]["body"]["messages"][0]["content"]
    assert not [e for e in errors if "409" not in e], errors
    ctx.close()


def test_b8_a_file_still_being_read_can_be_dismissed_and_the_message_then_sends_without_it(browser, server, streaming_mc, board_media, tmp_path):
    streaming_mc["runtime"].script = _script("Sent plain.", *FINISH)
    f = tmp_path / "skip.txt"
    f.write_text("SKIPPED-TEXT")
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")
    held = _hold_documents(page)
    page.set_input_files("[data-mc-file]", [str(f)])
    _wait_held(page, held)
    page.fill("#mc-input", "Never mind the file")
    page.click("[data-mc-send]")
    expect(page.locator("[data-mc-refusal]")).to_contain_text("still being read")
    page.click(".att-x")
    page.click("[data-mc-send]")
    expect(_thread_note(page, "Sent plain.")).to_have_count(1, timeout=8000)
    held[0].continue_()
    [post] = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    assert "SKIPPED-TEXT" not in post["body"]["messages"][0]["content"]
    ctx.close()


def test_b8_a_refusal_before_dispatch_gives_the_files_back_and_they_go_with_the_next_try(browser, server, streaming_mc, board_media, tmp_path):
    from minimoi_portal.guild_ui.api import _MC_INFLIGHT
    streaming_mc["runtime"].script = _script("Got it.", *FINISH)
    f = tmp_path / "again.txt"
    f.write_text("AGAIN-FILE-TEXT")
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")
    page.set_input_files("[data-mc-file]", [str(f)])
    expect(page.locator('.att[data-state="ok"]')).to_have_count(1)
    page.fill("#mc-input", "First try")
    _MC_INFLIGHT.add("robert")                                                    # another turn is in flight: the server refuses before sending
    try:
        page.click("[data-mc-send]")
        expect(page.locator("[data-turn-failure]")).to_have_count(1)
        expect(page.locator('.att[data-state="ok"]')).to_have_count(1)           # the file is back in the tray
        assert streaming_mc["runtime"].posts == []
    finally:
        _MC_INFLIGHT.discard("robert")
    page.fill("#mc-input", "Second try")
    page.click("[data-mc-send]")
    expect(_thread_note(page, "Got it.")).to_have_count(1, timeout=8000)
    [post] = [p for p in streaming_mc["runtime"].posts if p["url"].endswith("/chat/completions")]
    assert "AGAIN-FILE-TEXT" in post["body"]["messages"][0]["content"]
    ctx.close()


def test_b8_a_silent_run_says_it_is_still_working_and_a_failed_try_is_one_line_not_a_stack(browser, server, streaming_mc):
    from test_mc_streaming import nd
    streaming_mc["runtime"].script = [("sleep", 11.5), nd({"t": "error", "class": "idle"})]
    ctx, page = _context(browser, server, 1440, 900)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/build")
    _nav_click(page, "[data-conv-new]")
    page.fill("#mc-input", "Please save a file for me")
    page.click("[data-mc-send]")
    expect(page.locator("[data-mc-stream-line] [data-slot='elapsed']")).to_have_text("11s", timeout=14000)   # time only, no wording about what for
    assert "Still working" not in page.inner_text("[data-mc-thread]")
    failure = page.locator("[data-turn-failure]")
    expect(failure).to_have_count(1, timeout=10000)
    expect(failure).to_contain_text("No answer: the relay cancelled the run because Master Craftsman sent nothing for too long while it worked")
    expect(failure).to_contain_text("Your message is kept")
    assert "Nothing was kept" not in page.inner_text("[data-mc-thread]")
    streaming_mc["runtime"].script = [nd({"t": "error", "class": "deadline"})]
    page.fill("#mc-input", "Try again please")
    page.click("[data-mc-send]")
    expect(failure).to_have_count(1)                                              # the second try replaced the first notice
    expect(failure).to_contain_text("time limit ran out")
    assert page.locator("[data-mc-thread]").inner_text().count("No answer:") == 1
    assert not errors, errors
    ctx.close()


# ── Guild 1.1 finishing: linked work and retired addresses, with the reserve switch OFF as in 1.1 ──

def _fresh_conversation(page, server):
    """A brand-new conversation, made through the page's own API (works at any width), opened on the Build page."""
    go(page, f"{server['url']}/guild-next/guild/build")
    cid = page.evaluate("""async () => {
        const page = JSON.parse(document.getElementById('guild-page').textContent);
        const r = await fetch(`${page.urls.api}/conversations`, { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': 'on_record' },
          body: JSON.stringify({ idempotency_key: 'b9-' + Math.random().toString(36).slice(2) + Date.now() }) });
        return (await r.json()).conversation.id; }""")
    go(page, f"{server['url']}/guild-next/guild/build?c={cid}")
    return cid


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 780)])
def test_b9_linked_work_opens_its_build_log_item_and_comes_back_without_the_workbench(browser, server, monkeypatch, width, height):
    monkeypatch.delenv("MINIMOI_GUILD_RESERVE_PAGES", raising=False)
    ctx, page = _context(browser, server, width, height)
    bad = []
    page.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    cid = _fresh_conversation(page, server)
    if width < 1000:
        page.locator("[data-floor-context] > summary").click()                                # the phone's folded Context area
    page.fill("[data-linked-input]", "12")
    page.press("[data-linked-input]", "Enter")
    expect(page.locator("[data-linked-link]")).to_have_text("#12 Floor API")
    assert page.get_attribute("[data-linked-link]", "href").endswith(f"/items/12?c={cid}")
    with page.expect_navigation():
        page.click("[data-linked-link]")
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("h1.page-title")).to_contain_text("#12")
    crumbs = page.locator(".crumbs")
    expect(crumbs).to_contain_text("Back to the conversation")
    expect(crumbs).to_contain_text("Build Log")
    for retired in ("Workbench", "Build Queue", "Open wall"):
        expect(page.locator("body")).not_to_contain_text(retired)
    with page.expect_navigation():                                                          # the Build Log crumb
        page.click("[data-back-build-log]")
    page.wait_for_selector("body[data-ready=true]")
    assert page.url.endswith("/guild/build/log?only=12")
    expect(page.locator("[data-bl-only]")).to_contain_text("Showing only #12")           # filtered to this item, not the whole log
    expect(page.locator("[data-bl-row]")).to_have_count(1)
    page.click("[data-bl-only-clear]")
    expect(page.locator("[data-bl-only]")).to_be_hidden()
    assert page.url.endswith("/guild/build/log")                                                  # the address is clean again
    page.go_back()
    page.wait_for_selector("body[data-ready=true]")
    with page.expect_navigation():                                                          # back to the asking conversation
        page.click("[data-back-conversation]")
    page.wait_for_selector("body[data-ready=true]")
    assert f"?c={cid}" in page.url and "/guild/build" in page.url
    if width < 1000:
        page.locator("[data-floor-context] > summary").click()
    expect(page.locator("[data-linked-link]")).to_have_text("#12 Floor API")                 # still attached; nothing was lost
    assert not [b for b in bad if b[0] != 401 and "/api/v1/continue" not in b[1]], bad    # Continue has no floor database in this test server: unknown, by design
    ctx.close()


def test_b9_every_1_1_page_shows_its_own_content_on_a_phone(browser, server, monkeypatch):
    """Found on 7 Oct: a page that is not marked as opening its content on a phone showed only chat there (the item page a
    linked-work link lands on, and Operate). Every page reachable from 1.1 must show its main content at phone width."""
    monkeypatch.delenv("MINIMOI_GUILD_RESERVE_PAGES", raising=False)
    ctx, page = _context(browser, server, 390, 780)
    paths = ["build", "build/log", "board", "workshop", "rooms", "operate", "improve", "experiment",
             "experiment/iot-connect", "build/items/12", "build/items/999", "build/postits", "media"]
    hidden = []
    for path in paths:
        response = page.goto(f"{server['url']}/guild-next/guild/{path}")
        if response is None or response.status >= 500:
            continue
        page.wait_for_load_state("load")
        box = page.evaluate("() => { const m = document.querySelector('main'); if (!m) return null; const r = m.getBoundingClientRect();"
                            " return [getComputedStyle(m).display, r.width, r.height]; }")
        if path != "build" and (box is None or box[0] == "none" or box[2] < 40):
            hidden.append((path, box))
    assert not hidden, hidden
    ctx.close()


def test_b9_a_missing_linked_item_says_so_and_offers_the_build_log(browser, server, monkeypatch):
    monkeypatch.delenv("MINIMOI_GUILD_RESERVE_PAGES", raising=False)
    ctx, page = _context(browser, server, 1440, 900)
    response = page.goto(f"{server['url']}/guild-next/guild/build/items/999")
    assert response.status == 404
    expect(page.locator("body")).to_contain_text("The Build Log has no item with this id")
    with page.expect_navigation():
        page.click("text=Back to the Build Log")
    assert page.url.endswith("/guild/build/log")
    ctx.close()


@pytest.mark.parametrize("old,landing", [("/guild/build/bench", "/guild/board"), ("/guild/build/queue", "/guild/build/log"),
                                         ("/guild/labs", "/guild/experiment")])
def test_b9_a_retired_address_lands_on_the_page_that_replaced_it(browser, server, monkeypatch, old, landing):
    monkeypatch.delenv("MINIMOI_GUILD_RESERVE_PAGES", raising=False)
    ctx, page = _context(browser, server, 1440, 900)
    page.goto(f"{server['url']}/guild-next{old}")
    page.wait_for_load_state("load")
    assert page.url.endswith(f"/guild-next{landing}"), page.url
    for retired in ("Workbench", "Build Queue", "Open wall", "The wall"):
        expect(page.locator("body")).not_to_contain_text(retired)
    ctx.close()


def test_b5_the_board_fits_twelve_notes_and_long_notes_open_in_place(browser, server, board_media):
    from minimoi_portal.guild_ui.stores import Author
    floor, _media = board_media
    robert = Author("robert", "owner", "Robert")
    store = floor.store()
    long_text = "A long note that needs more than a short preview. " * 6
    for i in range(12):
        store.add_postit(long_text if i == 3 else f"Note number {i}", robert, idempotency_key=f"b5-seed-{i:04d}")
    ctx, page = _context(browser, server, 1280, 720)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/board")
    expect(page.locator("[data-bd-note]")).to_have_count(12)
    bottom = page.locator("[data-bd-note]").evaluate_all("ns => Math.max(...ns.map(n => n.getBoundingClientRect().bottom))")
    assert bottom <= 720, bottom                                                      # all twelve in one desktop view
    more = page.locator("[data-bd-more]:not([hidden])")
    expect(more).to_have_count(1)                                                     # only the long note shows it
    expect(more).to_have_text("More")
    more.click()
    expect(more).to_have_text("Less")
    assert more.evaluate("b => getComputedStyle(b.parentElement.querySelector('.bd-text')).display") == "block"
    first = page.locator("[data-bd-note] .bd-text").first.inner_text()
    page.locator("[data-bd-note]").first.focus()
    page.keyboard.press("Alt+ArrowRight")                                             # keyboard reorder, kept
    page.reload()
    assert page.locator("[data-bd-note] .bd-text").nth(1).inner_text() == first
    page.locator(".bd-edit > summary").first.click()                                  # Discard lives in the small menu
    page.locator(".bd-discard").first.click()
    expect(page.locator("[data-bd-note]")).to_have_count(11)
    assert not errors, errors
    ctx.close()


def test_b5_the_prototype_lab_has_cards_list_and_a_run_book(browser, server, floor):
    ctx, page = _context(browser, server, 1280, 800)
    errors = _errors(page)
    go(page, f"{server['url']}/guild-next/guild/experiment")
    expect(page.locator("[data-proto-cards]")).to_be_visible()
    page.click('[data-proto-view="list"]')
    expect(page.locator("[data-proto-list]")).to_be_visible()
    page.reload()
    expect(page.locator("[data-proto-list]")).to_be_visible()                         # remembered
    page.click('[data-proto-view="cards"]')
    expect(page.locator("a.proto-open").first).to_have_attribute("href", "https://minimoi.ai/app/iotconnect/")
    page.locator('a:has-text("Look inside")').first.click()
    expect(page.locator("h1")).to_have_text("IoT Connect")
    page.evaluate("document.querySelectorAll('.proto-shot img').forEach(i => { i.loading = 'eager'; })")
    page.wait_for_function("() => [...document.querySelectorAll('.proto-shot img')].every(i => i.complete && i.naturalWidth > 0)", timeout=15000)
    page.locator(".proto-cmd .proto-copy").first.click()
    expect(page.locator(".proto-cmd .proto-copy").first).to_contain_text("cop")
    assert not errors, errors
    ctx.close()


def test_b5_more_appears_exactly_where_text_is_cut_off(browser, server, board_media):
    """Codex review P2: a short note with many lines, a photo caption, and a narrow screen that wraps more
    must each have a way to open, and a note that is not cut off must not show one."""
    from board_media_helpers import image_bytes
    from minimoi_portal.guild_ui.media import MediaStore, sanitize
    from minimoi_portal.guild_ui.stores import Author
    floor, media_dir = board_media
    robert = Author("robert", "owner", "Robert")
    store = floor.store()
    ms = MediaStore(store, media_dir)
    asset = ms.create("robert", sanitize(image_bytes("PNG")), idempotency_key="b5-asset-0001").value["id"]
    store.add_photo(asset, "robert", "caption one two three four five six seven eight nine ten eleven twelve thirteen "
                    "fourteen fifteen sixteen seventeen eighteen nineteen twenty", robert, idempotency_key="b5-photo-0001")
    store.add_postit("one\ntwo\nthree\nfour\nfive", robert, idempotency_key="b5-multi-0001")          # under 90 characters, five lines
    store.add_postit("short", robert, idempotency_key="b5-short-0001")
    store.add_postit("a considerably longer line of words that will wrap a good deal more on a narrow screen than on a wide one, "
                     "so it is cut off there", robert, idempotency_key="b5-wide-0001")
    check = """() => [...document.querySelectorAll('[data-bd-note]')].map(li => {
        const t = li.querySelector('.bd-text'), b = li.querySelector('[data-bd-more]');
        const was = li.dataset.open, preview = t.offsetHeight;
        li.dataset.open = 'true'; const full = t.offsetHeight;
        if (was === undefined) delete li.dataset.open; else li.dataset.open = was;
        return { text: t.textContent.slice(0, 12), cut: full > preview + 2, more: !!b && !b.hidden, photo: li.classList.contains('bd-photo') }; })"""
    ctx, page = _context(browser, server, 1280, 720)
    go(page, f"{server['url']}/guild-next/guild/board")
    rows = page.evaluate(check)
    assert len(rows) == 4 and all(r["cut"] == r["more"] for r in rows), rows                  # More only where it is cut off
    multi = next(r for r in rows if r["text"].startswith("one"))
    assert multi["cut"] and multi["more"]                                                        # the short five-line note
    assert next(r for r in rows if r["text"] == "short")["more"] is False, rows
    ctx.close()
    ctx, page = _context(browser, server, 320, 640)
    go(page, f"{server['url']}/guild-next/guild/board")
    narrow = page.evaluate(check)
    assert all(r["cut"] == r["more"] for r in narrow), narrow
    assert next(r for r in narrow if r["photo"])["more"] or not next(r for r in narrow if r["photo"])["cut"]
    page.locator("[data-bd-more]:not([hidden])").first.click()                                 # it opens in place, and says Less
    assert page.locator("[data-bd-more]").evaluate_all("bs => bs.some(b => b.textContent === 'Less')")
    ctx.close()


def test_b5_attachment_messages_are_fully_readable_on_a_phone(browser, server, board_media, tmp_path):
    """Codex review P2: on a phone a kept image must say, in full, that Master Craftsman cannot see it, a readable
    document must say it is ready, and a refused file must say why, with nothing clipped or ellipsized."""
    from board_media_helpers import image_bytes
    png = tmp_path / "plan.png"
    png.write_bytes(image_bytes("PNG"))
    sheet = tmp_path / "budget.xlsx"
    sheet.write_bytes(b"PK\x03\x04 not really a sheet")
    notes = tmp_path / "notes.txt"
    notes.write_text("Launch is 14 November.")
    for size in ({"width": 390, "height": 780}, {"width": 320, "height": 640}):
        ctx, page = _context(browser, server, **size)
        go(page, f"{server['url']}/guild-next/guild/build")
        page.set_input_files("[data-mc-file]", [str(png), str(sheet), str(notes)])
        expect(page.locator('.att[data-state="ok"]')).to_have_count(2)
        expect(page.locator('.att[data-state="ok"] .att-status').first).to_contain_text("can’t see images yet")
        expect(page.locator('.att[data-state="ok"] .att-status').nth(1)).to_have_text("")
        expect(page.locator('.att[data-state="failed"] .att-status')).to_contain_text("can’t be read yet")
        rows = page.evaluate("""() => [...document.querySelectorAll('.att')].map(a => {
            const s = a.querySelector('.att-status'), r = a.getBoundingClientRect();
            return { clipped: s.scrollWidth > s.clientWidth + 1 || s.scrollHeight > s.clientHeight + 1,
                     inside: r.left >= 0 && r.right <= innerWidth, text: s.textContent }; })""")
        assert len(rows) == 3 and all(not r["clipped"] and r["inside"] for r in rows), rows
        assert page.evaluate("document.documentElement.scrollWidth") <= size["width"]
        expect(page.locator("[data-mc-input]")).to_be_visible()                       # the composer is still reachable
        ctx.close()
    ctx, page = _context(browser, server, 1440, 900)                                  # leave no picture behind for the next test
    go(page, f"{server['url']}/guild-next/guild/build")
    page.click("[data-file-remove]")
    expect(page.locator("[data-conv-files] [data-file]")).to_have_count(0)
    page.click("[data-doc-remove]")
    expect(page.locator("[data-conv-files] [data-doc]")).to_have_count(0)
    ctx.close()


def test_b5_remove_says_when_the_library_hold_was_not_released_and_retry_works(browser, server, board_media, tmp_path, monkeypatch):
    """Codex re-review P2: a failed release is shown, never hidden, and the page offers a Retry that works."""
    from board_media_helpers import image_bytes
    from minimoi_portal.guild_ui.media import MediaStore
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    png = tmp_path / "plan.png"
    png.write_bytes(image_bytes("PNG"))
    ctx, page = _context(browser, server, 1440, 900)
    go(page, f"{server['url']}/guild-next/guild/build")
    before = page.locator("[data-conv-files] .fc-file").count()            # the shared test conversation may hold earlier files
    page.set_input_files("[data-mc-file]", [str(png)])
    expect(page.locator("[data-conv-files] .fc-file")).to_have_count(before + 1)
    real = MediaStore.detach

    def broken(self, *a, **kw):
        raise FloorStoreUnavailable("down (test)")

    monkeypatch.setattr(MediaStore, "detach", broken)
    page.locator("[data-conv-files] [data-file-remove]").last.click()
    expect(page.locator("[data-conv-pending] [data-file-retry]")).to_have_count(1)
    expect(page.locator("[data-conv-pending]")).to_contain_text("not released yet")
    expect(page.locator("[data-conv-files] .fc-file")).to_have_count(before)
    page.reload()                                                                      # it is remembered, not just on screen
    expect(page.locator("[data-conv-pending] [data-file-retry]")).to_be_visible()
    page.click("[data-file-retry]")                                                    # still failing: it stays, and says so
    expect(page.locator("[data-conv-pending] [data-file-retry]")).to_be_enabled()
    monkeypatch.setattr(MediaStore, "detach", real)
    page.click("[data-file-retry]")
    expect(page.locator("[data-conv-pending]")).to_be_hidden()
    page.reload()
    expect(page.locator("[data-file-retry]")).to_have_count(0)
    ctx.close()


def test_b5_a_stale_retry_cannot_remove_a_file_that_was_attached_again(browser, server, board_media, tmp_path, monkeypatch):
    """Codex second re-review: failed release -> the image is attached again from another tab -> the first page's stale Retry.
    The current file and its hold must survive, and the stale page must show the true state."""
    from board_media_helpers import image_bytes
    from minimoi_portal.guild_ui.media import MediaStore
    from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
    png = tmp_path / "plan.png"
    png.write_bytes(image_bytes("PNG", color=(11, 22, 33)))
    ctx_a, page_a = _context(browser, server, 1440, 900)
    ctx_b, page_b = _context(browser, server, 1440, 900)
    go(page_a, f"{server['url']}/guild-next/guild/build")
    before = page_a.locator("[data-conv-files] [data-file]").count()
    page_a.set_input_files("[data-mc-file]", [str(png)])
    expect(page_a.locator("[data-conv-files] [data-file]")).to_have_count(before + 1)
    asset = page_a.locator("[data-conv-files] [data-file]").last.get_attribute("data-file")
    real = MediaStore.detach
    monkeypatch.setattr(MediaStore, "detach", lambda self, *a, **kw: (_ for _ in ()).throw(FloorStoreUnavailable("down (test)")))
    page_a.locator("[data-conv-files] [data-file-remove]").last.click()
    expect(page_a.locator(f'[data-pending="{asset}"]')).to_be_visible()
    monkeypatch.setattr(MediaStore, "detach", real)
    go(page_b, f"{server['url']}/guild-next/guild/build")                                 # another tab attaches the same image again
    status = page_b.evaluate("""async (asset) => {
        const page = JSON.parse(document.getElementById('guild-page').textContent);
        const r = await fetch(`${page.urls.api}/conversations/${page.conversation.id}/attachments`, { method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': page.csrf_token, 'X-Record-Mode': 'on_record' },
            body: JSON.stringify({ asset_id: asset, name: 'plan.png', idempotency_key: 'stale-retry-' + Date.now() }) });
        return r.status; }""", asset)
    assert status == 200
    page_a.locator(f'[data-pending="{asset}"] [data-file-retry]').click()                 # the stale page's Retry
    expect(page_a.locator(f'[data-pending="{asset}"]')).to_have_count(0)                  # it learned the truth ...
    expect(page_a.locator(f'[data-conv-files] [data-file="{asset}"]')).to_have_count(1)  # ... and the file is still there
    page_a.reload()
    expect(page_a.locator(f'[data-conv-files] [data-file="{asset}"]')).to_have_count(1)
    expect(page_a.locator(f'[data-pending="{asset}"]')).to_have_count(0)
    ctx_a.close()
    ctx_b.close()



