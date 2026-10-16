"""Shared contextual composer, real local B1 pages/scripts and synthetic capabilities.
No provider/microphone/model calls or live owner data. Optional private CSS route
substitution and screenshot output are only for independent candidate review.
"""
import os
from pathlib import Path
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context, go  # noqa:F401

# Contextual layouts under review, including the Board popout.
PAGES = [('admin','/guild/admin'),('board','/guild/board'),('board_popout','/guild/board/popout'),
         ('rooms','/guild/rooms'),('operate','/guild/operate'),('operate_agents','/guild/operate/agents'),
         ('improve','/guild/improve'),('experiment','/guild/experiment'),('build_log','/guild/build/log')]
SIZES = [(320,780),(390,780),(640,780),(641,780),(768,800),(900,800),(1440,900),(320,420),(390,420)]

@pytest.fixture
def flags(server, monkeypatch):
    # This is the mount's admitted feature mapping, read on EVERY page request.
    # Set before navigation; do not rely on process-environment fixture timing.
    config=server['app'].extensions['guild_ui_next']
    original=dict(config['feature_environ'])
    def set_flags(on):
        monkeypatch.setitem(config,'feature_environ',{**original,'MINIMOI_MC_VOICE':'1' if on else '0','MINIMOI_DICTATION':'1' if on else '0'})
    set_flags(True)
    return set_flags

def opened(browser,server,width,height,path,capabilities=True):
    ctx,page=_context(browser,server,width=width,height=height)
    candidate=os.environ.get('MINIMOI_PANEL_CSS_CANDIDATE')
    if candidate:
        css=Path(candidate).read_text()
        page.route('**/components.css*',lambda route:route.fulfill(body=css,content_type='text/css'))
    calls=[];writes=[]
    def capability(route):
        calls.append(route.request.url)
        route.fulfill(json={'ok':True,'providers':[{'provider':'openai','label':'OpenAI'}] if capabilities else [],
                            'managed_realtime':{'session_url':'/no-provider-session'}})
    # No test sends a note, uploads a file, edits a Board row, or starts voice.
    def api(route):
        if route.request.method not in ('GET','HEAD','OPTIONS'):
            writes.append(route.request.url);route.abort()
        else:route.continue_()
    page.route('**/api/v1/**',api)
    page.route('**/app/records/api/**',api)
    # Playwright route precedence is last-registered first: capability responses
    # must win over the read-only request guard.
    page.route('**/mc/voice/capabilities',capability)
    page.route('**/dictation/capabilities',capability)
    go(page,server['url']+'/guild-next'+path)
    return ctx,page,calls,writes

def check_controls(page,*,voice=True,attachment=False):
    panel=page.locator('[data-mc-panel]')
    selectors=['[data-mc-input]','[data-mc-send]','[data-mc-record]']
    # Confirm is covered only when actual client state exposes it. Never force it.
    if panel.locator('[data-mc-record-confirm]').is_visible():selectors+=['[data-mc-record-confirm]']
    if attachment:selectors+=['[data-mc-attach]']
    if voice:selectors+=['[data-mc-voice-btn]','[data-mc-voice-provider]','[data-mc-voice-transcript-choice]','[data-dictate="#mc-input"]']
    for selector in selectors:
        expect(panel.locator(selector)).to_be_visible()
        # Trial checks browser hit-testing/scroll reachability without dispatching
        # Send, Dictate, Voice, file selection, or changing provider/settings.
        if selector!='[data-mc-record]' or not panel.locator(selector).is_disabled():
            panel.locator(selector).click(trial=True,timeout=3000)
    result=panel.evaluate('''(panel,selectors)=>{
      const b=panel.getBoundingClientRect();return {overflow:panel.scrollWidth-panel.clientWidth,
       controls:selectors.map(s=>{const e=panel.querySelector(s),r=e.getBoundingClientRect(),label=e.closest('label'),q=(label||e).getBoundingClientRect();
        return {s,width:r.width,fits:r.width>0&&r.left>=b.left-1&&r.right<=b.right+1&&q.left>=b.left-1&&q.right<=b.right+1};})};}''',selectors)
    assert result['overflow']<=1,result
    assert all(r['fits'] for r in result['controls']),result
    # A textarea may shrink, but must retain useful typing space beside Send.
    assert panel.locator('[data-mc-input]').bounding_box()['width']>=80
    return selectors

@pytest.mark.parametrize('name,path',PAGES)
@pytest.mark.parametrize('width,height',SIZES)
def test_contextual_panel_modes_controls_focus_and_draft(browser,server,floor,flags,name,path,width,height):
    ctx,page,calls,writes=opened(browser,server,width,height,path)
    try:
        expect(page.locator('body')).to_have_attribute('data-page',name)
        expect(page.locator('body')).to_have_attribute('data-mc-voice','true')
        expect(page.locator('body')).to_have_attribute('data-dictation','true')
        panel=page.locator('[data-mc-panel]');pill=page.locator('[data-mc-pill]')
        # Preserve the legacy inline-phone Rooms/Build Log behavior. Other
        # contextual pages already use the reviewed launcher/bottom sheet.
        inline_phone=width<=640 and name in ('rooms','build_log')
        rooms=name=='rooms'
        if inline_phone or rooms:
            expect(pill).to_be_hidden()
            if inline_phone:expect(panel).to_be_visible()
            else:expect(panel).to_be_hidden()
            # Exercise actual script mode toggles from URL, not DOM mutation.
            go(page,server['url']+'/guild-next'+path+'?chat=floating')
        else:
            expect(panel).to_be_hidden();expect(pill).to_be_visible();pill.click()
        expect(panel).to_be_visible();expect(pill).to_be_hidden()
        expect(page.locator('[data-mc-voice-btn]')).to_be_visible()
        expect(page.locator('[data-dictate="#mc-input"]')).to_be_visible()
        expect(page.locator('[data-mc-managed-settings]')).to_be_visible()
        if width<=640 and not inline_phone:
            assert panel.evaluate('p=>getComputedStyle(p).position')=='fixed'
        expect(page.locator('[data-mc-attach]')).to_have_count(0)
        selectors=check_controls(page)
        box=page.locator('[data-mc-input]');box.fill('synthetic unsent draft');box.focus();expect(box).to_be_focused()
        page.keyboard.press('Tab');expect(page.locator('[data-mc-send]')).to_be_focused()
        # Minimize is an existing contextual action, reachable even at short height.
        # Legacy phone inline pages intentionally hide this chrome.
        if not inline_phone and not rooms:
            minimum=page.locator('[data-mc-min]');expect(minimum).to_be_visible();minimum.click()
            expect(panel).to_be_hidden();expect(pill).to_be_visible();expect(pill).to_be_focused()
            pill.click();expect(panel).to_be_visible();expect(pill).to_be_hidden();expect(box).to_be_focused();expect(box).to_have_value('synthetic unsent draft')
        # Exercise floating as well as default docked mode on desktop via actual UI.
        dock=page.locator('[data-mc-dock]')
        if width>=641 and dock.is_visible():
            dock.click();check_controls(page);expect(box).to_have_value('synthetic unsent draft')
        assert calls and not writes,writes
        capture=os.environ.get('MINIMOI_PANEL_SCREENSHOTS')
        if capture and width in (320,390,641,1440):
            page.screenshot(path=str(Path(capture)/f'{name}-{width}x{height}.png'))
    finally:ctx.close()

@pytest.mark.parametrize('on,capabilities',[(False,True),(True,False)])
def test_flag_off_and_unavailable_capability_do_not_expose_voice_controls(browser,server,floor,flags,on,capabilities):
    flags(on);ctx,page,calls,writes=opened(browser,server,390,780,'/guild/admin',capabilities)
    try:
        page.locator('[data-mc-pill]').click()
        if not on:
            expect(page.locator('body')).not_to_have_attribute('data-mc-voice','true')
            expect(page.locator('[data-mc-voice-btn]')).to_have_count(0)
            expect(page.locator('[data-dictate="#mc-input"]')).to_have_count(0)
            expect(page.locator('[data-mc-managed-settings]')).to_have_count(0)
            assert not calls
        else:
            expect(page.locator('[data-mc-voice-btn]')).to_be_hidden()
            expect(page.locator('[data-dictate="#mc-input"]')).to_be_hidden()
            expect(page.locator('[data-mc-managed-settings]')).to_be_hidden()
        check_controls(page,voice=False);box=page.locator('[data-mc-input]');box.fill('typing still available');expect(box).to_have_value('typing still available');assert not writes
    finally:ctx.close()

@pytest.mark.parametrize('width,height',[(320,780),(390,780),(640,780),(641,780),(1440,900),(390,420)])
def test_full_conversation_keeps_existing_attachment_and_typing(browser,server,floor,flags,width,height):
    ctx,page,calls,writes=opened(browser,server,width,height,'/guild/build')
    try:
        expect(page.locator('body')).to_have_attribute('data-page','floor')
        expect(page.locator('[data-mc-panel]')).to_have_attribute('data-mode','inpage')
        expect(page.locator('[data-mc-voice-btn]')).to_be_visible()
        check_controls(page,attachment=True)
        box=page.locator('[data-mc-input]');box.fill('synthetic unsent attachment review draft')
        box.focus();page.keyboard.press('Tab');expect(page.locator('[data-mc-send]')).to_be_focused()
        expect(box).to_have_value('synthetic unsent attachment review draft')
        assert calls and not writes
    finally:ctx.close()

@pytest.mark.parametrize('private_allowed',[False,True])
@pytest.mark.parametrize('width',[320,390,1440,1441])
@pytest.mark.parametrize('path',['/guild/admin','/guild/board','/guild/operate'])
def test_secondary_controls_real_state_and_wide_boundary(browser,server,floor,flags,monkeypatch,width,path,private_allowed):
    from types import SimpleNamespace
    # Supply backend metadata through the actual server render boundary. No DOM
    # disabled/hidden overrides and no backend request implementation/call.
    services=server['app'].extensions['guild_ui_next']['services']
    monkeypatch.setattr(services,'mc',SimpleNamespace(supports_private=private_allowed))
    ctx,page,calls,writes=opened(browser,server,width,780,path)
    try:
        page.locator('[data-mc-pill]').click()
        expect(page.locator('[data-mc-voice-btn]')).to_be_visible()
        check_controls(page)
        if private_allowed:expect(page.locator('[data-mc-record]')).to_be_enabled()
        else:expect(page.locator('[data-mc-record]')).to_be_disabled()
        # Confirm stays hidden in the actual known-record state; do not force it.
        expect(page.locator('[data-mc-record-confirm]')).to_be_hidden()
        assert calls and not writes
    finally:ctx.close()
