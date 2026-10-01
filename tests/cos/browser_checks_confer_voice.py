"""Browser checks for Confer's voice button (#268). Opt-in (Playwright + Chrome):

    venv/bin/python3 -m pytest tests/cos/browser_checks_confer_voice.py -q -p no:cacheprovider

A stand-in cos-scheduler on a loopback port renders the real cos_ui.html and
serves the real voice controller. The provider adapter (the part that opens
the microphone and talks to the voice provider) is replaced, in the browser
only, by a fake that records whether the "microphone" is open and lets the
test decide when a start opens or throws. Every request that is not to the
loopback stand-in is aborted: no provider, no microphone and no model is
ever reached.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, send_from_directory
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

REPO = Path(__file__).resolve().parents[2]

FAKE_ADAPTER = """
// Test double for the OpenAI WebRTC adapter: same interface, no network, no microphone.
// The test sets window.__voiceTest at any time, so it is looked up on every call.
const T = () => (window.__voiceTest ||= { micOn: false, connects: 0, ended: [] });
export class OpenAIWebRTCAdapter {
  constructor() { this._listeners = {}; }
  on(name, fn) { (this._listeners[name] ||= []).push(fn); }
  _emit(name, payload) { for (const fn of this._listeners[name] || []) fn(payload); }
  prepareSession(config) { this._config = config; }
  async connect(credentials) {
    const t = T();
    t.connects += 1;
    t.micOn = true;                                    // a real adapter opens the microphone here
    if (t.hold) await new Promise((resolve) => { t.release = resolve; });
    if (t.throwOnConnect) throw new Error('connect failed in test');
  }
  start() {}
  mute() {}
  unmute() {}
  sendContinuationInstruction() {}
  sendFunctionResult() {}
  end(reason) { const t = T(); t.micOn = false; t.ended.push(reason); this._emit('closed', { reason }); }
}
"""


@pytest.fixture(scope="module")
def cos():
    state = {"boot": "fail", "delay": 0.0, "boots": 0}
    app = Flask("cos_voice_stand_in", template_folder=str(REPO / "domains/cos/templates"),
                static_folder=str(REPO / "domains/cos/static"))

    @app.route("/ui/<tab>")
    def ui(tab):
        return render_template("cos_ui.html", initial_tab=tab)

    @app.route("/static/realtime-voice/<path:name>")
    def voice_static(name):
        return send_from_directory(REPO / "core/realtime_voice/static", name)

    @app.route("/status")
    def status():
        return jsonify({"backend_label": "stand-in", "model_label": "none"})

    @app.route("/ui/api/<what>")
    def lists(what):
        return jsonify([])

    @app.route("/api/realtime-voice/confer/capabilities")
    def capabilities():
        return jsonify({"ok": True, "default_provider": "openai",
                        "providers": [{"provider": "openai", "label": "OpenAI Voice"}]})

    @app.route("/api/realtime-voice/confer/bootstrap", methods=["POST"])
    def bootstrap():
        state["boots"] += 1
        time.sleep(state["delay"])
        if state["boot"] == "fail":
            return jsonify({"ok": False, "error": "voice is unavailable right now"}), 503
        return jsonify({"ok": True, "provider": "openai", "model": "test-model", "client_secret": "not-a-secret",
                        "warning_minutes": 20, "max_minutes": 30, "session_config": {}})

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{port}", "state": state}
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def confer(browser, cos):
    cos["state"].update(boot="fail", delay=0.0, boots=0)
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    origin = cos["url"]

    def only_loopback(route):
        if route.request.url.startswith(origin):
            return route.continue_()
        return route.abort()
    ctx.route("**/*", only_loopback)
    ctx.route("**/adapters/openai-webrtc-adapter.js*",
              lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_ADAPTER))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{origin}/ui/confer")
    expect(page.locator("#btn-voice")).to_be_enabled()
    yield page, cos["state"], errors
    ctx.close()


def _t(page, key):
    return page.evaluate(f"(window.__voiceTest || {{}})[{key!r}]")


def _at_start(page):
    button = page.locator("#btn-voice")
    expect(button).to_have_text("🎤")
    expect(button).to_have_attribute("aria-label", "Start voice conversation")
    expect(button).not_to_have_class("recording")
    expect(button).to_be_enabled()
    expect(page.locator("#voice-provider-select")).to_be_enabled()


def test_a_failed_voice_start_returns_the_button_to_start(confer):
    page, state, errors = confer
    for attempt in (1, 2):                    # the second tap starts again; it does not try to stop a ghost session
        page.click("#btn-voice")
        expect(page.locator("#voice-transcript")).to_contain_text("Voice error: voice is unavailable right now")
        _at_start(page)
        expect(page.locator("#voice-status")).to_have_text("Voice did not start")
        assert state["boots"] == attempt
    assert not errors, errors


def test_a_started_session_stops_normally(confer):
    page, state, errors = confer
    state["boot"] = "ok"
    page.click("#btn-voice")
    expect(page.locator("#btn-voice")).to_have_attribute("aria-label", "Stop voice conversation")
    assert _t(page, "micOn") is True
    page.click("#btn-voice")
    _at_start(page)
    assert _t(page, "micOn") is False and _t(page, "ended") == ["user_ended"]
    assert not errors, errors


@pytest.mark.parametrize("when", ["during-bootstrap", "while-connecting"])
def test_stop_during_a_start_that_then_succeeds_ends_the_session(confer, when):
    page, state, errors = confer
    state["boot"] = "ok"
    if when == "during-bootstrap":
        state["delay"] = 1.0
    else:
        page.evaluate("window.__voiceTest = { micOn: false, connects: 0, ended: [], hold: true }")
    page.click("#btn-voice")
    button = page.locator("#btn-voice")
    expect(button).to_have_attribute("aria-label", "Cancel voice start")
    if when == "while-connecting":
        page.wait_for_function("() => window.__voiceTest.connects === 1")
        assert _t(page, "micOn") is True                               # the microphone is open, the start not done
    page.click("#btn-voice")                                           # Stop, while the start is in progress
    expect(page.locator("#voice-status")).to_have_text("Stopping…")
    expect(button).to_be_disabled()
    if when == "while-connecting":
        page.evaluate("window.__voiceTest.release()")                  # the start now succeeds
    page.wait_for_function("() => window.__voiceTest && window.__voiceTest.connects === 1 && !window.__voiceTest.micOn")
    _at_start(page)
    assert _t(page, "ended") == ["user_ended"]                         # ended as soon as it opened
    expect(page.locator("#voice-status")).to_have_text("Stopped")
    assert not errors, errors


def test_a_start_that_throws_closes_what_it_opened(confer):
    page, state, errors = confer
    state["boot"] = "ok"
    page.evaluate("window.__voiceTest = { micOn: false, connects: 0, ended: [], throwOnConnect: true }")
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_have_text("Voice error: connect failed in test")
    _at_start(page)
    expect(page.locator("#voice-status")).to_have_text("Voice did not start")
    assert _t(page, "micOn") is False and _t(page, "ended") == ["start_failed"]
    page.evaluate("window.__voiceTest.throwOnConnect = false")
    page.click("#btn-voice")                                           # and it can start again afterwards
    expect(page.locator("#btn-voice")).to_have_attribute("aria-label", "Stop voice conversation")
    page.click("#btn-voice")
    _at_start(page)
    assert not errors, errors
