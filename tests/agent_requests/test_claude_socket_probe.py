"""Offline selected-peer tests. No installed Claude, process probes or provider."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import pytest
from core.agent_requests import claude_socket_probe as S
from core.agent_requests import handoff_payload as H
from core.agent_requests import claude_jsonl_observer as N
from core.agent_requests.direct_contact import Target,OwnerSource,_read


def setup(tmp_path,monkeypatch,mode='reply'):
    tmp_path.chmod(0o700)
    target=Target('claude-probe','claude-code','22222222-2222-2222-2222-222222222222','local','/fixture',S.TRANSPORT,'selected-v1')
    transcript=tmp_path/'native.jsonl';transcript.write_text('{}\n');transcript.chmod(0o600)
    record=dict(version=2,purpose=H.PROBE_PURPOSE,principal='robert',environment='dev',conversation_id='c-123456789abc',
        note_request_id='approved-note',target=asdict(target),owner_text='Run the fixed synthetic test',
        owner_text_sha256=hashlib.sha256(b'Run the fixed synthetic test').hexdigest(),constraints=H.PROBE_CONSTRAINTS.copy(),expires_at=time.time()+3600)
    approved=dict(ref='synthetic-original',record=record,sha256=H.digest(record),action=H.action(record))
    source=OwnerSource(record['principal'],record['conversation_id'],record['note_request_id'],'approved-owner-handoff:'+approved['ref']+':'+approved['sha256'])
    enrolled=dict(target=asdict(target),transcript=str(transcript),socket_path='/selected/private/inbox.sock',socket_identity={},native_shape_validated=True)
    calls=[]
    class Connection:
        def settimeout(self,value):assert 0<value<=1
        def connect(self,path):calls.append(('connect',path))
        def close(self):calls.append(('close',))
        def sendall(self,raw):
            calls.append(('write',raw))
            if mode=='partial':raise TimeoutError('partial write')
            if mode=='silent':return
            frame=json.loads(raw);wrapped=frame['message']['content'];body=wrapped.removeprefix('<cross-session-message>\n').removesuffix('\n</cross-session-message>')
            receipt=dict(type='user',sessionId=target.session_id,isSidechain=False,uuid='receipt',timestamp=S.now_iso(),
                origin=dict(kind='peer',**{'from':'unknown'},verifiedPeerPid=os.getpid(),msg_id=frame['msg_id'],body=body),
                message=dict(role='user',content=wrapped))
            if mode=='transformed':receipt['message']['content']='changed by hook'
            if mode=='wrong_pid':receipt['origin']['verifiedPeerPid']+=1
            if mode=='self_sent':receipt['origin']['selfSent']=True
            rows=[receipt]
            if mode in ('reply','wrong_reply'):
                rows.append(dict(type='assistant',sessionId=target.session_id,isSidechain=False,uuid='reply',parentUuid='receipt',
                    message=dict(id='answer',content=[dict(type='text',text=H.TOKEN if mode=='reply' else 'not the token')],stop_reason='end_turn')))
            with transcript.open('a') as f:
                for row in rows:f.write(json.dumps(row)+'\n')
    monkeypatch.setattr(S,'preflight',lambda *args,**kwargs:None)
    monkeypatch.setattr(S,'socket_identity',lambda path:{})
    # Source-only fixture must never launch a command/provider sender.
    monkeypatch.setattr(S.subprocess,'run',lambda *a,**k:pytest.fail('unexpected subprocess'))
    deadline=time.time()+25
    box=S.SocketProbeOutbox(tmp_path,{target.alias:target},enrolled,approved,deadline,socket_factory=lambda *args:Connection(),
        confirm=lambda:dict(admitted=True,expires_at=deadline))
    rid=box.enqueue(target.alias,source,approved['action'])
    return box,rid,calls,target,transcript


def test_exact_wire_pickup_reply_and_no_second_write(tmp_path,monkeypatch):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch)
    result=box.dispatch(rid,timeout=1)
    assert result['state']=='result' and result['proof']['report']==H.TOKEN
    assert result['proof']['provenance_mode']==S.MODE and result['proof']['task_complete'] is False
    raw=[c[1] for c in calls if c[0]=='write'][0]
    wire=json.loads(raw)
    assert raw.endswith(b'\n') and set(wire)=={'msgV','msg_id','type','message','priority','session_id'}
    assert wire['msgV']==1 and wire['type']=='user' and wire['priority']=='next' and wire['session_id']==target.session_id
    body=wire['message']['content'][len('<cross-session-message>\n'):-len('\n</cross-session-message>')]
    payload=json.loads(body)
    assert payload['handoff']['payload']==H.PROBE_TEXT and payload['request_id']==rid
    assert box.observe(rid)['state']=='result'
    box.dispatch(rid)
    assert len([c for c in calls if c[0]=='write'])==1


@pytest.mark.parametrize('mode',['partial','silent','transformed','wrong_pid','self_sent','wrong_reply'])
def test_attempt_unknown_never_retries_or_claims_enqueue(tmp_path,monkeypatch,mode):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch,mode)
    assert box.dispatch(rid,timeout=1)['state']=='unknown'
    assert box.observe(rid)['state']=='unknown'
    assert box.dispatch(rid,timeout=1)['state']=='unknown'
    assert len([c for c in calls if c[0]=='write'])==1
    assert not (box.root/'requests'/rid/'native-enqueue.json').exists()


def test_stop_and_expiry_before_write_have_no_intent(tmp_path,monkeypatch):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch)
    box.confirm=lambda:dict(admitted=False)
    with pytest.raises(S.Refused):box.dispatch(rid)
    assert not (box.root/'requests'/rid/'intent.json').exists()
    assert not [c for c in calls if c[0]=='write']
    box.approved['record']['expires_at']=time.time()-1
    with pytest.raises((S.Refused,ValueError)):box.dispatch(rid)
    assert not [c for c in calls if c[0]=='write']


def test_career_v1_rejected_by_socket_transport(tmp_path,monkeypatch):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch)
    record=box.approved['record'].copy();record['version']=1;record.pop('purpose')
    with pytest.raises(S.Refused):S.frame(record,target,rid,'c',H.action(record),'m')
    record['version']=2;record['purpose']='arbitrary'
    with pytest.raises(S.Refused):S.frame(record,target,rid,'c','arbitrary','m')
    assert not calls


def test_candidate_needs_exact_separate_operator_review(tmp_path,monkeypatch):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch)
    box.native_enrollment['native_shape_validated']=False
    assert box.dispatch(rid,timeout=1)['state']=='unknown'
    candidate=_read(box.root/'requests'/rid/'native-pickup-candidate.json')
    assert candidate['authoritative'] is False and candidate['sender_alive_at_capture'] is True
    box.native_enrollment['native_shape_validated']=True
    assert box.observe(rid)['state']=='unknown'
    box.native_enrollment['pickup_candidate_sha256']='f'*64
    assert box.observe(rid)['state']=='unknown'
    box.native_enrollment['pickup_candidate_sha256']=H.digest(candidate)
    assert box.observe(rid)['state']=='result'
    assert len([c for c in calls if c[0]=='write'])==1


def test_direct_live_pid_cannot_be_reconstructed_after_sender_exit(tmp_path,monkeypatch):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch)
    assert box.dispatch(rid,timeout=1)['state']=='result'
    folder=box.root/'requests'/rid
    base=_read(folder/'native-baseline.json');sender=_read(folder/'sender-process.json')
    binding=box._binding(base['baseline'],target,sender,base['payload_sha256'])
    assert N.observe(base['baseline'],binding)['state']=='unknown'
    binding['direct_process_alive']=True;binding['sender_pid']+=1
    assert N.observe(base['baseline'],binding)['state']=='unknown'
    prior=_read(folder/'native-pickup.json');prior['receipt_sha256']='0'*64
    binding=box._binding(base['baseline'],target,sender,base['payload_sha256'],prior=prior)
    assert N.observe(base['baseline'],binding)['state']=='unknown'


def test_preflight_exact_selected_record_and_process(tmp_path,monkeypatch):
    tmp_path.chmod(0o700);directory=tmp_path/'sessions';directory.mkdir(mode=0o700)
    path=directory/'123.json';transcript=str(tmp_path/'exact.jsonl')
    record=dict(sessionId='22222222-2222-2222-2222-222222222222',messagingSocketPath='/selected/inbox.sock',cwd='/fixture',logPath=transcript,procStart='fixed-start')
    path.write_text(json.dumps(record));path.chmod(0o600)
    target=Target('claude-probe','claude-code',record['sessionId'],'local','/fixture',S.TRANSPORT,'v1')
    process=dict(binary=S.BINARY,cwd='/fixture',process_start='fixed-start')
    enrolled=dict(target=asdict(target),enrolled_until=time.time()+60,socket_path=record['messagingSocketPath'],socket_identity={},receiver_pid=123,
        peer_address='uds:'+record['messagingSocketPath'],pid_record=str(path),transcript=transcript,receiver_process=process,binary_sha256=S.BINARY_SHA256)
    monkeypatch.setattr(S,'socket_identity',lambda p:{})
    monkeypatch.setattr(S,'binary_hash',lambda p,d:S.BINARY_SHA256)
    kwargs=dict(peer=lambda c:(123,os.geteuid()),process=lambda pid:process)
    S.preflight(enrolled,target,object(),**kwargs)
    with pytest.raises(S.Refused):S.preflight(enrolled,target,object(),peer=lambda c:(124,os.geteuid()),process=kwargs['process'])
    with pytest.raises(S.Refused):S.preflight(enrolled,target,object(),peer=lambda c:(123,os.geteuid()+1),process=kwargs['process'])
    for key,value in [('sessionId','foreign'),('logPath','/other/log'),('procStart','other'),('cwd','/other'),('messagingSocketPath','/other/socket')]:
        changed=dict(record);changed[key]=value;path.write_text(json.dumps(changed))
        with pytest.raises(S.Refused):S.preflight(enrolled,target,object(),**kwargs)
    path.write_text(json.dumps(record));saved=path.with_suffix('.saved');path.rename(saved);path.symlink_to(saved)
    with pytest.raises(OSError):S.preflight(enrolled,target,object(),**kwargs)


def test_socket_path_rejects_file_and_symlink_without_connect(tmp_path):
    tmp_path.chmod(0o700);p=tmp_path/'inbox';p.write_text('not socket')
    with pytest.raises(S.Refused):S.socket_identity(p)
    link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(S.Refused):S.socket_identity(link)
    sub=tmp_path/'sub';sub.mkdir();alias=tmp_path/'alias';alias.symlink_to(sub,target_is_directory=True)
    with pytest.raises(OSError):S.socket_identity(alias/'inbox')


def test_only_pinned_standard_os_alias_is_canonicalized(monkeypatch):
    from types import SimpleNamespace
    st=SimpleNamespace(st_dev=1,st_ino=2,st_uid=0,st_mode=0o120755)
    monkeypatch.setattr(S.os,'lstat',lambda path:st)
    monkeypatch.setattr(S.os,'readlink',lambda path:'private/tmp')
    pin={'/tmp':{'target':'/private/tmp','identity':S.identity(st)}}
    assert S.canonical_socket('/tmp/private/inbox.sock',pin)=='/private/tmp/private/inbox.sock'
    with pytest.raises(S.Refused):S.canonical_socket('/tmp/private/inbox.sock',{})
    monkeypatch.setattr(S.os,'readlink',lambda path:'/attacker')
    with pytest.raises(S.Refused):S.canonical_socket('/tmp/private/inbox.sock',pin)
    with pytest.raises(S.Refused):S.canonical_socket('/tmp/../elsewhere',pin)


@pytest.mark.skipif(S.sys.platform!='darwin',reason='Darwin credential ABI fixture')
def test_actual_darwin_selected_unix_peer_credentials(tmp_path):
    # Both endpoints belong to this offline test process, never Claude/native IPC.
    # Keep pathname below macOS sockaddr_un length even with pytest temp nesting.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='mc-peer-',dir='/private/tmp') as directory:
        path=str(Path(directory)/'inbox.sock')
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        accepted=None
        try:
            listener.bind(path);os.chmod(path,0o600);listener.listen(1)
            pin=S.socket_identity(path)
            client.settimeout(1);client.connect(path)
            accepted,_=listener.accept()
            assert S.peer_identity(client)==(os.getpid(),os.geteuid())
            assert S.peer_identity(accepted)==(os.getpid(),os.geteuid())
            assert S.socket_identity(path)==pin
            client.sendall(b'fixture-only\n')
            assert accepted.recv(100)==b'fixture-only\n'
            os.chmod(path,0o666)
            with pytest.raises(S.Refused):S.socket_identity(path)
        finally:
            if accepted is not None:accepted.close()
            client.close();listener.close()


@pytest.mark.parametrize('field,value',[('principal','foreign-owner'),('expires_at',1),('purpose','new-work')])
def test_authority_changes_refuse_before_any_write(tmp_path,monkeypatch,field,value):
    box,rid,calls,target,path=setup(tmp_path,monkeypatch)
    box.approved['record'][field]=value
    box.approved['sha256']=H.digest(box.approved['record'])
    with pytest.raises((S.Refused,ValueError)):box.dispatch(rid)
    assert not [c for c in calls if c[0]=='write']
    assert not (box.root/'requests'/rid/'intent.json').exists()


def test_enrollment_expiry_refuses_before_process_or_file_probe(tmp_path,monkeypatch):
    target=Target('probe','claude-code','22222222-2222-2222-2222-222222222222','local','/fixture',S.TRANSPORT,'v1')
    enrolled=dict(target=asdict(target),enrolled_until=time.time()-1)
    with pytest.raises(S.Refused,match='expired'):
        S.preflight(enrolled,target,object(),peer=lambda c:pytest.fail('peer accessed'),process=lambda p:pytest.fail('process accessed'))
