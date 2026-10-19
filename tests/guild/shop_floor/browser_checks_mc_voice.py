"""MC voice, v1 (real Chrome, real portal and page scripts). The speech provider and the shared controller are STAND-INS served in place of the real module, so no
network, no microphone and no paid call; everything else is the real thing: mc_voice.js, the page's note path, the server, a scripted Master Craftsman."""
import json
import re
import threading

import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context, go, _errors  # noqa:F401

FAKE_CONTROLLER = """
export class ConferVoiceController {
  constructor(o) {
    this.o = o; this._active = false; this._turnChain = Promise.resolve(); this.played = []; this.headers = null;
    this._onStateChange = o.onStateChange || (() => {}); this._onUserTurn = o.onUserTurn || (() => {}); this._onError = o.onError || (() => {});
    this._requestHeaders = o.requestHeaders; window.__voice = this;
  }
  async start(provider) {
    this._active = true; this.provider = provider;
    this.headers = typeof this._requestHeaders === 'function' ? this._requestHeaders() : {};
    this._onStateChange('requesting'); this._onStateChange('listening');
  }
  stop(reason) { if (!this._active) return; this._active = false; this._onStateChange('stopped', { reason }); }
  async _play(url) { this.played.push(url); this._onStateChange('speaking'); this._onStateChange('listening'); }
  say(text) {
    const id = crypto.randomUUID();
    this._turnChain = this._turnChain.then(() => this._submitTurn(text, id)).catch((e) => this._onError({ reason: 'turn_failed', detail: e.message }));
    this.lastTurn = this._turnChain;     // not returned: page.evaluate would wait for the whole turn
  }
}
"""


def _mc(server, reply="Item 12 is in build.", gate=None, fail=False):
    import minimoi_portal.guild_ui.mc.openclaw as oc
    from minimoi_portal.guild_ui.mc import CachedHealth
    services = server["app"].extensions["guild_ui_next"]["services"]
    sent = []

    class Resp:
        def __init__(self, status, text, headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    def post(url, data=None, headers=None, timeout=None):
        body = json.loads(data)
        sent.append(body["messages"][-1]["content"])
        if gate is not None:
            gate.wait(10)
        if fail:
            return Resp(500, "boom")
        return Resp(200, json.dumps({"id": "c", "object": "chat.completion", "model": "openclaw/mc-chat",
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}),
                    {"X-MC-Correlation-Id": (headers or {}).get("X-MC-Correlation-Id")})
    saved = (services.mc, services.mc_health, services.mc_turns)
    backend = oc.OpenClawMasterCraftsman("http://mc-relay:8790/v1", "t" * 40, http_get=lambda *a, **k: Resp(200, "{}"), http_post=post)
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    return services, saved, sent


def _open(browser, server, monkeypatch, path="/guild-next/guild/build"):
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    ctx, page = _context(browser, server)
    errors = _errors(page)
    bodies = []
    page.on("request", lambda r: bodies.append((r.method, r.url, r.post_data)) if "/api/v1/" in r.url and r.method == "POST" else None)
    page.route("**/mc/voice/capabilities", lambda route: route.fulfill(json={"ok": True, "providers": [{"provider": "openai", "label": "OpenAI"}], "default_provider": "openai"}))
    page.route("**/mc/voice/static/realtime-confer-controller.js", lambda route: route.fulfill(body=FAKE_CONTROLLER, content_type="text/javascript"))
    go(page, server["url"] + path)
    return ctx, page, errors, bodies


def _start(page):
    page.click("[data-mc-voice-btn]")
    for _ in range(100):                                                       # the shared controller module loads on first use (the page's CSP forbids wait_for_function's eval)
        if page.evaluate("Boolean(window.__voice && window.__voice._active)"):
            return
        page.wait_for_timeout(50)
    raise AssertionError("the voice session never started")


def _posts(bodies, fragment):
    return [json.loads(b) for m, u, b in bodies if fragment in u and b]


def test_a_spoken_message_is_a_kept_note_answered_by_mc_and_only_his_kept_reply_is_read(browser, server, floor, monkeypatch):
    services, saved, sent = _mc(server)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        expect(page.locator("[data-mc-voice-btn]")).to_be_visible()
        page.fill("#mc-input", "my unsent typed draft")                       # a typed draft that voice must leave alone
        _start(page)
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("Listening")
        headers = page.evaluate("window.__voice.headers")
        assert headers["X-Record-Mode"] == "on_record" and headers["X-CSRF-Token"]
        page.evaluate("window.__voice.say('What is stuck on twelve?')")
        expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(2, timeout=10000)      # the spoken note and MC's kept reply
        expect(page.locator("[data-mc-thread]")).to_contain_text("What is stuck on twelve?")
        expect(page.locator("[data-mc-thread]")).to_contain_text("Item 12 is in build.")
        played = page.evaluate("window.__voice.played")
        assert len(played) == 1
        note = _posts(bodies, "/notes")[0]
        assert re.fullmatch(r".*/mc/voice/speech/" + re.escape(note["request_id"]) + r"\?c=.+", played[0])    # the kept note it answers, nothing else
        turn = _posts(bodies, "/mc/turns")[0]
        assert turn["note_request_id"] == note["request_id"] and "attachment_ids" not in turn and "topic_context" not in turn
        assert len(_posts(bodies, "/notes")) == 1 and len(_posts(bodies, "/mc/turns")) == 1
        assert sent == [sent[0]] and "What is stuck on twelve?" in sent[0]
        assert page.input_value("#mc-input") == "my unsent typed draft"          # the draft is untouched
        assert not errors, errors
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_no_voice_button_when_the_provider_does_not_answer(browser, server, floor, monkeypatch):
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    ctx, page = _context(browser, server)
    page.route("**/mc/voice/capabilities", lambda route: route.fulfill(status=503, json={"ok": False, "error": "unavailable"}))
    go(page, server["url"] + "/guild-next/guild/build")
    expect(page.locator("[data-mc-voice-btn]")).to_be_hidden()
    ctx.close()


def _go_private(page):
    """Private sits in the conversation options menu (4a7e4cfc). Open it the way the owner does, then press the lock.
    The lock and everything it guards are unchanged; only the route to it is the real one."""
    if not page.locator("[data-mc-options]").evaluate("el => el.open"):
        page.click("[data-mc-options] summary")
    page.click("[data-mc-record]")


def test_going_private_ends_the_session_and_voice_cannot_start_in_private(browser, server, floor, monkeypatch):
    services, saved, sent = _mc(server)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        _start(page)
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "true")
        _go_private(page)                                                      # go Private
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("not built for Private yet")
        assert page.evaluate("window.__voice._active") is False
        page.click("[data-mc-voice-btn]")                                      # asking again in Private says so and starts nothing
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("not built for Private yet")
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "false")
        assert not _posts(bodies, "/notes") and not sent
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_a_failed_mc_turn_is_not_spoken_and_says_so(browser, server, floor, monkeypatch):
    services, saved, sent = _mc(server, fail=True)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        _start(page)
        page.evaluate("window.__voice.say('Are you there?')")
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("did not answer", timeout=10000)
        assert page.evaluate("window.__voice.played") == []
        expect(page.locator("[data-mc-blocker]")).to_be_visible()             # the failure is visible in the panel, not only announced
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "true")      # the session itself carries on
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_stopping_while_mc_is_working_means_the_late_answer_is_never_spoken(browser, server, floor, monkeypatch):
    gate = threading.Event()
    services, saved, sent = _mc(server, gate=gate)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        _start(page)
        page.evaluate("window.__voice.say('Slow question')")
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("Working", timeout=10000)
        page.click("[data-mc-voice-btn]")                                      # Stop voice
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "false")
        gate.set()                                                             # MC answers late
        expect(page.locator("[data-mc-thread]")).to_contain_text("Item 12 is in build.", timeout=10000)   # the answer is kept and shown as text
        page.wait_for_timeout(500)
        assert page.evaluate("window.__voice.played") == []                     # but it is not read aloud into a closed session
        ctx.close()
    finally:
        gate.set()
        services.mc, services.mc_health, services.mc_turns = saved


def test_utterances_are_answered_one_at_a_time_in_order_and_typing_is_held_meanwhile(browser, server, floor, monkeypatch):
    gate = threading.Event()
    services, saved, sent = _mc(server, gate=gate)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        _start(page)
        page.fill("#mc-input", "typed while MC works")
        page.evaluate("window.__voice.say('First question')")
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("Working", timeout=10000)
        page.evaluate("window.__voice.say('Second question')")                  # queued behind the first in the controller
        assert page.evaluate("document.querySelector('[data-mc-send]').disabled") is True        # the Send button is held while the spoken turn runs
        page.press("#mc-input", "Enter")                                          # and so is a typed send by keyboard: nothing extra is sent
        page.wait_for_timeout(300)
        assert len(_posts(bodies, "/notes")) == 1 and len(_posts(bodies, "/mc/turns")) == 1
        assert page.input_value("#mc-input") == "typed while MC works"           # the typed draft is kept, not sent, not cleared
        gate.set()
        expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(4, timeout=15000)   # two spoken notes, two MC replies
        notes = _posts(bodies, "/notes")
        assert [n["text"] for n in notes] == ["First question", "Second question"] and len({n["request_id"] for n in notes}) == 2
        assert len(_posts(bodies, "/mc/turns")) == 2
        assert [("First question" in m, "Second question" in m) for m in sent] == [(True, False), (False, True)]   # in order, none duplicated
        played = page.evaluate("window.__voice.played")
        assert [u.split("/speech/")[1].split("?")[0] for u in played] == [n["request_id"] for n in notes]
        ctx.close()
    finally:
        gate.set()
        services.mc, services.mc_health, services.mc_turns = saved


def test_stopping_discards_an_utterance_that_was_still_queued(browser, server, floor, monkeypatch):
    gate = threading.Event()
    services, saved, sent = _mc(server, gate=gate)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch)
        _start(page)
        page.evaluate("window.__voice.say('First question')")
        expect(page.locator("[data-mc-voice-status]")).to_contain_text("Working", timeout=10000)
        page.evaluate("window.__voice.say('Queued question')")
        page.click("[data-mc-voice-btn]")                                          # Stop voice
        gate.set()
        expect(page.locator("[data-mc-thread]")).to_contain_text("Item 12 is in build.", timeout=10000)   # the first, already in flight, finishes as text
        page.wait_for_timeout(600)
        assert [n["text"] for n in _posts(bodies, "/notes")] == ["First question"]                  # the queued one was never kept or sent
        assert len(_posts(bodies, "/mc/turns")) == 1 and page.evaluate("window.__voice.played") == []
        ctx.close()
    finally:
        gate.set()
        services.mc, services.mc_health, services.mc_turns = saved


def test_minimizing_the_chat_ends_the_session(browser, server, floor, monkeypatch):
    services, saved, sent = _mc(server)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch, "/guild-next/guild/board")
        page.click("[data-mc-pill]")
        expect(page.locator("[data-mc-voice-btn]")).to_be_visible()
        _start(page)
        expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "true")
        page.click("[data-mc-min]")
        assert page.evaluate("window.__voice._active") is False
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_a_typed_send_closes_the_history_picker(browser, server, floor, monkeypatch):
    services, saved, sent = _mc(server)
    try:
        ctx, page, errors, bodies = _open(browser, server, monkeypatch, "/guild-next/guild/board")
        page.click("[data-mc-pill]")
        page.locator("[data-conv-picker] summary").click()
        expect(page.locator("[data-conv-picker]")).to_have_attribute("open", "")
        page.fill("#mc-input", "hello from the keyboard")
        page.click("[data-mc-send]")
        expect(page.locator("[data-conv-picker]")).not_to_have_attribute("open", "")
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_real_shared_speech_plays_a_blob_under_the_actual_guild_csp(browser,server,floor,monkeypatch):
    import io,wave
    services,saved,sent=_mc(server)
    ctx=None
    try:
        ctx,page,errors,bodies=_open(browser,server,monkeypatch)
        module="""
import {ConferVoiceController as Real} from '/guild-next/api/v1/mc/voice/static/realtime-confer-controller.js?real=1';
export class ConferVoiceController extends Real {
  constructor(o){super({...o,onStateChange:(s,i)=>{(window.__playStates ||= []).push(s);o.onStateChange(s,i);}});window.__voice=this;}
  async start(){this._active=true;++this._startToken;this._onStateChange('listening');}
  say(text){this._turnChain=this._submitTurn(text,crypto.randomUUID());}
}
"""
        def shared(route):
            if 'real=1' in route.request.url:route.continue_()
            else:route.fulfill(body=module,content_type='text/javascript')
        page.route('**/mc/voice/static/realtime-confer-controller.js*',shared)
        wav=io.BytesIO()
        with wave.open(wav,'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*1280)
        speech=[]
        def audio(route):
            speech.append(route.request.url)
            route.fulfill(body=wav.getvalue(),content_type='audio/wav')
        page.route('**/mc/voice/speech/**',audio)
        page.reload();_start(page)
        page.evaluate("window.__voice.say('A synthetic playback check')")
        for _ in range(100):
            ready=page.evaluate("(window.__playStates||[]).includes('speaking') && window.__playStates.at(-1)==='listening'")
            if ready:break
            page.wait_for_timeout(50)
        assert ready,page.evaluate("({states:window.__playStates,status:document.querySelector('[data-mc-voice-status]').textContent})")
        expect(page.locator('[data-mc-thread]')).to_contain_text('Item 12 is in build.')
        assert len(speech)==1 and not errors
        assert page.evaluate('window.__voice._audio===null && window.__voice._audioUrl===null')
    finally:
        if ctx:ctx.close()
        services.mc,services.mc_health,services.mc_turns=saved
