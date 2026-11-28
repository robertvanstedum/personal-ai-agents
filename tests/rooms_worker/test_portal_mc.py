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
