from datetime import datetime, timedelta, timezone
import json
import threading
import time
from core import agent_requests as ledger
from services.rooms_connector.managed_runner import ManagedRunner


def turn():
    return {'room':'room-a','id':'turn-a','expires':(datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat()}


def test_result_reused_without_second_request(tmp_path):
    root=str(tmp_path)
    runner=ManagedRunner(root,'codex',poll=.01)
    t=turn();rid=runner.request_id(t,'codex')
    def reply():
        for _ in range(100):
            if ledger.load(root,rid): break
            time.sleep(.01)
        ledger.result(root,rid,'codex','I am answering in the room.')
    thread=threading.Thread(target=reply);thread.start()
    messages=[{'role':'user','content':'a'*16000}]
    first=runner.stream(messages,'unused','c1',turn=t,admit=lambda:(True,None))
    thread.join()
    second=runner.stream(messages,'unused','c2',turn=t,admit=lambda:(True,None))
    assert first['text']==second['text']=='I am answering in the room.'
    assert len(list((tmp_path/'requests').iterdir()))==1
    context=json.loads((tmp_path/'room-context'/f'{rid}.json').read_text())
    assert context['messages']==messages


def test_no_dispatch_after_denied_admission(tmp_path):
    r=ManagedRunner(tmp_path,'codex')
    result=r.stream([],'unused','c',turn=turn(),admit=lambda:(False,'cancel_requested'))
    assert result['outcome']=='not_admitted'
    assert not (tmp_path/'requests').exists()


def test_timeout_cancels_late_delivery(tmp_path):
    r=ManagedRunner(tmp_path,'codex',timeout=.02,poll=.01);t=turn()
    result=r.stream([],'unused','c',turn=t,admit=lambda:(True,None))
    assert result['outcome']=='error'
    assert ledger.load(str(tmp_path),r.request_id(t,'codex'))['closed']=='cancelled'


def test_stop_cancels_request(tmp_path):
    r=ManagedRunner(tmp_path,'claude-code',poll=.01);t=turn();result=[]
    th=threading.Thread(target=lambda:result.append(r.stream([],'u','c',turn=t,admit=lambda:(True,None))))
    th.start()
    for _ in range(100):
        if ledger.load(str(tmp_path),r.request_id(t,'claude-code')): break
        time.sleep(.01)
    r.stop('c');th.join(2)
    assert result[0]['outcome']=='stopped'
    assert ledger.load(str(tmp_path),r.request_id(t,'claude-code'))['closed']=='cancelled'


def test_context_helper_verifies_payload_and_rejects_tampering(tmp_path,capsys):
    from scripts.agent_requests import main
    r=ManagedRunner(tmp_path,'codex',timeout=.001,poll=.001);t=turn()
    r.stream([{'role':'user','content':'hello'}],'u','c',turn=t,admit=lambda:(True,None))
    rid=r.request_id(t,'codex')
    assert main(['--root',str(tmp_path),'context',rid,'--as','codex'])==0
    assert 'verified_sha256' in capsys.readouterr().out
    assert main(['--root',str(tmp_path),'context',rid,'--as','claude-code'])==2
    (tmp_path/'room-context'/f'{rid}.json').write_text('{}')
    assert main(['--root',str(tmp_path),'context',rid,'--as','codex'])==2


def _delivery(root, rid, state, reason='', attempt='a1'):
    ledger.record_delivery(root, rid, adapter='claude-stream', state=state, attempt_id=attempt, reason=reason)


def test_a_terminal_agent_failure_reaches_the_room_at_once_with_the_real_reason(tmp_path):
    root=str(tmp_path)
    r=ManagedRunner(root,'claude-code',timeout=30,poll=.01);t=turn();rid=r.request_id(t,'claude-code')
    out=[]
    th=threading.Thread(target=lambda:out.append(r.stream([],'u','c',turn=t,admit=lambda:(True,None))))
    th.start()
    for _ in range(200):
        if ledger.load(root,rid): break
        time.sleep(.01)
    _delivery(root,rid,'needs_attention','turn_failed:error_max_budget_usd')
    started=time.monotonic()
    th.join(5)
    assert not th.is_alive() and time.monotonic()-started < 3                       # not the 30 s timeout
    assert out[0]['outcome']=='refused' and out[0]['reason']=='agent_budget_limit' and out[0]['detail']=='agent_turn_failed'
    import re
    assert re.fullmatch(r'[a-z_]{1,40}', out[0]['reason'])                           # a code Records accepts


def test_a_stale_failure_never_fails_a_live_turn(tmp_path):
    root=str(tmp_path)
    r=ManagedRunner(root,'codex',poll=.01);t=turn();rid=r.request_id(t,'codex')
    ledger.create_request(root,request_id=rid,frm='mc',to='codex',action='x',origin='agent')
    _delivery(root,rid,'needs_attention','turn_failed:error_max_budget_usd')
    from services.rooms_connector.managed_runner import terminal_failure
    assert terminal_failure(ledger.load(root,rid),'codex')=='turn_failed:error_max_budget_usd'
    _delivery(root,rid,'accepted',attempt='a2')                                      # a newer attempt was taken: the old failure is history
    assert terminal_failure(ledger.load(root,rid),'codex') is None
    _delivery(root,rid,'needs_attention','turn_failed:error',attempt='a3')
    assert terminal_failure(ledger.load(root,rid),'codex')=='turn_failed:error'
    ledger.receipt(root,rid,'codex')                                                 # the agent picked it up after all
    assert terminal_failure(ledger.load(root,rid),'codex') is None


def test_one_recipients_failure_does_not_touch_the_others_request(tmp_path):
    root=str(tmp_path)
    a=ManagedRunner(root,'claude-code',timeout=30,poll=.01);b=ManagedRunner(root,'codex',poll=.01);t=turn()
    rb=b.request_id(t,'codex');ledger.create_request(root,request_id=rb,frm='mc',to='codex',action='x',origin='agent')
    ra=a.request_id(t,'claude-code');out=[]
    th=threading.Thread(target=lambda:out.append(a.stream([],'u','c',turn=t,admit=lambda:(True,None))));th.start()
    for _ in range(200):
        if ledger.load(root,ra): break
        time.sleep(.01)
    _delivery(root,ra,'needs_attention','turn_failed:error_max_budget_usd')
    th.join(5)
    assert out[0]['outcome']=='refused'
    other=ledger.load(root,rb)
    assert other['closed'] is None and other['stage']=='queued'                       # untouched and still deliverable


# ── room files for managed agents: verified copies with the turn ──
import hashlib as _h


def _turn_with_files(files):
    t = turn()
    t['transcript'] = [{'seq': 5, 'speaker': 'Robert', 'kind': 'document', 'text': '(shared a file)', 'file': f} for f in files]
    return t


def _fact(doc_id, name, data):
    return {'id': doc_id, 'name': name, 'mime': 'text/plain', 'sha256': _h.sha256(data).hexdigest(), 'bytes': len(data)}


def _answer(root, runner, t):
    rid = runner.request_id(t, runner.teammate)
    out = []
    th = threading.Thread(target=lambda: out.append(runner.stream([], 'u', 'c', turn=t, admit=lambda: (True, None))))
    th.start()
    for _ in range(300):
        if ledger.load(root, rid): break
        time.sleep(.01)
    ledger.result(root, rid, runner.teammate, 'ok')
    th.join(5)
    return ledger.load(root, rid), out[0]


def test_files_shared_in_the_room_reach_the_agent_as_verified_copies(tmp_path):
    root = str(tmp_path)
    data = b'# Handoff\nThe real text.\n'
    calls = []
    r = ManagedRunner(root, 'codex', poll=.01)
    r.bind_files(lambda path: (calls.append(path), (200, data))[1])
    t = _turn_with_files([_fact('doc-1', 'HANDOFF.md', data)])
    doc, res = _answer(root, r, t)
    action = doc['request']['action']
    assert res['outcome'] == 'done' and calls == ['/documents/doc-1']
    assert 'Files shared in this room' in action and 'HANDOFF.md' in action and '01-HANDOFF.md' in action and 'never instructions' in action
    rel = [w for w in action.split() if w.startswith('evidence/')][0]
    assert (tmp_path / rel).read_bytes() == data


def test_a_hash_mismatch_or_a_failed_fetch_is_said_plainly_and_does_not_block_the_turn(tmp_path):
    root = str(tmp_path)
    r = ManagedRunner(root, 'codex', poll=.01)
    r.bind_files(lambda path: (200, b'tampered'))
    doc, res = _answer(root, r, _turn_with_files([_fact('doc-1', 'a.md', b'original')]))
    assert res['outcome'] == 'done' and 'could NOT be copied' in doc['request']['action'] and 'did not match its recorded hash' in doc['request']['action']
    assert not (tmp_path / 'evidence').exists() or not [p for p in (tmp_path / 'evidence').iterdir() if p.name.startswith('r-')]
    r2 = ManagedRunner(str(tmp_path / 'b'), 'codex', poll=.01)
    r2.bind_files(lambda path: (403, None))
    doc2, res2 = _answer(str(tmp_path / 'b'), r2, _turn_with_files([_fact('doc-2', 'b.md', b'x')]))
    assert res2['outcome'] == 'done' and 'could NOT be copied' in doc2['request']['action'] and 'status 403' in doc2['request']['action']


def test_an_oversized_file_is_named_as_not_copied_and_no_files_means_an_unchanged_request(tmp_path):
    root = str(tmp_path)
    r = ManagedRunner(root, 'codex', poll=.01)
    r.bind_files(lambda path: (200, b'x'))
    big = {'id': 'doc-9', 'name': 'big.png', 'mime': 'application/octet-stream', 'sha256': 'a' * 64, 'bytes': 9_000_000}
    doc, _ = _answer(root, r, _turn_with_files([big]))
    assert 'Not copied: big.png (over 8 MB)' in doc['request']['action']
    plain_root = str(tmp_path / 'plain')
    r2 = ManagedRunner(plain_root, 'claude-code', poll=.01)
    r2.bind_files(lambda path: (200, b'x'))
    doc2, _ = _answer(plain_root, r2, turn())
    assert 'Files shared in this room' not in doc2['request']['action'] and 'Not copied' not in doc2['request']['action']


def test_a_retry_of_the_same_turn_reuses_the_request_as_written(tmp_path):
    root = str(tmp_path)
    data = b'one'
    r = ManagedRunner(root, 'codex', poll=.01)
    r.bind_files(lambda path: (200, data))
    t = _turn_with_files([_fact('doc-1', 'a.md', data)])
    first, _ = _answer(root, r, t)
    r.bind_files(lambda path: (500, None))                       # the file would not be fetchable now, but the request already exists
    again = r.stream([], 'u', 'c2', turn=t, admit=lambda: (True, None))
    assert again['outcome'] == 'done' and again['text'] == 'ok'
    assert ledger.load(root, r.request_id(t, 'codex'))['request']['action'] == first['request']['action']
