"""Synthetic owner, local fixture only: UI/domain convergence and draft protection."""
import time
import pytest
from playwright.sync_api import expect
from browser_checks_board_popout import (browser, server, board_media, floor, fresh_queue,
    test_the_board_in_two_windows_stays_one_board,
    test_a_note_being_typed_is_never_overwritten_and_a_conflict_keeps_their_words)
from browser_checks_b1 import _context, go, _errors, _no_page_overflow
from minimoi_portal.guild_ui.board_actions import BoardActions, BoardContext
from uuid import uuid4

@pytest.fixture(autouse=True)
def domain_board(server, board_media):
    env = server["app"].extensions["guild_ui_next"]["feature_environ"]
    old = dict(env)
    env.update(MINIMOI_BOARD_ACTIONS="1", MINIMOI_BOARD_ENV="dev")
    yield
    env.clear(); env.update(old)


def test_whole_unicode_draft_and_cross_client_domain_refresh(browser, server, board_media, tmp_path):
    store = board_media[0].store()
    ctx, page = _context(browser, server, 390, 844)
    errors = _errors(page)
    ctx.add_init_script("delete window.BroadcastChannel")
    go(page, server["url"] + "/guild-next/guild/board")
    page.click("[data-bd-new]")
    draft = "😀" * 281
    field = page.locator("[data-bd-new-field]")
    field.fill(draft)
    expect(page.locator('.board-text-count')).to_contain_text('281 / 280')
    assert field.input_value() == draft
    page.keyboard.press('Control+Enter')
    expect(field).to_be_visible()
    assert store.board().data['counts']['active'] == 0
    # A remote operation should not destroy an editor, even across several polling cycles.
    svc = BoardActions(store, owner='robert', environment='dev', cursor_key=b'test-cursor-only' * 3)
    actor = BoardContext('robert', 'dev', store.floor, 'test-app', 'synthetic-call', frozenset({'guild.board.note.create'}))
    started = time.monotonic()
    out = svc.create(actor, str(uuid4()), {'text': 'Remote note'})
    assert out['state'] == 'verified'
    page.wait_for_timeout(2300)
    assert field.input_value() == draft
    field.fill('😀' * 280)
    page.keyboard.press('Control+Enter')
    expect(page.locator('[data-bd-note] .bd-text')).to_have_count(2)
    assert any(p['text'] == '😀' * 280 for p in store.board().data['active'])
    # With no draft/channel hint, the visible Board discovers the backend write within 3 seconds.
    started = time.monotonic()
    svc.create(actor, str(uuid4()), {'text': 'Second remote note'})
    expect(page.locator('[data-bd-note] .bd-text', has_text='Second remote note')).to_have_count(1, timeout=3000)
    elapsed = time.monotonic() - started
    assert elapsed < 3
    _no_page_overflow(page)
    page.screenshot(path=str(tmp_path / 'mobile-board-domain.png'), full_page=True)
    assert not errors, errors
    ctx.close()


def test_receipt_link_selects_a_done_note_and_survives_reload(browser,server,board_media):
    store=board_media[0].store()
    svc=BoardActions(store,owner='robert',environment='dev',cursor_key=b'test-cursor-only'*3)
    actor=BoardContext('robert','dev',store.floor,'test-app','synthetic-link',
                       frozenset({'guild.board.note.create','guild.board.note.update'}))
    created=svc.create(actor,str(uuid4()),{'text':'Finished remotely'})
    ref=created['current']['id']
    svc.update(actor,str(uuid4()),ref,1,{'done':True})
    ctx,page=_context(browser,server,390,844)
    go(page,server['url']+'/guild-next/guild/board?note='+ref)
    expect(page.locator('[data-bd-note] .bd-text',has_text='Finished remotely')).to_be_visible()
    page.reload()
    expect(page.locator('[data-bd-note] .bd-text',has_text='Finished remotely')).to_be_visible()
    _no_page_overflow(page)
    ctx.close()
