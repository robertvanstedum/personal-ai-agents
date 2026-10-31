"""Approval changes must fence both credentials and already claimed work."""
from test_rooms_r1 import env
from domains.records.store import Store


def activate(e):
    a=e.store.approved_agents
    pending=a.change('robert','mc',True,'Keep replies useful.')
    bundle=a.issue('mc',pending['generation'])
    pair=bundle['credentials']
    assert e.store.platform_access.authenticate(token=pair['membership']['token']) is None
    a.acknowledge('mc',dict(generation=bundle['generation'],member_id=pair['membership']['credential_id'],
        worker_id=pair['work']['credential_id'],brief_hash=bundle['brief_hash']))
    e.mc={'Authorization':'Bearer '+pair['membership']['token']}
    e.worker={'Authorization':'Bearer '+pair['work']['token']}
    return bundle


def test_approval_restart_off_on_fences_running_answer(env):
    e=env
    old=e.mc.copy()
    activate(e)
    assert e.call('GET',old,'/rooms').status_code==401
    e.prove();e.join();e.say('Hello',target='mc')
    t=e.claim()
    assert t['standing_brief']['text']=='Keep replies useful.'
    assert e.start(t).status_code==200
    stale=e.worker.copy()
    e.store.approved_agents.change('robert','mc',False)
    assert e.call('POST',stale,'/turns/claim',{'addressees':['mc']}).status_code==401
    assert e.reply(t).status_code==401
    activate(e)
    assert e.reply(t).status_code==409
    restarted=Store(e.store.root)
    row=restarted.approved_agents.inventory('robert')[0]
    assert row['approved'] and row['availability']=='Unknown'
    assert row['image_understanding'] is False


def test_lost_issue_rotates_pending_credentials_stale_ack_denied(env):
    from domains.records.platform_access import AccessError
    import pytest
    a=env.store.approved_agents
    p=a.change('robert','mc',True)
    first=a.issue('mc',p['generation']);second=a.issue('mc',p['generation'])
    assert first['credentials']!=second['credentials']
    with pytest.raises(AccessError):
        a.acknowledge('mc',dict(generation=p['generation'],
          member_id=first['credentials']['membership']['credential_id'],
          worker_id=first['credentials']['work']['credential_id'],brief_hash=first['brief_hash']))
    assert env.store.platform_access.authenticate(token=first['credentials']['work']['token']) is None


def test_agent_cannot_admin_or_self_approve(env):
    e=env;activate(e)
    for path,body in [('/approved-agents/mc',{'enabled':True}),
                      ('/rooms',{'title':'no','purpose':'no','recording_acknowledged':True}),
                      (f'/rooms/{e.room}/invite',{'actor':'codex'})]:
        assert e.call('POST',e.mc,path,body).status_code in (401,403)
    assert e.call('GET',e.mc,'/approved-agents').status_code==403
    assert e.call('GET',e.owner,'/agent-installer/pending').status_code==401


def test_installer_crash_recovery_verifies_files_before_ack(env, tmp_path):
    import hashlib
    from services.agent_installer.installer import Installer, FILES
    a=env.store.approved_agents
    a.change('robert','mc',True,'A short owner brief')
    key=tmp_path/'installer-key';key.write_text('a'*40);key.chmod(0o600)
    installer=Installer('http://127.0.0.1:18881',key,tmp_path/'private-installer')
    issued=[]
    lost=[True]
    def call(method,path,body=None):
        if path=='/pending': return 200,{'pending':a.pending()}
        if path.endswith('/issue'):
            bundle=a.issue('mc',body['generation']);issued.append(bundle);return 200,bundle
        if path.endswith('/ack'):
            if lost[0]: return 503,{}
            return 200,a.acknowledge('mc',body)
        raise AssertionError(path)
    installer.call=call
    assert installer.once() is False
    assert a.inventory('robert')[0]['state']=='pending'
    # Simulate a damaged partial installation after restart: never acknowledge it.
    member=installer.root/'mc'/FILES['mc'][0]
    member.write_text('broken')
    lost[0]=False
    assert installer.once()
    assert len(issued)==2
    assert env.store.platform_access.authenticate(token=issued[0]['credentials']['membership']['token']) is None
    assert member.read_text().strip()==issued[1]['credentials']['membership']['token']
    assert member.stat().st_mode&0o777==0o600
    assert a.inventory('robert')[0]['approved']


def test_installer_api_is_private_and_ack_is_generation_bound(tmp_path):
    from domains.records.app import create_app
    from domains.records.manage import provision_rooms
    app=create_app(tmp_path/'records',testing=True,installer_key='x'*40)
    store=app.extensions['records_store']
    provision_rooms(store,'mc','Master Craftsman',tmp_path/'tokens')
    c=app.test_client();h={'Authorization':'Bearer '+'x'*40}
    root='/api/v1/agent-installer'
    assert c.get(root+'/pending').status_code==401
    assert c.get(root+'/pending',headers={**h,'Origin':'https://dev.minimoi.ai'}).status_code==403
    p=store.approved_agents.change('robert','mc',True)
    b=c.post(root+'/mc/issue',headers=h,json={'generation':p['generation']})
    assert b.status_code==200
    assert b.headers['Cache-Control']=='no-store'
    store.approved_agents.change('robert','mc',False)
    pair=b.json['credentials']
    r=c.post(root+'/mc/ack',headers=h,json=dict(generation=p['generation'],
        member_id=pair['membership']['credential_id'],worker_id=pair['work']['credential_id'],
        brief_hash=b.json['brief_hash']))
    assert r.status_code==409


def test_approved_member_cannot_use_coordination_or_import(env):
    e=env;activate(e)
    for suffix in ('imports','transfers','coordination','coordination/example'):
        assert e.call('POST',e.mc,f'/rooms/{e.room}/{suffix}',{}).status_code==403
    assert e.call('GET',e.mc,'/coordination/inbox').status_code==403
    assert e.call('GET',{'Authorization':'Bearer café'},'/agent-installer/pending').status_code==401


def test_installer_to_live_http_client_rotation_and_reply(env,tmp_path,monkeypatch):
    from domains.records.app import create_app
    from services.agent_installer.installer import Installer, FILES
    from services.rooms_worker.clients import Records,read_secret
    from werkzeug.serving import make_server, WSGIRequestHandler
    from threading import Thread
    e=env
    class Quiet(WSGIRequestHandler):
        def log_request(self,*a,**kw): pass
    server=make_server('127.0.0.1',0,lambda e,s: [],request_handler=Quiet)
    server.app=create_app(e.store.root,port=server.server_port,testing=True,installer_key='y'*40)
    # Production/dev endpoint policy stays exact; admit only this disposable
    # loopback server for the integration test's client construction.
    from services.rooms_worker import transport_profile
    monkeypatch.setattr(transport_profile, 'DEV_ENDPOINTS',
                        (*transport_profile.DEV_ENDPOINTS, f'http://127.0.0.1:{server.server_port}'))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        key=tmp_path/'install.key';key.write_text('y'*40);key.chmod(0o600)
        root=tmp_path/'secrets'
        installer=Installer(f'http://127.0.0.1:{server.server_port}',key,root)
        a=e.store.approved_agents
        a.change('robert','mc',True)
        assert installer.once()
        member=Records(installer.base.split('/api/')[0],lambda:read_secret(root/'mc'/FILES['mc'][0]))
        work=Records(member.base,lambda:read_secret(root/'mc'/FILES['mc'][1]))
        assert member.call('GET','/me')[0]==200
        e.mc={'Authorization':'Bearer '+read_secret(root/'mc'/FILES['mc'][0])}
        e.worker={'Authorization':'Bearer '+read_secret(root/'mc'/FILES['mc'][1])}
        e.prove();e.join();e.say('Hello',target='mc');t=e.claim();e.start(t)
        assert e.reply(t).status_code==201
        a.change('robert','mc',False)
        assert member.call('GET','/me')[0]==401
        assert work.call('POST','/turns/claim',{'addressees':['mc']})[0]==401
        a.change('robert','mc',True)
        assert installer.once()
        assert member.call('GET','/me')[0]==200
        from domains.records.manage import provision_rooms
        from services.rooms_connector.connector import build
        for agent in ('claude-code','codex'):
            provision_rooms(e.store,agent,agent,tmp_path/('legacy-'+agent),worker='legacy-worker-'+agent)
            a.change('robert',agent,True)
            assert installer.once()
            worker,_=build({'ROOMS_TEAMMATE':agent,'ROOMS_MANAGED_LEDGER':str(tmp_path/'ledger'),
                'ROOMS_CONNECTOR_SECRETS':str(root/agent),'ROOMS_CONNECTOR_STATE':str(tmp_path/('state-'+agent)),
                'RECORDS_URL':member.base})
            assert worker.mc.call('GET','/me')[0]==200
            assert worker.work.call('POST','/turns/claim',{'addressees':[agent]})[0]==200
            a.change('robert',agent,False)
            assert worker.mc.call('GET','/me')[0]==401
            assert worker.work.call('POST','/turns/claim',{'addressees':[agent]})[0]==401
    finally:
        server.shutdown();thread.join();server.server_close()


def test_agent_reply_does_not_spawn_turn_and_agent_cap_is_enforced(env):
    e=env;activate(e);e.prove();e.join()
    with e.db() as db:
        db.execute("UPDATE teammates SET max_turns_per_meeting=1 WHERE principal='mc'")
    e.say('Hello',target='mc');t=e.claim();e.start(t)
    assert e.reply(t,'@mc continue by yourself').status_code==201
    with e.db() as db:
        assert db.execute("SELECT COUNT(*) FROM turns WHERE room=?",(e.room,)).fetchone()[0]==1
    e.say('Second',target='mc')
    assert e.claim() is None


def test_brief_edit_preserves_credentials_and_manual_keys_stay_revoked(env,tmp_path):
    from domains.records.manage import provision_rooms
    from services.rooms_worker.adapter import messages
    e=env
    provision_rooms(e.store,'mc','MC',tmp_path/'manual',manual=True)
    manual=(tmp_path/'manual'/'mc-manual.token').read_text().strip()
    bundle=activate(e)
    a=e.store.approved_agents
    a.edit_brief('robert','mc','Answer in short conversational paragraphs.')
    assert a.inventory('robert')[0]['generation']==bundle['generation']
    assert e.store.platform_access.authenticate(token=bundle['credentials']['membership']['token'])
    assert e.store.platform_access.authenticate(token=manual) is None
    e.prove();e.join();e.say('Hello',target='mc');t=e.claim()
    prompt=messages(t)
    assert 'Answer in short conversational paragraphs.' in prompt[0]['content']
    assert a.inventory('robert')[0]['brief_hash'] in prompt[0]['content']
    assert 'standing_brief' not in prompt[1]['content']
    a.change('robert','mc',False)
    assert e.store.platform_access.authenticate(token=manual) is None


def test_pending_brief_edit_refused_without_invalidating_installation(env):
    import pytest
    from domains.records.platform_access import AccessError
    a=env.store.approved_agents
    pending=a.change('robert','mc',True,'Initial brief')
    with pytest.raises(AccessError,match='Wait for installation'):
        a.edit_brief('robert','mc','Changed during installation')
    row=a.inventory('robert')[0]
    assert row['generation']==pending['generation'] and row['brief']=='Initial brief'
