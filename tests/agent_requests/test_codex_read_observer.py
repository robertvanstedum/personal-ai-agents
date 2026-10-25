"""Synthetic protocol only: never start the real desktop or invoke its queue."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import pytest

from core.agent_requests.codex_read_observer import Observer, ReadEnrollment, baseline_for, correlate
from core.agent_requests.direct_contact import Refused
from tests.agent_requests.test_codex_desktop_queue import TARGET, IDENTITY

ENROLLMENT = ReadEnrollment(TARGET, IDENTITY, '/synthetic/.codex')
CORR = 'c' * 48


def fixture():
    before = {'thread_id': TARGET.session_id, 'target_revision': TARGET.revision,
              'observed_at': 100, 'turns': [{'id': 't1', 'items': [{'id': 'old', 'type':'userMessage', 'sha256': 'a' * 64}]}]}
    baseline = baseline_for(before)
    after = {'thread_id': TARGET.session_id, 'target_revision': TARGET.revision, 'observed_at': 200,
             'turns': [{'id': 't1', 'status': 'completed', 'startedAt': 90, 'completedAt': 190,
                       'items': [{'id': 'old', 'type': 'agentMessage', 'sha256': 'a' * 64},
                                 {'id': 'u', 'type': 'userMessage', 'sha256': 'b' * 64, 'text': 'exact'},
                                 {'id': 'a', 'type': 'agentMessage', 'sha256': 'd' * 64,
                                  'phase': 'final_answer', 'text': 'MC-RESULT:' + CORR + '\nReported blocked.'}]}]}
    return baseline, after


def test_correlated_final_is_report_not_success():
    baseline, after = fixture()
    result = correlate(after, baseline, 'exact', CORR)
    assert result['state'] == 'result' and result['report'] == 'Reported blocked.'
    assert result['turn_id'] == 't1' and result['result_id'] == 'a'


def test_active_tool_progress_can_finish_without_becoming_a_correlated_reply():
    baseline,after=fixture()
    baseline['items']['old-tool']=None
    after['turns'][0]['items'].insert(1,{'id':'old-tool','type':'commandExecution','sha256':'e'*64})
    assert correlate(after,baseline,'exact',CORR)['state']=='result'
    # A mutable preexisting agent item cannot become the new correlated final.
    baseline['items']['a']=None
    assert correlate(after,baseline,'exact',CORR)['state']=='delivered'


@pytest.mark.parametrize('change', ['wrong_role', 'quote', 'wrong_phase', 'null_completed', 'interrupted',
                                  'before_input', 'wrong_correlation', 'future_time', 'bool_time'])
def test_incomplete_or_unrelated_native_data_is_not_completion(change):
    baseline, after = fixture()
    turn = after['turns'][0]
    final = turn['items'][2]
    if change == 'wrong_role': final['type'] = 'functionCallOutput'
    elif change == 'quote': final['text'] = 'Quoted:\n' + final['text']
    elif change == 'wrong_phase': final['phase'] = 'commentary'
    elif change == 'null_completed': turn['completedAt'] = None
    elif change == 'interrupted': turn['status'] = 'interrupted'
    elif change == 'before_input': turn['items'][1:] = reversed(turn['items'][1:])
    elif change == 'wrong_correlation': final['text'] = 'MC-RESULT:' + 'e' * 48 + '\nwrong'
    elif change == 'future_time': turn['completedAt'] = 300
    elif change == 'bool_time': turn['startedAt'] = True
    assert correlate(after, baseline, 'exact', CORR)['state'] == 'delivered'


@pytest.mark.parametrize('change', ['duplicate', 'changed_old', 'missing_old', 'wrong_thread', 'wrong_revision'])
def test_ambiguous_or_changed_source_refused(change):
    baseline, after = fixture()
    if change == 'duplicate': after['turns'][0]['items'].append(dict(after['turns'][0]['items'][1], id='u2'))
    elif change == 'changed_old': after['turns'][0]['items'][0]['sha256'] = 'z' * 64
    elif change == 'missing_old': after['turns'][0]['items'].pop(0)
    elif change == 'wrong_thread': after['thread_id'] = 'other'
    elif change == 'wrong_revision': after['target_revision'] = 'other'
    with pytest.raises(Refused): correlate(after, baseline, 'exact', CORR)


def test_input_persistence_is_not_pickup_but_native_ack_is():
    baseline, after = fixture()
    after['turns'][0]['items'].pop()
    assert correlate(after, baseline, 'exact', CORR)['state'] == 'delivered'
    after['turns'][0]['items'].append({'id': 'ack', 'type': 'agentMessage', 'sha256': 'e'*64,
                                     'text': 'MC-ACK:' + CORR})
    assert correlate(after, baseline, 'exact', CORR)['state'] == 'acknowledged'


class Reader:
    def __init__(self, *, wrong_cwd=False, infinite=False):
        self.calls, self.wrong_cwd, self.infinite = [], wrong_cwd, infinite

    @contextmanager
    def session(self):
        def call(method, args):
            self.calls.append((method, args))
            if method == 'thread/read':
                return {'thread': {'id': TARGET.session_id, 'cwd': 'wrong' if self.wrong_cwd else TARGET.cwd,
                                   'status': {'type': 'notLoaded'}}}
            if method == 'thread/turns/list':
                return {'data': [{'id': 't1', 'status': 'completed', 'startedAt': 10, 'completedAt': 20}], 'nextCursor': None}
            n = len([m for m, _ in self.calls if m == 'thread/items/list'])
            return {'data': [{'turnId': 't1', 'item': {'id': 'u' + str(n), 'type': 'userMessage',
                           'content': [{'type': 'text', 'text': 'bounded'}]}}],
                    'nextCursor': str(n) if self.infinite or n == 1 else None}
        yield call


def test_observer_pages_items_in_order_and_never_loads_worker():
    reader = Reader()
    snapshot = Observer(ENROLLMENT, reader=reader).snapshot()
    assert [i['id'] for i in snapshot['turns'][0]['items']] == ['u1', 'u2']
    assert [m for m, _ in reader.calls] == ['thread/read', 'thread/turns/list', 'thread/items/list', 'thread/items/list']
    assert reader.calls[-1][1]['cursor'] == '1'
    assert all(a.get('sortDirection') == 'asc' for m, a in reader.calls if m == 'thread/items/list')


def test_changed_thread_cwd_refuses_before_reading_history():
    reader = Reader(wrong_cwd=True)
    with pytest.raises(Refused, match='native_thread_binding_changed'):
        Observer(ENROLLMENT, reader=reader).snapshot()
    assert len(reader.calls) == 1


def test_incomplete_pagination_refuses_instead_of_truncating():
    with pytest.raises(Refused, match='native_history_page_bound'):
        Observer(ENROLLMENT, reader=Reader(infinite=True)).snapshot()


@pytest.mark.parametrize('mode',['normal','server_request','wrong_home','oversize','eof','timeout'])
def test_stdio_fixed_launch_bounds_refusals_and_reaps_only_reader(tmp_path,monkeypatch,mode):
    import subprocess,sys
    from core.agent_requests import codex_read_observer as R
    monkeypatch.setattr(R,'_binary',lambda p:IDENTITY)
    script=tmp_path/'synthetic_rpc.py'
    script.write_text('''import sys,json,time
mode=sys.argv[1]
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r:continue
 if mode=='timeout':time.sleep(3);continue
 if mode=='eof':break
 if mode=='oversize':print('x'*600000,flush=True);continue
 if mode=='server_request':print(json.dumps({'id':99,'method':'item/commandExecution/requestApproval','params':{}}),flush=True);continue
 result={'codexHome':'wrong' if mode=='wrong_home' else '/synthetic/.codex'}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
''')
    processes=[]
    def popen(argv,**kwargs):
        assert argv==[R.BUNDLED_BINARY,'app-server','--listen','stdio://']
        assert kwargs['cwd']==TARGET.cwd
        assert set(kwargs['env'])=={'HOME','CODEX_HOME','PATH','LANG'}
        kwargs['cwd']=str(tmp_path)
        p=subprocess.Popen([sys.executable,'-I',str(script),mode],**kwargs)
        processes.append(p)
        return p
    reader=R.StdioReader(ENROLLMENT,timeout=.3,popen=popen)
    if mode=='normal':
        with reader.session() as call:
            with pytest.raises(Refused,match='native_method_forbidden'):call('thread/start',{})
            assert call('thread/read',{'threadId':TARGET.session_id})['codexHome']=='/synthetic/.codex'
    else:
        with pytest.raises(Refused):
            with reader.session():pass
    assert len(processes)==1 and processes[0].poll() is not None


@pytest.mark.parametrize('count,expected', [(120, 'complete'), (800, 'native_read_bound')])
def test_small_wire_pages_preserve_full_baseline_and_aggregate_bound(tmp_path, monkeypatch, count, expected):
    """100 ordinary tool outputs exceed one line;10 fit without dropping IDs."""
    import json
    import subprocess
    import sys
    from core.agent_requests import codex_read_observer as R
    monkeypatch.setattr(R, '_binary', lambda p: IDENTITY)
    entries = [{'turnId': 't1', 'item': {'id': 'tool-' + str(n), 'type': 'commandExecution',
                'aggregatedOutput': 'x' * 6000}} for n in range(count)]
    assert len(json.dumps({'id': 4, 'result': {'data': entries[:100]}}).encode()) > R.MAX_LINE
    payload = tmp_path / 'entries.json'
    payload.write_text(json.dumps(entries))
    script = tmp_path / 'paged_rpc.py'
    script.write_text('''import json,sys
entries=json.load(open(sys.argv[1]))
for line in sys.stdin:
 r=json.loads(line)
 if 'id' not in r:continue
 m,a=r['method'],r['params']
 if m=='initialize':result={'codexHome':'/synthetic/.codex'}
 elif m=='thread/read':result={'thread':{'id':a['threadId'],'cwd':sys.argv[2]}}
 elif m=='thread/turns/list':result={'data':[{'id':'t1','status':'completed','startedAt':10,'completedAt':20}],'nextCursor':None}
 else:
  assert m=='thread/items/list' and a['sortDirection']=='asc'
  start=int(a.get('cursor','0'));end=start+a['limit']
  result={'data':entries[start:end],'nextCursor':str(end) if end<len(entries) else None}
 print(json.dumps({'id':r['id'],'result':result}),flush=True)
''')
    processes = []
    def popen(argv, **kwargs):
        kwargs['cwd'] = str(tmp_path)
        process = subprocess.Popen([sys.executable, '-I', str(script), str(payload), TARGET.cwd], **kwargs)
        processes.append(process)
        return process
    reader = R.StdioReader(ENROLLMENT, popen=popen)
    observer = Observer(ENROLLMENT, reader=reader)
    if expected == 'complete':
        snapshot = observer.snapshot()
        baseline = baseline_for(snapshot)
        assert set(baseline['items']) == {'tool-' + str(n) for n in range(count)}
        assert all(value is None for value in baseline['items'].values())
        refreshed = observer.snapshot(baseline)
        assert correlate(refreshed, baseline, 'no queued message', CORR)['state'] == 'unverified'
        refreshed['turns'][0]['items'].pop(50)
        with pytest.raises(Refused, match='native_source_missing'):
            correlate(refreshed, baseline, 'no queued message', CORR)
    else:
        with pytest.raises(Refused, match=expected):
            observer.snapshot()
    assert all(p.poll() is not None for p in processes)
    assert R.MAX_BYTES == 4 * 1024 * 1024 and R.MAX_LINE == 512 * 1024
    assert R.ITEM_PAGE_SIZE * R.MAX_ITEM_PAGES == 1600
