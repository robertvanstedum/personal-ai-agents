"""Browser checks for the shared realtime voice controller, on every page that
uses it: CoS Confer, Mein Deutsch (Gespräche) and Meu Português (Conversas).

- #273: when a session ends from the provider's side (a provider-side close,
  or a provider error), the microphone is released.
- Phase A: a provider error is a visible failure with the microphone released,
  never a silent "reconnecting" wait; benign provider notices keep the session.
- Phase A: the voice reply toggle ("speak and write" by default, or "write
  only": the reply's audio muted, its text shown live), remembered per page on
  this device, and still working when the browser refuses storage.

Opt-in (Playwright + Chrome):

    venv/bin/python3 -m pytest tests/browser_checks_voice_mic.py -q -p no:cacheprovider

The real pages are served on loopback: the German and Portuguese apps
themselves, and a stand-in cos-scheduler that renders the real cos_ui.html. The
provider adapter is replaced, in the browser only, by a fake that records
whether the "microphone" is open and lets the test close the session from the
provider's side. The voice bootstrap is answered by the test. Every request
that is not to loopback is aborted: no provider, no microphone, no model, no key.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, send_from_directory
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

REPO = Path(__file__).resolve().parent.parent
SHOTS = os.environ.get("VOICE_SHOTS")

FAKE_ADAPTER = """
// Test double for the OpenAI WebRTC adapter: the real interface, no network, no microphone.
const T = () => (window.__voiceTest ||= { micOn: false, ends: 0 });
export class OpenAIWebRTCAdapter {
  constructor() { this._listeners = {}; T().adapter = this; }
  on(name, fn) { (this._listeners[name] ||= []).push(fn); }
  _emit(name, payload) { for (const fn of this._listeners[name] || []) fn(payload); }
  prepareSession(config) { this._config = config; }
  async connect(credentials) {
    T().micOn = true;                                   // a real adapter opens the microphone here
    setTimeout(() => this._emit('connected', { provider: 'openai' }), 50);
  }
  start() {}
  mute() {}
  unmute() {}
  setOutputMuted(muted) { T().outputMuted = muted; }
  sendContinuationInstruction() {}
  sendFunctionResult() {}
  end(reason) { const t = T(); t.ends += 1; t.micOn = false; this._emit('closed', { reason }); }
  say(id, text) {
    this._emit('output_transcript', { item_id: id, text, is_delta: false, completed: true });
    this._emit('assistant_stopped', {});
  }
}
"""
BOOTSTRAP = {"ok": True, "provider": "openai", "model": "test-model", "client_secret": "not-a-secret",
             "warning_minutes": 20, "max_minutes": 30, "session_config": {}}


def _serve(app):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def _confer_app():
    app = Flask("cos_mic_stand_in", template_folder=str(REPO / "domains/cos/templates"),
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

    @app.route("/ui/voice/transcript", methods=["POST"])
    def transcript():
        return jsonify({"saved": False, "reason": "no_turn_log"})

    @app.route("/ui/private-mode")
    def private_mode():
        return jsonify({"available": False, "private": False})

    @app.route("/api/realtime-voice/confer/outcome", methods=["POST"])
    def outcome():
        return jsonify({"ok": True})
    return app


def _german_app():
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "domains" / "german"))
    from html_server import app
    return app


def _portuguese_app():
    spec = importlib.util.spec_from_file_location("portuguese_html_server_mic", REPO / "domains/portuguese/html_server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["portuguese_html_server_mic"] = module
    spec.loader.exec_module(module)
    return module.app


PAGES = {
    "confer": {"app": _confer_app, "path": "/ui/confer", "start": "#btn-voice"},
    "german": {"app": _german_app, "path": "/gesprache?realtime_voice=1", "start": "#realtime-voice-start-btn"},
    "portuguese": {"app": _portuguese_app, "path": "/conversas?realtime_voice=1", "start": "#realtime-voice-start-btn"},
}


@pytest.fixture(scope="module")
def servers():
    running = {name: _serve(page["app"]()) for name, page in PAGES.items()}
    yield {name: url for name, (_, url) in running.items()}
    for srv, _ in running.values():
        srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def _open(browser, servers, name, *, init_script=None):
    origin = servers[name]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                              extra_http_headers={"X-Minimoi-User-Tier": "owner", "X-Minimoi-Username": "robert"})
    if init_script:
        ctx.add_init_script(init_script)

    def only_loopback(route):
        return route.continue_() if route.request.url.startswith(origin) else route.abort()
    ctx.route("**/*", only_loopback)
    ctx.route("**/adapters/openai-webrtc-adapter.js*",
              lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_ADAPTER))
    ctx.route("**/api/realtime-voice/**bootstrap",
              lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(BOOTSTRAP)))
    page = ctx.new_page()
    page.goto(f"{origin}{PAGES[name]['path']}")
    if name != "confer":                                   # a persona and a scene, as a learner would pick them
        page.evaluate("""() => {
            document.querySelector('.persona-list-item')?.classList.add('active');
            if (!document.querySelector('.scene-btn.active')) {
                const b = document.createElement('button');
                b.className = 'scene-btn active'; b.dataset.key = 'test_scene'; b.hidden = true;
                document.body.append(b);
            }
        }""")
    return ctx, page


def _start(page, name):
    button = page.locator(PAGES[name]["start"])
    expect(button).to_be_enabled()
    button.click()
    page.wait_for_function("() => window.__voiceTest && window.__voiceTest.micOn === true")
    page.wait_for_timeout(150)                              # connected: the session is active


def _ended_on_screen(page, name):
    if name == "confer":
        expect(page.locator("#btn-voice")).to_have_text("🎤")
    else:
        expect(page.locator("#realtime-voice-start-btn")).to_be_visible()
        expect(page.locator("#realtime-voice-end-btn")).to_be_hidden()


@pytest.mark.parametrize("name", list(PAGES))
def test_a_provider_side_close_releases_the_microphone(browser, servers, name):
    ctx, page = _open(browser, servers, name)
    _start(page, name)
    page.evaluate("window.__voiceTest.adapter._emit('closed', { reason: 'connection_closed' })")
    _ended_on_screen(page, name)
    assert page.evaluate("window.__voiceTest.micOn") is False, "the microphone is still open"
    assert page.evaluate("window.__voiceTest.ends") == 1
    ctx.close()


ERROR_TEXT = {"confer": "#voice-transcript", "german": "#realtime-voice-error", "portuguese": "#realtime-voice-error"}


@pytest.mark.parametrize("name", list(PAGES))
def test_a_provider_error_is_visible_and_releases_the_microphone(browser, servers, name):
    ctx, page = _open(browser, servers, name)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    outcomes = []
    page.on("request", lambda r: outcomes.append(r) if r.url.endswith("/outcome") else None)
    _start(page, name)
    page.evaluate("""window.__voiceTest.adapter._emit('recoverable_error',
        { reason: 'provider_error', code: 'server_error', type: 'server_error_type',
          detail: 'The server had an error' })""")
    _ended_on_screen(page, name)                                          # at once: no "reconnecting" wait
    assert page.evaluate("window.__voiceTest.micOn") is False, "the microphone is still open"
    assert page.evaluate("window.__voiceTest.ends") == 1
    shown = page.locator(ERROR_TEXT[name])
    expect(shown).to_be_visible()
    expect(shown).to_contain_text("The server had an error")
    # #281 review F6: the code and type are logged on the server, never the message.
    page.wait_for_timeout(200)
    assert len(outcomes) == 1
    sent = json.loads(outcomes[0].post_data)
    assert sent == {"outcome": "provider_error", "provider": "openai", "reason": "provider_error",
                    "code": "server_error", "type": "server_error_type"}
    assert outcomes[0].url.endswith("/api/realtime-voice/confer/outcome" if name == "confer"
                                    else "/api/realtime-voice/outcome")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}-provider-error.png", full_page=name == "confer")
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("name", list(PAGES))
def test_benign_provider_notices_keep_the_session(browser, servers, name):
    ctx, page = _open(browser, servers, name)
    _start(page, name)
    for info in ("{ reason: 'provider_error', code: 'response_cancel_not_active', detail: 'no active response' }",
                 "{ reason: 'provider_error', code: 'conversation_already_has_active_response', detail: 'busy' }",
                 "{ reason: 'input_transcription_failed', detail: 'one turn not transcribed' }"):
        page.evaluate(f"window.__voiceTest.adapter._emit('recoverable_error', {info})")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__voiceTest.micOn") is True               # still live
    assert page.evaluate("window.__voiceTest.ends") == 0
    ctx.close()


REPLY = {"confer": "#voice-reply-mode", "german": "#realtime-voice-reply-mode",
         "portuguese": "#realtime-voice-reply-mode"}
WRITE_LABEL = {"confer": "Write only", "german": "Nur schreiben", "portuguese": "Só escrever"}
SPEAK_LABEL = {"confer": "Speak and write", "german": "Sprechen und schreiben", "portuguese": "Falar e escrever"}


NOTE = {"confer": "#voice-reply-note", "german": "#realtime-voice-reply-note",
        "portuguese": "#realtime-voice-reply-note"}
NOTE_TEXT = {"confer": "the provider still generates (and bills) the audio",
             "german": "der Anbieter erzeugt (und berechnet) das Audio weiterhin",
             "portuguese": "o provedor ainda gera (e cobra) o áudio"}


def _live_reply(page, name):
    if name == "confer":
        return page.locator("#chat-log .msg-cos .msg-text").last
    return page.locator("#realtime-live-replies-text")


@pytest.mark.parametrize("name", list(PAGES))
def test_the_reply_toggle_defaults_to_speak_and_write(browser, servers, name):
    ctx, page = _open(browser, servers, name)
    select = page.locator(REPLY[name])
    expect(select).to_be_visible()
    expect(select).to_have_value("speak")
    assert select.locator("option").all_text_contents() == [SPEAK_LABEL[name], WRITE_LABEL[name]]
    _start(page, name)
    assert page.evaluate("window.__voiceTest.outputMuted") is False       # spoken
    ctx.close()


@pytest.mark.parametrize("name", list(PAGES))
def test_write_only_mutes_the_reply_shows_its_text_and_is_remembered(browser, servers, name):
    ctx, page = _open(browser, servers, name)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    note = page.locator(NOTE[name])
    expect(note).to_be_hidden()
    page.select_option(REPLY[name], "write")
    expect(note).to_be_visible()                                          # #281 review F4: audio still billed
    expect(note).to_contain_text(NOTE_TEXT[name])
    _start(page, name)
    assert page.evaluate("window.__voiceTest.outputMuted") is True        # muted from the start
    page.evaluate("window.__voiceTest.adapter.say('a1', 'Guten Tag! Was darf es sein?')")
    reply = _live_reply(page, name)
    expect(reply).to_be_visible()
    expect(reply).to_contain_text("Guten Tag! Was darf es sein?")        # the text shows live
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}-write-only.png", full_page=name == "confer")
    page.select_option(REPLY[name], "speak")                              # during a session too
    assert page.evaluate("window.__voiceTest.outputMuted") is False
    page.select_option(REPLY[name], "write")
    page.reload()
    expect(page.locator(REPLY[name])).to_have_value("write")              # remembered on this device
    page.select_option(REPLY[name], "speak")                              # leave the device default
    assert not errors, errors
    ctx.close()


BLOCKED_STORAGE = """
Object.defineProperty(window, 'localStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } });
"""


@pytest.mark.parametrize("name", list(PAGES))
def test_the_toggle_works_when_storage_is_blocked(browser, servers, name):
    ctx, page = _open(browser, servers, name, init_script=BLOCKED_STORAGE)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    expect(page.locator(REPLY[name])).to_have_value("speak")
    page.select_option(REPLY[name], "write")
    _start(page, name)
    assert page.evaluate("window.__voiceTest.outputMuted") is True
    assert not [e for e in errors if "reply" in e.lower() or "blocked" in e], errors
    ctx.close()


@pytest.mark.parametrize("name", list(PAGES))
def test_the_owners_stop_still_releases_it_once(browser, servers, name):
    ctx, page = _open(browser, servers, name)
    _start(page, name)
    stop = "#btn-voice" if name == "confer" else "#realtime-voice-end-btn"
    page.click(stop)
    _ended_on_screen(page, name)
    assert page.evaluate("window.__voiceTest.micOn") is False
    assert page.evaluate("window.__voiceTest.ends") == 1                  # idempotent: ended once
    ctx.close()
