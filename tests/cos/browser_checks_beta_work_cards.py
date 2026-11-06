"""Real browser UI contracts with synthetic HTTP, no provider or owner writes."""
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / 'domains/cos/static/cos-work-cards.js').read_text()
HTML = '''<meta name="minimoi-csrf-token" content="synthetic-token">
<div id="chat-log"></div><div id="tab-flight"><section data-wc>
<button data-wc-refresh>Refresh</button><p data-wc-say hidden></p><div data-wc-list></div>
</section></div><script>window.cosHistory={conversationId:'c1'};
function cosWriteHeaders(){return {'X-CSRF-Token':'synthetic-token'}};</script>
<script type="module" src="/cards.js"></script>'''


def admission(op='op1', conversation='c1'):
    return dict(operation_id=op, task_id='TK-test', card_digest='digest-original',
                conversation_id=conversation, approval=None,
                card=dict(class_='build_only', repository='demo', ref='abc', attempts=1,
                          step_deadline_s=60, briefs={'build': 'Review the example'},
                          workers={'builder': {'actor': 'test', 'route': 'fakework'}}))


@pytest.fixture
def ui():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('BETA_CHROMIUM_PATH'), headless=True)
        page = browser.new_page()
        state = {'cards': [admission()], 'posts': [], 'failure': None, 'read_fails': False, 'held': [], 'hold_next_get': False}
        def route(r):
            path = r.request.url.split('localhost')[-1]
            if path == '/cards.js': return r.fulfill(body=SCRIPT, content_type='text/javascript')
            if '/work-cards' in path:
                if r.request.method == 'POST':
                    state['posts'].append((r.request.post_data_json, r.request.headers))
                    if state['failure'] == 'network': return r.abort()
                    if state['failure'] == 'malformed': return r.fulfill(body='null', content_type='application/json')
                    if state['failure']: return r.fulfill(status=409, json={'error':'changed','message':'This card changed. Read it again.'})
                    state['cards'][0]['approval'] = {'receipt': 'test'}
                    return r.fulfill(json={'state':'approved','operation_id':'op1','note':'Approved; awaiting admission.'})
                if state['hold_next_get']:
                    state['hold_next_get'] = False
                    state['held'].append(r)
                    return
                if state['read_fails']: return r.fulfill(status=503, json={'message':'Could not check work cards.'})
                return r.fulfill(json={'admissions': state['cards']})
            return r.fulfill(body=HTML, content_type='text/html')
        page.route('**/*', route)
        page.goto('http://localhost/app/cos/ui')
        expect(page.locator('[data-wc-chat] [data-wc-approve]')).to_be_visible()
        yield page, state
        browser.close()


def test_same_record_exact_digest_and_guard_in_both_views(ui):
    page, state = ui
    expect(page.locator('[data-wc-chat]')).to_contain_text('Simulated work')
    page.locator('[data-wc-chat] [data-wc-approve]').click()
    expect(page.locator('[data-wc-chat]')).to_contain_text('Approved; awaiting admission.')
    expect(page.locator('#tab-flight [data-wc-approve]')).to_be_disabled()
    expect(page.locator('[data-wc-chat] [data-wc-approve]')).to_be_disabled()
    assert len(state['posts']) == 1
    body, headers = state['posts'][0]
    assert body == {'operation_id':'op1','card_digest':'digest-original'}
    assert headers['x-csrf-token'] == 'synthetic-token'


@pytest.mark.parametrize('failure', ['changed', 'network', 'malformed'])
def test_failed_decision_never_claims_success_and_failed_read_removes_buttons(ui, failure):
    page, state = ui
    state.update(failure=failure, read_fails=True)
    page.locator('[data-wc-chat] [data-wc-approve]').click()
    expect(page.locator('[data-wc-chat]')).to_contain_text('Could not check work cards.')
    expect(page.locator('[data-wc-approve]')).to_have_count(0)
    assert len(state['posts']) == 1
    assert 'Approved;' not in page.locator('[data-wc-chat]').inner_text()
    state.update(failure=None, read_fails=False)
    page.locator('[data-wc-chat] [data-wc-refresh]').click()
    expect(page.locator('[data-wc-chat] [data-wc-approve]')).to_be_enabled()
    assert len(state['posts']) == 1


def test_conversation_filter_unassigned_fallback_and_history_redraw(ui):
    page, state = ui
    state['cards'] = [admission(), admission('op2','c2'), admission('unassigned',None)]
    page.evaluate("""() => {document.dispatchEvent(new CustomEvent('cos:conversation'));
      window.cosHistory.conversationId='c2';document.getElementById('chat-log').replaceChildren();
      document.dispatchEvent(new CustomEvent('cos:conversation-selected'));} """)
    expect(page.locator('[data-wc-chat] [data-wc-op]')).to_have_count(2)
    expect(page.locator('[data-wc-chat] [data-wc-op="op1"]')).to_have_count(0)
    expect(page.locator('#tab-flight [data-wc-op]')).to_have_count(3)
    page.evaluate("""() => {document.dispatchEvent(new CustomEvent('cos:turns-changed'));
      document.getElementById('chat-log').replaceChildren();} """)
    expect(page.locator('#chat-log [data-wc-op]')).to_have_count(2)
    assert not state['posts']


def test_switch_during_decision_reread_does_not_unlock_stale_card(ui):
    page, state = ui
    state['cards'].append(admission('op2','c2'))
    state['hold_next_get'] = True
    page.locator('[data-wc-chat] [data-wc-approve]').click()
    expect(page.locator('#tab-flight [data-wc-approve]')).to_be_disabled()
    for _ in range(100):
        if state['held']: break
        page.wait_for_timeout(20)
    assert state['held']
    page.evaluate("""() => {document.dispatchEvent(new CustomEvent('cos:conversation'));
      window.cosHistory.conversationId='c2';document.getElementById('chat-log').replaceChildren();
      document.dispatchEvent(new CustomEvent('cos:conversation-selected'));} """)
    assert state['held']
    state['held'].pop().fulfill(json={'admissions': []})
    expect(page.locator('[data-wc-chat] [data-wc-op="op2"]')).to_be_visible()
    expect(page.locator('#tab-flight [data-wc-approve="op1"]')).to_be_disabled()
    assert len(state['posts']) == 1


def test_new_card_follows_bottom_but_does_not_pull_reader_away(ui):
    page, state = ui
    page.evaluate('''() => {const log=document.getElementById('chat-log');
      log.style.cssText='height:200px;overflow:auto';
      const old=document.createElement('div');old.style.height='900px';old.textContent='Earlier conversation';
      log.prepend(old);log.scrollTop=0;}''')
    state['cards'][0]['card_digest'] = 'new-version'
    page.evaluate("document.dispatchEvent(new CustomEvent('cos:turn-completed'))")
    expect(page.locator('.wc-jump')).to_be_visible()
    assert page.locator('#chat-log').evaluate('(el)=>el.scrollTop') == 0
    page.locator('.wc-jump').click()
    expect(page.locator('.wc-jump')).to_be_hidden()
    state['cards'].append(admission('op3'))
    page.evaluate("document.dispatchEvent(new CustomEvent('cos:turn-completed'))")
    expect(page.locator('[data-wc-chat] [data-wc-op]')).to_have_count(2)
    assert page.locator('#chat-log').evaluate('(el)=>el.scrollHeight-el.clientHeight-el.scrollTop') < 2


def test_proposal_repeats_collapse_but_distinct_terms_remain(ui):
    page, state = ui
    source = (ROOT / 'minimoi_portal/guild_ui/static/js/tasks.js').read_text()
    source = source.replace("import { apiGet, apiPost } from './api.js';", '''
const proposal = {interpretation_id:'IN-test',proposal_digest:'digest',proposed:{outcome:'One outcome',
 task:{outcome:'One outcome',lead:'MC',holder:'CoS',done_when:'A distinct acceptance condition'},scope:'Personal'},
 sources:[{content:'One outcome',source_event_id:'source-1'}, {content:'A materially different source',source_event_id:'source-2'}]};
const apiGet=async()=>({ok:true,body:{proposals:[proposal],work:[]}});
const apiPost=async()=>{throw Error('No writes in this test')};
''')
    page.route('**/tasks.js', lambda r: r.fulfill(body=source + '\ninitTasks({});', content_type='text/javascript'))
    page.evaluate('''() => {document.body.innerHTML='<div data-task-view><p data-task-status></p><div data-task-proposals></div><div data-task-open></div><div data-task-detail><div data-task-record></div></div><button data-task-refresh>Refresh</button></div>';
      const script=document.createElement('script');script.type='module';script.src='/tasks.js';document.body.append(script);}''')
    expect(page.locator('[data-task-proposals] h3')).to_have_text('One outcome')
    expect(page.get_by_text('One outcome', exact=True)).to_have_count(1)
    expect(page.locator('[data-task-proposals]')).to_contain_text('A distinct acceptance condition')
    page.get_by_text('Source words and references', exact=True).click()
    expect(page.get_by_text('A materially different source', exact=True)).to_be_visible()
    expect(page.get_by_text('Same wording as shown above.', exact=True)).to_be_visible()
    assert not state['posts']


@pytest.mark.parametrize('width', [1440,720,390])
def test_rendered_cos_composer_geometry(ui, width):
    import re
    from jinja2 import Environment, FileSystemLoader, ChoiceLoader, PrefixLoader
    page, state = ui
    env = Environment(loader=ChoiceLoader([FileSystemLoader(ROOT / 'domains/cos/templates'),
        PrefixLoader({'founders_ui': FileSystemLoader(ROOT / 'core/founders_ui/templates')})]))
    html = env.get_template('cos_ui.html').render(initial_tab='confer', founders_ui_version='test', managed_realtime_ready=False)
    html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
    html = html.replace('data-picture="shown"', 'data-picture="shown" data-cos-tab="confer"')
    html = html.replace('id="tab-confer" class="tab-panel"', 'id="tab-confer" class="tab-panel active"')
    def assets(r):
        path = r.request.url.split('localhost')[-1].split('?')[0]
        if path.startswith('/founders-ui/'):
            f = ROOT / 'core/founders_ui/static' / path.removeprefix('/founders-ui/')
        else:
            f = ROOT / 'domains/cos/static' / path.removeprefix('/static/')
        if f.is_file(): return r.fulfill(path=str(f))
        r.fulfill(status=404)
    page.route('**/static/**', assets)
    page.route('**/founders-ui/**', assets)
    page.route('**/cos-render', lambda r:r.fulfill(body=html,content_type='text/html'))
    page.set_viewport_size({'width':width,'height':844})
    page.goto('http://localhost/cos-render')
    expect(page.locator('#send-input')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    bounds = page.locator('#btn-send').bounding_box()
    assert bounds['height'] >= (44 if width < 640 else 38)
    page.screenshot(path=f'/private/tmp/beta-cos-{width}.png')
    assert not state['posts']
