"""Actual companion shell and embedded chat, synthetic local data, no providers."""
from pathlib import Path
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context

@pytest.mark.parametrize('width,height', [(320,780),(390,780),(768,800),(900,800),(1440,900),(390,420)])
def test_companion_open_history_draft_minimize(browser, server, floor, monkeypatch, width, height):
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE','1')
    ctx,page=_context(browser,server,width,height)
    try:
        page.goto(server['url']+'/dashboard')
        launch=page.locator('.companion-launch')
        expect(launch).to_be_visible()
        assert page.locator('#minimoi-companion iframe').get_attribute('src') is None
        launch.click()
        frame=page.frame_locator('#minimoi-companion iframe')
        expect(frame.locator('body')).to_have_attribute('data-ready','true')
        expect(frame.locator('[data-mc-input]')).to_be_visible()
        frame.locator('[data-mc-input]').fill('Synthetic unsent draft')
        frame.locator('[data-mc-input]').click(trial=True)
        frame.locator('[data-mc-send]').click(trial=True)
        assert page.locator('#companion-dialog').evaluate('(e)=>e.scrollWidth-e.clientWidth')<=1
        assert frame.locator('body').evaluate('(e)=>e.scrollWidth-e.clientWidth')<=1
        # Stop event is observed inside the real child document; no microphone acquired.
        frame.locator('body').evaluate("()=>{window.stopClaim=false;document.addEventListener('guild:mic-claim',e=>{window.stopClaim=e.detail.who==='companion-closed'})}")
        page.locator('.companion-close').click()
        expect(launch).to_be_focused()
        launch.click()
        expect(frame.locator('[data-mc-input]')).to_have_value('Synthetic unsent draft')
        assert frame.locator('body').evaluate('()=>window.stopClaim')
        frame.locator('[data-history-toggle]').click()
        expect(frame.locator('[data-floor-history]')).to_be_visible()
        frame.locator('[data-history-close]').click()
        output=Path('/private/tmp/minimoi-mc-everywhere-review-20261017');output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'companion-{width}-{height}.png'))
        if width==1440:
            (output/'raw-chat.html').write_text(frame.locator('html').evaluate('(e)=>e.outerHTML'))
            (output/'raw-shell.html').write_text(page.locator('#minimoi-companion').evaluate('(e)=>e.outerHTML'))
    finally:ctx.close()

def test_history_switch_and_failure_retry(browser,server,floor,monkeypatch):
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE','1')
    ctx,page=_context(browser,server,900,800)
    try:
        page.goto(server['url']+'/dashboard')
        page.route('**/guild/companion/dashboard',lambda route:route.fulfill(status=503,body='Unavailable'))
        page.locator('.companion-launch').click()
        expect(page.locator('.companion-fallback')).to_be_visible()
        page.unroute('**/guild/companion/dashboard')
        page.locator('.companion-retry').click()
        frame=page.frame_locator('#minimoi-companion iframe')
        expect(frame.locator('body')).to_have_attribute('data-ready','true')
        frame.locator('[data-history-toggle]').click()
        frame.locator('[data-conv-new]').click()
        expect(frame.locator('body')).to_have_attribute('data-ready','true')
        expect(frame.locator('body')).to_have_attribute('data-mc-embed','true')
        expect(frame.locator('body')).to_have_attribute('data-area','Home')
        expect(frame.locator('#portal-nav-bar')).to_have_count(0)
        assert '/guild/companion/dashboard' in page.locator('iframe').evaluate('(e)=>e.contentWindow.location.pathname')
        # A failed native voice shutdown must not silently open a second chat.
        page.locator('.companion-close').click()
        page.evaluate("document.addEventListener('minimoi:companion-opening',e=>e.detail.waitUntil(Promise.reject(new Error('synthetic unconfirmed stop'))),{once:true})")
        page.locator('.companion-launch').click()
        expect(page.locator('.companion-open-error')).to_be_visible()
        expect(page.locator('#companion-dialog')).not_to_be_visible()
    finally:ctx.close()

def test_native_voice_handover_waits_and_times_out(browser,server,floor,monkeypatch):
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE','1')
    ctx,page=_context(browser,server,900,800)
    try:
        page.goto(server['url']+'/dashboard')
        page.evaluate("document.addEventListener('minimoi:companion-opening',e=>e.detail.waitUntil(new Promise(resolve=>window.finishVoice=resolve)),{once:true})")
        page.locator('.companion-launch').click()
        expect(page.locator('.companion-launch')).to_be_disabled()
        expect(page.locator('#companion-dialog')).not_to_be_visible()
        page.evaluate('window.finishVoice()')
        expect(page.locator('#companion-dialog')).to_be_visible()
        page.locator('.companion-close').click()
        page.clock.install()
        page.evaluate("document.addEventListener('minimoi:companion-opening',e=>e.detail.waitUntil(new Promise(()=>{})),{once:true})")
        page.locator('.companion-launch').click()
        page.clock.run_for(8100)
        expect(page.locator('.companion-open-error')).to_be_visible()
        expect(page.locator('.companion-launch')).to_be_enabled()
        expect(page.locator('#companion-dialog')).not_to_be_visible()
    finally:ctx.close()

def test_phone_rotation_preserves_draft_and_reading_position(browser,server,floor,monkeypatch):
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE','1')
    ctx,page=_context(browser,server,390,844)
    try:
        page.goto(server['url']+'/dashboard')
        # Synthetic long reading surface, using the actual injected companion.
        page.locator('main').first.evaluate("e=>e.style.minHeight='2400px'")
        page.evaluate('window.scrollTo(0,600)')
        before=page.evaluate('window.scrollY')
        page.locator('.companion-launch').click()
        frame=page.frame_locator('#minimoi-companion iframe')
        expect(frame.locator('body')).to_have_attribute('data-ready','true')
        frame.locator('[data-mc-input]').fill('Keep this through rotation')
        for width,height in [(844,390),(740,360),(390,844)]:
            page.set_viewport_size({'width':width,'height':height})
            frame.locator('[data-mc-input]').click(trial=True)
            frame.locator('[data-mc-send]').click(trial=True)
            page.locator('.companion-close').click(trial=True)
            expect(frame.locator('[data-mc-input]')).to_have_value('Keep this through rotation')
            assert frame.locator('body').evaluate('(e)=>e.scrollWidth-e.clientWidth')<=1
        page.locator('.companion-close').click()
        assert abs(page.evaluate('window.scrollY')-before)<=1
        page.locator('.companion-launch').click()
        expect(frame.locator('[data-mc-input]')).to_have_value('Keep this through rotation')
    finally:ctx.close()
