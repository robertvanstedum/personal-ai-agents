from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from flask import Flask
from core.canonical_json import digest
from services.partnership_tasks.helper_return import displayed_text
from core.lead_wake.publication import Publication
from core.lead_wake.inbox import Inbox,StaleWake
from core.lead_wake.hosts import CoSHost
from core.lead_wake.lane import Lane
from core.lead_wake.transport import blueprint,Port
from domains.cos.history import History
from tests.cos.test_history import send


def rig(tmp_path):
    history=History(tmp_path/'history','dev')
    cid=history.create('Return test')['conversation_id']
    tid='t-000000000001'
    history.begin(send(cid,tid));history.answer(cid,tid,'I asked the EA.')
    source={'store':'cos-history','id':cid+':'+tid,'position':1}
    wake='OB-wake-'+'a'*40
    op='OB-publish-'+'a'*40
    review={'verdict':'approve','reasons':'Checked source','answer':'EA found the reminder.'}
    packet=dict(kind='helper_return_committed',wake_operation_id=wake,task_id='TK-test',lead='cos',principal='robert',environment='dev',data_scope='personal',source_event_id='SE-owner',source_ref=source,conversation_id=cid,source_turn_id=tid,store='cos-history',id='consult:'+op,created_at='2026-11-11T01:00:00+00:00',result_digest='sha256:'+'b'*64,review=review,review_digest=digest(review),drafted_by='ea',text=displayed_text('cos','ea',review['answer']))
    row=dict(operation_id=op,operation='publish_return',destination='lead_publication',packet=packet,bundle_digest=digest(packet),task_id='TK-test',state='attempted',attempts=[{'observation':'attempted'}])
    reader=SimpleNamespace(get=lambda kind,key:row if kind=='outbox' else dict(source=source,data_scope='personal',provenance={'origin':'platform'}))
    inbox=Inbox(tmp_path/'inbox.sqlite',lambda _:dict(lead='cos',result_digest=packet['result_digest']))
    with inbox._db() as db:
        import json
        db.execute('INSERT INTO entries VALUES(?,?,?,?)',(wake,'x',json.dumps(dict(lead='cos',result_digest=packet['result_digest'])),'reviewed'))
    allowed=[True];settled=[True]
    host=CoSHost(history=history,lane=Lane(tmp_path/'lanes',environment='dev',lead='cos'),principal='robert',environment='dev',access=lambda _:allowed[0],settled=lambda _:settled[0])
    dest=Publication(journal=inbox,reader=reader,host=host,principal='robert',lead='cos',environment='dev')
    return SimpleNamespace(dest=dest,row=row,p=packet,op=op,inbox=inbox,history=history,allowed=allowed,settled=settled)


def deliver(r):return r.dest.deliver(r.op,r.row['bundle_digest'],task_id='TK-test',kind='helper_return_committed',attempt=1)


def test_real_cos_history_readback_retry_and_lost_ack(tmp_path):
    r=rig(tmp_path)
    first=deliver(r)
    assert deliver(r)==first
    assert r.dest.lookup(r.op,attempt=1)==dict(state='applied',**first)
    assert len(r.history.turns(r.p['conversation_id'])['notes'])==1
    assert r.inbox.status(r.p['wake_operation_id'])=='presented'


def test_absence_fences_delayed_attempt_and_pending_not_deliverable(tmp_path):
    r=rig(tmp_path)
    assert r.dest.lookup(r.op,attempt=1)=={'state':'absent'}
    with pytest.raises(StaleWake):deliver(r)
    r.row['state']='pending'
    with pytest.raises(StaleWake):r.dest.lookup(r.op,attempt=2)
    assert not r.history.turns(r.p['conversation_id'])['notes']


@pytest.mark.parametrize('gate',['access','settled','private','pending'])
def test_disclosure_and_owner_priority_gates(tmp_path,gate):
    r=rig(tmp_path)
    if gate=='access':r.allowed[0]=False
    if gate=='settled':r.settled[0]=False
    if gate=='private':
        from core import durable_files as fsio
        c=r.history.conversation(r.p['conversation_id']);c['private']=True
        fsio.write_doc(r.history._conversation_path(c['conversation_id']),c)
    if gate=='pending':r.history.begin(send(r.p['conversation_id'],'t-000000000002'))
    with pytest.raises((PermissionError,RuntimeError)):deliver(r)
    assert not r.history.turns(r.p['conversation_id'])['notes']


def test_endpoint_authentication_and_no_model_selected_text(tmp_path):
    r=rig(tmp_path);app=Flask(__name__)
    app.register_blueprint(blueprint(token='secret',inbox=r.inbox,publication=r.dest))
    c=app.test_client();path='/internal/lead-return/v1/publication/deliver'
    body=dict(operation_id=r.op,payload_digest=r.row['bundle_digest'],task_id='TK-test',kind='helper_return_committed',attempt=1)
    assert c.post(path,json=body).status_code==401
    assert c.post(path,json=dict(body,text='injected'),headers={'Authorization':'Bearer secret'}).status_code==400
    assert c.post(path,json=body,headers={'Authorization':'Bearer secret'}).status_code==200
