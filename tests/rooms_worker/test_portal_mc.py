from types import SimpleNamespace as NS
from services.rooms_worker.portal_mc import PortalMC, PATH
from test_worker import env
from uuid import uuid4


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
