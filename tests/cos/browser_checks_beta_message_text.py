"""Confer assistant text: inline code on arrival and after reload, never as HTML."""
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / 'core/founders_ui/static/message-text.js').read_text()
CHROME = os.environ.get('BETA_CHROMIUM_PATH') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
PAGE = '''<!doctype html><meta charset="utf-8">
<div id="live"></div><div id="reloaded"></div>
<script src="/message-text.js"></script>
<script>
function appendMsg(role, text) {
  const body = document.createElement('div');
  body.className = 'msg-text';
  body.dataset.path = 'received';
  body.textContent = text;
  if (role !== 'user') window.MiniMoiMessageText.render(body, text);
  document.getElementById('live').append(body);
}
function historyRow(label, text) {
  const words = document.createElement('div');
  words.className = 'msg-text';
  words.dataset.path = 'reloaded';
  words.textContent = text;
  if (label !== 'You') window.MiniMoiMessageText.render(words, text);
  document.getElementById('reloaded').append(words);
}
</script>'''
REPLY = 'Use **`Board`** and ```token```. <script>alert(1)</script>'


def test_confer_sources_render_assistant_text_and_leave_user_text_alone():
    template = (ROOT / 'domains/cos/templates/cos_ui.html').read_text()
    history = (ROOT / 'domains/cos/static/cos-history-ui.js').read_text()
    assert 'message-text.js?v=20261106-ticks1' in template
    assert "if (role !== 'user') window.MiniMoiMessageText?.render(body, text);" in template
    assert "if (!mine) window.MiniMoiMessageText?.render(words, text);" in history


def test_new_and_reloaded_assistant_replies_drop_code_ticks_and_keep_markup_as_text():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page()
        def route(request):
            if request.request.url.endswith('/message-text.js'):
                return request.fulfill(body=SCRIPT, content_type='text/javascript')
            return request.fulfill(body=PAGE, content_type='text/html')
        page.route('**/*', route)
        page.goto('http://localhost/confer')
        page.wait_for_function('() => window.MiniMoiMessageText')
        page.evaluate('''(text) => {
          appendMsg('cos', text);
          historyRow('CoS', text);
          appendMsg('user', '`keep`');
          historyRow('You', '`keep`');
        }''', REPLY)
        for selector in ('#live [data-path="received"]', '#reloaded [data-path="reloaded"]'):
            node = page.locator(selector).first
            expect(node.locator('code')).to_have_count(2)
            expect(node.locator('strong code')).to_have_text('Board')
            expect(node.locator('code').nth(1)).to_have_text('token')
            assert '`' not in node.inner_text().split('<script>')[0]
            assert node.locator('script').count() == 0
            assert '<script>alert(1)</script>' in node.inner_text()
        expect(page.locator('#live [data-path="received"]').nth(1)).to_have_text('`keep`')
        expect(page.locator('#reloaded [data-path="reloaded"]').nth(1)).to_have_text('`keep`')
        page.screenshot(path='/private/tmp/beta-cos-inline-code.png')
        browser.close()
