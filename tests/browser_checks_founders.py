"""Offline Chromium acceptance against real canonical history and a synthetic reply transport."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import pytest
from flask import Flask, render_template, request, jsonify
from playwright.sync_api import sync_playwright, expect
from domains.cos.history import History, turn_id_for
from domains.cos.history_api import create_blueprint
from core.founders_ui import install
ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('width,height', [(1440,900),(390,844)])
def test_history_draft_picture_and_reply_survive_reload(tmp_path,width,height):
    app=Flask(__name__,template_folder=str(ROOT/'domains/cos/templates'),static_folder=str(ROOT/'domains/cos/static'))
    history=History(tmp_path/'history','dev'); install(app)
    app.register_blueprint(create_blueprint(history,resolve_owner=lambda request: 1))
    @app.get('/ui/confer')
    def page():return render_template('cos_ui.html',initial_tab='confer',managed_realtime_ready=False)
    @app.get('/ui/files')
    def files():return jsonify(available=True,files=[])
    @app.post('/ui/send')
    def send():
        b=request.json
        turn,replayed=history.begin({'schema':'minimoi.cos.send/1','conversation_id':b['conversation_id'],
          'turn_id':turn_id_for(b['request_id']),'text':b['text'],'channel':'web'})
        if not replayed:turn=history.answer(turn['conversation_id'],turn['turn_id'],'Synthetic reply, saved.')
        return jsonify(reply=turn['reply_text'],history={'saved':True,'conversation_id':turn['conversation_id']})
    client=app.test_client()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        context=browser.new_context(viewport={'width':width,'height':height},service_workers='block')
        tab=context.new_page();errors=[];tab.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            u=urlsplit(r.request.url)
            response=client.open(u.path+('?' +u.query if u.query else ''),method=r.request.method,data=r.request.post_data,content_type=r.request.headers.get('content-type'))
            r.fulfill(status=response.status_code,body=response.data,headers={'content-type':response.content_type})
        tab.route('**/*',route)
        tab.goto('https://founders.invalid/ui/confer')
        tab.wait_for_function('window.cosHistory?.ready === true')
        tab.locator('#send-input').fill('Keep this draft')
        tab.reload();tab.wait_for_function('window.cosHistory?.ready === true')
        expect(tab.locator('#send-input')).to_have_value('Keep this draft')
        tab.locator('#btn-send').click();expect(tab.locator('#chat-log')).to_contain_text('Synthetic reply, saved.')
        tab.reload();tab.wait_for_function('window.cosHistory?.ready === true')
        expect(tab.locator('#chat-log')).to_contain_text('Keep this draft')
        expect(tab.locator('#chat-log')).to_contain_text('Synthetic reply, saved.')
        tab.locator('#picture-mode').click();expect(tab.locator('body')).to_have_attribute('data-picture','faded')
        tab.reload();tab.wait_for_function('document.body.dataset.picture === "faded"')
        tab.evaluate("attached=[{file_id:'synthetic-file',name:'Draft attachment'}];renderTray();")
        tab.locator('#history-open').click();expect(tab.locator('#history-sheet')).to_be_visible()
        tab.locator('#history-new').click();expect(tab.locator('#chat-log')).not_to_contain_text('Synthetic reply, saved.')
        expect(tab.locator('#btn-voice')).to_be_disabled()
        assert tab.evaluate('attached.length') == 0
        tab.evaluate("window.cosHistory.requestId('Keep attachment',[{file_id:'pending-file',name:'Pending'}]);sendInput.value='Keep attachment';attached=[{file_id:'pending-file',name:'Pending'}];window.cosHistory.persistDraft();")
        tab.reload();tab.wait_for_function('window.cosHistory?.ready === true')
        assert tab.evaluate("attached[0].file_id") == 'pending-file'
        assert tab.evaluate("window.cosHistory.requestId('Keep attachment',attached) !== null")
        tab.evaluate("window.cosHistory.confirm(null,{type:'note_save',status:'saved',operation_id:'note-1'});")
        assert tab.evaluate("window.cosHistory.requestId('Next',[]) !== null")
        assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors,errors
        evidence=Path('/Users/vanstedum/Projects/personal-ai-agents/_working/minimoi12-dev-20261031')
        tab.screenshot(path=str(evidence/f'cos-{width}.png'))
        browser.close()
