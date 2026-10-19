"""Contextual chat history stays with the Board; real browser, synthetic local records."""
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context, go, _wt, _no_page_overflow  # noqa:F401


@pytest.mark.parametrize('width,height', [(320,900), (390,900), (768,900), (900,900), (1440,900), (390,480)])
def test_history_picker_keeps_board_and_panel_mode_with_ten_recent_chats(browser, server, floor, width, height):
    ctx, page = _context(browser, server, width, height)
    go(page, server['url'] + '/guild-next/guild/board')
    for index in range(12):
        answer = _wt(page, '/conversations', {'title': f'History check {index}'})
        assert answer['conversation']['id']
    page.click('[data-mc-pill]')
    expect(page.locator('[data-mc-invite]')).to_have_count(0)
    page.locator('[data-conv-picker] summary').click()
    expect(page.locator('[data-conv-pick]')).to_have_count(10)
    _no_page_overflow(page)
    picker_box = page.locator('[data-conv-picker-body]').bounding_box()
    panel_box = page.locator('[data-mc-panel]').bounding_box()
    assert picker_box['y'] >= panel_box['y']
    assert picker_box['y'] + picker_box['height'] <= min(height, panel_box['y'] + panel_box['height'])
    send_box = page.locator('[data-mc-send]').bounding_box()
    assert send_box['y'] + send_box['height'] <= min(height, panel_box['y'] + panel_box['height'])
    page.screenshot(path=f'/private/tmp/mc-history-{width}x{height}.png')
    first_recent = page.locator('[data-conv-pick]').first.get_attribute('data-conv-pick')
    page.locator('[data-conv-picker-body]').get_by_role('button', name='Older', exact=True).click()
    expect(page.locator('[data-conv-pick]').first).not_to_have_attribute('data-conv-pick', first_recent)
    assert 0 < page.locator('[data-conv-pick]').count() <= 10
    selected = page.locator('[data-conv-pick]').first.get_attribute('data-conv-pick')
    page.locator('[data-conv-pick]').first.click()
    expect(page).to_have_url(__import__('re').compile(r'/guild-next/guild/board\?.*c=' + selected))
    expect(page.locator('[data-mc-panel]')).to_have_attribute('data-mode', 'floating')
    expect(page.locator('[data-bd-grid]')).to_be_attached()
    page.locator('[data-conv-picker] summary').click()
    page.locator('[data-conv-picker-body]').get_by_role('button', name='Archived', exact=True).click()
    expect(page.locator('[data-conv-picker-body]')).to_contain_text('Nothing archived.')
    _no_page_overflow(page)
    ctx.close()


def _go_private(page):
    """Private sits in the conversation options menu (4a7e4cfc). Open it the way the owner does, then press the lock.
    The lock and everything it guards are unchanged; only the route to it is the real one."""
    if not page.locator("[data-mc-options]").evaluate("el => el.open"):
        page.click("[data-mc-options] summary")
    page.click("[data-mc-record]")


def test_drafts_follow_their_conversation_and_never_persist_private_text(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    base = server['url'] + '/guild-next/guild/board'
    go(page, base)
    first = _wt(page, '/conversations', {'title': 'Draft one'})['conversation']['id']
    second = _wt(page, '/conversations', {'title': 'Draft two'})['conversation']['id']
    go(page, base + '?c=' + first + '&chat=floating')
    box = page.locator('[data-mc-input]')
    box.fill('On-record draft for one')
    page.locator('[data-conv-picker] summary').click()
    page.locator(f'[data-conv-pick="{second}"]').click()
    expect(box).to_have_value('')
    box.fill('On-record draft for two')
    page.locator('[data-conv-picker] summary').click()
    page.locator(f'[data-conv-pick="{first}"]').click()
    expect(box).to_have_value('On-record draft for one')
    _go_private(page)
    box.fill('PRIVATE DRAFT MUST NOT BE STORED')
    for store in ('sessionStorage', 'localStorage'):
        assert 'PRIVATE DRAFT' not in page.evaluate(f'JSON.stringify({store})')
    page.reload()
    expect(page.locator('[data-mc-record]')).to_have_attribute('aria-pressed', 'true')
    expect(box).to_have_value('')
    _go_private(page)
    expect(box).to_have_value('On-record draft for one')
    page.click('[data-mc-send]')
    expect(box).to_have_value('')
    page.reload()
    expect(box).to_have_value('')
    ctx.close()


def test_saving_one_note_does_not_erase_a_newer_draft(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, server['url'] + '/guild-next/guild/board?chat=floating')
    box = page.locator('[data-mc-input]')
    held = []
    page.route('**/api/v1/notes', lambda route: held.append(route) if route.request.method == 'POST' else route.continue_())
    box.fill('First note')
    page.click('[data-mc-send]')
    expect(page.locator('[data-mc-send]')).to_be_disabled()
    box.fill('A newer draft while saving')
    assert len(held) == 1
    held[0].continue_()
    expect(page.locator('[data-mc-send]')).to_be_enabled()
    expect(box).to_have_value('A newer draft while saving')
    page.reload()
    expect(box).to_have_value('A newer draft while saving')
    ctx.close()


def test_restored_long_draft_keeps_full_text_and_immediately_shows_limit(browser, server, floor):
    ctx, page = _context(browser, server, 1440, 900)
    go(page, server['url'] + '/guild-next/guild/board?chat=floating')
    box = page.locator('[data-mc-input]')
    text = 'long draft ' * 3000
    box.fill(text)
    expect(box).to_have_attribute('aria-invalid', 'true')
    page.reload()
    expect(box).to_have_value(text)
    expect(box).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('[data-mc-refusal]')).to_contain_text('Nothing was sent')
    ctx.close()


def test_cos_disabled_private_control_keeps_truthful_accessible_label(browser, server, floor):
    from types import SimpleNamespace
    from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
    from minimoi_portal.guild_ui.mc.backend import CachedHealth
    services = server['app'].extensions['guild_ui_next']['services']
    saved = services.mc, services.mc_health, services.mc_turns
    backend = ChiefOfStaffBackend('http://unused.invalid', http_get=lambda *a, **k: SimpleNamespace(status_code=200))
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    ctx, page = _context(browser, server, 390, 900)
    try:
        go(page, server['url'] + '/guild-next/guild/board?chat=floating')
        expect(page.locator('body')).to_have_attribute('data-mc-private', 'false')
        button = page.locator('[data-mc-record]')
        expect(button).to_be_disabled()
        expect(button).to_have_attribute('aria-label', 'Private is not available')
        expect(button).to_have_attribute('title', 'Private is not available with the Chief of Staff: it keeps its own record')
    finally:
        ctx.close()
        services.mc, services.mc_health, services.mc_turns = saved
