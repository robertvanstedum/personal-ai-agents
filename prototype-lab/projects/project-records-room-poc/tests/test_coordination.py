from uuid import uuid4
import pytest
from app import create_app
from store import Problem


@pytest.fixture
def setup(tmp_path):
    app=create_app(tmp_path,testing=True);s=app.extensions['records_store'];q=app.extensions['coordination']
    def room(title):return s.create_room('robert',str(uuid4()),dict(title=title,purpose='Synthetic',mode='meeting',recording_acknowledged=True))['result']['id']
    work=room('Work');executive=room('Executive')
    token=s.add_principal('robert','new-reviewer',dict(id='reviewer',label='Reviewer'))['access_token']
    s.membership('robert','invite-reviewer',work,dict(actor='reviewer',role='contributor'))
    return app,s,q,work,executive,token


def test_review_lifecycle_identity_receipts_and_conflicts(setup):
    _,s,q,room,_,_=setup
    payload=dict(kind='review',title='Review candidate',body='Candidate manifest abc',assignee='reviewer')
    created=q.create('robert','request',room,payload);item=created['result'];assert item['state']=='requested'
    assert q.create('robert','request',room,payload)==created
    with pytest.raises(Problem):q.transition('robert','fake-pickup',room,item['id'],dict(action='pickup',body='Cannot impersonate reviewer',version=1))
    picked=q.transition('reviewer','pickup',room,item['id'],dict(action='pickup',body='Read manifest abc',version=1));assert picked['receipt']['actor']=='reviewer'
    with pytest.raises(Problem):q.transition('reviewer','stale',room,item['id'],dict(action='submit',body='Old version',version=1))
    q.transition('reviewer','result',room,item['id'],dict(action='submit',body='Review result and evidence',version=2))
    with pytest.raises(Problem):q.transition('reviewer','self-ack',room,item['id'],dict(action='acknowledge',body='Not requester',version=3))
    final=q.transition('robert','ack',room,item['id'],dict(action='acknowledge',body='Finding accepted',version=3))['result']
    assert final['state']=='acknowledged' and len(final['steps'])==4
    assert final['execution'].endswith('no runtime launched')


def test_owner_input_and_revocation(setup):
    _,s,q,room,other,_=setup
    item=q.create('reviewer','question',room,dict(kind='owner_input',title='Need direction',body='Choose a plan',assignee='robert'))['result']
    with pytest.raises(Problem):q.transition('reviewer','fake-answer',room,item['id'],dict(action='answer',body='Not owner',version=1))
    answered=q.transition('robert','answer',room,item['id'],dict(action='answer',body='Use plan A',version=1))['result']
    assert answered['state']=='result_submitted'
    with pytest.raises(Problem):q.list('reviewer',other)
    s.membership('robert','remove',room,dict(actor='reviewer',role='remove'))
    with pytest.raises(Problem):q.list('reviewer',room)
    with pytest.raises(Problem):q.transition('reviewer','late-ack',room,item['id'],dict(action='acknowledge',body='No access',version=2))


def test_executive_snapshot_is_explicit_immutable_and_source_untouched(setup):
    _,s,q,work,executive,_=setup
    event=s.append('robert','progress',work,dict(body='Work in progress'))['result']
    before=s.room('robert',work)['events']
    payload=dict(source=work,event_ids=[event['id']],disclosure_acknowledged=True)
    snap=q.snapshot('robert','brief',executive,payload)
    assert q.snapshot('robert','brief',executive,payload)==snap
    assert s.room('robert',work)['events']==before
    s.append('robert','later',work,dict(body='New work after briefing'))
    copied=q.list('robert',executive)['snapshots'][0]
    assert [r['text'] for r in copied['records']]==['Work in progress']
    with pytest.raises(Problem):q.snapshot('robert','no-consent',executive,{**payload,'disclosure_acknowledged':False})
    with pytest.raises(Problem):q.snapshot('reviewer','nonowner',executive,payload)
    with pytest.raises(Problem):q.snapshot('robert','wrong-source',executive,{**payload,'source':executive})


def test_authenticated_routes_and_idempotency(setup):
    app,s,q,room,_,token=setup;c=app.test_client()
    headers={'Authorization':'Bearer '+token,'Idempotency-Key':'api-question'}
    payload=dict(kind='owner_input',title='Question',body='Need input',assignee='robert')
    result=c.post(f'/api/v1/rooms/{room}/coordination',json=payload,headers=headers)
    assert result.status_code==201
    assert c.get(f'/api/v1/rooms/{room}/coordination',headers=headers).json['items'][0]['requester']=='reviewer'
    assert c.post(f'/api/v1/rooms/{room}/coordination',json=payload,headers=headers).json==result.json
    assert c.get(f'/api/v1/rooms/{room}/coordination').status_code==401


def test_inbox_and_linked_handoff(setup):
    _,s,q,work,executive,_=setup
    q.create('reviewer','ask-owner',work,dict(kind='owner_input',title='Input needed',body='Question',assignee='robert'))
    assert len(q.inbox('robert')['items'])==1
    assert not q.inbox('reviewer')['items']
    source=s.append('robert','source-record',work,dict(body='Source progress'))['result']
    snap=q.snapshot('robert','snapshot-for-handoff',executive,dict(source=work,event_ids=[source['id']],disclosure_acknowledged=True))['result']
    before=s.room('robert',work)['events']
    s.append('robert','exec-discussion',executive,dict(body='Discuss status separately'))
    assert s.room('robert',work)['events']==before
    item=q.create('robert','linked-handoff',work,dict(kind='handoff',title='Next step',body='Explicit direction',assignee='reviewer',source_snapshot=snap['snapshot_id']))['result']
    assert item['source_snapshot']==snap['snapshot_id'] and item['state']=='requested'
    assert q.inbox('reviewer')['items'][0]['id']==item['id']
    s.membership('robert','remove-inbox',work,dict(actor='reviewer',role='remove'))
    assert not q.inbox('reviewer')['items']


def test_native_cli_receipt_backed_coordination(setup,tmp_path):
    import socket,subprocess,sys,json
    from pathlib import Path
    from threading import Thread
    from werkzeug.serving import make_server
    _,store,q,work,_,token=setup
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    app=create_app(store.root,port=port,testing=True)
    server=make_server('127.0.0.1',port,app,threaded=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    key=tmp_path/'client.key';key.write_text(token);key.chmod(0o600)
    cli=Path(__file__).resolve().parents[1]/'roomctl.py'
    def run(*args):
        result=subprocess.run([sys.executable,str(cli),'--url',f'http://127.0.0.1:{port}','--token-file',str(key),*args],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)
    try:
        payload=tmp_path/'question.json';payload.write_text(json.dumps(dict(kind='owner_input',title='CLI question',body='Need owner input',assignee='robert')))
        sent=run('--operation-id','cli-question','request',work,str(payload))
        assert sent['receipt']['actor']=='reviewer'
        assert run('--operation-id','cli-question','request',work,str(payload))==sent
        assert run('receipt',work,'cli-question')==sent
        review=q.create('robert','cli-review',work,dict(kind='review',title='Review assigned',body='Inspect candidate',assignee='reviewer'))['result']
        assert run('inbox')['items'][0]['id']==review['id']
        payload.write_text(json.dumps(dict(action='pickup',body='Manifest received',version=1)))
        picked=run('--operation-id','cli-pickup','respond',work,review['id'],str(payload))
        assert picked['receipt']['actor']=='reviewer' and picked['result']['state']=='picked_up'
        assert run('coordination',work)['items'][0]['id']==review['id']
    finally:server.shutdown();thread.join(timeout=5)


def test_coordination_and_briefing_export_remain_valid(setup):
    from transcript_snapshot import capture
    from transcript_format import render
    _,s,q,work,executive,_=setup
    event=s.append('robert','export-source',work,dict(body='Exportable progress'))['result']
    q.create('robert','export-request',work,dict(kind='review',title='Review',body='Inspect progress',assignee='reviewer'))
    q.snapshot('robert','export-brief',executive,dict(source=work,event_ids=[event['id']],disclosure_acknowledged=True))
    for room,kind in [(work,'coordination'),(executive,'executive_snapshot')]:
        snapshot,stamp=capture(s,'robert',room)
        assert any(r['kind']==kind for r in snapshot['raw_transcript'])
        assert render(snapshot,snapshot_at=stamp)


def test_requests_require_serviceable_independent_recipient(setup):
    _,s,q,room,_,_=setup
    for actor,assignee in [('reviewer','reviewer'),('robert','robert'),('robert','cos-dev')]:
        with pytest.raises(Problem):
            q.create(actor,'invalid-'+actor+assignee,room,dict(kind='review',title='Review',body='Candidate',assignee=assignee))
    assert q.list('robert',room)['items']==[]


def test_cancelled_item_cannot_add_duplicate_history(setup):
    _,s,q,room,_,_=setup
    item=q.create('robert','cancel-target',room,dict(kind='review',title='Review',body='Candidate',assignee='reviewer'))['result']
    q.transition('robert','first-cancel',room,item['id'],dict(action='cancel',body='Cancelled',version=1))
    before=q.list('robert',room)
    with pytest.raises(Problem):q.transition('robert','repeat-cancel',room,item['id'],dict(action='cancel',body='Cancelled again',version=2))
    assert q.list('robert',room)==before


def test_briefing_text_in_all_exports_without_history_rewrite(setup):
    from transcript_snapshot import capture
    _,s,q,work,executive,_=setup
    event=s.append('reviewer','quoted-source',work,dict(body='Portable briefing contents'))['result']
    q.snapshot('robert','portable-brief',executive,dict(source=work,event_ids=[event['id']],disclosure_acknowledged=True))
    before=s.room('robert',executive)['events']
    assert 'Portable briefing contents' in s.transcript('robert',executive)
    exported=s.export('robert',executive)
    assert any('Portable briefing contents' in e['body'] for e in exported['events'])
    snapshot,_=capture(s,'robert',executive)
    record=next(r for r in snapshot['raw_transcript'] if r['kind']=='executive_snapshot')
    assert 'Portable briefing contents' in record['text'] and 'Quoted speaker: reviewer' in record['text']
    assert record['submitted_by']=='robert'
    assert s.room('robert',executive)['events']==before
