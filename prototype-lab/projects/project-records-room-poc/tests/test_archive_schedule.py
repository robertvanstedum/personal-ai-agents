"""Scheduled optional trusted proof: owner v2 unchanged; deadline at write lock."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import pytest
from domains.records import archive as module
from domains.records.store import Store, Problem
NOW=datetime(2026,10,14,8,tzinfo=timezone.utc)

def proof(room):return {'kind':'portal_locked_archive_v1','room':room,'state':'clear','checked_at':NOW.isoformat(),
    'room_version':1,'latest_seq':0,'mapping_sha256':'a'*64,'request_count':0,
    'scheduled':{'purpose':'rooms.archive.inactive30.v1','delegation_id':'synthetic','run_id':'run','deadline':NOW.replace(hour=11).isoformat()}}

@pytest.mark.parametrize('change',['purpose','extra','deadline_wrong_day','deadline_wrong_time','at_six','bad_run'])
def test_invalid_scheduled_proofs_refused(change):
    room=str(uuid4());data=proof(room)
    if change=='purpose':data['scheduled']['purpose']='general-owner'
    elif change=='extra':data['scheduled']['admin']=True
    elif change=='deadline_wrong_day':data['scheduled']['deadline']=(NOW+timedelta(days=1)).replace(hour=11).isoformat()
    elif change=='deadline_wrong_time':data['scheduled']['deadline']=NOW.replace(hour=12).isoformat()
    elif change=='bad_run':data['scheduled']['run_id']='has spaces'
    else:data['checked_at']=NOW.replace(hour=11).isoformat()
    with pytest.raises(Problem):module.portal_proof(data,room)

def test_regular_interactive_proof_unchanged():
    room=str(uuid4());data=proof(room);del data['scheduled']
    assert module.portal_proof(data,room)==data

def test_deadline_checked_inside_write_transaction(tmp_path):
    from domains.records.app import create_app
    store=create_app(tmp_path/'private',testing=True).extensions['records_store'];room=store.create_room('robert','create',dict(title='Synthetic',purpose='Synthetic',recording_acknowledged=True))['result']['id']
    with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',((NOW-timedelta(days=31)).isoformat(),room))
    store.archive.clock=lambda:NOW
    snapshot=store.archive.work_snapshot('robert',room)
    data=proof(room);data.update(room_version=snapshot['version'],latest_seq=snapshot['latest_seq'])
    token=module.VERIFIED_EXTERNAL.set(module.portal_proof(data,room))
    original=store.archive._assessment
    def cross_boundary(db,item,now):
        result=original(db,item,now);store.archive.clock=lambda:NOW.replace(hour=11)
        return result
    store.archive._assessment=cross_boundary
    payload={k:snapshot[k] for k in ('version','archive_version','latest_seq','last_activity')}
    try:
        with pytest.raises(Problem,match='window ended'):store.archive.change('robert','archive',room,payload)
    finally:module.VERIFIED_EXTERNAL.reset(token)
    with store.connect() as db:assert db.execute('SELECT count(*) FROM room_archive').fetchone()[0]==0


# Full paired v2 sender/receiver acceptance uses the actual synthetic HTTP app.
import copy,json
from types import SimpleNamespace
from minimoi_portal import rooms_archive_caller as caller_module
from minimoi_portal.guild_ui import rooms_archive_api as orchestration,rooms_archive_runner as runner
@pytest.fixture
def configured(tmp_path):
    access=tmp_path/'access';access.mkdir(mode=0o700)
    policy=access/'archive-schedule.json';policy.write_text(json.dumps({'enabled':True,'delegation_id':'synthetic-installed','owner':'robert'}));policy.chmod(0o600)
    binding=access/'bindings.json';binding.write_text(json.dumps({'version':1,'bindings':[{'account':'account:robert','principal':'robert','active':True}]}));binding.chmod(0o600)
    key=access/'key';key.write_text('synthetic:'+'s'*48);key.chmod(0o600)
    calls=[]
    class Transport:
        def request(self,*args,**kw):calls.append((args,kw));return SimpleNamespace(status_code=200,content=b'{}')
    users=[{'username':'robert','tier':'owner'}]
    clock=[NOW]
    caller=caller_module.ArchiveCaller(backend='http://127.0.0.1:18880',access_dir=access,key_file=key,
        run_id='synthetic-run',load_users=lambda:users,transport=Transport(),clock=lambda:clock[0])
    return SimpleNamespace(caller=caller,access=access,policy=policy,binding=binding,key=key,calls=calls,users=users,clock=clock)

def test_real_pair_hmac_http_and_real_ledger_archive(configured,tmp_path,monkeypatch):
    from domains.records import portal_identity; from domains.records import archive
    from domains.records.app import create_app
    r=configured;monkeypatch.setattr(portal_identity.time,'time',lambda:NOW.timestamp())
    app=create_app(tmp_path/'records',testing=True,portal_key=('synthetic',b's'*48))
    store=app.extensions['records_store'];store.archive.clock=lambda:NOW
    room=store.create_room('robert','create',dict(title='Synthetic',purpose='Synthetic',recording_acknowledged=True))['result']['id']
    with store.connect() as db:db.execute('UPDATE rooms SET updated=? WHERE id=?',((NOW-timedelta(days=31)).isoformat(),room))
    client=app.test_client()
    class Transport:
        def request(self,method,url,**kw):
            response=client.open(url.replace('http://127.0.0.1:18880',''),method=method,headers={**kw['headers'],'Host':'127.0.0.1:18880'},data=kw['data'])
            return SimpleNamespace(status_code=response.status_code,content=response.data)
    r.caller.transport=Transport()
    class Fixed(datetime):
        @classmethod
        def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(orchestration,'datetime',Fixed)
    ledger=tmp_path/'ledger';ledger.mkdir(mode=0o700);(ledger/'requests').mkdir(mode=0o700)
    receipts=[]
    result=runner.run_once(caller=r.caller,ledger_root=ledger,record=lambda value:receipts.append(copy.deepcopy(value)),clock=lambda:NOW)
    assert result['state']=='ok' and len(result['attempts'])==1
    attempt=result['attempts'][0];assert attempt['state']=='completed'
    assert attempt['records_receipt']['receipt']['actor']=='robert'
    assert attempt['records_receipt']['result']['external_proof']['scheduled']['delegation_id']=='synthetic-installed'
    assert receipts[1]['attempts'][0]['state']=='attempting'
    assert store.archive.preview('robert')['rooms'][0]['archived_at']
    assert result['kind']=='automated_archive_job' and not any('session' in str(k) for k in result)
