"""One fixed synthetic probe to an owner-selected native inbox; no discovery/CLI.

A successful write is NOT an acknowledgement. Only a native append captured
while this sender process is alive can establish pickup. Recovery never writes.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import time
from uuid import uuid4

from . import claude_jsonl_observer as N
from . import handoff_payload as H
from . import ledger as L
from .claude_send_guard import BINARY, BINARY_SHA256
from .claude_fixed_send import ClaudeOutbox, need, now_iso
from .direct_contact import Refused, _read, _publish, _hash

TRANSPORT='claude-fixed-socket-probe-v1'
MODE='direct-live-process-v1'


def identity(st):return [st.st_dev,st.st_ino,st.st_uid,st.st_mode]


def socket_identity(path):
    path=Path(path)
    need(path.is_absolute() and '..' not in path.parts,'socket_path_invalid')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent);parent=child
        ps=os.fstat(parent);ss=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
        need(ps.st_uid==os.geteuid() and not ps.st_mode & 0o077,'socket_parent_unsafe')
        need(stat.S_ISSOCK(ss.st_mode) and ss.st_uid==os.geteuid() and not ss.st_mode & 0o077,'socket_unsafe')
        return {'socket':identity(ss),'parent':identity(ps)}
    finally:os.close(parent)


def canonical_socket(path,aliases):
    """Only protected, exact standard macOS aliases; never resolve arbitrary links."""
    need(type(path) is str and path.startswith('/') and '..' not in Path(path).parts,'socket_path_invalid')
    for prefix,destination in (('/tmp','/private/tmp'),('/var','/private/var')):
        if path.startswith(prefix+'/'):
            pin=aliases.get(prefix)
            st=os.lstat(prefix)
            need(type(pin) is dict and pin=={'target':destination,'identity':identity(st)}
                 and stat.S_ISLNK(st.st_mode) and st.st_uid==0
                 and os.readlink(prefix) in (destination,destination.lstrip('/')),'os_alias_changed')
            return destination+path[len(prefix):]
    return path


def peer_identity(connection):
    """Pinned macOS SDK constants; no guessed xucred struct or platform fallback."""
    need(sys.platform=='darwin','peer_credentials_unavailable')
    pid=struct.unpack('i',connection.getsockopt(0,2,struct.calcsize('i')))[0]
    uid,gid=ctypes.c_uint(),ctypes.c_uint()
    libc=ctypes.CDLL(None,use_errno=True)
    call=libc.getpeereid
    call.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
    call.restype=ctypes.c_int
    need(call(connection.fileno(),ctypes.byref(uid),ctypes.byref(gid))==0 and pid>0,'peer_credentials_unavailable')
    return pid,uid.value


def process_identity(pid):
    """Exact selected PID only. Never list sessions/processes or invoke Claude."""
    need(type(pid) is int and pid>0 and sys.platform=='darwin','process_identity_unavailable')
    lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    call=lib.proc_pidpath;call.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32];call.restype=ctypes.c_int
    buf=ctypes.create_string_buffer(4096)
    need(call(pid,buf,len(buf))>0,'process_identity_unavailable')
    binary=buf.value.decode('utf-8','strict')
    def run(argv):
        r=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                         timeout=1,check=False,env={'PATH':'/usr/bin:/bin','LC_ALL':'C','TZ':'UTC'})
        need(r.returncode==0 and len(r.stdout)<=8192,'process_identity_unavailable')
        return r.stdout.decode('utf-8','strict')
    start=run(['/bin/ps','-p',str(pid),'-o','lstart=']).strip()
    rows=run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn']).splitlines()
    paths=[v[1:] for v in rows if v.startswith('n')]
    need(start and len(paths)==1 and paths[0].startswith('/'),'process_identity_unavailable')
    return {'binary':binary,'cwd':paths[0],'process_start':start}


def binary_hash(path,deadline):
    fd,st=N.opened(path)
    try:
        need(0<st.st_size<=512*1024*1024,'receiver_binary_bound')
        h=hashlib.sha256()
        remaining=st.st_size
        while remaining:
            need(time.monotonic()<deadline,'claim_budget_exhausted')
            raw=os.read(fd,min(1024*1024,remaining))
            need(bool(raw),'receiver_binary_changed')
            remaining-=len(raw);h.update(raw)
        after=os.fstat(fd)
        need((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'receiver_binary_changed')
        check,current=N.opened(path);os.close(check)
        need((st.st_dev,st.st_ino)==(current.st_dev,current.st_ino),'receiver_binary_changed')
        return h.hexdigest()
    finally:os.close(fd)


def transcript_binding(enrolled, target, record, pid):
    """Ordinary native logPath, or separately reviewed owned-launch attestation.

    The hook alone is not authority. Protected enrollment pins the operator's
    reviewed bootstrap, hook receipt, supervisor bytes and exact transcript inode.
    """
    if record.get('logPath') is not None:
        need(record['logPath']==enrolled['transcript'],'pid_record_target_changed')
        return
    att=enrolled.get('owned_probe_attestation')
    need(type(att) is dict and att.get('reviewed') is True,'transcript_attestation_required')
    def pinned(path, expected):
        fd,st=N.opened(path)
        try:
            need(st.st_size<=65536,'attestation_bound')
            raw=os.read(fd,65537)
            need(len(raw)==st.st_size and N.hashed(raw)==expected,'attestation_changed')
            return raw
        finally:os.close(fd)
    bootstrap=N.decode(pinned(att['bootstrap_path'],att['bootstrap_sha256']))
    hook=N.decode(pinned(att['hook_receipt_path'],bootstrap['hook_receipt_sha256']))
    pinned(att['supervisor_path'],bootstrap['supervisor_sha256'])
    need(bootstrap.get('format')=='minimoi.owned-probe-bootstrap.v1'
         and bootstrap.get('pid')==pid and bootstrap.get('process_start')==record.get('procStart')
         and bootstrap.get('session_id')==target.session_id and bootstrap.get('cwd')==target.cwd
         and bootstrap.get('transcript')==enrolled['transcript']
         and bootstrap.get('binary_sha256')==BINARY_SHA256 and bootstrap.get('tools')==[]
         and bootstrap.get('mcp_servers')==[] and str(bootstrap.get('model','')).startswith('claude-haiku')
         and type(bootstrap.get('expires_at')) in (int,float)
         and time.time()<bootstrap['expires_at']<1e12,'transcript_attestation_binding')
    need(hook=={'session_id':target.session_id,'cwd':target.cwd,'transcript_path':enrolled['transcript']},
         'transcript_hook_binding')
    fd,st=N.opened(enrolled['transcript']);os.close(fd)
    need([st.st_dev,st.st_ino,st.st_uid]==att['transcript_identity'],'transcript_replaced')


def preflight(enrolled,target,connection,*,peer=peer_identity,process=process_identity,deadline=None):
    deadline=time.monotonic()+12 if deadline is None else deadline
    need(time.monotonic()<deadline,'claim_budget_exhausted')
    need(target.transport==TRANSPORT and target.recipient=='claude-code' and target.host=='local' and enrolled['target']==target.__dict__)
    need(type(enrolled['enrolled_until']) in (int,float) and time.time()<enrolled['enrolled_until']<1e12,'socket_enrollment_expired')
    address=enrolled['peer_address']
    need(type(address) is str and address.startswith('uds:'),'peer_address_invalid')
    need(canonical_socket(address[4:],enrolled.get('os_aliases',{}))==enrolled['socket_path'],'peer_address_changed')
    need(socket_identity(enrolled['socket_path'])==enrolled['socket_identity'],'socket_changed')
    pid,uid=peer(connection)
    need(type(enrolled['receiver_pid']) is int and uid==os.geteuid() and pid==enrolled['receiver_pid'],'socket_peer_changed')
    # The selected socket yields exactly one canonical PID record. No glob/listing.
    path=Path(enrolled['pid_record'])
    need(path.name==str(pid)+'.json' and path.parent.name=='sessions','pid_record_invalid')
    fd,st=N.opened(path)
    try:
        need(st.st_size<=16384,'pid_record_bound')
        raw=os.read(fd,16385);after=os.fstat(fd)
        need(len(raw)==st.st_size and (st.st_size,st.st_mtime_ns,st.st_ctime_ns)==
             (after.st_size,after.st_mtime_ns,after.st_ctime_ns),'pid_record_changed')
        record=N.decode(raw)
    finally:os.close(fd)
    need(record.get('sessionId')==target.session_id and canonical_socket(record.get('messagingSocketPath'),enrolled.get('os_aliases',{}))==enrolled['socket_path']
         and record.get('cwd')==target.cwd,'pid_record_target_changed')
    transcript_binding(enrolled,target,record,pid)
    actual=process(pid)
    need(actual==enrolled['receiver_process'] and actual['cwd']==target.cwd
         and actual['binary']==BINARY and record.get('procStart')==actual['process_start'],'receiver_process_changed')
    need(binary_hash(actual['binary'],deadline)==enrolled['binary_sha256']==BINARY_SHA256,'receiver_binary_changed')
    need(time.monotonic()<deadline,'claim_budget_exhausted')
    need(process(pid)==actual and peer(connection)==(pid,uid),'receiver_process_changed')
    need(socket_identity(enrolled['socket_path'])==enrolled['socket_identity'],'socket_changed')


def frame(record,target,rid,correlation,action,mid):
    need(record.get('version')==2 and record.get('purpose')==H.PROBE_PURPOSE,'socket_probe_only')
    fixed=H.send_input(record,target=target.session_id,request_id=rid,correlation=correlation,approved_action=action)['message']
    wrapper='<cross-session-message>\n'+fixed+'\n</cross-session-message>'
    value={'msgV':1,'msg_id':mid,'type':'user','session_id':target.session_id,
           'message':{'role':'user','content':wrapper},'priority':'next'}
    raw=(H.canonical(value)+'\n').encode('ascii')
    need(len(raw)<=16384,'probe_frame_bound')
    return fixed,raw


class SocketProbeOutbox(ClaudeOutbox):
    def __init__(self,*args,socket_factory=socket.socket,**kwargs):
        super().__init__(*args,**kwargs)
        self.socket_factory=socket_factory
        self.dispatcher=type('Transport',(),{'name':TRANSPORT})()

    def _check(self,binding,target,request):
        super()._check(binding,target,request)
        need(target.transport==TRANSPORT and self.approved['record'].get('version')==2,'socket_probe_only')

    def dispatch(self,rid,*,timeout=12):
        need(type(timeout) in (int,float) and 0<timeout<=12,'invalid_timeout')
        with self._lock():
            folder,binding,target,request,events=self._load(rid)
            self._check(binding,target,request)
            current=self.status(rid)
            if current['state']!='queued':return current
            H.validate(self.approved['record'],live=True)
            budget=min(timeout,self.expires_at-time.time()-8)
            if budget<1:return dict(current,state='not_started',reason='claim_budget_exhausted')
            deadline=time.monotonic()+budget
            base=N.baseline(self.native_enrollment['transcript'],target.session_id)
            mid=str(uuid4());fixed,raw=frame(self.approved['record'],target,rid,binding['correlation'],request['action'],mid)
            connection=self.socket_factory(socket.AF_UNIX,socket.SOCK_STREAM)
            try:
                need(socket_identity(self.native_enrollment['socket_path'])==self.native_enrollment['socket_identity'],'socket_changed')
                connection.settimeout(min(1,max(.001,deadline-time.monotonic())))
                connection.connect(self.native_enrollment['socket_path'])
                preflight(self.native_enrollment,target,connection,deadline=deadline)
                need(self.confirm is not None,'live_confirmation_required')
                confirmation=self.confirm()
                need(confirmation.get('admitted') is True and confirmation.get('expires_at')==self.expires_at,'live_confirmation_refused')
                need(time.monotonic()<deadline and time.time()<self.expires_at-8,'claim_budget_exhausted')
                H.validate(self.approved['record'],live=True)
                need(socket_identity(self.native_enrollment['socket_path'])==self.native_enrollment['socket_identity'],'socket_changed')
                sender={'pid':os.getpid(),'dispatch_at':now_iso(),'provenance_mode':MODE,'msg_id':mid}
                _publish(folder/'native-baseline.json',{'v':1,'baseline':base,'payload_sha256':N.hashed(fixed.encode()),
                    'authorization_sha256':self.approved['sha256']})
                _publish(folder/'sender-process.json',sender)
                _publish(folder/'intent.json',{'v':1,'at':time.time(),'transport':TRANSPORT,
                    'target_sha256':_hash(binding['target']),'correlation':binding['correlation']})
                # No ack/read protocol: any write attempt is uncertain until native proof.
                connection.settimeout(min(1,max(.001,deadline-time.monotonic())))
                try:connection.sendall(raw)
                except OSError:return dict(self.status(rid),native_observation='write_uncertain',proof={})
            finally:connection.close()
            while time.monotonic()<deadline:
                live=self._binding(base,target,sender,N.hashed(fixed.encode()),alive=True)
                live['native_shape_validated']=True  # Candidate only until operator shape gate.
                proof=N.observe(base,live)
                if H.valid_reply(self.approved['record'],proof) and proof.get('state') in ('pickup_observed','reply_observed'):
                    need(os.getpid()==sender['pid'],'sender_process_changed')
                    if self.native_enrollment.get('native_shape_validated') is not True:
                        _publish(folder/'native-pickup-candidate.json',{'format':'minimoi.private-direct-pickup-candidate.v1',
                            'authoritative':False,'request_id':rid,'sender':sender,'sender_alive_at_capture':True,
                            'target_session_id':target.session_id,'payload_sha256':N.hashed(fixed.encode()),'proof':proof})
                        return dict(self.status(rid),native_observation='candidate_pending_review',proof={})
                    _publish(folder/'native-pickup.json',proof)
                    if proof['state']=='reply_observed':_publish(folder/'native-reply.json',proof)
                    return self._receipt(rid,target,proof)
                time.sleep(min(.05,max(0,deadline-time.monotonic())))
            return dict(self.status(rid),native_observation='attempted_unknown',proof={})

    def _binding(self,base,target,sender,payload_hash,*,alive=False,prior=None):
        return {'native_shape_validated':self.native_enrollment.get('native_shape_validated') is True,
            'provenance_mode':MODE,'direct_process_alive':alive,'authenticated_receipt':prior,
            'session_id':target.session_id,'sender_pid':sender['pid'],'dispatch_at':sender['dispatch_at'],
            'observation_at':now_iso(),'msg_id':sender['msg_id'],'payload_sha256':payload_hash}

    def _receipt(self,rid,target,proof):
        if proof['state']=='reply_observed':L.result(str(self.root),rid,target.recipient,proof['report'],session_ref=target.session_id)
        else:L.receipt(str(self.root),rid,target.recipient,session_ref=target.session_id)
        return dict(self.status(rid),native_observation=proof['state'],proof=proof)

    def _promote_direct(self,folder,target):
        path=folder/'native-pickup-candidate.json'
        if (folder/'native-pickup.json').exists() or not path.exists():return
        candidate=_read(path)
        if self.native_enrollment.get('native_shape_validated') is not True or self.native_enrollment.get('pickup_candidate_sha256')!=H.digest(candidate):return
        base=_read(folder/'native-baseline.json');sender=_read(folder/'sender-process.json')
        need(candidate.get('format')=='minimoi.private-direct-pickup-candidate.v1' and candidate.get('authoritative') is False
             and candidate.get('request_id')==folder.name and candidate.get('sender')==sender
             and candidate.get('sender_alive_at_capture') is True and candidate.get('target_session_id')==target.session_id
             and candidate.get('payload_sha256')==base['payload_sha256'],'candidate_binding_changed')
        proof=candidate['proof']
        need(proof.get('state') in ('pickup_observed','reply_observed') and proof.get('provenance_mode')==MODE,'candidate_proof_invalid')
        _publish(folder/'native-pickup.json',proof)

    def observe(self,rid,observer=None):
        with self._lock():
            folder,binding,target,request,events=self._load(rid)
            self._check(binding,target,request);self._intent(folder,binding,target)
            self._promote_direct(folder,target)
            if not (folder/'native-pickup.json').exists():
                return dict(self.status(rid),native_observation='attempted_unknown',proof={})
            prior=_read(folder/'native-pickup.json');base=_read(folder/'native-baseline.json');sender=_read(folder/'sender-process.json')
            proof=N.observe(base['baseline'],self._binding(base['baseline'],target,sender,base['payload_sha256'],prior=prior))
            if not H.valid_reply(self.approved['record'],proof) or proof.get('state') not in ('pickup_observed','reply_observed'):
                return dict(self.status(rid),native_observation='unverified_append',proof={})
            if proof['state']=='reply_observed' and not (folder/'native-reply.json').exists():_publish(folder/'native-reply.json',proof)
            return self._receipt(rid,target,proof)
