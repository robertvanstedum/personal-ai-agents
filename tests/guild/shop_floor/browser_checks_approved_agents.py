"""Owner approval panel: real JS at desktop and phone widths; mocked status API."""
import json
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, _context, go


@pytest.mark.parametrize('width',[320,390,768,900,1440])
def test_approval_state_is_separate_from_connection(browser,server,width):
    ctx,page=_context(browser,server,width,900)
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    rows=[dict(agent='mc',label='Master Craftsman',state='active',availability='Offline',brief='owner brief'),
          dict(agent='codex',label='Codex',state='pending',availability='Offline',brief=''),
          dict(agent='claude-code',label='Claude Code',state='legacy',availability='Connected',brief='')]
    page.route('**/api/v1/rooms/agents',lambda route:route.fulfill(json={'agents':rows}))
    go(page,server['url']+'/guild-next/guild/rooms')
    panel=page.locator('[data-rm-agents]')
    # The details sidebar is an overlay on narrow devices.
    if not page.locator('[data-rm-about]').is_visible(): page.locator('[data-rm-panel-toggle]').click()
    page.locator('[data-rm-manage] > summary').click()
    panel.locator('summary').click()
    expect(panel.locator('li')).to_have_count(3)
    expect(panel).to_contain_text('Approved · Offline')
    expect(panel).to_contain_text('Setting up · Offline')
    expect(panel.get_by_role('button',name='Switch off legacy access')).to_have_count(1)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    if width in (390,1440): page.screenshot(path=f'/private/tmp/approved-agents-{width}.png',full_page=True)
    assert not errors,errors
    ctx.close()
