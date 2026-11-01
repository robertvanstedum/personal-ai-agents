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


def _wall_notes(store):
    """One of each look, as CoS's Board holds them: labels set by the existing Board API, one note put up by CoS."""
    from uuid import uuid4
    from minimoi_portal.guild_ui.stores import Author, CHIEF_OF_STAFF
    robert = Author('robert', 'owner', 'Robert')
    notes = [('RVS Associates: MiniMoi as the commercial version? Who pays first?', 'idea', None, robert),
             ('One builder, one host. What if I am away two weeks?', 'blocked', None, robert),
             ('Who else builds personal agent teams https://example.org/agent-teams', 'fyi', None, CHIEF_OF_STAFF),
             ('Portfolio walkthrough: show the wall, not the code.', 'decide', None, robert),
             ('Local models for 1.3, so it runs with no provider at all.', 'remember', None, CHIEF_OF_STAFF),
             ('Reply to the outside issue before linking anything', 'followup', None, robert)]
    for text, label, ref, who in notes:
        assert store.add_postit(text, who, idempotency_key=str(uuid4()), label=label, item_ref=ref).outcome == 'added'


def test_cos_board_is_a_wall_and_guilds_board_is_not(browser, server, floor, tmp_path):
    import os
    shots = os.environ.get('COS_WALL_SHOTS')
    store = floor.store('cos-board')
    server['app'].extensions['cos_board']['services'].floor = store
    _wall_notes(store)
    for width, height in ((1440, 900), (390, 844)):
        ctx, page = _context(browser, server, width, height)
        errors = _errors(page)
        page.goto(server['url'] + '/guild-next/cos-board')
        expect(page.locator('body')).to_have_attribute('data-floor', 'cos-board')
        notes = page.locator('[data-bd-note]')
        expect(notes).to_have_count(6)
        looks = page.eval_on_selector_all('[data-bd-note]', 'ns => ns.map(n => n.dataset.look).sort()')
        assert looks == ['clipping', 'marked', 'marked', 'sticky', 'sticky', 'worry'], looks
        # No label words, no label filter; ＋ Note, ＋ Photo, search and "Taken down (n)".
        expect(page.locator('.bd-kicker')).to_have_count(0)
        expect(page.locator('[data-bd-label-filter]')).to_be_hidden()
        expect(page.locator('[data-wall-taken]')).to_contain_text('Taken down (0)')
        for name in ('Idea', 'Blocked', 'Decide', 'FYI', 'Remember', 'Follow-up'):
            assert page.locator('.bd-grid').get_by_text(name, exact=True).count() == 0
        # CoS's own notes carry its mark; the clipping says where it is from.
        expect(page.locator('[data-author="cos"] img.wall-mark')).to_have_count(2)
        expect(page.locator('[data-look="clipping"] .wall-source')).to_have_text('example.org')
        expect(page.locator('[data-look="clipping"] .bd-text')).to_have_text('Who else builds personal agent teams')
        assert page.evaluate("getComputedStyle(document.querySelector('[data-look=worry]')).backgroundColor") == 'rgb(246, 201, 194)'
        assert page.evaluate("getComputedStyle(document.querySelector('.bd-cork')).backgroundColor") == 'rgb(244, 242, 238)'
        assert page.evaluate("document.querySelector('.bd-grid').classList.contains('bd-free')") is False
        if width < 640:
            xs = page.eval_on_selector_all('[data-bd-note]', 'ns => new Set(ns.map(n => Math.round(n.getBoundingClientRect().x))).size')
            assert xs == 1                                                       # one column on the phone
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if shots:
            page.screenshot(path=f'{shots}/wall-{width}.png', full_page=width < 640)
        # A note opens on the right (full screen on the phone, with "← Board"); its text edits there as on the wall.
        page.locator('[data-look="worry"] .bd-text').click()
        sheet = page.locator('[data-wall-sheet]')
        expect(sheet).to_be_visible()
        expect(sheet.locator('[data-bd-note]')).to_have_attribute('data-look', 'worry')
        expect(sheet).to_contain_text('Put up by Robert')
        box = sheet.bounding_box()
        if width < 640:
            assert box['x'] == 0 and box['width'] == width
            expect(sheet.locator('.wall-back')).to_be_visible()
        else:
            assert box['x'] + box['width'] == width and box['width'] < width / 2
        if shots:
            page.screenshot(path=f'{shots}/wall-note-open-{width}.png')
        sheet.locator('.bd-text').click()
        expect(page.locator('[data-bd-edit-field]')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('[data-bd-edit-field]')).to_have_count(0)
        sheet.locator('.wall-back' if width < 640 else '.wall-x').click()
        expect(page.locator('[data-wall-sheet]')).to_have_count(0)
        page.locator('[data-wall-taken]').click()
        expect(page.locator('[data-bd-view="done"]')).to_have_attribute('aria-pressed', 'true')
        page.locator('[data-bd-view="active"]').click()
        expect(notes).to_have_count(6)
        assert not errors, errors
        ctx.close()
    # Guild's own Board is untouched: its labels, its filter and its toolbar.
    ctx, page = _context(browser, server, 1440, 900)
    page.goto(server['url'] + '/guild-next/guild/board')
    expect(page.locator('body')).not_to_have_attribute('data-floor', 'cos-board')
    expect(page.locator('[data-bd-label-filter]')).to_be_visible()
    expect(page.locator('[data-wall-taken]')).to_have_count(0)
    expect(page.locator('link[href*="cos_wall.css"]')).to_have_count(0)
    ctx.close()
