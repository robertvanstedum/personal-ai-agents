"""Current real Guild DOM/JS; fake mic/provider, synthetic local stores only."""
from pathlib import Path
from playwright.sync_api import expect
from browser_checks_b1 import browser,server,fresh_queue,floor  # noqa:F401
from browser_checks_mc_voice_cancel import _setup,_quiet


def test_voice_controls_responsive_and_stop_cleanup(browser,server,floor,monkeypatch):
    context,page,errors,*_= _setup(browser,server,monkeypatch)
    destination=Path('/private/tmp/minimoi-mc-repair-live-dom-20261016');destination.mkdir(exist_ok=True)
    button=page.locator('[data-mc-voice-btn]')
    expect(button.locator('[data-voice-icon="mic"]')).to_be_visible()
    expect(button).to_have_attribute('aria-label','Start voice conversation')
    for width in (320,390,768,900,1440):
        page.set_viewport_size({'width':width,'height':844})
        page.wait_for_timeout(100)
        size=button.bounding_box();assert size and size['width']>=44 and size['height']>=44
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'overflow at {width}'
        page.screenshot(path=str(destination/f'guild-{width}.png'),full_page=True)
    page.click('[data-mc-voice-btn]')
    page.wait_for_timeout(600)
    expect(button.locator('[data-voice-icon="stop"]')).to_be_visible()
    page.click('[data-mc-voice-btn]')
    _quiet(page)
    expect(button).to_have_attribute('aria-pressed','false')
    assert not errors,errors
    context.close()
