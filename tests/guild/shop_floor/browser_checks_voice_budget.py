"""The voice budget's refusals as the owner sees them in the composer (real Chrome, real portal and page scripts; stand-ins for the provider and the shared controller).
A refused session start, a refused reading aloud, and a refused dictation each say why, in plain words, and say that typing still works."""
import json

from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, rooms, _context, go, _errors  # noqa:F401
from browser_checks_mc_voice import FAKE_CONTROLLER, _mc, _open, _start  # noqa:F401
from browser_checks_dictation import _setup  # noqa:F401

USED_UP = ("Today's voice allowance is used up ($19.99 of $20.00 reserved; reserved allowance: the provider's exposure estimate, counted when a session or reading starts; "
           "not a bill and not a guaranteed spending limit). It renews at midnight Chicago time.")
BUDGET_GONE = {"ok": True, "day": "2026-10-13", "timezone": "America/Chicago", "cap_usd": 20.0, "reserved_usd": 19.99, "remaining_usd": 0.01,
               "label": "reserved allowance: the provider's exposure estimate, counted when a session or reading starts; not a bill and not a guaranteed spending limit"}

# The same stand-in as the MC voice checks, except that it asks the real bootstrap URL like the shared controller does (throwing the server's own message) and its
# speech playback fails the way an <audio> element does when it is handed a JSON refusal.
REFUSING = (FAKE_CONTROLLER
            .replace("async start(provider) {\n    this._active = true;", """async start(provider) {
    const response = await fetch(this.o.bootstrapUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const credentials = await response.json().catch(() => ({}));
    if (!response.ok || !credentials.ok) throw new Error(credentials.error || `Voice session failed (HTTP ${response.status})`);
    this._active = true;""")
            .replace("async _play(url) { this.played.push(url); this._onStateChange('speaking'); this._onStateChange('listening'); }",
                     "async _play(url) { this.played.push(url); const r=await fetch(url); const b=await r.json(); throw new Error(b.error + ' The answer is in the chat.'); }"))


def test_a_refused_session_start_says_why_in_the_composer_and_typing_still_works(browser, server, floor, monkeypatch):
    ctx, page, errors, bodies = _open(browser, server, monkeypatch)
    page.route("**/mc/voice/static/realtime-confer-controller.js", lambda route: route.fulfill(body=REFUSING, content_type="text/javascript"))
    page.route("**/mc/voice/bootstrap", lambda route: route.fulfill(status=429, json={"ok": False, "error": USED_UP, "budget": BUDGET_GONE}))
    page.click("[data-mc-voice-btn]")
    status = page.locator("[data-mc-voice-status]")
    expect(status).to_contain_text("Voice could not start: Today's voice allowance is used up", timeout=10000)
    expect(status).to_contain_text("midnight Chicago time")
    expect(status).to_contain_text("Typing still works.")
    expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "false")
    page.fill("#mc-input", "typing is unaffected")
    expect(page.locator("#mc-input")).to_have_value("typing is unaffected")
    ctx.close()


def test_a_ledger_that_cannot_be_read_says_voice_is_paused(browser, server, floor, monkeypatch):
    ctx, page, errors, bodies = _open(browser, server, monkeypatch)
    page.route("**/mc/voice/static/realtime-confer-controller.js", lambda route: route.fulfill(body=REFUSING, content_type="text/javascript"))
    page.route("**/mc/voice/bootstrap", lambda route: route.fulfill(status=503, json={"ok": False, "error": "Voice is paused: the daily voice allowance could not be checked, so nothing was sent to the speech provider."}))
    page.click("[data-mc-voice-btn]")
    expect(page.locator("[data-mc-voice-status]")).to_contain_text("Voice could not start: Voice is paused", timeout=10000)
    expect(page.locator("[data-mc-voice-status]")).to_contain_text("nothing was sent to the speech provider")
    ctx.close()


def test_a_refused_reading_says_the_allowance_is_used_up_and_points_to_the_chat(browser, server, floor, monkeypatch):
    services, saved, sent = _mc(server)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        page.route("**/mc/voice/static/realtime-confer-controller.js", lambda route: route.fulfill(body=REFUSING, content_type="text/javascript"))
        page.route("**/mc/voice/bootstrap", lambda route: route.fulfill(json={"ok": True, "provider": "openai", "client_secret": "ek", "warning_minutes": 13, "max_minutes": 15}))
        page.route("**/mc/voice/capabilities", lambda route: route.fulfill(json={"ok": True, "providers": [{"provider": "openai", "label": "OpenAI"}], "default_provider": "openai", "budget": BUDGET_GONE}))
        page.route("**/mc/voice/speech/**", lambda route: route.fulfill(status=429, json={"ok": False, "error": USED_UP}))
        _start(page)
        page.evaluate("window.__voice.say('What is stuck on twelve?')")
        status = page.locator("[data-mc-voice-status]")
        expect(status).to_contain_text("Today's voice allowance is used up ($19.99 of $20.00 reserved", timeout=10000)
        expect(status).to_contain_text("The answer is in the chat.")
        expect(page.locator("[data-mc-thread]")).to_contain_text("Item 12 is in build.")           # MC's kept reply is still in the thread
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_a_refused_dictation_says_why_and_leaves_the_draft_alone(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.route("**/dictation/bootstrap", lambda route: route.fulfill(status=429, json={"ok": False, "error": USED_UP, "budget": BUDGET_GONE}))
    page.fill("#mc-input", "my draft")
    page.click("[data-dictate='#mc-input']")
    status = page.locator("[data-dictation-status]")
    expect(status).to_contain_text("Dictation could not start: Today's voice allowance is used up", timeout=10000)
    expect(status).to_contain_text("Typing still works.")
    expect(page.locator("#mc-input")).to_have_value("my draft")
    assert page.evaluate("document.querySelector('#mc-input').readOnly") is False
    ctx.close()
