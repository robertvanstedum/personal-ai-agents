"""MC voice, cancellation (real Chrome). The REAL shared controller, the REAL transport adapter and the REAL page script run against a stand-in browser: a fake microphone
(tracks that record being stopped), a fake peer connection, and held server/provider answers that the test releases late. A stop at any point must leave no live microphone
and no open connection, and must never start one afterwards. No network, no real microphone, no paid call."""
import json

import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context, go, _errors  # noqa:F401

INIT = """
window.__mic = { tracks: [], gum: 0, hold: false, release: null, pcs: [], dc: null };
const gum = () => new Promise((resolve) => {
  window.__mic.gum += 1;
  const track = { stopped: false, stop() { this.stopped = true; } };
  window.__mic.tracks.push(track);
  const stream = { getAudioTracks: () => [track] };
  if (window.__mic.hold) window.__mic.release = () => resolve(stream); else resolve(stream);
});
if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = gum;
class FakePC {
  constructor() { this.closed = false; this.remote = false; window.__mic.pcs.push(this); }
  addTrack() {}
  addEventListener() {}
  createDataChannel() {
    const handlers = {};
    const dc = { readyState: 'connecting', addEventListener(n, f) { handlers[n] = f; }, send(m) { (window.__mic.sent ||= []).push(m); if (window.__mic.onSend) window.__mic.onSend(m); }, close() {}, fire(n, e) { if (n === 'open') dc.readyState = 'open'; if (handlers[n]) handlers[n](e); } };
    window.__mic.dc = dc; return dc;
  }
  async createOffer() { return { sdp: 'v=0' }; }
  async setLocalDescription() {}
  async setRemoteDescription() { this.remote = true; }
  close() { this.closed = true; }
}
window.RTCPeerConnection = FakePC;
class FakeAudioContext {
  createMediaStreamSource() { return {connect(){}}; }
  createAnalyser() { return {fftSize:1024,getFloatTimeDomainData(samples){samples.fill(window.__mic.energy || 0);}}; }
  close() {return Promise.resolve();}
}
window.AudioContext = FakeAudioContext;
"""

CAPS = {"ok": True, "providers": [{"provider": "openai", "label": "OpenAI"}], "default_provider": "openai"}
CRED = {"ok": True, "provider": "openai", "client_secret": "ek_stand_in", "warning_minutes": 13, "max_minutes": 15}


class Held:
    """A route whose answer the test releases late."""
    def __init__(self):
        self.pending, self.count = [], 0

    def __call__(self, route):
        self.count += 1
        self.pending.append(route)

    def release(self, **kw):
        while self.pending:
            self.pending.pop().fulfill(**kw)


def _setup(browser, server, monkeypatch, hold_caps=False, hold_boot=False, hold_calls=False, hold_gum=False):
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    ctx, page = _context(browser, server)
    errors = _errors(page)
    page.add_init_script(INIT + ("window.__mic.hold = true;" if hold_gum else ""))
    caps, boot, calls = Held(), Held(), Held()
    state = {"caps": 0}

    def capabilities(route):
        state["caps"] += 1
        if hold_caps and state["caps"] > 1:                    # the first call is the page's own availability check; the controller's is the one held
            caps(route)
        else:
            route.fulfill(json=CAPS)
    page.route("**/mc/voice/capabilities", capabilities)
    page.route("**/mc/voice/bootstrap", boot if hold_boot else (lambda route: route.fulfill(json=CRED)))
    page.route("https://api.openai.com/v1/realtime/calls", calls if hold_calls else (lambda route: route.fulfill(body="v=0", content_type="application/sdp")))
    go(page, server["url"] + "/guild-next/guild/build")
    expect(page.locator("[data-mc-voice-btn]")).to_be_visible()
    return ctx, page, errors, caps, boot, calls


def _mic(page):
    return page.evaluate("({gum: window.__mic.gum, stopped: window.__mic.tracks.map(t => t.stopped), pcs: window.__mic.pcs.map(p => ({closed: p.closed, remote: p.remote}))})")


def _quiet(page):
    """Nothing live: every microphone track that was ever opened is stopped, every connection closed."""
    m = _mic(page)
    assert all(m["stopped"]) and all(p["closed"] for p in m["pcs"]), m
    return m


def test_a_normal_session_connects_a_microphone_and_stop_releases_it(browser, server, floor, monkeypatch):
    ctx, page, errors, *_ = _setup(browser, server, monkeypatch)
    page.click("[data-mc-voice-btn]")
    page.wait_for_timeout(600)
    m = _mic(page)
    assert m["gum"] == 1 and m["stopped"] == [False] and m["pcs"] == [{"closed": False, "remote": True}]
    page.click("[data-mc-voice-btn]")
    _quiet(page)
    expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "false")
    ctx.close()


def test_stop_while_capabilities_is_pending_never_opens_a_microphone(browser, server, floor, monkeypatch):
    ctx, page, errors, caps, boot, calls = _setup(browser, server, monkeypatch, hold_caps=True)
    page.click("[data-mc-voice-btn]")
    for _ in range(40):
        if caps.count:
            break
        page.wait_for_timeout(50)
    assert caps.count == 1
    page.click("[data-mc-voice-btn]")                                  # Stop while the provider list is still out
    caps.release(json=CAPS)
    page.wait_for_timeout(600)
    assert _mic(page)["gum"] == 0 and not boot.count
    ctx.close()


def test_stop_while_the_session_request_is_pending_never_connects(browser, server, floor, monkeypatch):
    ctx, page, errors, caps, boot, calls = _setup(browser, server, monkeypatch, hold_boot=True)
    page.click("[data-mc-voice-btn]")
    for _ in range(40):
        if boot.count:
            break
        page.wait_for_timeout(50)
    assert boot.count == 1
    page.click("[data-mc-voice-btn]")
    boot.release(json=CRED)
    page.wait_for_timeout(600)
    assert _mic(page)["gum"] == 0 and _mic(page)["pcs"] == []
    ctx.close()


def test_stop_while_the_browser_is_asking_for_the_microphone_stops_it_the_moment_it_arrives(browser, server, floor, monkeypatch):
    ctx, page, errors, *_ = _setup(browser, server, monkeypatch, hold_gum=True)
    page.click("[data-mc-voice-btn]")
    for _ in range(40):
        if page.evaluate("window.__mic.gum"):
            break
        page.wait_for_timeout(50)
    page.click("[data-mc-voice-btn]")
    page.evaluate("window.__mic.release()")                           # the permission prompt is answered after Stop
    page.wait_for_timeout(500)
    m = _quiet(page)
    assert m["gum"] == 1 and m["stopped"] == [True] and m["pcs"] == []
    ctx.close()


def test_stop_while_the_provider_connection_is_pending_closes_everything(browser, server, floor, monkeypatch):
    ctx, page, errors, caps, boot, calls = _setup(browser, server, monkeypatch, hold_calls=True)
    page.click("[data-mc-voice-btn]")
    for _ in range(60):
        if calls.count:
            break
        page.wait_for_timeout(50)
    assert calls.count == 1
    page.click("[data-mc-voice-btn]")
    calls.release(body="v=0", content_type="application/sdp")
    page.wait_for_timeout(600)
    m = _quiet(page)
    assert m["gum"] == 1 and m["stopped"] == [True] and not any(p["remote"] for p in m["pcs"])      # the late answer was not applied
    ctx.close()


def test_going_private_during_a_pending_start_cancels_it_too(browser, server, floor, monkeypatch):
    ctx, page, errors, caps, boot, calls = _setup(browser, server, monkeypatch, hold_boot=True)
    page.click("[data-mc-voice-btn]")
    for _ in range(40):
        if boot.count:
            break
        page.wait_for_timeout(50)
    page.click("[data-mc-record]")                                    # Private
    boot.release(json=CRED)
    page.wait_for_timeout(600)
    assert _mic(page)["gum"] == 0
    expect(page.locator("[data-mc-voice-btn]")).to_have_attribute("aria-pressed", "false")
    ctx.close()


def test_a_late_transcript_after_stop_paints_nothing(browser, server, floor, monkeypatch):
    ctx, page, errors, *_ = _setup(browser, server, monkeypatch)
    page.click("[data-mc-voice-btn]")
    page.wait_for_timeout(600)
    delta = json.dumps({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i1", "delta": "hello there"})
    page.evaluate(f"window.__mic.dc.fire('message', {{data: {json.dumps(delta)}}})")
    expect(page.locator("[data-mc-voice-status]")).to_contain_text("Hearing: hello there")
    page.click("[data-mc-voice-btn]")
    expect(page.locator("[data-mc-voice-status]")).to_be_hidden()
    late = json.dumps({"type": "conversation.item.input_audio_transcription.delta", "item_id": "i2", "delta": "too late"})
    page.evaluate(f"window.__mic.dc.fire('message', {{data: {json.dumps(late)}}})")
    page.wait_for_timeout(300)
    expect(page.locator("[data-mc-voice-status]")).to_be_hidden()
    assert not errors, errors
    ctx.close()


# ── the shared controller itself, as the Chief of Staff uses it (its own turn URL), through Stop and a RESTART ──
SHARED = """
window.__audio = [];
window.Audio = class { constructor(u) { window.__audio.push(u); } play() { return Promise.resolve(); } pause() {} removeAttribute() {} load() {}
  addEventListener(n, f) { if (n === 'ended') setTimeout(f, 0); } };
window.__log = [];
window.__mk = async () => {
  const { ConferVoiceController } = await import('/guild-next/api/v1/mc/voice/static/realtime-confer-controller.js');
  const c = new ConferVoiceController({
    capabilitiesUrl: '/guild-next/api/v1/mc/voice/capabilities', bootstrapUrl: '/guild-next/api/v1/mc/voice/bootstrap', turnUrl: '/guild-next/api/v1/fake-turn',
    onStateChange: (s, i) => window.__log.push(['state', s]), onUserTurn: (t) => window.__log.push(['user', t]),
    onAssistantTurn: (t) => window.__log.push(['assistant', t]), onError: (e) => window.__log.push(['error', e && e.reason]),
  });
  window.__c = c;
  return c;
};
"""


def _shared(browser, server, monkeypatch, boot_plan):
    """boot_plan: per bootstrap call, 'ok' | 'hold' | 'fail'."""
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    ctx, page = _context(browser, server)
    page.add_init_script(INIT + SHARED)
    held_boot, held_turn = Held(), Held()
    calls = {"boot": 0}

    def boot(route):
        n = calls["boot"] = calls["boot"] + 1
        plan = boot_plan[min(n - 1, len(boot_plan) - 1)]
        if plan == "hold":
            held_boot(route)
        elif plan == "fail":
            route.fulfill(status=500, json={"ok": False, "error": "bootstrap down"})
        else:
            route.fulfill(json=CRED)
    page.route("**/mc/voice/capabilities", lambda route: route.fulfill(json=CAPS))
    page.route("**/mc/voice/bootstrap", boot)
    page.route("https://api.openai.com/v1/realtime/calls", lambda route: route.fulfill(body="v=0", content_type="application/sdp"))
    page.route("**/api/v1/fake-turn", held_turn)
    go(page, server["url"] + "/guild-next/guild/build")
    page.evaluate("window.__mk()")
    page.wait_for_timeout(200)
    return ctx, page, held_boot, held_turn, calls


def _log(page):
    return page.evaluate("window.__log")


def _say(page, text, n=1):
    delta = json.dumps({"type": "conversation.item.input_audio_transcription.completed", "item_id": f"item{n}", "transcript": text})
    page.evaluate(f"window.__mic.dc.fire('message', {{data: {json.dumps(delta)}}})")


def test_a_turn_still_out_when_voice_is_stopped_and_restarted_is_never_shown_or_spoken(browser, server, floor, monkeypatch):
    ctx, page, held_boot, held_turn, calls = _shared(browser, server, monkeypatch, ["ok"])
    page.evaluate("window.__c.start('openai')")
    page.wait_for_timeout(500)
    _say(page, "first session question", 1)
    for _ in range(40):
        if held_turn.count:
            break
        page.wait_for_timeout(50)
    assert held_turn.count == 1
    page.evaluate("window.__c.stop('user_ended')")
    page.evaluate("window.__c.start('openai')")                        # restart before the old answer arrives
    page.wait_for_timeout(500)
    held_turn.release(json={"reply": "an answer for the OLD session", "speech_url": "speech/old"})
    page.wait_for_timeout(500)
    log = _log(page)
    assert ["assistant", "an answer for the OLD session"] not in log and page.evaluate("window.__audio") == []
    assert page.evaluate("window.__c._active") is True and ["error", "turn_failed"] not in log
    ctx.close()


def test_a_transcript_queued_behind_a_turn_is_dropped_when_that_session_stops(browser, server, floor, monkeypatch):
    ctx, page, held_boot, held_turn, calls = _shared(browser, server, monkeypatch, ["ok"])
    page.evaluate("window.__c.start('openai')")
    page.wait_for_timeout(500)
    _say(page, "question one", 1)
    for _ in range(40):
        if held_turn.count:
            break
        page.wait_for_timeout(50)
    _say(page, "question two", 2)                                      # queued behind the first
    page.evaluate("window.__c.stop('user_ended')")
    page.evaluate("window.__c.start('openai')")
    page.wait_for_timeout(500)
    held_turn.release(json={"reply": "late", "speech_url": "speech/late"})
    page.wait_for_timeout(600)
    assert held_turn.count == 1                                        # the second never went out
    assert ["user", "question two"] not in _log(page)
    ctx.close()


def test_an_old_reconnect_that_fails_late_does_not_fail_the_restarted_session_and_the_new_one_gets_its_own_reconnect(browser, server, floor, monkeypatch):
    ctx, page, held_boot, held_turn, calls = _shared(browser, server, monkeypatch, ["ok", "hold", "ok", "ok"])
    page.evaluate("window.__c.start('openai')")
    page.wait_for_timeout(500)
    page.evaluate("window.__mic.dc.fire('close', {})")                 # the data channel drops: one reconnect is attempted (bootstrap call 2, held)
    for _ in range(40):
        if held_boot.count:
            break
        page.wait_for_timeout(50)
    assert held_boot.count == 1
    page.evaluate("window.__c.stop('user_ended')")
    page.evaluate("window.__c.start('openai')")                        # restart (bootstrap call 3)
    page.wait_for_timeout(500)
    held_boot.release(status=500, json={"ok": False, "error": "old reconnect failed"})        # the OLD reconnect fails now
    page.wait_for_timeout(500)
    assert page.evaluate("window.__c._active") is True and ["error", "reconnect_failed"] not in _log(page)
    page.evaluate("window.__mic.dc.fire('close', {})")                 # the new session may reconnect once itself
    page.wait_for_timeout(500)
    assert ["state", "reconnecting"] in _log(page)[_log(page).index(["state", "stopped"]):]
    ctx.close()
