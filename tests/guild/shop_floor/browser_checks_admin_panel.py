"""Real shared page/CSS/JS; local disposable existing B1 fixture, no model/audio.
Set MINIMOI_PANEL_CSS_CANDIDATE to a private proposed components.css for review.
No production/dev server, real login, microphone or provider is used.
"""
import os
from pathlib import Path
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, _context, go  # noqa:F401


def _open(browser, server, width, height):
    ctx, page = _context(browser, server, width=width, height=height)
    candidate = os.environ.get('MINIMOI_PANEL_CSS_CANDIDATE')
    if candidate:
        css = Path(candidate).read_text()
        page.route('**/components.css*', lambda route: route.fulfill(body=css, content_type='text/css'))
    # Expose the actual markup, without requesting microphone/provider capability.
    go(page, server['url'] + '/guild-next/guild/admin')
    return ctx, page


def _controls_fit(page):
    # Real template control text; no generated voice/controller, inference or audio.
    page.evaluate('''() => {
      document.querySelector('[data-mc-voice-btn]').hidden = false;
      document.querySelector('[data-mc-managed-settings]').hidden = false;
      document.querySelector('[data-dictate="#mc-input"]').hidden = false;
    }''')
    result = page.locator('[data-mc-panel]').evaluate('''panel => {
      const bounds = panel.getBoundingClientRect();
      const selectors = ['[data-mc-voice-btn]', '[data-mc-voice-provider]', '[data-mc-voice-transcript-choice]', '[data-dictate="#mc-input"]', '[data-mc-record]'];
      return {overflow: panel.scrollWidth-panel.clientWidth, children: selectors.map(s => {
        const element=panel.querySelector(s), r=element.getBoundingClientRect();
        const label=element.closest('label');const q=(label||element).getBoundingClientRect();
        return {s, fits:r.width>0 && q.left>=bounds.left && q.right<=bounds.right && r.left>=bounds.left && r.right<=bounds.right};
      })};
    }''')
    assert result['overflow'] <= 1, result
    assert all(row['fits'] for row in result['children']), result


@pytest.mark.parametrize('width,height', [(320,780),(390,780),(768,800),(900,800),(1440,900),(320,420),(390,420)])
def test_admin_panel_stays_collapsed_until_open_and_controls_fit(browser, server, fresh_queue, monkeypatch, width, height):
    monkeypatch.setenv('MINIMOI_MC_VOICE', '1')
    monkeypatch.setenv('MINIMOI_DICTATION', '1')
    ctx, page = _open(browser, server, width, height)
    try:
        panel=page.locator('[data-mc-panel]');pill=page.locator('[data-mc-pill]')
        expect(panel).to_have_attribute('data-mode','pill');expect(panel).to_be_hidden();expect(pill).to_be_visible()
        pill.click();expect(panel).to_be_visible();expect(pill).to_be_hidden()
        if width<=640:
            assert panel.evaluate('p=>getComputedStyle(p).position')=='fixed'
            r=panel.bounding_box();assert r['x']>=-1 and r['x']+r['width']<=width+1 and r['y']>=-1 and r['y']+r['height']<=height+1
        expect(page.locator('[data-mc-min]')).to_be_visible()
        page.locator('[data-mc-input]').fill('synthetic unsent draft')
        _controls_fit(page)
        if os.environ.get('MINIMOI_PANEL_SCREENSHOTS'):
            page.screenshot(path=str(Path(os.environ['MINIMOI_PANEL_SCREENSHOTS'])/f'admin-open-{width}x{height}.png'))
        page.locator('[data-mc-min]').click();expect(panel).to_be_hidden();expect(pill).to_be_visible()
        pill.click();expect(panel).to_be_visible();expect(pill).to_be_hidden();expect(page.locator('[data-mc-input]')).to_have_value('synthetic unsent draft')
    finally:
        ctx.close()
