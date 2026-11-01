"""Local browser acceptance for the copied Board, including same-origin embedding."""
from playwright.sync_api import expect
from browser_checks_b1 import _context, _errors, browser, floor, fresh_queue, server
from browser_checks_board_popout import _add, _popout


def test_cos_board_edit_reload_popout_and_embed(browser, server, floor):
    server['app'].extensions['cos_board']['services'].floor = floor.store('cos-board')
    ctx, page = _context(browser, server, 390, 844)
    errors = _errors(page)
    page.goto(server['url'] + '/guild-next/cos-board')
    _add(page, 'CoS acceptance note')
    note = page.locator('[data-bd-note]')
    note.locator('.bd-edit > summary').click()
    note.locator('[data-bd-edit-open]').click()
    page.locator('[data-bd-edit-field]').fill('Updated CoS note')
    page.keyboard.press('Control+Enter')
    expect(page.locator('.bd-text')).to_have_text('Updated CoS note')
    page.reload()
    expect(page.locator('.bd-text')).to_have_text('Updated CoS note')
    pop = _popout(ctx, page)
    expect(pop.locator('.bd-text')).to_have_text('Updated CoS note')
    assert floor.store().board().data['active'] == []
    # Embed from the same portal origin, as the Records shell does.
    page.goto(server['url'] + '/__b1_test_sign_in')
    page.evaluate("""() => { const f = document.createElement('iframe'); f.src='/guild-next/cos-board'; document.body.append(f); }""")
    expect(page.frame_locator('iframe').locator('.bd-text')).to_have_text('Updated CoS note')
    assert not errors, errors
    ctx.close()
