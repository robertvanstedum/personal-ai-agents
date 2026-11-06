"""Phone CoS header: Actions and Guildhall keep a real gap, in the bar and in the list."""
import os
import re
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CHROME = os.environ.get('BETA_CHROMIUM_PATH') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


def _page(width, scripts):
    env = Environment(loader=ChoiceLoader([
        FileSystemLoader(ROOT / 'domains/cos/templates'),
        PrefixLoader({'founders_ui': FileSystemLoader(ROOT / 'core/founders_ui/templates')}),
    ]))
    html = env.get_template('cos_ui.html').render(
        initial_tab='confer', founders_ui_version='test', managed_realtime_ready=False)
    html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
    html = html.replace('data-picture="shown"', 'data-picture="shown" data-cos-tab="confer"')
    html = html.replace('id="tab-confer" class="tab-panel"', 'id="tab-confer" class="tab-panel active"')
    if scripts:
        html = html.replace('</body>', '<script type="module" src="/founders-ui/mobile-nav.js"></script></body>')
    pw = sync_playwright().start()
    browser = pw.chromium.launch(executable_path=CHROME, headless=True)
    page = browser.new_page(viewport={'width': width, 'height': 844})

    def assets(route):
        path = route.request.url.split('localhost')[-1].split('?')[0]
        if path.startswith('/founders-ui/'):
            target = ROOT / 'core/founders_ui/static' / path.removeprefix('/founders-ui/')
        else:
            target = ROOT / 'domains/cos/static' / path.removeprefix('/static/')
        if target.is_file():
            return route.fulfill(path=str(target))
        return route.fulfill(status=404)

    page.route('**/static/**', assets)
    page.route('**/founders-ui/**', assets)
    page.route('**/cos-render', lambda route: route.fulfill(body=html, content_type='text/html'))
    page.goto('http://localhost/cos-render')
    return pw, browser, page


def _gap(page):
    box = page.evaluate('''() => {
      const actions = document.querySelector('[data-tab="flight"]');
      const guildhall = document.querySelector('a[data-portal-link]');
      const a = actions.getBoundingClientRect();
      const g = guildhall.getBoundingClientRect();
      return {gap: g.left - a.right, margin: getComputedStyle(guildhall).marginLeft,
              scroll: document.documentElement.scrollWidth, width: innerWidth,
              inBar: Boolean(guildhall.closest('.header-nav'))};
    }''')
    return box


def _fits(box):
    assert box['scroll'] <= box['width'] + 1


def test_phone_bar_keeps_actions_apart_from_guildhall_before_the_menu_script():
    pw, browser, page = _page(390, scripts=False)
    try:
        expect(page.locator('a[data-portal-link]')).to_be_visible()
        box = _gap(page)
        assert box['inBar']
        assert box['gap'] >= 16
        _fits(box)
        page.screenshot(path='/private/tmp/beta-cos-phone-header-390.png')
    finally:
        browser.close()
        pw.stop()


def test_very_narrow_phone_drops_the_extra_margin():
    pw, browser, page = _page(359, scripts=False)
    try:
        box = _gap(page)
        assert box['inBar']
        assert box['margin'] == '0px'
        # This width already sits about 2px past the viewport. The phone margin must not add its 14px on top.
        assert box['scroll'] <= box['width'] + 4
    finally:
        browser.close()
        pw.stop()


def test_desktop_header_does_not_take_the_phone_margin():
    pw, browser, page = _page(1440, scripts=False)
    try:
        box = _gap(page)
        assert box['inBar']
        assert box['margin'] == '0px'
        assert box['gap'] >= 16
        _fits(box)
    finally:
        browser.close()
        pw.stop()


def test_phone_menu_keeps_the_gap_after_the_script_moves_the_labels():
    pw, browser, page = _page(390, scripts=True)
    try:
        page.wait_for_function("() => document.querySelector('[data-mobile-menu] .tab-nav')")
        page.evaluate("() => { document.body.dataset.drawer = 'open' }")
        expect(page.locator('[data-mobile-menu] a[data-portal-link]')).to_be_visible()
        box = _gap(page)
        assert not box['inBar']
        assert box['margin'] == '0px'
        assert box['gap'] >= 16
        _fits(box)
        page.screenshot(path='/private/tmp/beta-cos-phone-menu-390.png')
    finally:
        browser.close()
        pw.stop()
