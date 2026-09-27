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
    yield {"url": f"http://127.0.0.1:{port}", "queue": queue}
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
        expect(page.locator("[data-mc-send]")).to_be_disabled()
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
