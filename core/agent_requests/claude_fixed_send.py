"""Fixed original-owner handoff. Own bounded sender; no receiver resume or retry.

Normal inherited settings/denies remain loaded. Effective settings coverage and
native shape are operator review gates, not model options. A tool receipt proves
enqueue only. Kernel-PID pickup is captured only while our own child stays alive.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from uuid import uuid4

from .direct_contact import Outbox, Delivery, Refused, _read, _publish, _hash
from . import ledger as L
from . import claude_send_guard as G
from . import claude_jsonl_observer as N
from . import handoff_payload as H
from core.mc_delegation_runner import launch, launch_env

TRANSPORT='claude-fixed-owner-handoff-v1'
ROUTE='claude-code.subscription'   # the launch contract (spec v0.5 R6.7-d): plan sign-in, unattended profile, its own record


def now_iso():return datetime.now(timezone.utc).isoformat()


def need(ok,reason='claude_handoff_refused'):
    if not ok:raise Refused(reason)


def file_hash(path):
    fd,st=N.opened(path)
    try:
        h=hashlib.sha256()
        for raw in iter(lambda:os.read(fd,1024*1024),b''):h.update(raw)
        after=os.fstat(fd)
        need((st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'installed_file_changed')
        return h.hexdigest(),[st.st_dev,st.st_ino,st.st_uid,st.st_mode,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
    finally:os.close(fd)


def preflight(enrolled,target):
    need(enrolled['target']==target.__dict__ and target.transport==TRANSPORT and target.recipient=='claude-code')
    need(time.time()<enrolled['enrolled_until'] and enrolled.get('effective_no_other_hooks') is True)
    need(all(type(enrolled.get(k)) is str and len(enrolled[k])==64 for k in ('configuration_review_sha256','listing_proof_sha256')))
    need(G.configuration_hash(enrolled['configuration'])==enrolled['configuration_sha256'])
    binary,identity=file_hash(G.BINARY)
    need(binary==G.BINARY_SHA256 and enrolled['binary_sha256']==binary,'claude_binary_changed')
    need(file_hash(enrolled['python_path'])[0]==enrolled['python_sha256'],'python_changed')
    need(file_hash(enrolled['hook_path'])[0]==enrolled['hook_sha256'],'hook_changed')
    need(enrolled['model']=='claude-haiku-4-5' and enrolled['budget_usd']==0.10,'sender_budget_unreviewed')
    # Native listing is a reviewed exact name/ref mapping, never a model target.
    need(type(enrolled['native_target']) is str and enrolled['native_target'].endswith(']'))
    contract_checks(enrolled,target)
    return identity


def contract_checks(enrolled,target):
    """Launch contract step 3 for the probe: the plan sign-in under the enrolled HOME, and no key setting in the user,
    managed or project configuration Claude Code would read there. Names only; refuses with the contract's code."""
    try:return launch.check_tool('claude-code',route_kind='subscription',home=enrolled['home'],project=target.cwd)
    except launch.LaunchRefused as exc:raise Refused(exc.code) from None


def sender_env(enrolled):
    """The probe's fixed environment, built through the one launch policy: HOME (the enrolled one), PATH and LANG only,
    never copied from this process."""
    return launch_env.child_env({'LANG':'en_US.UTF-8'},environ={'HOME':enrolled['home'],'PATH':'/usr/bin:/bin'})


def launch_record(env,session_id):
    """The probe's launch record (contract step 4): its pinned binary (in code, checked by preflight), the policy's
    digest and the names it gives and removes. Never a value."""
    return launch.record(start_path='probe',tool='claude-code',profile='unattended',route_name=ROUTE,binary_path=G.BINARY,
        binary_digest='sha256:'+G.BINARY_SHA256,pinned_by='code',env_names=list(env),parent_names=list(os.environ),
        checks={'preflight':'passed (binary, python, hook, configuration, sign-in, key settings)'},at=now_iso(),
        session_ref=session_id)


def normalize_result(event,tool_id):
    """Only own CLI stream's genuine user tool_result block is eligible."""
    if event.get('type')!='user':return None
    blocks=event.get('message',{}).get('content')
    if type(blocks) is not list:return None
    matches=[b for b in blocks if type(b) is dict and b.get('type')=='tool_result' and b.get('tool_use_id')==tool_id]
    if len(matches)!=1:return None
    b=dict(matches[0]);content=b.get('content')
    if type(content) is str:b['content']=content
    elif (type(content) is list and len(content)==1 and type(content[0]) is dict
          and content[0].get('type')=='text' and type(content[0].get('text')) is str):b['content']=content[0]['text']
    else:return None
    return G.tool_result(G.encoded(b),expected_tool_id=tool_id)


class ClaudeOutbox(Outbox):
    """Provider seam: base durable identities/intent, distinct observation protocol."""
    def __init__(self,root,targets,enrolled,approved,expires_at,*,popen=subprocess.Popen,confirm=None):
        super().__init__(root,targets,None)
        self.native_enrollment,self.approved,self.expires_at,self.popen=enrolled,approved,expires_at,popen
        self.confirm=confirm
        # status() uses transport.name only for untouched queued requests.
        self.dispatcher=type('Transport',(),{'name':TRANSPORT})()

    def _check(self,binding,target,request):
        record=self.approved['record']
        try:expected=H.action(record)
        except ValueError:raise Refused('handoff_payload_changed') from None
        need(self.approved['sha256']==H.digest(record) and request['action']==expected,'handoff_payload_changed')
        need(target.__dict__==record['target'] and request['action']==self.approved['action'])
        source=binding['source']
        need(source['principal']==record['principal'] and source['conversation_id']==record['conversation_id']
             and source['note_id']==record['note_request_id'] and source['authorization_ref']==
             'approved-owner-handoff:'+self.approved['ref']+':'+self.approved['sha256'],'owner_binding_changed')

    def dispatch(self,rid,*,timeout=12):
        need(type(timeout) in (int,float) and 0<timeout<=12,'invalid_timeout')
        with self._lock():
            folder,binding,target,request,events=self._load(rid)
            self._check(binding,target,request)
            current=self.status(rid)
            if current['state']!='queued':return current
            try:H.validate(self.approved['record'],live=True)
            except ValueError:raise Refused('handoff_authority_changed') from None
            identity=preflight(self.native_enrollment,target)
            base=N.baseline(self.native_enrollment['transcript'],target.session_id)
            remaining=min(timeout,self.expires_at-time.time()-8)
            if remaining<1:return dict(current,state='not_started',reason='claim_budget_exhausted')
            canonical=H.send_input(self.approved['record'],target=self.native_enrollment['native_target'],
                request_id=rid,correlation=binding['correlation'],approved_action=request['action'])
            message=canonical['message']
            session=str(uuid4());operation=_hash({'target':binding['target'],'source':binding['source']})
            admitted={'enabled':True,'operation_sha256':operation,'sender_session_id':session,'input':canonical,
                'issued_at':time.time(),'expires_at':time.time()+remaining,'binary_sha256':G.BINARY_SHA256,
                'binary_identity':identity,'configuration':self.native_enrollment['configuration'],
                'configuration_sha256':self.native_enrollment['configuration_sha256']}
            _publish(folder/'sender-binding.json',admitted)
            hook={'hooks':{'PreToolUse':[{'matcher':'','hooks':[{'type':'command','command':self.native_enrollment['python_path'],
                'args':['-I',self.native_enrollment['hook_path'],str(folder/'sender-binding.json')],'timeout':2,'onFailure':'block'}]}]}}
            _publish(folder/'sender-settings.json',hook);_publish(folder/'sender-mcp.json',{'mcpServers':{}})
            _publish(folder/'native-baseline.json',{'v':1,'baseline':base,'payload_sha256':hashlib.sha256(message.encode()).hexdigest(),
                'authorization_sha256':self.approved['sha256'],'sender_session_id':session})
            need(self.confirm is not None,'live_confirmation_required')
            confirmation=self.confirm()
            need(confirmation.get('admitted') is True and confirmation.get('expires_at')==self.expires_at,'live_confirmation_refused')
            remaining=min(remaining,self.expires_at-time.time()-8)
            if remaining<1:return dict(current,state='not_started',reason='claim_budget_exhausted')
            # The launch record before the intent: a record that cannot be written refuses with nothing sent.
            _publish(folder/'launch.json',launch_record(sender_env(self.native_enrollment),session))
            _publish(folder/'intent.json',{'v':1,'at':time.time(),'transport':TRANSPORT,
                'target_sha256':_hash(binding['target']),'correlation':binding['correlation']})
            # No daemon: this call owns launch, deadline, termination and reap.
            state,mid=self._run(folder,target,canonical,admitted,base,remaining)
            L.record_delivery(str(self.root),rid,adapter=TRANSPORT,state=state,
                attempt_id=binding['correlation'],native_ref=mid,reason='native_enqueue' if state=='accepted' else 'sender_uncertain')
            return self.status(rid)

    def _run(self,folder,target,canonical,admitted,base,budget):
        e=self.native_enrollment
        prompt='Use SendMessage exactly once with this exact JSON input. Do nothing else. Do not retry on any refusal/error.\n'+json.dumps(canonical,ensure_ascii=True,separators=(',',':'))
        argv=[G.BINARY,'-p',prompt,'--output-format','stream-json','--verbose','--permission-mode','default',
            '--session-id',admitted['sender_session_id'],'--model',e['model'],'--max-budget-usd','0.10','--max-turns','1',
            '--tools','SendMessage','--allowedTools','SendMessage','--disallowedTools','mcp__*',
            '--strict-mcp-config','--mcp-config',str(folder/'sender-mcp.json'),'--settings',str(folder/'sender-settings.json')]
        # Normal HOME + project cwd preserve managed/user/project denies. No
        # setting-sources/restricted/bypass flags; no executor/provider env copied.
        env=sender_env(e)
        process=None;selector=None;state='unknown';mid='';total=0;buffer=b'';tool_id=None
        deadline=min(time.monotonic()+budget,time.monotonic()+max(0,self.expires_at-time.time()-8))
        dispatch_at=now_iso()
        try:
            process=self.popen(argv,cwd=target.cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
            _publish(folder/'sender-process.json',{'pid':process.pid,'session_id':admitted['sender_session_id'],
                'binary_sha256':G.BINARY_SHA256,'dispatch_at':dispatch_at,'provenance_mode':'owned-live-pid-v1'})
            selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            while time.monotonic()<deadline:
                if mid and process.poll() is None:
                    self._capture_live(folder,process,base,mid,dispatch_at,canonical['message'])
                ready=selector.select(min(.05,max(0,deadline-time.monotonic())))
                if not ready:
                    if process.poll() is not None:break
                    continue
                chunk=os.read(process.stdout.fileno(),65536)
                if not chunk:break
                total+=len(chunk);buffer+=chunk
                need(total<=2*1024*1024 and len(buffer)<=256*1024,'sender_stream_bound')
                while b'\n' in buffer:
                    raw,buffer=buffer.split(b'\n',1);need(len(raw)<=128*1024,'sender_line_bound')
                    event=N.decode(raw)
                    if event.get('session_id') not in (None,admitted['sender_session_id']):raise Refused('sender_session_changed')
                    if event.get('type')=='assistant':
                        for block in event.get('message',{}).get('content',[]):
                            if type(block) is dict and block.get('type')=='tool_use':
                                need(tool_id is None and block.get('name')=='SendMessage' and block.get('input')==canonical,'sender_tool_changed')
                                tool_id=block['id']
                    if tool_id:
                        result=normalize_result(event,tool_id)
                        if result and result['state']=='enqueued':
                            intent=_read(folder/(admitted['operation_sha256']+'.intent.json'))
                            need(intent['tool_use_id']==tool_id and intent['input_sha256']==G.digest(canonical),'admission_missing')
                            mid=result['msg_id'];state='accepted'
                            _publish(folder/'native-enqueue.json',dict(result,tool_use_id=tool_id,payload_sha256=hashlib.sha256(canonical['message'].encode()).hexdigest()))
            return state,mid
        except Exception:
            # An existing native enqueue receipt remains authoritative even if
            # later stream formatting/cleanup is uncertain. Never send again.
            return state,mid
        finally:
            if selector:selector.close()
            if process is not None:
                if process.poll() is None:
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait(timeout=1)
                if process.stdout:process.stdout.close()

    def _capture_live(self,folder,process,base,mid,dispatch_at,message):
        if (folder/'native-pickup.json').exists() or (folder/'native-pickup-candidate.json').exists():return
        if process.poll() is not None:return
        binding={'native_shape_validated':True,'provenance_mode':'owned-live-pid-v1','owned_child_alive':True,
            'session_id':base['session_id'],'sender_pid':process.pid,'dispatch_at':dispatch_at,'observation_at':now_iso(),
            'msg_id':mid,'payload_sha256':hashlib.sha256(message.encode()).hexdigest()}
        proof=N.observe(base,binding)
        if not H.valid_reply(self.approved['record'],proof):return
        if process.poll() is None and proof.get('state') in ('pickup_observed','reply_observed'):
            if self.native_enrollment.get('native_shape_validated') is True:
                _publish(folder/'native-pickup.json',proof)
                if proof['state']=='reply_observed':_publish(folder/'native-reply.json',proof)
            else:
                # First authorized delivery can preserve evidence for independent
                # native-shape review without claiming it already proves pickup.
                sender=_read(folder/'sender-process.json')
                _publish(folder/'native-pickup-candidate.json',{
                    'format':'minimoi.private-pickup-candidate.v1','authoritative':False,
                    'request_id':folder.name,'sender':sender,'owned_child_alive_before_and_after':True,
                    'target_session_id':base['session_id'],'payload_sha256':binding['payload_sha256'],'proof':proof})

    def _promote_candidate(self,folder,target):
        path=folder/'native-pickup-candidate.json'
        if (folder/'native-pickup.json').exists() or not path.exists():return
        if self.native_enrollment.get('native_shape_validated') is not True:return
        candidate=_read(path)
        # This exact private candidate must be reviewed and pinned by the operator.
        # Merely turning on the schema gate cannot retroactively authenticate PID.
        if self.native_enrollment.get('pickup_candidate_sha256')!=G.digest(candidate):return
        base=_read(folder/'native-baseline.json');sender=_read(folder/'sender-process.json')
        need(candidate.get('format')=='minimoi.private-pickup-candidate.v1' and candidate.get('authoritative') is False
             and candidate.get('request_id')==folder.name and candidate.get('sender')==sender
             and candidate.get('owned_child_alive_before_and_after') is True
             and candidate.get('target_session_id')==target.session_id
             and candidate.get('payload_sha256')==base['payload_sha256'],'candidate_binding_changed')
        proof=candidate['proof']
        need(proof.get('state') in ('pickup_observed','reply_observed') and proof.get('provenance_mode')=='owned-live-pid-v1','candidate_proof_invalid')
        _publish(folder/'native-pickup.json',proof)
        if proof['state']=='reply_observed':_publish(folder/'native-reply.json',proof)

    def observe(self,rid,observer=None):
        with self._lock():
            folder,binding,target,request,events=self._load(rid)
            self._check(binding,target,request);self._intent(folder,binding,target)
            self._promote_candidate(folder,target)
            if (folder/'native-reply.json').exists():proof=_read(folder/'native-reply.json')
            elif (folder/'native-pickup.json').exists():
                prior=_read(folder/'native-pickup.json');base=_read(folder/'native-baseline.json');sender=_read(folder/'sender-process.json')
                proof=N.observe(base['baseline'],{'native_shape_validated':self.native_enrollment.get('native_shape_validated') is True,
                    'provenance_mode':'owned-live-pid-v1','owned_child_alive':False,'authenticated_receipt':prior,
                    'session_id':target.session_id,'sender_pid':sender['pid'],'dispatch_at':sender['dispatch_at'],
                    'observation_at':now_iso(),'msg_id':prior['msg_id'],'payload_sha256':base['payload_sha256']})
                if not H.valid_reply(self.approved['record'],proof):return dict(self.status(rid),native_observation='reply_mismatch',proof={})
                if proof.get('state')=='reply_observed':_publish(folder/'native-reply.json',proof)
                elif proof.get('state')!='pickup_observed':proof=prior
            else:return dict(self.status(rid),native_observation='enqueue_only',proof={})
            if not H.valid_reply(self.approved['record'],proof):return dict(self.status(rid),native_observation='reply_mismatch',proof={})
            if proof['state']=='reply_observed':L.result(str(self.root),rid,target.recipient,proof['report'],session_ref=target.session_id)
            else:L.receipt(str(self.root),rid,target.recipient,session_ref=target.session_id)
            return dict(self.status(rid),native_observation=proof['state'],proof=proof)
