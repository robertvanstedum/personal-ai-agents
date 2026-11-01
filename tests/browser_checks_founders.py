"""Offline Chromium acceptance against real canonical history and a synthetic reply transport."""
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import re
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
    @app.get('/guild-next/guild/companion/cos')
    def companion():return '<main data-mc-embed>MC test conversation</main>'
    @app.get('/guild-next/guild/rooms')
    def guildhall():return '<h1>Guildhall</h1>'
    @app.get('/ui/files')
    def files():return jsonify(available=True,files=[{'path':'notes/plan.md','name':'plan.md','bytes':120,'modified':'2026-11-01T09:00:00'}])
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
        # Review 06: secondary controls live in ⋯; on a phone the tabs and Guildhall sit at the top of ☰'s list.
        def click_control(selector):
            if tab.locator(f'[data-conv-menu] {selector}').count() and not tab.locator(selector).is_visible():
                tab.locator('[data-conv-more] > summary').click()
            elif width < 640 and tab.locator(f'[data-mobile-menu] {selector}').count():
                tab.locator('.header-nav [data-list-open]').click()
            tab.locator(selector).click()
        tab.goto('https://founders.invalid/ui/confer')
        tab.wait_for_function('window.cosHistory?.ready === true')
        expect(tab.locator('body')).to_have_attribute('data-picture','faded')   # the default: the faint picture behind
        expect(tab.locator('.tab-panel-photo')).to_be_visible()
        expect(tab.locator('.talk-meta')).to_be_hidden()
        expect(tab.locator('#files-open')).to_be_visible()
        expect(tab.locator('[data-files-count]')).to_have_text('1')
        assert tab.locator('.tab-panel-photo').bounding_box()['y'] == pytest.approx(tab.locator('.tab-layout-split').bounding_box()['y'],abs=1)
        tab.evaluate("document.addEventListener('minimoi:companion-opening',e=>e.detail.waitUntil(Promise.reject(new Error('uncertain'))),{once:true})")
        click_control('#both-open')
        expect(tab.locator('#save-state')).to_contain_text('Voice has not confirmed')
        expect(tab.locator('#mc-both')).to_be_hidden()
        click_control('#both-open')
        expect(tab.locator('#mc-both')).to_be_visible()
        expect(tab.frame_locator('#mc-both').locator('[data-mc-embed]')).to_contain_text('MC test conversation')
        expect(tab.locator('#mc-page')).to_have_attribute('href','https://founders.invalid/guild-next/guild/build')
        click_control('#both-open')
        expect(tab.locator('#mc-both')).to_be_hidden()
        tab.locator('#send-input').fill('Keep this draft')
        tab.reload();tab.wait_for_function('window.cosHistory?.ready === true')
        expect(tab.locator('#send-input')).to_have_value('Keep this draft')
        tab.locator('#btn-send').click();expect(tab.locator('#chat-log')).to_contain_text('Synthetic reply, saved.')
        tab.reload();tab.wait_for_function('window.cosHistory?.ready === true')
        expect(tab.locator('#chat-log')).to_contain_text('Keep this draft')
        expect(tab.locator('#chat-log')).to_contain_text('Synthetic reply, saved.')
        click_control('#picture-mode');expect(tab.locator('body')).to_have_attribute('data-picture','hidden')
        tab.reload();tab.wait_for_function('document.body.dataset.picture === "hidden"')
        tab.evaluate("attached=[{file_id:'synthetic-file',name:'Draft attachment'}];renderTray();")
        click_control('[data-tab="records"]')
        expect(tab.locator('#tab-records')).to_be_visible()
        # New chat is one click from the Board too: the rail's ＋ on a wide screen, ✎ in the phone's bar.
        new_chat=tab.locator('.header-nav [data-new-conversation]' if width < 640 else '.cos-rail [data-new-conversation]')
        expect(new_chat).to_be_visible()
        new_chat.click();expect(tab.locator('#chat-log')).not_to_contain_text('Synthetic reply, saved.')
        expect(tab.locator('#tab-confer')).to_be_visible()
        expect(tab.locator('#send-input')).to_be_focused()
        expect(tab.locator('#cos-greeting')).to_be_visible()
        expect(tab.locator('#cos-greeting h1')).to_have_text(re.compile(r'^Good (morning|afternoon|evening), Robert\.$'))
        if width >= 900:
            expect(tab.locator('#conversation-new')).to_be_visible()
            expect(tab.locator('.conv-title')).to_have_text('New chat')
            expect(tab.locator('.cos-list-row[aria-current="true"]')).to_have_count(1)
            tab.locator('.cos-list-row[aria-current="false"]').first.click()
            expect(tab.locator('#chat-log')).to_contain_text('Synthetic reply, saved.')
            expect(tab.locator('#cos-greeting')).to_have_count(0)
            expect(tab.locator('.msg-cos .msg-label').first).to_have_text('Chief of Staff')
            tab.locator('[data-list-fold]').click();expect(tab.locator('.cos-list')).to_be_hidden();expect(tab.locator('.cos-rail')).to_be_visible()
            tab.locator('.cos-rail [data-list-open]').click();expect(tab.locator('.cos-list')).to_be_visible()
            tab.locator('#conversation-new').click();expect(tab.locator('#chat-log')).not_to_contain_text('Synthetic reply, saved.')
        click_control('#history-open');expect(tab.locator('#history-sheet')).to_be_visible()
        tab.locator('#history-new').click();expect(tab.locator('#history-sheet')).to_be_hidden()
        expect(tab.locator('#btn-voice')).to_be_disabled()
        click_control('#both-open')
        expect(tab.locator('#mc-both')).to_be_visible()
        click_control('#both-open')
        expect(tab.locator('#btn-voice')).to_be_disabled()
        assert tab.evaluate('attached.length') == 0
        tab.evaluate("window.cosHistory.requestId('Keep attachment',[{file_id:'pending-file',name:'Pending'}]);sendInput.value='Keep attachment';attached=[{file_id:'pending-file',name:'Pending'}];window.cosHistory.persistDraft();")
        tab.reload();tab.wait_for_function('window.cosHistory?.ready === true')
        assert tab.evaluate("attached[0].file_id") == 'pending-file'
        assert tab.evaluate("window.cosHistory.requestId('Keep attachment',attached) !== null")
        tab.evaluate("window.cosHistory.confirm(null,{type:'note_save',status:'saved',operation_id:'note-1'});")
        assert tab.evaluate("window.cosHistory.requestId('Next',[]) !== null")
        assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width <= 700:
            expect(tab.locator('.cos-mark-name')).to_have_text('Chief of Staff')
            expect(tab.locator('[data-conv-title-short]')).to_be_visible()
            assert tab.locator('.header-nav').bounding_box()['height'] == pytest.approx(52,abs=1)
            click_control('#files-open')
            expect(tab.locator('#files-sheet')).to_be_visible()
            box=tab.locator('#files-sheet').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1
            tab.locator('#files-sheet [data-close-sheet]').click()
            tab.set_viewport_size({'width':1440,'height':900})
            expect(tab.locator('.conv-head [data-conv-more] > summary')).to_be_visible()
            expect(tab.locator('.header-nav > .tab-nav [data-tab="records"]')).to_be_visible()
            tab.set_viewport_size({'width':width,'height':height})
        if width < 640: tab.locator('.header-nav [data-list-open]').click()
        tab.get_by_role('link',name='Guildhall ↗',exact=True).click()
        expect(tab.locator('h1')).to_have_text('Guildhall')
        tab.go_back();tab.wait_for_function('window.cosHistory?.ready === true')
        expect(tab.locator('#tab-confer')).to_be_visible()
        assert '/undefined' not in tab.url
        assert not errors,errors
        evidence=Path(os.environ.get('FOUNDERS_EVIDENCE','/Users/vanstedum/Projects/personal-ai-agents/_working/minimoi12-dev-20261031'))
        tab.screenshot(path=str(evidence/f'cos-{width}.png'))
        browser.close()
