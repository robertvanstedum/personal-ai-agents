"""Browser checks: Confer voice's standard flow, end to end through the real
portal proxy on loopback (Phase A). Opt-in (Playwright + Chrome):

    venv/bin/python3 -m pytest tests/cos/browser_checks_confer_voice_portal.py -q -p no:cacheprovider

The real portal runs on a loopback port, signed in as the owner by a session
cookie it would issue itself. cos-scheduler is a stand-in on another loopback
port: it renders the real cos_ui.html, serves the real voice static files,
answers /ui/send like CoS would (recording what reached it), and mounts the
real transcript route (domains/cos/voice_transcripts.py) on a temporary turn
log. The provider adapter is replaced, in the browser only, by a fake that
plays the provider's side: greeting, the owner's turn, a function call, the
reply. Every request that is not to the portal is aborted: no provider, no
microphone, no model and no key is ever reached.

The flow: CoS greets first -> the owner's turn shows live -> a function call
POSTs ui/send (html_voice; the portal's write-guard token too, when the page
has one) -> the reply shows live, spoken or written only -> Stop posts the
transcript, which lands in the turn log unless Private.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest
from flask import Flask, jsonify, render_template, request, send_from_directory
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

REPO = Path(__file__).resolve().parents[2]
OWNER = {"username": "robert", "tier": "owner", "display_name": "Robert", "auth_id": 1}
SHOTS = os.environ.get("CONFER_SHOTS")

FAKE_ADAPTER = """
// Test double for the OpenAI WebRTC adapter: the real interface, no network, no microphone.
const T = () => (window.__voiceTest ||= {});
export class OpenAIWebRTCAdapter {
  constructor() { this._listeners = {}; const t = T(); t.adapter = this; t.results = []; t.opening = null;
                  t.outputMuted = false; t.micOn = false; }
  on(name, fn) { (this._listeners[name] ||= []).push(fn); }
  _emit(name, payload) { for (const fn of this._listeners[name] || []) fn(payload); }
  prepareSession(config) {}
  setOutputMuted(muted) { T().outputMuted = muted; }
  async connect() { T().micOn = true; T().mutedAtConnect = T().outputMuted;
                    setTimeout(() => this._emit('connected', { provider: 'openai' }), 30); }
  start() {}
  mute() {}
  unmute() {}
  sendContinuationInstruction(text) { T().opening = text; }
  sendFunctionResult(callId, output) { T().results.push({ callId, output }); }
  end(reason) { T().micOn = false; this._emit('closed', { reason }); }
  // The provider's side, driven by the test:
  say(id, text) {
    this._emit('output_transcript', { item_id: id, text, is_delta: false, completed: true });
    this._emit('assistant_stopped', {});
  }
  hear(id, text) { this._emit('input_transcript', { item_id: id, text, is_delta: false, completed: true }); }
  call(id, name, args) { this._emit('function_call', { call_id: id, name, arguments: JSON.stringify(args) }); }
}
"""


def _serve(app):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def _stand_in_cos(seen: list):
    from domains.cos import private_mode
    from domains.cos.voice_transcripts import create_voice_transcript_blueprint
    app = Flask("cos_voice_portal_stand_in", template_folder=str(REPO / "domains/cos/templates"),
                static_folder=str(REPO / "domains/cos/static"))
    app.register_blueprint(create_voice_transcript_blueprint())
    app.register_blueprint(private_mode.create_private_mode_blueprint())       # the real switch

    @app.before_request
    def note():
        if request.method == "POST":
            seen.append({"path": request.path, "csrf": request.headers.get("X-CSRF-Token"),
                         "auth_id": request.headers.get("X-Minimoi-Auth-Id"),
                         "body": request.get_json(silent=True)})

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
        # As chief_of_staff wires it: Private mode and its epoch at the start.
        return jsonify({"ok": True, "provider": "openai", "model": "test-model", "client_secret": "not-a-secret",
                        "warning_minutes": 20, "max_minutes": 30, "session_config": {},
                        "session_context": private_mode.state()})

    @app.route("/api/realtime-voice/confer/outcome", methods=["POST"])
    def outcome():
        return jsonify({"ok": True})

    @app.route("/ui/send", methods=["POST"])
    def send():
        # As chief_of_staff's /ui/send: each reply says whether it was Private.
        return jsonify({"reply": "Two items today: the voice fix and the review.", "operation": None,
                        "private": private_mode.state()["private"]})

    return app


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    import minimoi_portal.app as portal_module
    import minimoi_portal.config as cfg
    from minimoi_portal.app import app as portal
    turns = tmp_path_factory.mktemp("cos-turns")
    saved_env = os.environ.get("COS_TURNS_DIR")
    os.environ["COS_TURNS_DIR"] = str(turns)
    seen: list = []
    cos_srv, cos_url = _serve(_stand_in_cos(seen))
    saved = cfg.COS_BACKEND
    cfg.COS_BACKEND = cos_url
    portal.config["SESSION_COOKIE_SECURE"] = False
    cookie = portal.session_interface.get_signing_serializer(portal).dumps({"user": dict(OWNER)})
    portal_srv, portal_url = _serve(portal)
    # With the portal's write guard (#267) the page carries a token and every
    # write must too; without it (main before #267) there is none to check.
    guard = getattr(portal_module, "COS_CSRF_SESSION_KEY", None) is not None
    yield {"url": portal_url, "seen": seen, "turns": turns, "guard": guard,
           "cookie": {"name": portal.config.get("SESSION_COOKIE_NAME", "session"), "value": cookie,
                      "url": portal_url}}
    portal_srv.shutdown()
    cos_srv.shutdown()
    cfg.COS_BACKEND = saved
    if saved_env is None:
        os.environ.pop("COS_TURNS_DIR", None)
    else:
        os.environ["COS_TURNS_DIR"] = saved_env


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def confer(browser, stack):
    stack["seen"].clear()
    for path in stack["turns"].rglob("*"):
        if path.is_file():
            path.unlink()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    origin = stack["url"]
    ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin) else route.abort())
    ctx.route("**/adapters/openai-webrtc-adapter.js*",
              lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_ADAPTER))
    ctx.add_cookies([stack["cookie"]])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    sent = stack["sent"] = []                                             # what the browser sent the portal
    page.on("request", lambda r: sent.append({"path": r.url[len(origin):], "csrf": r.headers.get("x-csrf-token")})
            if r.method == "POST" else None)
    page.goto(f"{origin}/app/cos/ui/confer")
    expect(page.locator("#btn-voice")).to_be_enabled()
    yield page, stack, errors
    ctx.close()


def _t(page, key):
    return page.evaluate(f"(window.__voiceTest || {{}})[{key!r}]")


def _token(page):
    return page.evaluate("document.querySelector('meta[name=\"minimoi-csrf-token\"]')?.content || null")


def _guarded(page, stack, path, reached):
    """With the portal's write guard (#267) the browser sends the page's token,
    the guard lets the write through (it reached the stand-in), and the token
    stops at the portal. Without the guard there is no token."""
    token = _token(page)
    assert bool(token) is stack["guard"]
    [browser_sent] = [s for s in stack["sent"] if s["path"] == path]
    assert browser_sent["csrf"] == token
    assert reached["csrf"] is None                                        # never forwarded to CoS


def _messages(page):
    return page.evaluate("""() => [...document.querySelectorAll('#chat-log .msg')].map(m =>
        [m.classList.contains('msg-user') ? 'user' : 'cos', m.querySelector('.msg-text').textContent])""")


def _day_lines(turns):
    files = sorted(turns.glob("*/*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text().splitlines()]


def _conversation(page, stack):
    """Greeting -> the owner's turn -> function call -> ui/send -> the reply."""
    page.click("#btn-voice")
    page.wait_for_function("() => window.__voiceTest && window.__voiceTest.opening")
    assert "one short, natural line" in _t(page, "opening")              # CoS greets first
    expect(page.locator("#voice-status")).to_have_text("Listening")
    adapter = "window.__voiceTest.adapter"
    page.evaluate(f"{adapter}.say('a1', 'Hi Robert, what shall we work on?')")
    expect(page.locator("#chat-log .msg-cos .msg-text").last).to_have_text("Hi Robert, what shall we work on?")
    page.evaluate(f"{adapter}.hear('u1', 'What is on my list today?')")
    expect(page.locator("#chat-log .msg-user .msg-text").last).to_have_text("What is on my list today?")
    page.evaluate(f"{adapter}.call('c1', 'consult_cos_agent', {{ request: 'What is on my list today?' }})")
    page.wait_for_function("() => window.__voiceTest.results.length === 1")
    result = _t(page, "results")[0]
    assert result["callId"] == "c1"
    assert json.loads(result["output"])["reply"] == "Two items today: the voice fix and the review."
    sends = [s for s in stack["seen"] if s["path"] == "/ui/send"]
    assert len(sends) == 1
    send = sends[0]
    assert send["body"]["channel"] == "html_voice" and send["body"]["speech_output"] is False
    assert send["body"]["text"] == "What is on my list today?"
    assert send["auth_id"] == "1"                                         # identity from the portal
    _guarded(page, stack, "/app/cos/ui/send", send)
    page.evaluate(f"{adapter}.say('a2', 'Two items today: the voice fix and the review.')")
    expect(page.locator("#chat-log .msg-cos .msg-text").last).to_have_text(
        "Two items today: the voice fix and the review.")


def test_the_standard_flow_speaks_and_writes_and_saves_the_transcript(confer):
    page, stack, errors = confer
    expect(page.locator("#voice-reply-mode")).to_have_value("speak")      # the default: speak and write
    _conversation(page, stack)
    assert _t(page, "outputMuted") is False                               # spoken
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/confer-voice-speak.png", full_page=True)
    page.click("#btn-voice")                                              # Stop
    expect(page.locator("#voice-transcript")).to_have_text("Voice ended. Transcript saved to your CoS history.")
    assert _t(page, "micOn") is False
    posted = [s for s in stack["seen"] if s["path"] == "/ui/voice/transcript"]
    assert len(posted) == 1 and posted[0]["auth_id"] == "1"
    _guarded(page, stack, "/app/cos/ui/voice/transcript", posted[0])
    [record] = _day_lines(stack["turns"])
    assert record["channel"] == "html_voice" and record["reply_mode"] == "speak"
    assert [(t["speaker"], t["text"]) for t in record["turns"]] == [
        ("assistant", "Hi Robert, what shall we work on?"),
        ("user", "What is on my list today?"),
        ("assistant", "Two items today: the voice fix and the review."),
    ]
    assert _messages(page)[-3:] == [                                      # shown live, not added twice at the end
        ["cos", "Hi Robert, what shall we work on?"],
        ["user", "What is on my list today?"],
        ["cos", "Two items today: the voice fix and the review."],
    ]
    assert not errors, errors


def test_write_only_mutes_the_reply_and_still_writes_it(confer):
    page, stack, errors = confer
    expect(page.locator("#voice-reply-note")).to_be_hidden()
    page.select_option("#voice-reply-mode", "write")
    expect(page.locator("#voice-reply-note")).to_have_text(
        "Write only mutes playback; the provider still generates (and bills) the audio.")
    _conversation(page, stack)
    assert _t(page, "mutedAtConnect") is True                             # muted before any audio
    assert _t(page, "outputMuted") is True
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/confer-voice-write-only.png", full_page=True)
    page.select_option("#voice-reply-mode", "speak")                      # changes during a session too
    assert _t(page, "outputMuted") is False
    page.select_option("#voice-reply-mode", "write")
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_contain_text("Transcript saved")
    assert _day_lines(stack["turns"])[0]["reply_mode"] == "write"
    page.reload()
    expect(page.locator("#voice-reply-mode")).to_have_value("write")      # remembered on this device
    assert not errors, errors


def _mode(stack):
    path = stack["turns"] / "_mode.json"
    return json.loads(path.read_text())["owner"]["mode"] if path.exists() else None


def test_the_private_switch_is_sticky_and_covers_text_and_voice(confer):
    page, stack, errors = confer
    button = page.locator("#btn-private")
    expect(button).to_have_text("Private: off")
    expect(page.locator("#private-banner")).to_be_hidden()
    button.click()                                                        # through the portal (and its guard)
    expect(button).to_have_text("Private: on")
    expect(button).to_have_attribute("aria-pressed", "true")
    banner = page.locator("#private-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("not kept in your CoS history. The agent itself may still remember it.")
    assert _mode(stack) == "private"
    _guarded(page, stack, "/app/cos/ui/private-mode",
             [s for s in stack["seen"] if s["path"] == "/ui/private-mode"][0])
    page.reload()                                                         # sticky: the server keeps it
    expect(page.locator("#btn-private")).to_have_text("Private: on")
    expect(page.locator("#private-banner")).to_be_visible()
    stack["sent"].clear()
    stack["seen"].clear()
    # Voice: each reply marked, the voice status says Private, nothing kept.
    _conversation(page, stack)
    expect(page.locator("#voice-private")).to_be_visible()
    replies = page.locator("#chat-log .msg-cos")
    for i in range(replies.count()):
        expect(replies.nth(i).locator(".msg-private")).to_have_text("Private")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/confer-private-voice.png", full_page=True)
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_have_text("Voice ended. Private: not kept in your CoS history.")
    assert _day_lines(stack["turns"]) == []
    # Text: the reply is marked too.
    before = page.locator("#chat-log .msg-cos").count()
    page.fill("#send-input", "Plan the week")
    page.click("#btn-send")
    expect(page.locator("#chat-log .msg-cos")).to_have_count(before + 1)
    expect(page.locator("#chat-log .msg-cos").last.locator(".msg-private")).to_have_text("Private")
    # Off only by the switch.
    page.click("#btn-private")
    expect(page.locator("#btn-private")).to_have_text("Private: off")
    expect(page.locator("#private-banner")).to_be_hidden()
    assert _mode(stack) == "public"
    before = page.locator("#chat-log .msg-cos").count()
    page.fill("#send-input", "And tomorrow?")
    page.click("#btn-send")
    expect(page.locator("#chat-log .msg-cos")).to_have_count(before + 1)
    assert page.locator("#chat-log .msg-cos").last.locator(".msg-private").count() == 0
    assert not errors, errors


def test_a_switch_during_the_session_keeps_none_of_it(confer):
    page, stack, errors = confer
    _conversation(page, stack)                                            # started kept (Private off)
    page.click("#btn-private")                                            # Private on, mid-session
    expect(page.locator("#btn-private")).to_have_text("Private: on")
    expect(page.locator("#voice-private")).to_be_visible()
    page.click("#btn-private")                                            # and off again before Stop
    expect(page.locator("#btn-private")).to_have_text("Private: off")
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_have_text("Voice ended. Private: not kept in your CoS history.")
    assert _day_lines(stack["turns"]) == []
    # A change the page never saw (Telegram's /private later, or the file by
    # hand): the server compares the mode with the one the session started under.
    page.evaluate("document.getElementById('chat-log').replaceChildren()")
    stack["seen"].clear()
    stack["sent"].clear()
    _conversation(page, stack)
    from domains.cos import private_mode
    private_mode.set_private(stack["turns"], True)
    private_mode.set_private(stack["turns"], False)
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_have_text(
        "Voice ended. Private changed during this session: not kept in your CoS history.")
    assert _day_lines(stack["turns"]) == []
    assert not errors, errors


def test_an_unreadable_mode_reads_as_private(confer):
    page, stack, errors = confer
    (stack["turns"] / "_mode.json").write_text("{not json")
    page.reload()
    expect(page.locator("#btn-private")).to_have_text("Private: on")
    expect(page.locator("#private-banner")).to_be_visible()
    _conversation(page, stack)
    page.click("#btn-voice")
    expect(page.locator("#voice-transcript")).to_have_text("Voice ended. Private: not kept in your CoS history.")
    assert _day_lines(stack["turns"]) == []
    assert not errors, errors


def test_a_provider_error_is_visible_and_releases_the_microphone(confer):
    page, stack, errors = confer
    page.click("#btn-voice")
    page.wait_for_function("() => window.__voiceTest && window.__voiceTest.opening")
    page.evaluate("window.__voiceTest.adapter.say('a1', 'Hi Robert.')")
    page.evaluate("""window.__voiceTest.adapter._emit('recoverable_error',
        { reason: 'provider_error', code: 'server_error', detail: 'The server had an error' })""")
    expect(page.locator("#btn-voice")).to_have_text("🎤")
    assert _t(page, "micOn") is False
    expect(page.locator("#voice-transcript")).to_contain_text(
        "Voice error: The server had an error. Voice stopped; the microphone is off.")
    expect(page.locator("#voice-transcript")).to_contain_text("Transcript saved")   # what was said is kept
    [logged] = [s for s in stack["seen"] if s["path"] == "/api/realtime-voice/confer/outcome"]
    assert logged["body"] == {"outcome": "provider_error", "provider": "openai", "reason": "provider_error",
                              "code": "server_error", "type": None}          # code and type, never the message
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/confer-voice-provider-error.png", full_page=True)
    assert not errors, errors
