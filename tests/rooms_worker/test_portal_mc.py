from types import SimpleNamespace as NS
from services.rooms_worker.portal_mc import PortalMC, PATH
from test_worker import env
from uuid import uuid4
import pytest


def test_transport_only_sends_identifiers_and_never_falls_back(tmp_path):
    token=tmp_path/'token';token.write_text('test-room-secret-only')
    calls=[]
    session=NS(post=lambda url,**kw: (calls.append((url,kw)) or NS(status_code=200,json=lambda:{'outcome':'done','text':'ok'})))
    client=PortalMC(token,session=session)
    turn=dict(room='room',id='turn',claim_id='claim')
    assert client.stream([{'content':'untrusted'}],'fake-user','call',turn=turn,admit=lambda:(True,None))['outcome']=='done'
    assert calls[0][1]['json']==dict(room='room',turn_id='turn',claim_id='claim',correlation_id='call')
    assert not calls[0][1]['allow_redirects']
    assert client.stream([], '', 'x',turn={**turn,'discussion_reply':{'q':1}},admit=lambda:(True,None))['outcome']=='refused'
    assert client.stream([], '', 'x',turn=turn,admit=lambda:(False,'cancel_requested')) == dict(outcome='not_admitted',detail='dispatch_refused',state='cancel_requested',text='',usage=None)
    assert len(calls)==1


def test_transport_reads_the_portals_definite_answers_and_trusts_nothing_else(tmp_path):
    """#699: a definite answer is a 200 the portal wrote; a bare status, a bad shape or a loose flag stays uncertain."""
    token=tmp_path/'token';token.write_text('test-room-secret-only')
    answer=[None]
    session=NS(post=lambda url,**kw: NS(status_code=answer[0][0],json=lambda:answer[0][1]),
               get=lambda url,**kw: NS(status_code=answer[0][0],json=lambda:answer[0][1]))
    client=PortalMC(token,session=session)
    ask=lambda status,body: (answer.__setitem__(0,(status,body)),
        client.stream([], '', 'call',turn=dict(room='room',id='turn',claim_id='claim'),admit=lambda:(True,None)))[1]
    uncertain=dict(outcome='error',detail='portal_outcome_uncertain',text='',usage=None)
    assert ask(200,{'outcome':'not_admitted','state':'cancel_requested','detail':'x','text':'ignored'}) == dict(
        outcome='not_admitted',detail='dispatch_refused',state='cancel_requested',text='',usage=None)
    assert ask(200,{'outcome':'not_admitted'}) == uncertain
    assert ask(200,{'outcome':'not_admitted','state':'Not A Code'}) == uncertain
    assert ask(200,['done']) == uncertain
    assert ask(409,{'outcome':'refused','detail':'admission_refused','nothing_sent':True})['detail']=='portal_refused'
    assert ask(200,{'outcome':'refused','detail':'d','text':'','usage':None,'nothing_sent':True})['nothing_sent'] is True
    assert 'nothing_sent' not in ask(200,{'outcome':'error','detail':'d','text':'','usage':None,'nothing_sent':'yes'})
    answer[0]=(200,{'ready':True,'held':'mc_paused'}); assert client.ready() and client.held_reason=='mc_paused'
    answer[0]=(200,{'ready':True,'held':'Not A Code'}); assert client.ready() and client.held_reason is None
    answer[0]=(200,{'ready':False,'held':'mc_paused'}); assert not client.ready() and client.held_reason is None
    answer[0]=(503,{'ready':True,'held':'mc_paused'}); assert not client.ready() and client.held_reason is None


def test_records_snapshot_only_after_fresh_dispatch_admission(env):
    e=env;e.prove();e.join();e.say('MC, read issue 469')
    work=e.worker.work
    code,data=work.call('POST','/turns/claim',{'addressees':['mc'],'discussion_protocol':1},key=str(uuid4()))
    assert code==200
    t=data['turn'];body={'claim_id':t['claim_id']}
    assert work.call('POST',f"/turns/{t['id']}/start",body,key=str(uuid4()))[1]['dispatch'] is True
    plain=work.call('POST',f"/turns/{t['id']}/heartbeat",body,key=str(uuid4()))[1]
    assert 'turn' not in plain
    full=work.call('POST',f"/turns/{t['id']}/heartbeat",{**body,'intent':'dispatch','snapshot':True},key=str(uuid4()))[1]
    assert full['turn']['trigger']['text']=='MC, read issue 469'
    e.pause()
    cancelled=work.call('POST',f"/turns/{t['id']}/heartbeat",{**body,'intent':'dispatch','snapshot':True},key=str(uuid4()))[1]
    assert not cancelled.get('dispatch') and 'turn' not in cancelled


def test_room_authority_includes_pending_observer(env, tmp_path):
    from minimoi_portal.guild_ui.mc_rooms_bridge import Bridge
    from domains.records.meetings import rsvp_of
    import pytest
    e=env;e.prove();e.join()
    assert e.call('POST','/principals',{'id':'guest','label':'Guest'})[0] == 201
    assert e.call('POST',f'/rooms/{e.room}/members',{'actor':'guest','role':'observer'})[0] == 200
    with e.store.connect() as db:
        db.execute("INSERT INTO member_rsvp VALUES(?, 'guest', 'invited', NULL, NULL)", (e.room,))
        assert rsvp_of(db,e.room,'guest')['rsvp'] != 'accepted'
        assert e.store.access(db,'guest',e.room)['id'] == e.room
    e.say('MC, read issue 469')
    code,data=e.worker.work.call('POST','/turns/claim',{'addressees':['mc'],'discussion_protocol':1},key=str(uuid4()))
    assert code==200
    t=data['turn']
    assert 'guest' not in [p['id'] for p in t['brief']['participants']]
    assert e.worker.work.call('POST',f"/turns/{t['id']}/start",{'claim_id':t['claim_id']},key=str(uuid4()))[1]['dispatch']
    ids=dict(room=e.room,turn_id=t['id'],claim_id=t['claim_id'],correlation_id='private-test')
    bridge=Bridge(services=NS(mc=NS()),environ={},principal='robert',records=e.worker.work,
        state_dir=tmp_path/'bridge',craft_prefix='/guild-next')
    with pytest.raises(ValueError,match='owner_room_required'):
        bridge.admitted(ids,snapshot=True)
    # #687: run still refuses (nothing prepared); the HTTP endpoint turns this one refusal into a plain room line.
    from minimoi_portal.guild_ui.mc_rooms_bridge import OwnerRoomRequired, install, PATH as BRIDGE_PATH
    bridge.prepare=lambda *a,**k:(_ for _ in ()).throw(AssertionError('nothing may be prepared for a shared room'))
    with pytest.raises(OwnerRoomRequired):
        bridge.run(ids)
    import hashlib
    from flask import Flask
    app=Flask('t'); install(app,bridge,hashlib.sha256(b'room-token').hexdigest())
    r=app.test_client().post(BRIDGE_PATH,json=ids,headers={'Authorization':'Bearer room-token'})
    assert r.status_code==200 and r.json['outcome']=='done' and r.json['detail']=='owner_room_required'
    assert r.json['text'].startswith('Platform: MC answers only in meetings with just you, MC and CoS')


def test_worker_handles_cancel_at_portal_send_boundary(env, tmp_path):
    e=env;e.prove();e.join();e.say('MC, read issue 469')
    def unexpected(*args,**kwargs):
        raise AssertionError('Cancelled turn must not POST to the portal')
    client=PortalMC(tmp_path/'unused-token',session=NS(post=unexpected))
    client.ready=lambda:True
    e.worker.relay=client
    original=e.worker.spawn_admission
    def cancel_then_admit(turn):
        e.pause()
        return original(turn)
    e.worker.spawn_admission=cancel_then_admit
    assert e.worker.run_once() is True
    assert e.turns()[0]['state']=='cancelled'


# ── #687: a deliberate privacy refusal is a refusal, never "outcome unknown", through the real worker ──────────────
# The real rooms worker and PortalMC client talk to the real bridge endpoint over Flask's test client; Records is the
# test Records app. Only MC itself (backend, ledger, tool binding, Craft conversation) is a stand-in.

def _portal(e, tmp_path, *, fail_after_dispatch=False):
    import hashlib, json
    from flask import Flask
    from minimoi_portal.guild_ui.mc_rooms_bridge import Bridge, install
    from minimoi_portal.guild_ui.mc.internal_stream import collect
    from minimoi_portal.guild_ui.mc.stream import RunStarted, Delta, Finish
    seen=NS(dispatched=0,prepared=0,ledger=[])
    class Backend:
        kind='openclaw'; connected=True
        def stream_turn(self,req,cancel=None):
            def generate():
                seen.dispatched+=1
                yield RunStarted('run-1')
                if fail_after_dispatch:
                    raise RuntimeError('connection lost after MC started')
                yield Delta('Issue read with the bound tool.')
                yield Finish('stop')
            return generate()
        def stop(self,correlation): return True
        def settle(self,result): pass
    class Ledger:
        started=False
        def mark_started(self): self.started=True
        def finish_not_sent(self,*a): seen.ledger.append('not_sent')
        def finish_unknown(self,*a): seen.ledger.append('unknown')
        def finish_stream(self,*a): seen.ledger.append(a[0])
    class Binding:
        def __call__(self): pass
        def attach_run(self,rid): pass
        def revoke(self): return 'revoked'
    def prepare(turn,operation):
        seen.prepared+=1
        return {'id':'c-abc'},NS(add_note=lambda *a:NS(outcome='kept')),'owner-note','guild:robert:c-abc'
    bridge=Bridge(services=NS(mc=Backend()),environ={},principal='robert',records=e.worker.work,
        state_dir=tmp_path/'bridge',craft_prefix='/guild-next',gate=lambda *a,**k:Ledger(),
        make_binding=lambda *a,**k:Binding(),collect=lambda *a,**k:collect(*a,**k,record_usage=lambda **kw:None),
        prepare=prepare)
    bridge.published_proposal=lambda conversation:None      # no tool database in this test
    seen.bridge=bridge
    app=Flask('portal'); install(app,bridge,hashlib.sha256(b'room-token-for-this-test').hexdigest())
    http=app.test_client()
    seen.status=None                                        # set to force the portal's answer (an outage)
    def answer(r):
        return NS(status_code=r.status_code,json=lambda:json.loads(r.data or b'{}'))
    def post(url,headers=None,json=None,**_):
        if seen.status:
            return NS(status_code=seen.status,json=lambda:{'outcome':'error','detail':'bridge_unavailable'})
        return answer(http.post(url.replace('http://portal:5011',''),json=json,headers=headers))
    def get(url,headers=None,**_):
        return answer(http.get(url.replace('http://portal:5011',''),headers=headers))
    token=tmp_path/'room.token'; token.write_text('room-token-for-this-test')
    e.worker.relay=PortalMC(token,session=NS(post=post,get=get,trust_env=False))
    return seen


def _mc_in_the_room(e):
    e.worker._last_hosted=0; e.worker.housekeeping()        # the worker's next readiness report to Records
    people=e.call('GET',f'/rooms/{e.room}/turns')[1]['participants']
    return [p for p in people if p['id']=='mc'][0]['reach']['state']


def _said_by_mc(e):
    return [ev['body'] for ev in e.events() if ev.get('actor')=='mc' and ev.get('kind')=='message']


def _share_the_room(e):
    assert e.call('POST','/principals',{'id':'guest','label':'Guest'})[0]==201
    assert e.call('POST',f'/rooms/{e.room}/members',{'actor':'guest','role':'observer'})[0]==200


def test_private_room_mc_answers_as_before(env, tmp_path):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert turn['state']=='committed' and seen.dispatched==1 and seen.prepared==1 and seen.ledger==['answered']
    assert _said_by_mc(e)==['Issue read with the bound tool.']
    assert _mc_in_the_room(e)=='reachable'


def test_shared_room_shows_mcs_refusal_and_its_reason_and_mc_stays_connected(env, tmp_path):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    assert _mc_in_the_room(e)=='reachable'
    _share_the_room(e)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    # A definite answer: not failed, not uncertain, so the room offers no "Start a new attempt" for an unknown outcome.
    assert (turn['state'],turn['disposition'])==('committed','answered')
    said=_said_by_mc(e)
    assert len(said)==1 and said[0].startswith('Platform: MC answers only in meetings with just you, MC and CoS')
    assert 'private context and tools' in said[0]
    # The safety behaviour is unchanged: nothing was prepared, recorded in MC's ledger or sent to MC.
    assert seen.dispatched==0 and seen.prepared==0 and seen.ledger==[]
    assert _mc_in_the_room(e)=='reachable'
    # Asked again, the same plain answer; still nothing reaches MC.
    e.say('MC, are you there?')
    assert e.worker.run_once() is True
    assert e.turns()[0]['state']=='committed' and len(_said_by_mc(e))==2 and seen.dispatched==0
    assert _mc_in_the_room(e)=='reachable'


def test_a_genuinely_unknown_outcome_is_still_unknown_and_never_replayed(env, tmp_path):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path,fail_after_dispatch=True)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert turn['state']=='uncertain' and seen.dispatched==1 and seen.ledger==['unknown']
    assert _said_by_mc(e)==[]
    assert _mc_in_the_room(e)!='reachable'
    e.worker.run_once()
    assert seen.dispatched==1                               # MC is not sent the same turn again


@pytest.mark.parametrize('status',[409,503])
def test_any_other_portal_answer_in_a_shared_room_is_not_dressed_up_as_the_refusal(env, tmp_path, status):
    """Only the bridge's own owner-private refusal becomes the plain line. A portal that could not say what happened
    (an outage, or any other refusal status) keeps the cautious reading."""
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    _share_the_room(e)
    seen.status=status
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    assert e.turns()[0]['state']=='uncertain' and _said_by_mc(e)==[] and seen.dispatched==0
    assert _mc_in_the_room(e)!='reachable'


# ── #699: the other definite refusals before dispatch are refusals too, and a paused MC stays connected ────────────
# Same pieces as above: the real worker, PortalMC client, bridge endpoint and test Records.

def _reach(e):
    e.worker._last_hosted=0; e.worker.housekeeping()
    people=e.call('GET',f'/rooms/{e.room}/turns')[1]['participants']
    return [p for p in people if p['id']=='mc'][0]['reach']


def _panel(e):
    agents=e.call('GET','/approved-agents')[1]['agents']
    return [a for a in agents if a['agent']=='mc'][0]['availability']


def _records_at_the_portal(e, seen, change):
    """What Records answers the portal's own admission request, changed by the test (the worker's calls are not)."""
    real=e.worker.work
    def call(method, path, body=None, **kw):
        status,data=real.call(method,path,body,**kw)
        return change(status,data) if path.endswith('/heartbeat') and (body or {}).get('snapshot') else (status,data)
    seen.bridge.records=NS(call=call)


def _refuse_at_the_ledger(seen, code):
    from minimoi_portal.guild_ui.mc.ledger_gate import Refusal
    def gate(*a,**k):
        raise Refusal(code,'Nothing was sent.',409,ledger_id=7)
    seen.bridge.gate=gate


def _nothing_reached_mc(seen):
    return seen.dispatched==0 and 'unknown' not in seen.ledger and 'answered' not in seen.ledger


@pytest.mark.parametrize('change', ['another_member_wrote_it','relayed','discussion_reply'])
def test_a_turn_the_owner_did_not_write_shows_mcs_refusal_and_mc_stays_connected(env, tmp_path, change):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    assert _mc_in_the_room(e)=='reachable'
    def altered(status,data):
        if change=='another_member_wrote_it': data['turn']['trigger']['actor']='cos'
        if change=='relayed': data['turn']['trigger']['origin']={'mode':'agent_response'}
        if change=='discussion_reply': data['turn']['discussion_reply']={'question_event':'q'}
        return status,data
    _records_at_the_portal(e,seen,altered)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert (turn['state'],turn['disposition'])==('committed','answered')     # definite: no "outcome unknown"
    said=_said_by_mc(e)
    assert len(said)==1 and said[0].startswith('Platform: MC answers only a message you wrote yourself')
    assert 'Nothing was sent to MC' in said[0]
    assert seen.dispatched==0 and seen.prepared==0 and seen.ledger==[]
    assert _mc_in_the_room(e)=='reachable'


def test_a_meeting_paused_at_the_send_boundary_is_a_plain_stop_not_an_unknown_outcome(env, tmp_path):
    """Records refuses the portal's admission because the owner paused the meeting in between: the stop is
    acknowledged, nothing was prepared, and MC stays connected."""
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    real=e.worker.work
    def call(method, path, body=None, **kw):
        if path.endswith('/heartbeat') and (body or {}).get('snapshot'):
            e.pause()
        return real.call(method,path,body,**kw)
    seen.bridge.records=NS(call=call)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert turn['state']=='cancelled' and turn['stop_ack']=='worker'
    assert seen.dispatched==0 and seen.prepared==0 and seen.ledger==[] and _said_by_mc(e)==[]
    assert _mc_in_the_room(e)=='reachable'


@pytest.mark.parametrize('answer,reason,connected', [
    ((409,{'error':'stale_claim'}),'admission_refused',True),          # the claim is no longer this worker's
    ((200,{'dispatch':False,'state':'running'}),'admission_refused',True),
    ((401,{'error':'unauthorized'}),'admission_unavailable',False),      # a real fault: the badge shows it
])
def test_records_refusing_admission_at_the_portal_is_a_definite_refusal_with_its_reason(env, tmp_path, answer, reason, connected):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    _records_at_the_portal(e,seen,lambda status,data: answer)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert (turn['state'],turn['disposition'])==('failed',reason)        # failed with its reason: Retry, never unknown
    assert seen.dispatched==0 and seen.prepared==0 and seen.ledger==[] and _said_by_mc(e)==[]
    assert (_mc_in_the_room(e)=='reachable') is connected


def test_a_paused_mc_is_connected_and_paused_until_the_owner_continues(env, tmp_path):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    paused=[{'id':7,'conversation_id':'c-abc'}]
    seen.bridge.services.turn_ledger=NS(paused_entry=lambda principal: paused[0])
    seen.bridge.environ={'MINIMOI_MC_TURN_LEDGER':'1'}
    _refuse_at_the_ledger(seen,'paused')
    assert _panel(e)=='Connected'
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    # The room still says MC is paused (rooms.js words it from this disposition and links to Craft), with Retry.
    assert (turn['state'],turn['disposition'])==('failed','mc_paused')
    assert _nothing_reached_mc(seen) and _said_by_mc(e)==[]
    assert _reach(e)=={'state':'reachable','reason':None,'held':'mc_paused'}
    assert _panel(e)=='Connected · paused, waiting for you'
    # Asked again while paused: refused again, never sent, still connected and paused.
    e.say('MC, are you there?')
    assert e.worker.run_once() is True
    assert e.turns()[0]['disposition']=='mc_paused' and _nothing_reached_mc(seen)
    assert _reach(e).get('held')=='mc_paused'
    # Only the owner's Continue (in Craft) lifts the pause; the worker then reports MC as simply connected.
    paused[0]=None
    seen.bridge.gate=lambda *a,**k: (_ for _ in ()).throw(AssertionError('not asked in this step'))
    assert _reach(e)=={'state':'reachable','reason':None}
    assert _panel(e)=='Connected'


def test_the_pause_shows_at_once_and_only_while_mc_is_connected(env, tmp_path):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    _refuse_at_the_ledger(seen,'paused')
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    people=e.call('GET',f'/rooms/{e.room}/turns')[1]['participants']       # before the worker's next check-in
    assert [p for p in people if p['id']=='mc'][0]['reach']=={'state':'reachable','reason':None,'held':'mc_paused'}
    with e.store.connect() as db:                                          # MC's worker stops checking in
        db.execute("DELETE FROM presence WHERE kind='reachable'")
    people=e.call('GET',f'/rooms/{e.room}/turns')[1]['participants']
    reach=[p for p in people if p['id']=='mc'][0]['reach']
    assert reach['state']=='away' and 'held' not in reach and 'failed' not in (reach['reason'] or '')
    assert _panel(e)=='Unknown'


@pytest.mark.parametrize('code,reason', [('busy','mc_busy'),('queue_pending','mc_queue_pending')])
def test_mc_busy_elsewhere_is_a_refusal_with_its_reason_and_mc_stays_connected(env, tmp_path, code, reason):
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    _refuse_at_the_ledger(seen,code)
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert (turn['state'],turn['disposition'])==('failed',reason) and _nothing_reached_mc(seen)
    assert _reach(e)=={'state':'reachable','reason':None}


def test_a_real_failure_before_dispatch_still_drops_the_badge(env, tmp_path):
    """Not every refusal is MC holding: MC's turn ledger being down is a fault, and the badge still shows it."""
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path)
    _refuse_at_the_ledger(seen,'ledger_unavailable')
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True
    turn=e.turns()[0]
    assert (turn['state'],turn['disposition'])==('failed','relay_refused') and seen.dispatched==0
    reach=_reach(e)
    assert reach['state']=='away' and 'last reply failed' in reach['reason']
    assert _panel(e)=='Unknown'


def test_an_unknown_outcome_after_a_pause_still_drops_the_badge(env, tmp_path):
    """The exemption is for the paused refusal only: a later turn MC may have started is unknown as before."""
    e=env;e.prove();e.join()
    seen=_portal(e,tmp_path,fail_after_dispatch=True)
    real_gate=seen.bridge.gate
    _refuse_at_the_ledger(seen,'paused')
    e.say('MC, read issue 469')
    assert e.worker.run_once() is True and _mc_in_the_room(e)=='reachable'
    seen.bridge.gate=real_gate
    e.say('MC, try again')
    assert e.worker.run_once() is True
    assert e.turns()[0]['state']=='uncertain' and seen.dispatched==1 and seen.ledger==['unknown']
    assert _mc_in_the_room(e)!='reachable'
    e.worker.run_once()
    assert seen.dispatched==1


def test_a_worker_cannot_use_a_holding_reason_to_hide_an_unknown_outcome(env):
    e=env;e.prove();e.join();e.say('MC, read issue 469')
    work=e.worker.work
    t=work.call('POST','/turns/claim',{'addressees':['mc'],'discussion_protocol':1},key=str(uuid4()))[1]['turn']
    body={'claim_id':t['claim_id']}
    assert work.call('POST',f"/turns/{t['id']}/start",body,key=str(uuid4()))[1]['dispatch'] is True
    assert work.call('POST',f"/turns/{t['id']}/fail",{**body,'outcome':'uncertain','reason':'mc_paused'},key=str(uuid4()))[0]==200
    assert e.turns()[0]['state']=='uncertain'
    assert _mc_in_the_room(e)!='reachable'
    assert work.call('POST','/hosted-teammates/mc/reachable',{'readyz_at':'2026-11-28T00:00:00+00:00','held':'Not A Code'},key=str(uuid4()))[0]==400
