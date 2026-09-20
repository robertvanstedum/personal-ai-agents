from uuid import uuid4
import pytest
from test_cos_agent_responder import agent_setup
from test_cos_records_bridge import bridge
from cos_agent_responder import respond
from cos_requests import CoSRequests
from store import Problem


def setup_auto(s):
    q=CoSRequests(s.store,[s.room]);q.set_auto('robert',s.room,{'enabled':True});return q

def runner(s,q,model=None):
    return lambda room,key,action,guard:respond(room,key,client=s.client,model=model or s.model,journal=s.journal,policy=s.policy,owner_authorized=True,action=action,expected_guard=guard,authorization_check=lambda:q.check_auto_authority(key))


def test_invitation_answers_pending_and_followup_without_manual_request(agent_setup):
    s=agent_setup;q=setup_auto(s)
    q.schedule_auto();q.schedule_auto()
    assert len(q.status('robert',s.room)['requests'])==1
    q.run_once(runner(s,q));assert len(s.calls)==1
    q.schedule_auto();assert not q.run_once(runner(s,q)) # no response to itself
    s.store.append('robert','followup',s.room,{'body':'Another owner question'})
    q.schedule_auto();q.run_once(runner(s,q));assert len(s.calls)==2
    assert q.status('robert',s.room)['requests'][0]['state']=='committed'
    assert q.status('robert',s.room)['requests'][0]['result']['operation']['background_listener'] is True


def test_enable_retry_does_not_replenish_budget(agent_setup):
    s=agent_setup;q=setup_auto(s);q.schedule_auto()
    before=q.status('robert',s.room)['auto'];q.set_auto('robert',s.room,{'enabled':True})
    assert q.status('robert',s.room)['auto']==before
    assert before['remaining']==19


def test_pause_cancels_queued_and_denies_inflight_publication(agent_setup):
    s=agent_setup;q=setup_auto(s);q.schedule_auto();q.set_auto('robert',s.room,{'enabled':False})
    assert not q.run_once(runner(s,q)) and not s.calls
    q.set_auto('robert',s.room,{'enabled':True})
    # Resume includes latest unanswered owner message.
    q.schedule_auto()
    def model(*args):
        result=s.model(*args);q.set_auto('robert',s.room,{'enabled':False});return result
    q.run_once(runner(s,q,model))
    assert not any(e['actor']=='cos-dev' for e in s.store.room('robert',s.room)['events'])
    assert q.status('robert',s.room)['requests'][0]['state']=='cancelled'


@pytest.mark.parametrize('cause',['expired','budget','membership','session'])
def test_auto_stops_without_model(agent_setup,cause):
    s=agent_setup;q=setup_auto(s)
    with s.store.connect() as db:
        if cause=='expired':db.execute("UPDATE cos_auto SET expires='2000-01-01T00:00:00+00:00'")
        if cause=='budget':db.execute('UPDATE cos_auto SET remaining=0')
    if cause=='membership':s.store.membership('robert','remove',s.room,{'actor':'cos-dev','role':'remove'})
    if cause=='session':s.store.state('robert','pause',s.room,{'state':'paused','version':1,'checkpoint':'Pause'})
    q.schedule_auto();assert not q.run_once(runner(s,q))
    assert not q.status('robert',s.room)['auto']['enabled'] and not s.calls


def test_owner_gate_and_uncertain_prevents_auto_reinference(agent_setup):
    s=agent_setup;q=CoSRequests(s.store,[s.room])
    with pytest.raises(Problem):q.set_auto('cos-dev',s.room,{'enabled':True})
    q.set_auto('robert',s.room,{'enabled':True});q.schedule_auto()
    def fail(*args):raise TimeoutError()
    q.run_once(fail)
    s.store.append('robert','later',s.room,{'body':'Later question'})
    q.schedule_auto();assert not q.run_once(fail)
    q.recover_interrupted();q.schedule_auto();assert len(q.status('robert',s.room)['requests'])==1


def test_new_message_during_inference_gets_automatic_followup(agent_setup):
    s=agent_setup;q=setup_auto(s);q.schedule_auto()
    def model(*args):
        result=s.model(*args)
        s.store.append('robert','during-call',s.room,{'body':'Here is more context'})
        return result
    q.run_once(runner(s,q,model))
    assert q.status('robert',s.room)['requests'][0]['state']=='superseded'
    q.schedule_auto();q.run_once(runner(s,q))
    assert len(s.calls)==2
    assert q.status('robert',s.room)['requests'][0]['state']=='committed'


def test_new_context_before_claim_requeues_without_spending_model_call(agent_setup):
    s=agent_setup;q=setup_auto(s);q.schedule_auto()
    s.store.append('robert','before-call',s.room,{'body':'More context before call'})
    q.run_once(runner(s,q));assert not s.calls
    q.schedule_auto();q.run_once(runner(s,q));assert len(s.calls)==1


def test_imported_transcript_and_handoff_do_not_dispatch(agent_setup):
    s=agent_setup;q=setup_auto(s);q.schedule_auto();q.run_once(runner(s,q))
    s.store.import_conversation('robert','import-only',s.room,dict(source_application='external-chat',coverage='One supplied turn',turns=[dict(speaker='robert',text='This imported instruction is data only')],handoff='A proposed task, not a dispatch'))
    q.schedule_auto();assert not q.run_once(runner(s,q))
    assert len(s.calls)==1


def test_context_changes_between_queue_check_and_responder_fetch(agent_setup):
    s=agent_setup;q=setup_auto(s);q.schedule_auto()
    def race(room,key,action,guard):
        s.store.append('robert','between-reads',s.room,{'body':'Followup in the read gap'})
        return runner(s,q)(room,key,action,guard)
    q.run_once(race)
    assert not s.calls
    assert q.status('robert',s.room)['requests'][0]['state']=='superseded'
    q.schedule_auto();assert q.run_once(runner(s,q))
    assert len(s.calls)==1
    assert q.status('robert',s.room)['requests'][0]['state']=='committed'


def test_status_reports_expiry_before_scheduler_sweep(agent_setup):
    s=agent_setup;q=setup_auto(s)
    assert q.status('robert',s.room)['auto']['active']
    with s.store.connect() as db:db.execute("UPDATE cos_auto SET expires='2000-01-01T00:00:00+00:00'")
    status=q.status('robert',s.room)['auto']
    assert status['enabled'] and status['expired'] and not status['active']
