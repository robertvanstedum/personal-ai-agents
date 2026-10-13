"""Dictation (insertion-only), real Chrome. The REAL shared memo controller, the REAL transport adapter and the REAL page scripts run against a stand-in browser (fake microphone
tracks, fake peer connection, held server/provider answers). No network, no real microphone, no provider, no paid call."""
import json

import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, rooms, _context, go, _errors, _records_fetch  # noqa:F401
from browser_checks_mc_voice_cancel import INIT, Held, CAPS, CRED  # noqa:F401

FINAL = "(() => { window.__final = ''; window.__mic.onSend = (m) => { if (JSON.parse(m).type === 'input_audio_buffer.commit') setTimeout(() => window.__mic.dc.fire('message', {data: JSON.stringify({type: 'conversation.item.input_audio_transcription.completed', item_id: 'i1', event_id: 'ev-final', transcript: window.__final})}), 20); }; })()"


def _setup(browser, server, monkeypatch, path="/guild-next/guild/build", hold_boot=False, hold_gum=False, hold_calls=False, voice=False):
    monkeypatch.setenv("MINIMOI_DICTATION", "1")
    if voice:
        monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    ctx, page = _context(browser, server)
    errors = _errors(page)
    page.add_init_script(INIT + ("window.__mic.hold = true;" if hold_gum else ""))
    boot, calls = Held(), Held()
    posts = []
    page.on("request", lambda r: posts.append((r.method, r.url)) if r.method == "POST" and "/api/v1/" in r.url else None)
    page.route("**/dictation/capabilities", lambda route: route.fulfill(json={"ok": True, "mode": "memo", "providers": [{"provider": "openai", "label": "OpenAI"}], "default_provider": "openai"}))
    page.route("**/mc/voice/capabilities", lambda route: route.fulfill(json=CAPS))
    page.route("**/dictation/bootstrap", boot if hold_boot else (lambda route: route.fulfill(json=CRED)))
    page.route("https://api.openai.com/v1/realtime/calls", calls if hold_calls else (lambda route: route.fulfill(body="v=0", content_type="application/sdp")))
    go(page, server["url"] + path)
    return ctx, page, errors, boot, calls, posts


def _connected(page):
    for _ in range(100):
        if page.evaluate("Boolean(window.__mic.dc)"):
            break
        page.wait_for_timeout(50)
    page.evaluate("window.__mic.dc.fire('open', {})")                          # the provider data channel opens: the session is listening
    page.evaluate(FINAL)


def _heard(page, text, n=1, final=False):
    kind = "completed" if final else "delta"
    body = {"type": f"conversation.item.input_audio_transcription.{kind}", "item_id": f"i{n}", "event_id": f"ev-{kind}-{n}-{len(text)}"}
    body["transcript" if final else "delta"] = text
    page.evaluate(f"window.__mic.dc.fire('message', {{data: {json.dumps(json.dumps(body))}}})")


def _caret(page, start, end=None):
    page.evaluate(f"(() => {{ const b = document.querySelector('#mc-input'); b.focus(); b.setSelectionRange({start}, {end if end is not None else start}); }})()")


def _no_sends(posts):
    assert not [p for p in posts if "/notes" in p[1] or "/mc/turns" in p[1] or "/rooms" in p[1] and p[0] == "POST" and "dictation" not in p[1]]


def test_dictation_inserts_at_the_cursor_and_finishing_commits_the_rest_without_sending_anything(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.fill("#mc-input", "Hello world")
    _caret(page, 5)
    expect(page.locator("[data-dictate='#mc-input']")).to_be_visible()
    page.click("[data-dictate='#mc-input']")
    _connected(page)
    expect(page.locator("[data-dictation-status]")).to_contain_text("Listening")
    _heard(page, "brave new")
    expect(page.locator("#mc-input")).to_have_value("Hello brave new world")
    assert page.evaluate("document.querySelector('#mc-input').readOnly") is True       # not editable under the words being written
    page.evaluate("window.__final = 'brave new place'")
    page.click("[data-dictate='#mc-input']")                                            # finish: the shared controller commits, the final text replaces the partial
    expect(page.locator("#mc-input")).to_have_value("Hello brave new place world", timeout=10000)
    expect(page.locator("[data-dictate='#mc-input']")).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("document.querySelector('#mc-input').readOnly") is False
    assert page.evaluate("document.querySelector('#mc-input').selectionStart") == len("Hello brave new place")      # the caret sits after the words
    assert any(json.loads(m)["type"] == "input_audio_buffer.commit" for m in page.evaluate("window.__mic.sent"))
    assert page.evaluate("window.__mic.tracks.every(t => t.stopped)") is True
    _no_sends(posts)
    assert not [p for p in posts if "/notes" in p[1] or "/mc/turns" in p[1]] and not errors, errors
    ctx.close()


def test_a_selection_is_replaced_and_the_normal_draft_keeping_sees_the_dictated_words(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.fill("#mc-input", "alpha BETA gamma")
    _caret(page, 6, 10)
    page.click("[data-dictate='#mc-input']")
    _connected(page)
    page.evaluate("window.__final = 'delta'")
    _heard(page, "delta")
    expect(page.locator("#mc-input")).to_have_value("alpha delta gamma")
    page.click("[data-dictate='#mc-input']")
    expect(page.locator("[data-dictate='#mc-input']")).to_have_attribute("aria-pressed", "false", timeout=10000)
    page.reload()
    page.wait_for_selector("body[data-ready=true]")
    expect(page.locator("#mc-input")).to_have_value("alpha delta gamma")             # the existing on-record draft keeping saw an ordinary edit
    assert not [p for p in posts if "/notes" in p[1] or "/mc/turns" in p[1]]
    ctx.close()


def test_dictation_does_not_start_in_private_and_going_private_stops_it_leaving_the_text(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.click("[data-mc-record]")                                                       # Private
    page.click("[data-dictate='#mc-input']")
    expect(page.locator("[data-dictation-status]")).to_contain_text("not built for Private yet")
    assert page.evaluate("window.__mic.gum") == 0 and boot.count == 0
    page.click("[data-mc-record]")                                                       # back on the record
    page.fill("#mc-input", "keep me")
    _caret(page, 7)
    page.click("[data-dictate='#mc-input']")
    _connected(page)
    _heard(page, "more")
    expect(page.locator("#mc-input")).to_have_value("keep me more")
    page.click("[data-mc-record]")                                                       # Private begins mid-session
    expect(page.locator("[data-dictate='#mc-input']")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("[data-dictation-status]")).to_contain_text("not built for Private yet")
    assert page.evaluate("window.__mic.tracks.every(t => t.stopped)") is True
    ctx.close()


def test_stop_at_each_pending_step_leaves_no_live_microphone_and_a_restart_works(browser, server, floor, monkeypatch):
    # bootstrap held, stopped, then a second start: the late answer of the first start must not connect anything
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, hold_boot=True)
    page.click("[data-dictate='#mc-input']")
    for _ in range(40):
        if boot.count:
            break
        page.wait_for_timeout(50)
    page.click("[data-dictate='#mc-input']")                                            # stop during the session request
    boot.release(json=CRED)
    page.wait_for_timeout(600)
    assert page.evaluate("window.__mic.gum") == 0 and page.evaluate("window.__mic.pcs.length") == 0
    ctx.close()
    # microphone prompt held, stopped, answered late
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, hold_gum=True)
    page.click("[data-dictate='#mc-input']")
    for _ in range(40):
        if page.evaluate("window.__mic.gum"):
            break
        page.wait_for_timeout(50)
    page.click("[data-dictate='#mc-input']")
    page.evaluate("window.__mic.release()")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__mic.tracks.every(t => t.stopped)") is True and page.evaluate("window.__mic.pcs.length") == 0
    ctx.close()
    # provider connection held, stopped
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, hold_calls=True)
    page.click("[data-dictate='#mc-input']")
    for _ in range(60):
        if calls.count:
            break
        page.wait_for_timeout(50)
    page.click("[data-dictate='#mc-input']")
    calls.release(body="v=0", content_type="application/sdp")
    page.wait_for_timeout(600)
    m = page.evaluate("({stopped: window.__mic.tracks.map(t => t.stopped), pcs: window.__mic.pcs.map(p => ({closed: p.closed, remote: p.remote}))})")
    assert all(m["stopped"]) and all(p["closed"] and not p["remote"] for p in m["pcs"]), m
    ctx.close()


def test_a_start_that_was_stopped_and_restarted_is_not_taken_over_by_the_old_answer(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, hold_boot=True)
    page.click("[data-dictate='#mc-input']")
    for _ in range(40):
        if boot.count:
            break
        page.wait_for_timeout(50)
    page.click("[data-dictate='#mc-input']")                                            # stop the first attempt
    page.click("[data-dictate='#mc-input']")                                            # start a second (its session request is also held)
    for _ in range(40):
        if boot.count == 2:
            break
        page.wait_for_timeout(50)
    first, second = boot.pending[0], boot.pending[1]
    boot.pending.clear()
    first.fulfill(json=CRED)                                                            # the OLD answer arrives first
    page.wait_for_timeout(500)
    assert page.evaluate("window.__mic.gum") == 0                                       # it connected nothing
    second.fulfill(json=CRED)
    _connected(page)
    assert page.evaluate("window.__mic.gum") == 1                                       # only the live attempt opened a microphone
    page.click("[data-dictate='#mc-input']")
    page.wait_for_timeout(300)
    ctx.close()


def test_voice_and_dictation_never_hold_the_microphone_together(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, voice=True)
    page.route("**/mc/voice/bootstrap", lambda route: route.fulfill(json={"ok": True, "provider": "openai", "client_secret": "ek", "warning_minutes": 13, "max_minutes": 15}))
    expect(page.locator("[data-mc-voice-btn]")).to_be_visible()
    page.click("[data-dictate='#mc-input']")
    _connected(page)
    page.click("[data-mc-voice-btn]")                                                   # starting Voice ends dictation
    expect(page.locator("[data-dictate='#mc-input']")).to_have_attribute("aria-pressed", "false")
    page.click("[data-mc-voice-btn]")                                                   # stop Voice
    ctx.close()


def test_rooms_gets_a_real_dictation_button_that_fills_the_message_box_and_never_sends(browser, server, rooms, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, path="/guild-next/guild/rooms")
    base = server["url"] + "/guild-next/guild/rooms"
    made = _records_fetch(page, "/v1/rooms", {"title": "Dictation room", "purpose": "Synthetic dictation check", "mode": "meeting", "recording_acknowledged": True}, "dictation-room-0001")
    assert made["status"] == 201
    go(page, base + "?room=" + made["body"]["result"]["id"])
    expect(page.locator("[data-rm-mic]")).to_be_visible()
    expect(page.locator("[data-rm-mic]")).not_to_be_disabled()
    page.fill("#rm-input", "Agenda: ")
    page.evaluate("(() => { const b = document.querySelector('#rm-input'); b.focus(); b.setSelectionRange(b.value.length, b.value.length); })()")
    page.click("[data-rm-mic]")
    _connected(page)
    _heard(page, "review the plan")
    expect(page.locator("#rm-input")).to_have_value("Agenda: review the plan")
    page.evaluate("window.__final = 'review the plan'")
    page.click("[data-rm-mic]")
    expect(page.locator("[data-rm-mic]")).to_have_attribute("aria-pressed", "false", timeout=10000)
    expect(page.locator("#rm-input")).to_have_value("Agenda: review the plan")
    assert not [p for p in posts if "/messages" in p[1] or "/v1/rooms" in p[1] and "dictation" not in p[1] and p[1].endswith("/messages")]
    ctx.close()


# ── Codex's source review of the wrapper (12 Oct): empty finish, atomic read-only, paused rooms, manual send, cancel during finalizing ──
def test_a_finish_with_nothing_said_leaves_the_draft_and_the_selection_exactly_as_they_were(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.fill("#mc-input", "alpha BETA gamma")
    _caret(page, 6, 10)
    page.click("[data-dictate='#mc-input']")
    _connected(page)
    page.click("[data-dictate='#mc-input']")                                            # finish; the provider heard nothing (final transcript is empty)
    expect(page.locator("[data-dictate='#mc-input']")).to_have_attribute("aria-pressed", "false", timeout=10000)
    expect(page.locator("#mc-input")).to_have_value("alpha BETA gamma")                 # BETA was not deleted
    assert page.evaluate("[document.querySelector('#mc-input').selectionStart, document.querySelector('#mc-input').selectionEnd]") == [6, 10]
    ctx.close()


def test_the_box_cannot_be_typed_in_from_the_moment_dictation_starts_and_is_restored_on_every_exit(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, hold_boot=True)
    page.fill("#mc-input", "draft")
    _caret(page, 5)
    page.click("[data-dictate='#mc-input']")                                            # the session request is still held
    for _ in range(40):
        if boot.count:
            break
        page.wait_for_timeout(50)
    assert page.evaluate("document.querySelector('#mc-input').readOnly") is True        # atomic with the snapshot: typing now cannot be overwritten later
    page.click("[data-dictate='#mc-input']")                                            # cancel while held
    assert page.evaluate("document.querySelector('#mc-input').readOnly") is False
    boot.release(json=CRED)
    page.wait_for_timeout(500)
    expect(page.locator("#mc-input")).to_have_value("draft")
    ctx.close()
    # a failed start restores it too
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.route("**/dictation/bootstrap", lambda route: route.fulfill(status=502, json={"ok": False, "error": "OpenAI API key not configured"}))
    page.fill("#mc-input", "draft")
    page.click("[data-dictate='#mc-input']")
    expect(page.locator("[data-dictation-status]")).to_contain_text("could not start", timeout=10000)
    assert page.evaluate("document.querySelector('#mc-input').readOnly") is False
    expect(page.locator("#mc-input")).to_have_value("draft")
    ctx.close()


def _room(page, server, title):
    base = server["url"] + "/guild-next/guild/rooms"
    made = _records_fetch(page, "/v1/rooms", {"title": title, "purpose": "Synthetic dictation check", "mode": "meeting", "recording_acknowledged": True}, f"{title.lower().replace(' ', '-')}-0001")
    assert made["status"] == 201
    go(page, base + "?room=" + made["body"]["result"]["id"])


def _pause(page):
    page.click("[data-rm-details] > summary")
    page.fill("[data-rm-state-note]", "Lunch break")
    page.click("[data-rm-set-state='paused']")
    expect(page.locator("[data-rm-input]")).to_have_attribute("data-room-state", "paused", timeout=10000)


def test_a_paused_room_refuses_dictation_and_pausing_mid_dictation_stops_it(browser, server, rooms, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch, path="/guild-next/guild/rooms")
    _room(page, server, "Pause check")
    expect(page.locator("[data-rm-mic]")).to_be_visible()
    page.fill("#rm-input", "Agenda: ")
    page.click("[data-rm-mic]")
    _connected(page)
    _heard(page, "first item")
    expect(page.locator("#rm-input")).to_have_value("Agenda: first item")
    _pause(page)                                                                         # pause mid-dictation
    expect(page.locator("[data-rm-mic]")).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("window.__mic.tracks.every(t => t.stopped)") is True
    gum = page.evaluate("window.__mic.gum")
    page.click("[data-rm-mic]")                                                          # already paused: refused, nothing starts
    expect(page.locator(".rm-status[data-dictation-status]")).to_contain_text("paused or closed")
    assert page.evaluate("window.__mic.gum") == gum
    ctx.close()


def test_a_manual_send_ends_dictation_first_and_a_late_final_cannot_bring_the_text_back(browser, server, floor, monkeypatch):
    from browser_checks_mc_voice import _mc
    services, saved, sent = _mc(server)
    try:
        ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
        page.click("[data-dictate='#mc-input']")
        _connected(page)
        _heard(page, "what is stuck on twelve")
        expect(page.locator("#mc-input")).to_have_value("what is stuck on twelve")
        page.press("#mc-input", "Enter")                                                 # the owner sends by hand while dictation is running
        expect(page.locator('[data-mc-thread] [data-kind="note"]')).to_have_count(2, timeout=10000)
        assert page.evaluate("window.__mic.tracks.every(t => t.stopped)") is True
        expect(page.locator("[data-dictate='#mc-input']")).to_have_attribute("aria-pressed", "false")
        expect(page.locator("#mc-input")).to_have_value("")
        _heard(page, "what is stuck on twelve and more", final=True)                      # a late final from the closed session
        page.wait_for_timeout(300)
        expect(page.locator("#mc-input")).to_have_value("")                               # it did not resurrect the sent draft
        expect(page.locator("[data-mc-thread]")).to_contain_text("what is stuck on twelve")      # the text written so far is what was sent
        ctx.close()
    finally:
        services.mc, services.mc_health, services.mc_turns = saved


def test_cancelling_while_the_last_words_are_awaited_emits_one_stop_and_no_final_text(browser, server, floor, monkeypatch):
    ctx, page, errors, boot, calls, posts = _setup(browser, server, monkeypatch)
    page.evaluate("""(() => { window.__log = []; window.__mk = async () => {
      const { RealtimeMemoController } = await import('/guild-next/api/v1/dictation/static/realtime-memo-controller.js');
      window.__c = new RealtimeMemoController({ bootstrapUrl: '/guild-next/api/v1/dictation/bootstrap', provider: 'openai',
        onStateChange: (s) => window.__log.push(['state', s]), onTranscript: (t, i) => window.__log.push(['text', t, !!i.completed]), onError: () => window.__log.push(['error']) });
    }; })()""")
    page.evaluate("window.__mk()")
    page.wait_for_timeout(300)
    page.evaluate("void window.__c.start()")
    _connected(page)
    _heard(page, "partial words")
    page.evaluate("void window.__c.stop()")                                              # the owner finishes: the commit is out, the last words are awaited
    page.wait_for_timeout(200)
    page.evaluate("window.__c.cancel('private')")                                        # then the microphone must close at once
    page.wait_for_timeout(400)
    log = page.evaluate("window.__log")
    assert [e for e in log if e[0] == "state"].count(["state", "stopped"]) == 1, log
    assert ["text", "partial words", True] not in log                                    # stop()'s tail did not emit a final after the cancel
    assert page.evaluate("window.__mic.tracks.every(t => t.stopped)") is True
    ctx.close()
