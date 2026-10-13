"""Real CoS template/shared controller, synthetic microphone/provider/backend.

No credentials, real microphone, paid call or production state. These checks
exercise browser commits, stop/restart with an unresolved turn and actual
speech refusal parsing (one request, positive but insufficient allowance).
"""
import json
import threading
from pathlib import Path

import pytest
from flask import Flask,render_template,send_from_directory
from playwright.sync_api import expect
from werkzeug.serving import make_server

from browser_checks_b1 import browser  # noqa:F401
from browser_checks_mc_voice_cancel import INIT

ROOT=Path(__file__).resolve().parents[3]
CAPS={'ok':True,'providers':[{'provider':'openai'}],'default_provider':'openai','budget':{'remaining_usd':.10}}

@pytest.fixture(scope='module')
def cos_server():
    app=Flask(__name__,template_folder=str(ROOT/'domains/cos/templates'),static_folder=str(ROOT/'domains/cos/static'))
    app.add_url_rule('/ui/confer','page',lambda:render_template('cos_ui.html',initial_tab='confer'))
    app.add_url_rule('/static/realtime-voice/<path:filename>','voice_static',lambda filename:send_from_directory(ROOT/'core/realtime_voice/static',filename))
    srv=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    yield 'http://127.0.0.1:'+str(srv.server_port)
    srv.shutdown();thread.join(3)


def opened(browser,url):
    ctx=browser.new_context(viewport={'width':390,'height':740})
    page=ctx.new_page();page.add_init_script(INIT)
    page.route('**/api/realtime-voice/confer/capabilities',lambda r:r.fulfill(json=CAPS))
    page.route('**/api/realtime-voice/confer/bootstrap',lambda r:r.fulfill(json={'ok':True,'provider':'openai','client_secret':'synthetic','warning_minutes':13,'max_minutes':15}))
    page.route('https://api.openai.com/v1/realtime/calls',lambda r:r.fulfill(body='v=0',content_type='application/sdp'))
    page.goto(url+'/ui/confer')
    expect(page.locator('#btn-voice')).to_be_enabled()
    return ctx,page


def start(page):
    page.locator('#btn-voice').click()
    expect(page.locator('#btn-voice')).to_have_attribute('aria-pressed','true')
    page.wait_for_function('window.__mic && window.__mic.dc && window.__mic.pcs.at(-1).remote')
    page.evaluate("window.__mic.dc.fire('open')")
    expect(page.locator('#voice-status')).to_have_text('Listening')


def utter(page,text,item):
    page.evaluate("([text,item])=>window.__mic.dc.fire('message',{data:JSON.stringify({type:'conversation.item.input_audio_transcription.completed',item_id:item,transcript:text})})",[text,item])


def test_coS_restart_does_not_wait_on_the_abandoned_turn(browser,cos_server):
    ctx,page=opened(browser,cos_server)
    pending=[];texts=[]
    def send(route):
        text=route.request.post_data_json['text'];texts.append(text)
        if text=='old': pending.append(route)
        else: route.fulfill(json={'reply':'new canonical reply'})
    page.route('**/ui/send',send)
    try:
        start(page);utter(page,'old','one')
        expect(page.locator('#voice-status')).to_have_text('Working on it…')
        page.wait_for_function("document.querySelector('#chat-log').textContent.includes('old')")
        page.locator('#btn-voice').click();start(page)
        utter(page,'new','two')
        expect(page.locator('#chat-log')).to_contain_text('new canonical reply',timeout=5000)
        assert texts==['old','new']
        pending.pop().fulfill(json={'reply':'stale reply must stay suppressed'})
        page.wait_for_timeout(100)
        expect(page.locator('#chat-log')).not_to_contain_text('stale reply')
    finally:ctx.close()


def test_real_controller_displays_insufficient_speech_allowance_without_retry(browser,cos_server):
    ctx,page=opened(browser,cos_server);speech=[]
    page.route('**/ui/send',lambda r:r.fulfill(json={'reply':'the saved canonical answer','speech_url':'speech/fixture'}))
    def refused(route):
        speech.append(route.request.url)
        route.fulfill(status=429,json={'error':'Today\'s voice allowance cannot cover this reading ($0.10 remaining; $0.20 requested).'})
    page.route('**/ui/speech/fixture',refused)
    try:
        start(page);utter(page,'hello','one')
        expect(page.locator('#voice-transcript')).to_contain_text('$0.10 remaining; $0.20 requested')
        expect(page.locator('#chat-log')).to_contain_text('the saved canonical answer')
        expect(page.locator('#voice-transcript')).to_contain_text('The answer is in the chat.')
        assert len(speech)==1
        page.locator('#send-input').fill('typing still works')
        expect(page.locator('#send-input')).to_have_value('typing still works')
    finally:ctx.close()


def test_browser_vad_commits_after_a_pause_and_stop_releases_the_mic(browser,cos_server):
    ctx,page=opened(browser,cos_server)
    try:
        start(page)
        page.evaluate('window.__mic.energy=.05')
        page.wait_for_timeout(350)
        page.evaluate('window.__mic.energy=0')
        page.wait_for_function("(window.__mic.sent||[]).some(s=>JSON.parse(s).type==='input_audio_buffer.commit')")
        page.locator('#btn-voice').click()
        assert page.evaluate('window.__mic.tracks.every(t=>t.stopped) && window.__mic.pcs.every(p=>p.closed)')
    finally:ctx.close()
