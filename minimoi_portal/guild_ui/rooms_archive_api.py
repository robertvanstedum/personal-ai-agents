"""Owner-mediated Rooms archive admission; no agent/model/scheduler calls."""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from flask import current_app, jsonify
from core import agent_requests as ar
from core.agent_requests import ledger
from .security import json_error

ROOM = re.compile(r'[0-9a-f-]{36}\Z')
HELD = ContextVar('rooms_admission_locks', default=frozenset())
DEADLINE = ContextVar('rooms_admission_deadline', default=None)
ANCHORS = ContextVar('rooms_admission_anchors', default={})
MAX_ITEMS = 1000
MAX_SEQS = 1000


class WorkUnknown(RuntimeError): pass


def _flock(fd):
    end=min(time.monotonic()+3,DEADLINE.get() or float("inf"))
    while True:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic()>=end:raise WorkUnknown('work_lock_busy')
            time.sleep(0.02)


def _walk_dir(path):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for component in os.path.abspath(path).split('/')[1:]:
            child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise


def _check_dir(fd, *, trusted_root=False):
    info=os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or (info.st_mode&0o022 if trusted_root else stat.S_IMODE(info.st_mode)!=0o700):
        raise WorkUnknown('private_source_refused')


def _private_dir(path, *, trusted_root=False):
    fd=_walk_dir(path)
    try:_check_dir(fd,trusted_root=trusted_root)
    finally:os.close(fd)


@contextmanager
def room_lock(root, room):
    if not isinstance(room,str) or not ROOM.fullmatch(room):raise WorkUnknown('room_invalid')
    scope=(os.path.abspath(root),room)
    if scope in HELD.get():
        yield
        return
    rootfd=_walk_dir(root);folderfd=None;fd=None
    try:
        _check_dir(rootfd,trusted_root=True)
        try:os.mkdir('room-handoffs',0o700,dir_fd=rootfd)
        except FileExistsError:pass
        folderfd=os.open('room-handoffs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
        _check_dir(folderfd)
        fd=os.open('.'+room+'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=folderfd)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
            raise WorkUnknown('lock_refused')
        _flock(fd)
        token=HELD.set(HELD.get()|{scope});deadline=DEADLINE.set(time.monotonic()+10)
        anchors=ANCHORS.set({**ANCHORS.get(),scope:(rootfd,folderfd)})
        try:yield
        finally:
            ANCHORS.reset(anchors);DEADLINE.reset(deadline);HELD.reset(token)
    finally:
        if fd is not None:os.close(fd)
        if folderfd is not None:os.close(folderfd)
        os.close(rootfd)


def _read(path, maximum, *, mapping=False, parentfd=None):
    owned=parentfd is None
    if owned:parentfd=_walk_dir(os.path.dirname(path))
    fd=None
    try:
        _check_dir(parentfd)
        fd=os.open(os.path.basename(path),os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parentfd)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&(0o022 if mapping else 0o077) or info.st_size>maximum:
            raise WorkUnknown('source_refused')
        data=b''
        while len(data)<=maximum:
            block=os.read(fd,min(65536,maximum+1-len(data)))
            if not block:break
            data+=block
        if len(data)>maximum:raise WorkUnknown('source_oversize')
        return data
    finally:
        if fd is not None:os.close(fd)
        if owned:os.close(parentfd)


def strict_mapping(root,room):
    path=os.path.join(root,'room-handoffs',room+'.json')
    parentfd=(ANCHORS.get().get((os.path.abspath(root),room)) or (None,None))[1]
    try:data=json.loads(_read(path,256*1024,mapping=True,parentfd=parentfd))
    except FileNotFoundError:return {'items':[],'posted':[]}
    except (OSError,ValueError):raise WorkUnknown('mapping_unavailable')
    if not isinstance(data,dict) or not {'items','posted'}<=set(data) or set(data)-{'items','posted','archive_attempt'} or not isinstance(data['items'],list) or not isinstance(data['posted'],list) or len(data['items'])>MAX_ITEMS or len(data['posted'])>10000:
        raise WorkUnknown('mapping_invalid')
    attempt=data.get('archive_attempt')
    if attempt is not None and (not isinstance(attempt,dict) or set(attempt)!={'operation_key','checked_at','payload'}
            or not isinstance(attempt['operation_key'],str) or not 1<=len(attempt['operation_key'])<=160
            or not isinstance(attempt['checked_at'],str) or len(attempt['checked_at'])>50
            or not isinstance(attempt['payload'],dict) or set(attempt['payload'])!={'version','archive_version','latest_seq','last_activity'}):
        raise WorkUnknown('admission_fence_invalid')
    seen=set()
    for item in data['items']:
        if (not isinstance(item,dict) or set(item)!={'request_id','agent','seq'}
                or not ar.valid_request_id(item.get('request_id')) or item.get('agent') not in {'claude-code','codex'}
                or type(item.get('seq')) is not int or item['seq']<1
                or item['request_id']!=ar.request_id_for(f"handoff:{room}:{item['seq']}:{item['agent']}")
                or item['request_id'] in seen):raise WorkUnknown('mapping_invalid')
        seen.add(item['request_id'])
    if any(not isinstance(mark,str) or len(mark)>160 for mark in data['posted']):raise WorkUnknown('mapping_invalid')
    return data


def update_mapping(root,room,change):
    with room_lock(root,room):
        data=strict_mapping(root,room);change(data)
        folderfd=ANCHORS.get()[(os.path.abspath(root),room)][1]
        name='.'+room+'.'+secrets.token_hex(8)+'.tmp'
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=folderfd)
        try:
            try:
                encoded=json.dumps(data).encode()
                if len(encoded)>256*1024:raise WorkUnknown('mapping_not_bounded')
                view=memoryview(encoded)
                while view:
                    count=os.write(fd,view)
                    if count<=0:raise WorkUnknown('mapping_write_failed')
                    view=view[count:]
                os.fsync(fd)
            finally:os.close(fd)
            os.replace(name,room+'.json',src_dir_fd=folderfd,dst_dir_fd=folderfd)
            os.fsync(folderfd)
        finally:
            try:os.unlink(name,dir_fd=folderfd)
            except FileNotFoundError:pass
        return data


@contextmanager
def locked_request(root,rid,agent, *, requestsfd):
    folder=os.path.join(root,'requests',rid)
    folderfd=os.open(rid,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=requestsfd)
    try:_check_dir(folderfd)
    except BaseException:
        os.close(folderfd);raise
    # Reports acquire request lock then append-file lock; preserve that order.
    try:lockfd=os.open('.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=folderfd)
    except BaseException:
        os.close(folderfd);raise
    try:
        lock_info=os.fstat(lockfd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid!=os.geteuid() or lock_info.st_nlink!=1 or stat.S_IMODE(lock_info.st_mode)!=0o600:
            raise WorkUnknown('request_lock_refused')
        _flock(lockfd)
        fd=os.open('events.jsonl',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=folderfd)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>8*1024*1024:
                raise WorkUnknown('events_refused')
            _flock(fd)  # delivery writers lock this inode too
            raw=b''
            while len(raw)<=8*1024*1024:
                block=os.read(fd,min(65536,8*1024*1024+1-len(raw)))
                if not block:break
                raw+=block
            if len(raw)>8*1024*1024 or not raw.endswith(b'\n'):raise WorkUnknown('events_invalid')
            try:
                request=json.loads(_read(os.path.join(folder,'request.json'),ledger.LINE_MAX*2,parentfd=folderfd))
                events=[json.loads(line) for line in raw.splitlines()]
            except (ValueError,UnicodeError):raise WorkUnknown('ledger_invalid')
            if (not isinstance(request,dict) or request.get('request_id')!=rid or request.get('to')!=agent
                    or request.get('from')!='robert' or request.get('origin')!='owner_handoff_from_room'
                    or any(not isinstance(event,dict) or event.get('kind') not in ledger.EVENT_KINDS for event in events)):
                raise WorkUnknown('ledger_invalid')
            for event in events:
                kind=event['kind'];actor=event.get('actor')
                if kind in {'receipt','progress','result','needs_you'} and actor!=agent:
                    raise WorkUnknown('event_actor_invalid')
                if kind=='cancel' and actor!='robert':raise WorkUnknown('event_actor_invalid')
                if kind=='delivery' and not ((actor=='ledger' and event.get('state')=='queued')
                        or (actor=='connection' and event.get('state') in ledger.DELIVERY_STATES)):
                    raise WorkUnknown('event_actor_invalid')
            body={k:v for k,v in request.items() if k not in {'created_at','content_sha256'}}
            if ledger._digest(body)!=request.get('content_sha256'):raise WorkUnknown('request_integrity_invalid')
            yield {'request':request,'events':events,'_bytes':len(raw),**ledger._derive(request,events)}
        finally:os.close(fd)
    finally:
        os.close(lockfd);os.close(folderfd)


def milestone_keys(doc,agent):
    keys=[];events=doc['events']
    if any(e.get('kind')=='receipt' and e.get('actor')==agent for e in events):keys.append('picked_up')
    for event in events:
        if event.get('kind')=='needs_you' and event.get('actor')==agent:
            identity=event.get('event_id')
            if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{16}',identity):raise WorkUnknown('event_invalid')
            keys.append('needs_you:'+identity)
    if any(e.get('kind')=='result' and e.get('actor')==agent for e in events):keys.append('reported')
    if doc.get('closed')=='cancelled':keys.append('cancelled')
    return keys


@contextmanager
def external_clear(root,room,metadata):
    """Caller holds room_lock across this check AND remote archive completion."""
    with ExitStack() as stack:
        if (os.path.abspath(root),room) not in HELD.get():raise WorkUnknown('admission_lock_required')
        rootfd=ANCHORS.get()[(os.path.abspath(root),room)][0]
        requestsfd=os.open('requests',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd)
        stack.callback(os.close,requestsfd)
        _check_dir(requestsfd)
        seqs=metadata.get('owner_message_seqs')
        if not isinstance(seqs,list) or len(seqs)>MAX_SEQS or any(type(n) is not int or n<1 for n in seqs) or len(set(seqs))!=len(seqs):raise WorkUnknown('history_unavailable')
        mapping=strict_mapping(root,room);items={item['request_id']:item for item in mapping['items']}
        # Reconcile every possible legacy deterministic request, not a truncated
        # ledger listing or conversation lookup. No owner message text is needed.
        for seq in seqs:
            for agent in ('claude-code','codex'):
                rid=ar.request_id_for(f'handoff:{room}:{seq}:{agent}')
                try:os.stat(rid,dir_fd=requestsfd,follow_symlinks=False)
                except FileNotFoundError:continue
                else:
                    items.setdefault(rid,{'request_id':rid,'agent':agent,'seq':seq})
        if len(items)>MAX_ITEMS:raise WorkUnknown('requests_not_bounded')
        if any(item['seq'] not in seqs for item in items.values()):raise WorkUnknown('mapping_history_mismatch')
        posted=set(mapping['posted']);digests=[]
        total=0
        for rid,item in sorted(items.items()):
            if time.monotonic()>(DEADLINE.get() or float('inf')):raise WorkUnknown('evidence_deadline')
            doc=stack.enter_context(locked_request(root,rid,item['agent'],requestsfd=requestsfd))
            total+=doc['_bytes']
            if total>16*1024*1024:raise WorkUnknown('ledger_not_bounded')
            # Transport failure alone is not closure; it may be retried.
            if doc.get('closed') not in {'result','cancelled'}:raise WorkUnknown('external_work_pending')
            if any(rid+':'+key not in posted for key in milestone_keys(doc,item['agent'])):raise WorkUnknown('milestones_unposted')
            digests.append({'request_id':rid,'event_count':len(doc['events']),
                            'events_sha256':hashlib.sha256(json.dumps(doc['events'],sort_keys=True).encode()).hexdigest()})
        digest=hashlib.sha256(json.dumps({'items':digests,'posted':sorted(posted)},sort_keys=True).encode()).hexdigest()
        yield {'kind':'portal_locked_archive_v1','room':room,'state':'clear',
               'checked_at':datetime.now(timezone.utc).isoformat(),
               'room_version':metadata['version'],'latest_seq':metadata['latest_seq'],
               'mapping_sha256':digest,'request_count':len(items)}

def _failure(error,message,status):
    return {'error':error,'message':message},status


def archive_room(*, root, call, principal, room, key, precondition):
    """Canonical orchestration, not an authorization mechanism.

    Caller must supply an ALREADY authorized Records transport and authenticated
    canonical owner identity. This function creates neither a principal nor a
    signing credential. Scheduled authorization does not currently exist.
    """
    if principal!='robert':return _failure('not_owner','Only the owner can archive.',403)
    if not isinstance(room,str) or not ROOM.fullmatch(room):return _failure('invalid','Name the session.',422)
    if not callable(call) or not isinstance(root,(str,os.PathLike)) or not isinstance(precondition,dict):
        return _failure('unavailable','Archive evidence is not connected.',409)
    if not isinstance(key,str) or not 1<=len(key)<=160:return _failure('invalid','Name the operation.',422)
    try:
        with room_lock(root,room):
            pending=strict_mapping(root,room).get('archive_attempt')
            if pending:
                code,resolution=call('POST',f'/api/v1/rooms/{room}/archive-reconcile',pending)
                if code!=200 or not isinstance(resolution,dict) or resolution.get('state') not in {'committed','not_committed','conflict'}:
                    raise WorkUnknown('prior_archive_uncertain')
                update_mapping(root,room,lambda document:document.pop('archive_attempt',None))
                if resolution['state']=='conflict':
                    return _failure('idempotency_conflict','That key belongs to another operation; choose a new one.',409)
                if resolution['state']=='committed' and pending['operation_key']==key:
                    return resolution['receipt'],200
            code,metadata=call('GET',f'/api/v1/rooms/{room}/archive-check')
            if code!=200 or not isinstance(metadata,dict):raise WorkUnknown('records_unavailable')
            proof_body={k:precondition.get(k) for k in ('version','archive_version','latest_seq','last_activity')}
            same_activity=all(proof_body[k]==metadata.get(k) for k in ('version','latest_seq','last_activity'))
            same_revision=proof_body['archive_version']==metadata.get('archive_version')
            repeated_revision=(metadata.get('archived_at') is not None
                and type(proof_body['archive_version']) is int
                and metadata.get('archive_version')==proof_body['archive_version']+1)
            if not same_activity or not (same_revision or repeated_revision):raise WorkUnknown('activity_changed')
            with external_clear(root,room,metadata) as proof:
                update_mapping(root,room,lambda document:document.__setitem__('archive_attempt',
                    {'operation_key':key,'checked_at':proof['checked_at'],'payload':proof_body}))
                code,result=call('POST',f'/api/v1/rooms/{room}/archive',
                    {**proof_body,'_portal_archive_proof':proof},operation_key=key)
                if code in {400,403,404,409,422}:
                    update_mapping(root,room,lambda document:document.pop('archive_attempt',None))
                if code!=200:return _failure('archive_refused','Archive was not confirmed; check the session before retrying.',409 if code==409 else 502)
                if (not isinstance(result,dict) or not isinstance(result.get('result'),dict)
                        or result['result'].get('room')!=room or result['result'].get('archived') is not True
                        or result['result'].get('archive_version')!=proof_body['archive_version']+1
                        or result['result'].get('through_seq')!=proof_body['latest_seq']
                        or result['result'].get('last_activity')!=proof_body['last_activity']
                        or not isinstance(result.get('receipt'),dict) or result['receipt'].get('actor')!='robert'):
                    return _failure('archive_unconfirmed','Archive receipt is unavailable; check before retrying.',502)
                update_mapping(root,room,lambda document:document.pop('archive_attempt',None))
                return result,200
    except Exception:
        return _failure('archive_unconfirmed','Archive evidence is unavailable or pending. Nothing is reported archived.',409)


def archive():
    from . import api,agent_ask
    refusal,body=api._write_body()
    if refusal is not None:return refusal
    room=body.get('room');svc=agent_ask.service();call=current_app.extensions.get('rooms_call');who=current_app.extensions.get('rooms_who')
    if svc is None or call is None or who is None:return json_error('unavailable','Archive evidence is not connected.',409)
    decision,_=who()
    if not decision.ok or decision.principal!='robert':return json_error('not_owner','Only the owner can archive.',403)
    if not isinstance(room,str) or not ROOM.fullmatch(room):return json_error('invalid','Name the session.',422)
    result,status=archive_room(root=svc.root,call=call,principal=decision.principal,
        room=room,key=body['_key'],precondition=body)
    response=jsonify(result)
    response.status_code=status
    return response


RULES=[('/rooms/archive','api_rooms_archive',archive,['POST'])]
