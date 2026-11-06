"""Beta UI acceptance against the rendered portal with isolated stores."""
import pytest
from playwright.sync_api import expect
from browser_checks_b1 import browser, server, fresh_queue, floor, _context, go

@pytest.mark.parametrize('embedded', [False, True])
@pytest.mark.parametrize('width,height', [(1440,900), (720,900), (390,844)])
def test_craft_stays_in_viewport_and_composer_remains_reachable(browser, server, floor, width, height, embedded, monkeypatch):
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE', '1')
    ctx, page = _context(browser, server, width, height)
    go(page, server['url'] + ('/guild-next/guild/companion/cos' if embedded else '/guild-next/guild/build'))
    expect(page.locator('[data-mc-input]')).to_have_attribute('placeholder', 'Message MC…')
    metrics = page.evaluate('''() => {const input=document.querySelector('[data-mc-input]').getBoundingClientRect();
      return {height:innerHeight, scroll:document.documentElement.scrollHeight,
      width:innerWidth, scrollWidth:document.documentElement.scrollWidth, top:input.top,bottom:input.bottom};}''')
    assert metrics['scroll'] <= metrics['height'] + 1, metrics
    assert metrics['scrollWidth'] <= metrics['width'], metrics
    assert 0 <= metrics['top'] < metrics['bottom'] <= metrics['height'], metrics
    page.screenshot(path=f'/private/tmp/beta-craft-{width}-{embedded}.png')
    page.locator('[data-mc-input]').fill('Draft kept through a reload')
    page.reload()
    expect(page.locator('[data-mc-input]')).to_have_value('Draft kept through a reload')
    ctx.close()

from browser_checks_b1 import _script, FINISH
from browser_checks_mc_queue import queued, streaming_mc, _held, _say, _wait, _chat_posts, BUILD

@pytest.mark.parametrize('width', [1440,390])
def test_one_compact_queue_count_and_cancel_remains_available(browser, server, queued, width):
    runtime = queued['runtime']
    _held(runtime)
    ctx, page = _context(browser, server, width, 900)
    go(page, server['url'] + BUILD)
    _say(page, 'First question')
    assert _wait(lambda: len(_chat_posts(runtime)) == 1)
    _say(page, 'Second question')
    expect(page.locator('[data-queued-chip]')).to_have_count(1)
    expect(page.locator('[data-mc-queue-text]')).to_have_text('1 message waiting')
    expect(page.get_by_text('1 message waiting', exact=True)).to_have_count(1)
    expect(page.locator('[data-queued-chip]')).to_contain_text('Not sent · next to send')
    expect(page.locator('[data-queued-cancel]')).to_be_visible()
    page.locator('[data-queued-cancel]').click()
    expect(page.locator('[data-queued-chip]')).to_have_count(0)
    expect(page.locator('[data-mc-queue-bar]')).to_be_hidden()
    runtime.gate.set()
    ctx.close()


def _composer_gap(page):
    return page.evaluate('''() => {
      const dock = document.querySelector('[data-mc-dock-bottom]').getBoundingClientRect();
      const strip = document.querySelector('.floor-strip');
      const shown = strip && getComputedStyle(strip).display !== 'none';
      const limit = shown ? strip.getBoundingClientRect().top : innerHeight;
      const input = document.querySelector('[data-mc-input]').getBoundingClientRect();
      return {gap: limit - dock.bottom, scroll: document.documentElement.scrollHeight,
        height: innerHeight, scrollWidth: document.documentElement.scrollWidth, width: innerWidth,
        top: input.top, bottom: input.bottom};
    }''')


def _assert_composer_sits_on_the_status(metrics):
    assert metrics['scroll'] <= metrics['height'] + 1, metrics
    assert metrics['scrollWidth'] <= metrics['width'] + 1, metrics
    assert 0 <= metrics['top'] < metrics['bottom'] <= metrics['height'], metrics
    assert 0 <= metrics['gap'] <= 16, metrics


@pytest.mark.parametrize('width,height', [(1219, 668), (390, 844)])
def test_craft_after_a_turn_keeps_the_composer_against_the_status_strip(browser, server, streaming_mc, width, height, monkeypatch):
    """The live 1219×668 report was a blank band under the composer after Send.
    The band is the 12px composer margin. A later shift that opens the page fails here."""
    monkeypatch.setenv('MINIMOI_MC_EVERYWHERE', '1')
    runtime = streaming_mc['runtime']
    runtime.script = _script('Short answer.', *FINISH)
    ctx, page = _context(browser, server, width, height)
    go(page, server['url'] + '/guild-next/guild/build')
    _assert_composer_sits_on_the_status(_composer_gap(page))
    page.fill('#mc-input', 'Hello')
    page.click('[data-mc-send]')
    expect(page.locator('[data-mc-thread] [data-kind="note"]').last).to_contain_text('Short answer.', timeout=8000)
    short = _composer_gap(page)
    _assert_composer_sits_on_the_status(short)
    page.screenshot(path=f'/private/tmp/beta-craft-after-turn-{width}.png')
    runtime.script = _script(('A longer kept line.\n' * 30), *FINISH)
    page.fill('#mc-input', 'Continue')
    page.click('[data-mc-send]')
    expect(page.locator('[data-mc-thread] [data-kind="note"]').last).to_contain_text('A longer kept line.', timeout=8000)
    _assert_composer_sits_on_the_status(_composer_gap(page))
    page.reload()
    page.wait_for_selector('body[data-ready=true]')
    _assert_composer_sits_on_the_status(_composer_gap(page))
    expect(page.locator('[data-mc-attach]')).to_be_visible()
    expect(page.locator('[data-mc-options] summary')).to_be_visible()
    page.locator('[data-mc-input]').focus()
    expect(page.locator('[data-mc-input]')).to_be_focused()
    if width > 640:
        page.locator('[data-craft-picture-toggle]').click()
        expect(page.locator('body')).to_have_attribute('data-craft-picture', 'hidden')
        _assert_composer_sits_on_the_status(_composer_gap(page))
    ctx.close()
