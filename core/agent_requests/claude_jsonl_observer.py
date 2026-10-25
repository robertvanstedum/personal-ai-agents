"""Native JSONL receipt/reply extraction, never task completion.
Requires a protected host enrollment and independently validated native record shape.
No provider, subprocess or socket access. Read only the exact enrolled file.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

MAX_DELTA = 1024 * 1024
MAX_LINE = 128 * 1024
MAX_REPORT = 16384

class Unknown(Exception): pass

def hashed(raw): return hashlib.sha256(raw).hexdigest()

def instant(value):
    if type(value) is not str: raise Unknown('timestamp')
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None: raise Unknown('timestamp_timezone')
    return dt.astimezone(timezone.utc)

def decode(raw):
    if len(raw) > MAX_LINE: raise Unknown('record_bound')
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result: raise Unknown('duplicate_key')
            result[k]=v
        return result
    value=json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict: raise Unknown('record_object')
    return value

def opened(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise Unknown('path')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent);parent=child
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    finally:os.close(parent)
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_nlink!=1 or st.st_mode & 0o022:
        os.close(fd);raise Unknown('unsafe_file')
    return fd,st

def baseline(path,session_id):
    """Trusted operator call before dispatch, not accepted from MC or native content.
    Binds identity, EOF and last complete bytes; does not attest entire earlier history.
    """
    fd,st=opened(path)
    try:
        tail=os.pread(fd,min(st.st_size,4096),max(0,st.st_size-4096))
        if tail and not tail.endswith(b'\n'):raise Unknown('partial_baseline')
        after=os.fstat(fd)
        if (st.st_size,st.st_mtime_ns,st.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise Unknown('baseline_changed')
        check,current=opened(path)
        os.close(check)
        if (current.st_dev,current.st_ino)!=(st.st_dev,st.st_ino):raise Unknown('path_replaced')
        return {'path':str(Path(path).absolute()),'session_id':session_id,'dev':st.st_dev,'ino':st.st_ino,
                'offset':st.st_size,'tail_sha256':hashed(tail),'uid':st.st_uid}
    finally:os.close(fd)

def snapshot(base):
    fd,st=opened(base['path'])
    try:
        if (st.st_dev,st.st_ino,st.st_uid)!=(base['dev'],base['ino'],base['uid']):raise Unknown('file_replaced')
        start=base['offset']
        if type(start) is not int or start<0 or st.st_size<start:raise Unknown('file_truncated')
        tail=os.pread(fd,min(start,4096),max(0,start-4096))
        if hashed(tail)!=base['tail_sha256']:raise Unknown('baseline_changed')
        size=st.st_size-start
        if size>MAX_DELTA:raise Unknown('delta_bound')
        raw=os.pread(fd,size,start)
        if len(raw)!=size:raise Unknown('short_read')
        # Incomplete last line is never interpreted; caller retries read, never send.
        end=raw.rfind(b'\n')+1
        records=[];offset=start
        for line in raw[:end].splitlines(keepends=True):
            if not line.strip():raise Unknown('empty_record')
            value=decode(line)
            records.append({'record':value,'offset':offset,'sha256':hashed(line)})
            offset+=len(line)
        after=os.fstat(fd)
        if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)!=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns):raise Unknown('read_changed')
        check,current=opened(base['path'])
        os.close(check)
        if (current.st_dev,current.st_ino)!=(st.st_dev,st.st_ino):raise Unknown('path_replaced')
        return records
    finally:os.close(fd)

def observe(base,binding):
    try:return correlate(snapshot(base),base,binding)
    except (Unknown,OSError,ValueError,KeyError,TypeError):
        return {'state':'unknown','automatic_retry':False,'task_complete':False}

def correlate(rows,base,binding):
    """Rows must originate from snapshot(), never an API/model-supplied evidence blob."""
    unknown={'state':'unknown','automatic_retry':False,'task_complete':False}
    if binding.get('native_shape_validated') is not True:return unknown
    if binding['session_id']!=base['session_id']:return unknown
    if type(binding['sender_pid']) is not int or binding['sender_pid']<=0:return unknown
    mode=binding.get('provenance_mode')
    direct=mode=='direct-live-process-v1'
    live_mode=mode in ('owned-live-pid-v1','direct-live-process-v1')
    alive=(binding.get('direct_process_alive') is True and binding['sender_pid']==os.getpid()) if direct else binding.get('owned_child_alive') is True
    if live_mode:
        if not alive and type(binding.get('authenticated_receipt')) is not dict:return unknown
    elif binding.get('sender_process_start') is None:return unknown
    dispatched,observed=instant(binding['dispatch_at']),instant(binding['observation_at'])
    if observed<dispatched:return unknown
    byid={};matches=[]
    for row in rows:
        v=row['record'];uid=v.get('uuid')
        if uid is not None:
            if type(uid) is not str or not uid or uid in byid:raise Unknown('duplicate_or_invalid_uuid')
            byid[uid]=row
        o=v.get('origin')
        if (v.get('type')!='user' or v.get('sessionId')!=binding['session_id'] or v.get('isSidechain') is not False
                or type(o) is not dict or o.get('kind')!='peer'):continue
        if any(k in o for k in ('hostInjected','handback','senderTaskId','members')):continue
        if (type(o.get('verifiedPeerPid')) is not int or o['verifiedPeerPid']!=binding['sender_pid']
                or (not live_mode and (type(o.get('verifiedPeerProcStart')) is not type(binding['sender_process_start'])
                    or o.get('verifiedPeerProcStart')!=binding['sender_process_start']))
                or o.get('msg_id')!=binding['msg_id'] or type(o.get('body')) is not str
                or hashed(o['body'].encode())!=binding['payload_sha256']):continue
        if direct:
            # Native receiver retains the wrapper and its parsed body. Both must
            # match: transformed hooks or caller-supplied sender modes cannot prove pickup.
            if o.get('from')!='unknown' or any(k in o for k in ('fromMode','fromPlugin','fromName','selfSent','plugin','fromSession','hopChain')):continue
            if v.get('message',{}).get('content')!='<cross-session-message>\n'+o['body']+'\n</cross-session-message>':continue
        # Native format validation must establish this origin's correspondence to rendered message.
        # The verified body, not reparsed wrapper text, is the payload authority.
        if not dispatched<=instant(v.get('timestamp'))<=observed:continue
        if row['offset']<base['offset'] or not uid:continue
        matches.append(row)
    if len(matches)!=1:return unknown
    receipt=matches[0];rid=receipt['record']['uuid']
    if live_mode and not alive:
        prior=binding['authenticated_receipt']
        if (prior.get('receipt_uuid')!=rid or prior.get('receipt_sha256')!=receipt['sha256'] or
                prior.get('receipt_offset')!=receipt['offset'] or prior.get('msg_id')!=binding['msg_id'] or
                prior.get('provenance_mode')!=mode):return unknown
    proof={'state':'pickup_observed','receipt_uuid':rid,'receipt_sha256':receipt['sha256'],
           'receipt_offset':receipt['offset'],'msg_id':binding['msg_id'],'automatic_retry':False,'task_complete':False,
           'provenance_mode':mode if live_mode else 'native-process-start'}
    chain={rid};tip=rid;final=None;texts=[];current_mid=None;tool_ids=set()
    for row in rows:
        v=row['record'];uid=v.get('uuid')
        if row['offset']<=receipt['offset']:continue
        typ=v.get('type')
        if typ not in ('user','assistant','system','attachment'):continue # index/summary metadata
        if v.get('sessionId')!=binding['session_id'] or v.get('isSidechain') is not False:raise Unknown('foreign_or_sidechain')
        if typ=='system' and v.get('subtype')=='compact_boundary':raise Unknown('compaction_boundary')
        parent=v.get('parentUuid')
        if parent not in chain:raise Unknown('unrelated_branch')
        if parent!=tip:raise Unknown('ambiguous_branch')
        if not uid:raise Unknown('missing_uuid')
        chain.add(uid);tip=uid
        if typ=='user':
            c=v.get('message',{}).get('content')
            if type(c) is not list or not c or any(type(x) is not dict or x.get('type')!='tool_result' or x.get('tool_use_id') not in tool_ids for x in c):
                raise Unknown('intervening_user')
            if final is not None:raise Unknown('continuation_after_final')
        elif typ=='assistant':
            m=v.get('message',{});mid=m.get('id');content=m.get('content')
            if type(mid) is not str or type(content) is not list:raise Unknown('assistant_shape')
            if final is not None:raise Unknown('continuation_after_final')
            if mid!=current_mid:current_mid=mid;texts=[]
            for block in content:
                if type(block) is not dict:raise Unknown('block_shape')
                if block.get('type')=='text':
                    if type(block.get('text')) is not str:raise Unknown('text_shape')
                    texts.append(block['text'])
                elif block.get('type')=='tool_use':
                    if type(block.get('id')) is not str:raise Unknown('tool_shape')
                    tool_ids.add(block['id'])
                # Thinking blocks are never read, copied or emitted.
            stop=m.get('stop_reason')
            if stop=='end_turn':
                report='\n'.join(texts)
                if not report or len(report.encode())>MAX_REPORT:raise Unknown('report_bound')
                if any(x.get('type')=='tool_use' for x in content):raise Unknown('tool_final')
                final={'state':'reply_observed','report':report,'reply_uuid':uid,'reply_message_id':mid,
                       'reply_sha256':row['sha256'],'clean_native_turn_complete':False,'report_untrusted':True}
            elif stop not in (None,'tool_use'):raise Unknown('nonfinal_stop')
        elif typ=='attachment':raise Unknown('unsupported_attachment_chain')
        elif v.get('subtype')!='stop_hook_summary':raise Unknown('unsupported_system_chain')
    if final:proof.update(final)
    return proof
