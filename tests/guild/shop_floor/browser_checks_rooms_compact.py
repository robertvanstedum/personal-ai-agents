"""Short room list and optional explanations; no lifecycle changes or live services."""
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import (browser, server, fresh_queue, floor, rooms, _context, go,
                               _records_fetch, _no_page_overflow)  # noqa: F401


@pytest.mark.parametrize('width', [320, 390, 768, 900, 1440])
def test_room_list_is_ten_at_a_time_and_keeps_selected_room_visible(browser, server, rooms, width):
    ctx, page = _context(browser, server, width, 900)
    base = server['url'] + '/guild-next/guild/rooms'
    go(page, base)
    ids = []
    for index in range(12):
        result = _records_fetch(page, '/v1/rooms', {'title': f'Compact room {index}',
            'purpose': 'Synthetic room pagination check', 'mode': 'meeting', 'recording_acknowledged': True}, f'compact-room-{index:04}')
        assert result['status'] == 201
        ids.append(result['body']['result']['id'])
    go(page, base + '?room=' + ids[-1])
    expect(page.locator('[data-rm-room]')).to_have_count(10)
    if not page.locator('[data-rm-list]').is_visible():
        page.click('[data-rm-list-open]')
    expect(page.locator('[data-rm-room]')).to_have_count(10)
    page.click('[data-rm-older]')
    expect(page.locator('[data-rm-room]')).to_have_count(2)
    selected = page.locator('[data-rm-room]').last.get_attribute('data-rm-room')
    page.locator('[data-rm-room]').last.click()
    expect(page.locator(f'[data-rm-room="{selected}"]')).to_have_attribute('aria-current', 'true')
    if not page.locator('[data-rm-list]').is_visible():
        page.click('[data-rm-list-open]')
    expect(page.locator(f'[data-rm-room="{selected}"]')).to_have_attribute('aria-current', 'true')
    if page.locator('[data-rm-list-close]').is_visible():
        page.click('[data-rm-list-close]')
    expect(page.locator('[data-room-art-select]')).to_have_count(0)
    expect(page.locator('[data-room-art-image]')).to_be_hidden()
    if not page.locator('[data-rm-details]').is_visible():
        page.click('[data-rm-panel-toggle]')
    expect(page.locator('[data-rm-details]')).to_be_visible()
    expect(page.locator('[data-rm-manage]')).to_be_visible()
    expect(page.locator('[data-rm-about]')).not_to_have_attribute('open', '')
    expect(page.locator('[data-rm-agents]')).to_be_hidden()
    page.locator('[data-rm-manage] > summary').click()
    expect(page.locator('[data-rm-agents]')).to_be_visible()
    expect(page.locator('[data-rm-access]')).to_be_visible()
    expect(page.locator('[data-rm-about]')).not_to_have_attribute('open', '')
    page.locator('[data-rm-manage] > summary').click()
    page.click('[data-rm-panel-close]')
    _no_page_overflow(page)
    page.screenshot(path=f'/private/tmp/rooms-compact-{width}.png')
    listing = _records_fetch(page, '/v1/rooms')['body']['rooms']
    assert len(listing) == 12 and all(room['state'] == 'active' for room in listing)
    ctx.close()


@pytest.mark.parametrize('width', [320, 390, 768, 900, 1440])
def test_operate_summaries_and_area_previews_keep_real_controls_accessible(browser, server, floor, width):
    ctx, page = _context(browser, server, width, 900)
    for area in ('operate', 'improve', 'experiment'):
        go(page, server['url'] + '/guild-next/guild/' + area)
        if area == 'operate':
            expect(page.locator('.operate-overview > details')).to_have_count(3)
            expect(page.locator('.operate-overview > details[open]')).to_have_count(0)
            page.locator('[data-usage-limits] > summary').click()
            expect(page.locator('[data-limit="claude.daily_ceiling_usd"]')).to_be_visible()
            page.locator('[data-usage-limits] > summary').click()
        elif area == 'experiment':
            expect(page.locator('.proto-tile-art')).to_be_visible()
            expect(page.locator('.proto-tile-art')).to_have_attribute('src', __import__('re').compile('slide-4.png$'))
        else:
            expect(page.locator('#library-query')).to_be_enabled()
        if width <= 640:
            expect(page.locator('[data-mc-panel]')).to_be_hidden()
            page.click('[data-mc-pill]')
            expect(page.locator('[data-mc-input]')).to_be_visible()
            page.click('[data-mc-min]')
            expect(page.locator('[data-mc-panel]')).to_be_hidden()
        _no_page_overflow(page)
        page.screenshot(path=f'/private/tmp/guild-{area}-{width}.png')
    ctx.close()


@pytest.mark.parametrize('state,word', [('yellow', 'Watch'), ('unknown', 'Unknown')])
def test_operate_warning_summary_stays_emphasized_while_collapsed(browser, server, floor, monkeypatch, state, word):
    from minimoi_portal.guild_ui import limits_view
    original = limits_view.overview
    def warning(**kwargs):
        view = original(**kwargs)
        view['light'].update(state=state, word=word, reason='Synthetic review warning')
        return view
    monkeypatch.setattr(limits_view, 'overview', warning)
    ctx, page = _context(browser, server, 390, 900)
    go(page, server['url'] + '/guild-next/guild/operate')
    row = page.locator('[data-usage-limits]')
    expect(row).not_to_have_attribute('open', '')
    expect(row.locator('summary')).to_contain_text(word)
    expect(row.locator('summary')).to_contain_text('Synthetic review warning')
    expect(row.locator('summary h2')).to_have_text('Usage & limits')
    assert row.locator('summary').evaluate('(e) => getComputedStyle(e).borderLeftWidth') == '4px'
    ctx.close()


def test_mobile_craft_identity_more_and_desktop_restore(browser, server, floor):
    # Review 06: on a phone the chat's own header is the one bar (☰, the Master Craftsman and the conversation, CoS, ✎, ⋯);
    # the mini-moi strip and the workshop row ride at the top of ☰'s list, and come back to their row on a wide screen.
    ctx, page = _context(browser, server, 390, 844)
    go(page, server['url'] + '/guild-next/guild/build')
    expect(page.locator('.mobile-identity')).to_have_text('MC')
    expect(page.locator('.workshop-primary')).to_contain_text('Craft')
    expect(page.locator('.workshop-name')).to_be_hidden()
    bar = page.locator('[data-floorbar]')
    expect(bar).to_be_visible()
    expect(bar.locator('.hero-name')).to_contain_text('Master Craftsman')
    expect(page.locator('#portal-nav-bar')).to_be_hidden()
    bar.locator('[data-history-toggle]').click()
    expect(page.locator('body')).to_have_attribute('data-history', 'open')
    expect(page.locator('[data-floor-history] #portal-nav-bar')).to_be_visible()
    expect(page.locator('[data-floor-history] .workshop-primary [data-section="rooms"]')).to_be_visible()
    page.locator('[data-history-close]').click()
    expect(bar.locator('[data-history-toggle]')).to_be_focused()
    expect(bar.locator('[data-rail-toggle]')).to_be_hidden()                  # no files: no Files
    assert _no_page_overflow(page)
    page.set_viewport_size({'width':1440,'height':900})
    expect(page.locator('.workshop-primary [data-section="rooms"]')).to_be_visible()
    expect(page.locator('.workshop-primary [data-section="rooms"]')).to_have_text('Guildhall')
    expect(page.locator('body > .guild-nav .workshop-cos')).to_have_attribute('href', '/app/cos/ui/confer')
    ctx.close()
