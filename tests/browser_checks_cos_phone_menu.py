"""Staging test A1, 28 Nov: on a 375px phone CoS's ⋯ menu ran about 9px past the right edge, because it hung from the ⋯
(which sits past ＋) at a fixed 320px. Offline Chromium, as browser_checks_founders: every row inside the screen and on top."""
import json
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from flask import Flask, render_template
from playwright.sync_api import sync_playwright
from domains.cos.history import History
from domains.cos.history_api import create_blueprint
from core.founders_ui import install
ROOT = Path(__file__).resolve().parents[1]
ROWS = ['#btn-dictate', '[data-partner-voice]', '[data-partner-model-row]', '#cos-private', '[data-partner-last]',
        '.partner-menu-foot']
MEASURE = """(rows) => { const box = (e) => { const b = e.getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return {left: b.left, top: b.top, right: b.right, bottom: b.bottom, on_top: !!(hit && e.contains(hit))}; };
  const out = {body: box(document.querySelector('.cos-options-body'))};
  for (const sel of rows) out[sel] = box(document.querySelector('[data-cos-options] ' + sel));
  return out; }"""
# The settings read on a host with voice: both rows shown, a long model label and the fallback line (the widest case).
SETTINGS = {'revision': 1, 'voice': {'selected': 'openai', 'options': [
    {'provider': 'openai', 'label': 'OpenAI', 'available': True}, {'provider': 'xai', 'label': 'Grok', 'available': False}]},
    'model': {'text': 'GPT-5.5 on your ChatGPT plan, then the API if it is down'},
    'last_answer': 'Last answer: GPT-5.5 on the API (fallback)'}


@pytest.mark.parametrize('width,height', [(320, 700), (375, 812), (390, 844), (639, 800), (1280, 800)])
def test_the_open_cos_menu_stays_inside_the_screen(tmp_path, width, height):
    app = Flask(__name__, template_folder=str(ROOT/'domains/cos/templates'), static_folder=str(ROOT/'domains/cos/static'))
    history = History(tmp_path/'history', 'dev'); install(app)
    app.register_blueprint(create_blueprint(history, resolve_owner=lambda request: 1))
    @app.get('/ui/confer')
    def page(): return render_template('cos_ui.html', initial_tab='confer', managed_realtime_ready=False)
    client = app.test_client()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        tab = browser.new_context(viewport={'width': width, 'height': height}, service_workers='block').new_page()
        def route(r):
            u = urlsplit(r.request.url)
            if u.path == '/ui/history/partner-settings':
                return r.fulfill(status=200, body=json.dumps(SETTINGS), headers={'content-type': 'application/json'})
            response = client.open(u.path + ('?' + u.query if u.query else ''), method=r.request.method,
                                   data=r.request.post_data, content_type=r.request.headers.get('content-type'))
            r.fulfill(status=response.status_code, body=response.data, headers={'content-type': response.content_type})
        tab.route('**/*', route)
        tab.goto('https://founders.invalid/ui/confer')
        tab.wait_for_function('window.cosHistory?.ready === true')
        tab.locator('[data-cos-options] > summary').click()
        tab.locator('[data-cos-options] [data-partner-model-row]').wait_for(state='visible', timeout=5000)
        tab.wait_for_timeout(200)
        boxes = tab.evaluate(MEASURE, ROWS)
        browser.close()
    for name, b in boxes.items():
        assert b['left'] >= 0 and b['top'] >= 0 and b['right'] <= width and b['bottom'] <= height, (name, b)
        assert name == 'body' or b['on_top'], (name, b)
