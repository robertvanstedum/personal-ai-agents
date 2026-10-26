#!/usr/bin/env python3
"""One bounded operator tick, installed with core/ outside model-writable roots.

Run with fixed Python -I and a private operator configuration; never a model tool
command. No daemon/session start, resume or steering. Native queue is gated by
reviewed real routing AND native-return proof. Unknown dispatch is never resent.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
from urllib.parse import urlsplit

# This is the immutable installed bundle, not cwd/PYTHONPATH or an input path.
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.agent_requests.codex_desktop_queue import QueueEnrollment, admission
from core.agent_requests.codex_observed_queue import ObservedQueue
from core.agent_requests.codex_read_observer import Observer, ReadEnrollment
from core.agent_requests.direct_contact import Outbox, OwnerSource, Target, Refused, _read


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise Refused('worker_redirect_refused')


class Endpoint:
    # Remote portals the worker may pull from (plan 2c): HTTPS with certificate verification, exactly these hosts, the
    # default port, no redirects, no proxy. Anything else stays the original loopback-only rule.
    REMOTE_HOSTS=frozenset({'minimoi.ai','dev.minimoi.ai'})

    def __init__(self,url,token):
        u=urlsplit(url)
        loopback=u.scheme=='http' and u.hostname=='127.0.0.1' and bool(u.port)
        remote=u.scheme=='https' and u.hostname in self.REMOTE_HOSTS and u.port is None
        if ((not loopback and not remote) or u.username or u.password or
                u.path!='/internal/mc/v1/agent-worker' or u.query or u.fragment):
            raise Refused('worker_endpoint_invalid')
        self.timeout=15 if remote else 5
        if type(token) is not str or not 32<=len(token)<=256:
            raise Refused('worker_credential_invalid')
        self.url,self.token=url,token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def __call__(self,body):
        raw=json.dumps(body,separators=(',',':')).encode()
        if len(raw)>16384:raise Refused('worker_request_bound')
        req=urllib.request.Request(self.url,data=raw,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with self.opener.open(req,timeout=self.timeout) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise Refused('worker_response_bound')
        value=json.loads(raw)
        if type(value) is not dict:raise Refused('worker_response_invalid')
        return value


class DeadlineQueue(ObservedQueue):
    def __init__(self,*args,expires_at,**kwargs):
        super().__init__(*args,**kwargs)
        self.expires_at=expires_at
    def deliver(self,target,envelope,*,timeout):
        remaining=self.expires_at-time.time()
        if remaining<=0:raise Refused('worker_admission_expired')
        return super().deliver(target,envelope,timeout=min(timeout,remaining,5))


def one(config,api,job,*,observer_factory=Observer,queue_factory=DeadlineQueue):
    if type(job) is not dict or set(job)!={'request_ref','mode'} or job['mode'] not in ('claim','observe'):
        raise Refused('worker_job_invalid')
    descriptor=api({'phase':job['mode'],'request_ref':job['request_ref']})
    if 'nonce' not in descriptor:return descriptor.get('state','unavailable')
    expected={'execute','request_ref','nonce','expires_at','target','source','action'}
    if descriptor.get('target',{}).get('recipient')=='claude-code':expected.add('approved_handoff')
    if 'reply_protocol' in descriptor:expected.add('reply_protocol')
    if set(descriptor)!=expected:
        raise Refused('worker_descriptor_invalid')
    target=Target(**descriptor['target'])
    enrolled=config['targets'].get(target.alias)
    if not enrolled or enrolled['target']!=asdict(target) or descriptor['request_ref']!=job['request_ref']:
        raise Refused('worker_enrollment_changed')
    if (type(descriptor['execute']) is not bool or descriptor['execute']!=(job['mode']=='claim') or
            type(descriptor['expires_at']) not in (int,float) or not time.time()<descriptor['expires_at']<=time.time()+25):
        raise Refused('worker_admission_expired')
    if target.recipient=='claude-code':
        return claude_one(config,api,job,descriptor,target,enrolled)
    protocol=descriptor.get('reply_protocol')
    if protocol is not None:
        from core.agent_requests.status_v2 import validate_protocol
        if enrolled.get('status_protocol_versions')!=[1,2]:raise Refused('worker_protocol_unavailable')
        validate_protocol(protocol,target,protocol.get('request_id'),protocol.get('correlation'))
    source=OwnerSource(**descriptor['source'])
    if source.principal!=config['principal'] or not source.authorization_ref.startswith(config['environment']+':kept-owner-note:'):
        raise Refused('worker_owner_changed')
    enrollment=QueueEnrollment(target,tuple(enrolled['binary_identity']),enrolled['routing_proof_sha256'],
        enrolled['native_return_proof_sha256'],enrolled['same_app_routing_verified'],enrolled['authenticated_native_return_ready'])
    observer=observer_factory(ReadEnrollment(target,enrollment.binary_identity,enrolled['codex_home']))
    transport=queue_factory(enrollment,observer,expires_at=descriptor['expires_at'])
    box=Outbox(config['private_root'],{target.alias:target},transport)
    rid=box.enqueue(target.alias,source,descriptor['action'],**({'reply_protocol':protocol} if protocol else {}))
    proof={}
    if descriptor['execute']:
        if not admission(enrollment)['ready']:
            state='not_started'
        else:
            try:state=box.dispatch(rid,timeout=5)['state']
            except Exception:state='unknown'
    else:
        # enqueue is idempotent metadata only; this branch NEVER calls dispatch.
        try:
            state=box.observe(rid,observer)['state']
            folder=box.root/'requests'/rid
            if state=='status_report':proof=_read(folder/'native-status.json')
            elif state=='result':proof=_read(folder/'native-result.json')
            elif state=='acknowledged':proof=_read(folder/'native-ack.json')
        except Exception:state='unknown'
    if state not in ('dispatched','unknown','acknowledged','result','status_report','not_started'):state='unknown'
    outcome={'state':state,'request_id':rid,'proof':proof}
    if len(json.dumps(outcome).encode())>12000:
        # Never silently truncate a native report and call it complete.
        outcome={'state':'unknown','request_id':rid,'proof':{}}
    api({'phase':'complete','request_ref':job['request_ref'],'nonce':descriptor['nonce'],'outcome':outcome})
    return outcome['state']


def claude_one(config,api,job,d,target,enrolled):
    from core.agent_requests.claude_fixed_send import ClaudeOutbox, TRANSPORT
    def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')).hexdigest()
    from core.agent_requests.claude_socket_probe import SocketProbeOutbox, TRANSPORT as SOCKET_TRANSPORT
    if target.transport not in (TRANSPORT,SOCKET_TRANSPORT):raise Refused('worker_transport_unavailable')
    box_type=SocketProbeOutbox if target.transport==SOCKET_TRANSPORT else ClaudeOutbox
    approved=d['approved_handoff']
    if type(approved) is not dict or set(approved)!={'ref','sha256','record','action'}:raise Refused('handoff_invalid')
    registry_path=Path(enrolled['handoff_registry'])
    if any(p.is_symlink() for p in (registry_path,*registry_path.parents)):raise Refused('handoff_registry_unsafe')
    registry=_read(registry_path)
    record=registry.get('handoffs',{}).get(approved['ref'])
    if (record!=approved['record'] or digest(record)!=approved['sha256'] or d['action']!=approved['action']
            or record['principal']!=config['principal'] or record['environment']!=config['environment']
            or record['expires_at']<=time.time()):raise Refused('handoff_authority_changed')
    from core.agent_requests.handoff_payload import action, validate
    try:
        validate(record,live=True)
        exact_action=action(record)
    except ValueError:raise Refused('handoff_payload_changed') from None
    if d['action']!=exact_action:raise Refused('handoff_payload_changed')
    source=OwnerSource(**d['source'])
    if (source.principal!=record['principal'] or source.conversation_id!=record['conversation_id']
            or source.note_id!=record['note_request_id'] or source.authorization_ref!=
            'approved-owner-handoff:'+approved['ref']+':'+approved['sha256']):raise Refused('handoff_source_changed')
    box=box_type(config['private_root'],{target.alias:target},enrolled,approved,d['expires_at'],
        confirm=lambda:api({'phase':'confirm','request_ref':job['request_ref'],'nonce':d['nonce']}))
    rid=box.enqueue(target.alias,source,d['action'])
    proof={}
    try:
        if d['execute']:
            observation=box.dispatch(rid,timeout=12)
            state=observation['state'];proof=observation.get('proof',{})
        else:
            observation=box.observe(rid)
            state=observation['state'];proof=observation.get('proof',{})
    except Exception:
        state='not_started' if d['execute'] and not (box.root/'requests'/rid/'intent.json').exists() else 'unknown'
    if state not in ('dispatched','unknown','acknowledged','result','not_started'):state='unknown'
    outcome={'state':state,'request_id':rid,'proof':proof}
    if len(json.dumps(outcome).encode())>12000:outcome={'state':'unknown','request_id':rid,'proof':{}}
    api({'phase':'complete','request_ref':job['request_ref'],'nonce':d['nonce'],'outcome':outcome})
    return outcome['state']


def task_one(config, api, job, *, observer_factory=None):
    """Separate observer-only path. Cannot instantiate an Outbox or queue."""
    from core.agent_requests.task_watch import TaskObserver, encoded, CHUNK, MAX_STATE
    if type(job) is not dict or set(job)!={'subscription_ref','mode'} or job['mode']!='task_observe':
        raise Refused('task_job_invalid')
    sid=job['subscription_ref']
    d=api({'phase':'task_begin','subscription_ref':sid})
    if 'nonce' not in d:return d.get('state','unavailable')
    expected={'subscription_ref','nonce','expires_at','target','principal','environment','spec','checkpoint_bytes','checkpoint_sha256'}
    if set(d)!=expected or d['subscription_ref']!=sid or d['principal']!=config['principal'] or d['environment']!=config['environment']:
        raise Refused('task_descriptor_invalid')
    target=Target(**d['target'])
    enrolled=config['targets'].get(target.alias)
    if not enrolled or enrolled['target']!=asdict(target):raise Refused('worker_enrollment_changed')
    if type(d['checkpoint_bytes']) is not int or not 0<d['checkpoint_bytes']<=MAX_STATE:
        raise Refused('task_checkpoint_bound')
    deadline=d['expires_at']
    if type(deadline) not in (int,float) or not time.time()<deadline<=time.time()+120:
        raise Refused('task_admission_expired')
    def call(phase, **kwargs):
        if time.time()>=deadline:raise Refused('task_admission_expired')
        return api({'phase':phase,'subscription_ref':sid,'nonce':d['nonce'],**kwargs})
    parts=[]
    for offset in range(0,d['checkpoint_bytes'],CHUNK):
        part=call('task_chunk',offset=offset)
        if (set(part)!={'offset','data'} or part['offset']!=offset or type(part['data']) is not str
                or not part['data'].isascii() or len(part['data'])!=min(CHUNK,d['checkpoint_bytes']-offset)):
            raise Refused('task_checkpoint_chunk_invalid')
        parts.append(part['data'])
    raw=''.join(parts).encode('ascii')
    if hashlib.sha256(raw).hexdigest()!=d['checkpoint_sha256']:raise Refused('task_checkpoint_changed')
    observer=(observer_factory or TaskObserver)(ReadEnrollment(target,tuple(enrolled['binary_identity']),enrolled['codex_home']))
    output=observer.step(json.loads(raw),d['spec'])
    raw=encoded(output)
    if len(raw)>MAX_STATE:raise Refused('task_checkpoint_bound')
    for offset in range(0,len(raw),CHUNK):
        call('task_stage',offset=offset,data=raw[offset:offset+CHUNK].decode('ascii'))
    return call('task_commit',sha256=hashlib.sha256(raw).hexdigest())['state']


def tick(config,api,**kwargs):
    jobs=api({'phase':'poll'}).get('requests')
    if type(jobs) is not list or len(jobs)>20:raise Refused('worker_poll_invalid')
    states=[]
    # Bounded process lifetime; operator schedules another tick. No busy loop.
    for job in jobs[:2]:
        try:
            if type(job) is dict and job.get('mode')=='task_observe':
                states.append(task_one(config,api,job))
            else:states.append(one(config,api,job,**kwargs))
        except Exception:states.append('unknown')
    return {'attempted':len(states),'states':states}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    try:
        path=Path(args.config)
        if any(p.is_symlink() for p in (path,*path.parents)):
            raise Refused('worker_config_symlink')
        config=_read(path)  # own regular 0600, no hardlinks, bounded
        required={'endpoint','token_file','principal','environment','private_root','targets'}
        if set(config)!=required or config['environment'] not in ('dev','prod'):
            raise Refused('worker_config_invalid')
        token_path=Path(config['token_file'])
        if any(p.is_symlink() for p in (token_path,*token_path.parents)):
            raise Refused('worker_token_symlink')
        token_doc=_read(token_path)
        if set(token_doc)!={'token'}:raise Refused('worker_credential_invalid')
        answer=tick(config,Endpoint(config['endpoint'],token_doc['token']))
    except Exception:
        answer={'state':'unavailable'}
    print(json.dumps(answer))  # No tokens, prompts, target IDs or native raw data.


if __name__=='__main__':main()
