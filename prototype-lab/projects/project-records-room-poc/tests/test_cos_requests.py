from uuid import uuid4
import pytest
from test_cos_agent_responder import agent_setup
from test_cos_records_bridge import bridge
from cos_requests import CoSRequests
from cos_agent_responder import respond
from store import Problem


def service(s):return CoSRequests(s.store,[s.room])
def runner(s):
    return lambda room,key,action,guard:respond(room,key,client=s.client,model=s.model,journal=s.journal,policy=s.policy,owner_authorized=True,action=action,expected_guard=guard)


def test_explicit_request_to_saved_reply_and_replay(agent_setup):
    s=agent_setup;q=service(s);key=str(uuid4())
    assert q.submit('robert',s.room,key,{'action':'contribute'})['state']=='queued'
    assert not s.calls
    assert q.run_once(runner(s))
    result=q.status('robert',s.room)['requests'][0]
    assert result['state']=='committed' and result['result']['operation']['receipt_id']
    assert s.store.room('robert',s.room)['events'][-1]['actor']=='cos-dev'
    assert q.submit('robert',s.room,key,{'action':'contribute'})==result
    assert not q.run_once(runner(s)) and len(s.calls)==1


def test_access_allowlist_and_request_conflict(agent_setup):
    s=agent_setup;q=service(s);key=str(uuid4())
    with pytest.raises(Problem):q.submit('cos-dev',s.room,key,{'action':'contribute'})
    with pytest.raises(Problem):CoSRequests(s.store).submit('robert',s.room,key,{'action':'contribute'})
    q.submit('robert',s.room,key,{'action':'contribute'})
    with pytest.raises(Problem):q.submit('robert',s.room,key,{'action':'brief'})
    with pytest.raises(Problem):q.submit('robert',s.room,str(uuid4()),{'action':'contribute'})
    assert not s.calls


def test_changed_context_cancels_without_inference(agent_setup):
    s=agent_setup;q=service(s)
    q.submit('robert',s.room,str(uuid4()),{'action':'contribute'})
    s.store.append('robert','newer',s.room,{'body':'New question'})
    q.run_once(runner(s))
    assert q.status('robert',s.room)['requests'][0]['state']=='cancelled'
    assert not s.calls


def test_uncertain_and_restart_never_repeat_inference(agent_setup):
    s=agent_setup;q=service(s);key=str(uuid4())
    q.submit('robert',s.room,key,{'action':'contribute'})
    calls=[]
    def timeout(*args):calls.append(1);raise TimeoutError('sensitive upstream text')
    q.run_once(timeout)
    assert q.status('robert',s.room)['requests'][0]['state']=='uncertain'
    assert not q.run_once(timeout)
    with pytest.raises(Problem):q.submit('robert',s.room,str(uuid4()),{'action':'contribute'})
    assert calls==[1]
    with s.store.connect() as db:db.execute("UPDATE cos_requests SET state='running'")
    q.recover_interrupted()
    assert q.status('robert',s.room)['requests'][0]['state']=='uncertain'


def test_http_owner_gate_and_scoped_credential_receipt(agent_setup):
    from datetime import datetime,timedelta,timezone
    s=agent_setup;app=s.client.session.app.application
    app.extensions['cos_requests'].allowed=frozenset([s.room])
    http=app.test_client();url=f'/api/v1/rooms/{s.room}/cos-requests';key=str(uuid4())
    assert http.post(url,json={'action':'contribute'}).status_code==401
    assert http.post(url,headers={'Authorization':'Bearer '+s.client.token,'Idempotency-Key':key},json={'action':'contribute'}).status_code==403
    cred=s.store.platform_access.issue('robert',dict(principal='cos-dev',label='Scoped CoS test',expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),grants={s.room:['read','post','receipt']}))
    s.client.token=cred['access_token']
    headers={'Authorization':'Bearer '+s.store.owner_key,'Idempotency-Key':key}
    assert http.post(url,headers=headers,json={'action':'contribute'}).status_code==202
    app.extensions['cos_requests'].run_once(runner(s))
    assert http.get(url,headers=headers).json['requests'][0]['state']=='committed'


def test_reconcile_saved_reply_never_calls_model_again(agent_setup):
    s=agent_setup;q=service(s);key=str(uuid4())
    q.submit('robert',s.room,key,{'action':'contribute'})
    def lost_ack(*args):
        runner(s)(*args)
        raise TimeoutError('lost result after commit')
    q.run_once(lost_ack)
    assert len(s.calls)==1 and q.status('robert',s.room)['requests'][0]['state']=='uncertain'
    q.reconcile('robert',s.room,key)
    def forbidden(*args):raise AssertionError('must not infer')
    def recover(room,key,action):
        return respond(room,key,client=s.client,model=forbidden,journal=s.journal,policy=s.policy,owner_authorized=True,action=action)
    q.run_once(forbidden,recover)
    assert q.status('robert',s.room)['requests'][0]['state']=='committed'
    assert len(s.calls)==1


def test_expiry_and_membership_revocation_prevent_model(agent_setup):
    s=agent_setup;q=service(s);key=str(uuid4())
    q.submit('robert',s.room,key,{'action':'contribute'})
    with s.store.connect() as db:db.execute("UPDATE cos_requests SET expires='2000-01-01T00:00:00+00:00'")
    q.run_once(runner(s));assert not s.calls
    q.submit('robert',s.room,str(uuid4()),{'action':'contribute'})
    s.store.membership('robert','remove-cos',s.room,dict(actor='cos-dev',role='remove'))
    q.run_once(runner(s));assert not s.calls
