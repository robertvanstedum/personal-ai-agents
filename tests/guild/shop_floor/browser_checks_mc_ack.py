"""Visible receipt before model output, streamed scope, and Stop during a held response."""
from playwright.sync_api import expect
from browser_checks_b1 import (browser, server, streaming_mc, floor, fresh_queue, _context,
                               _fresh_conversation, _script, FINISH)  # noqa: F401


def test_receipt_and_stop_are_visible_before_backend_output(browser, server, streaming_mc):
    runtime = streaming_mc['runtime']
    runtime.script = ['WAIT', *_script('Must not arrive.', *FINISH)]
    ctx, page = _context(browser, server)
    try:
        _fresh_conversation(page, server)
        page.fill('#mc-input', 'Synthetic held response; no side effects.')
        page.click('[data-mc-send]')
        live = page.locator('[data-mc-stream-line]')
        expect(live.locator('[data-slot="state"]')).to_have_text('Received · starting')
        expect(live.locator('[data-slot="tail"]')).to_be_empty()
        expect(live.locator('[data-mc-stop]')).to_be_visible()
        assert runtime.waiting.wait(1), 'backend must be held, not finished'
        live.locator('[data-mc-stop]').click()
        expect(page.locator('[data-mc-stream-line]')).to_have_count(0)
        expect(page.locator('[data-mc-thread]')).to_contain_text('relay confirmed')
        assert runtime.stopped.is_set()
        assert 'Must not arrive.' not in page.locator('[data-mc-thread]').inner_text()
    finally:
        runtime.gate.set()
        ctx.close()


def test_scope_text_streams_before_completion_then_renders_markdown(browser, server, streaming_mc):
    runtime = streaming_mc['runtime']
    runtime.script = [*_script('I will read the spec and tests first.\n\n'), 'WAIT',
                      *_script('**Spec 162** — source read; tests not executed.', *FINISH)]
    ctx, page = _context(browser, server)
    try:
        _fresh_conversation(page, server)
        page.fill('#mc-input', 'Synthetic source-reading response.')
        page.click('[data-mc-send]')
        live = page.locator('[data-mc-stream-line]')
        expect(live).to_contain_text('I will read the spec and tests first.')
        assert runtime.waiting.wait(1)
        expect(live.locator('[data-mc-stop]')).to_be_visible()
        assert 'Spec 162' not in live.inner_text()
        runtime.gate.set()
        expect(page.locator('[data-mc-thread] strong')).to_have_text('Spec 162')
        expect(page.locator('[data-mc-stream-line]')).to_have_count(0)
        expect(page.locator('[data-mc-thread]')).to_contain_text('tests not executed')
    finally:
        runtime.gate.set()
        ctx.close()
