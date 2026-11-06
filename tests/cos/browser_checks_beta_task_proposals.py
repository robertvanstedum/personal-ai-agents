"""Real browser UI contracts with synthetic HTTP, no provider or owner writes."""
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / 'domains/cos/static/cos-task-proposals.js').read_text()
HTML = '''<meta name="minimoi-csrf-token" content="synthetic-token">
<div id="chat-log"></div><div id="tab-flight"><section data-tp>
<button data-tp-refresh>Refresh</button><p data-tp-say hidden></p><div data-tp-list></div>
</section></div><script>window.cosHistory={conversationId:'c1'};
function cosWriteHeaders(){return {'X-CSRF-Token':'synthetic-token'}};</script>
<script type="module" src="/cards.js"></script>'''


def admission(op='ip1', conversation='c1'):
    return dict(interpretation_id=op, proposal_digest='digest-original', conversation_id=conversation,
                proposed={'outcome':'A useful result', 'scope':'Only this repository',
                          'task':{'outcome':'A useful result', 'done_when':'Tests pass', 'lead':'mc'}},
                sources=[{'content':'A useful result', 'source_event_id':'source-1'}])


@pytest.fixture
def ui():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('BETA_CHROMIUM_PATH'), headless=True)
        page = browser.new_page()
        state = {'cards': [admission()], 'posts': [], 'failure': None, 'read_fails': False, 'held': [], 'hold_next_get': False}
        def route(r):
            path = r.request.url.split('localhost')[-1]
            if path == '/cards.js': return r.fulfill(body=SCRIPT, content_type='text/javascript')
            if '/task-proposals' in path:
                if r.request.method == 'POST':
                    state['posts'].append((r.request.post_data_json, r.request.headers))
                    if state['failure'] == 'network': return r.abort()
                    if state['failure'] == 'malformed': return r.fulfill(body='null', content_type='application/json')
                    if state['failure']: return r.fulfill(status=409, json={'error':'changed','message':'This card changed. Read it again.'})
                    state['cards'] = []
                    return r.fulfill(json={'state':'confirmed','interpretation_id':'ip1','task_id':'TK-test'})
                if state['hold_next_get']:
                    state['hold_next_get'] = False
                    state['held'].append(r)
                    return
                if state['read_fails']: return r.fulfill(status=503, json={'message':'Could not check work cards.'})
                return r.fulfill(json={'proposals': state['cards']})
            return r.fulfill(body=HTML, content_type='text/html')
        page.route('**/*', route)
        page.goto('http://localhost/app/cos/ui')
        expect(page.locator('[data-tp-chat] [data-tp-confirm]')).to_be_visible()
        yield page, state
        browser.close()


def test_exact_confirmation_and_recorded_terms(ui):
    page, state = ui
    expect(page.locator('[data-tp-chat] h3')).to_have_text('A useful result')
    expect(page.locator('[data-tp-chat]')).to_contain_text('Tests pass')
    expect(page.locator('[data-tp-chat]')).to_contain_text('Only this repository')
    expect(page.locator('[data-tp-chat]')).to_contain_text('source-1')
    assert state['posts'] == []
    page.locator('[data-tp-confirm]').click()
    expect(page.locator('[data-tp-chat]')).to_contain_text('Confirmed; the task exists.')
    expect(page.locator('[data-tp-confirm]')).to_have_count(0)
    assert len(state['posts']) == 1
    body, headers = state['posts'][0]
    assert body == {'interpretation_id':'ip1','proposal_digest':'digest-original'}
    assert headers['x-csrf-token'] == 'synthetic-token'


@pytest.mark.parametrize('failure', ['changed', 'network', 'malformed'])
def test_uncertain_decision_requires_read_before_retry(ui, failure):
    page, state = ui
    state.update(failure=failure, read_fails=True)
    page.locator('[data-tp-confirm]').click()
    expect(page.locator('[data-tp-chat]')).to_contain_text('Could not check work cards.')
    expect(page.locator('[data-tp-confirm]')).to_have_count(0)
    assert 'Confirmed;' not in page.locator('[data-tp-chat]').inner_text()
    assert len(state['posts']) == 1


def test_finishing_and_incomplete_proposals(ui):
    page, state = ui
    state['cards'][0]['finishing'] = True
    page.locator('[data-tp-chat] [data-tp-refresh]').click()
    expect(page.locator('[data-tp-confirm]')).to_have_text('Finish confirmation')
    expect(page.locator('[data-tp-decline]')).to_have_count(0)
    del state['cards'][0]['proposed']['task']
    page.locator('[data-tp-chat] [data-tp-refresh]').click()
    expect(page.locator('[data-tp-chat]')).to_contain_text('This proposal is incomplete.')
    expect(page.locator('[data-tp-confirm]')).to_have_count(0)


def test_conversation_scope_and_safe_source_text(ui):
    page, state = ui
    state['cards'][0]['sources'][0]['content'] = '<img src=x onerror=alert(1)>'
    page.locator('[data-tp-chat] [data-tp-refresh]').click()
    expect(page.locator('[data-tp-chat] img')).to_have_count(0)
    page.evaluate("window.cosHistory.conversationId='other'; document.dispatchEvent(new Event('cos:conversation')); document.dispatchEvent(new Event('cos:conversation-selected'))")
    expect(page.locator('[data-tp-confirm]')).to_have_count(0)
    state['cards'][0]['conversation_id'] = None
    page.evaluate("document.dispatchEvent(new Event('cos:turn-completed'))")
    expect(page.locator('[data-tp-confirm]')).to_be_visible()
