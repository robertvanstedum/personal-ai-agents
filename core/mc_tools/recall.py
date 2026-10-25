"""Bounded retained-conversation retrieval. References are selectors, never authority."""
import base64
import hashlib
import json
import re
import time

MAX_OUTPUT = 16384
MAX_WORK = 4 * 1024 * 1024
CID = re.compile(r'c-[0-9a-f]{12}')

class Unavailable(ValueError):
    pass


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def token(value):
    return base64.urlsafe_b64encode(packed(value)).decode().rstrip('=')


def request(args):
    action = args.get('action')
    allowed = {'action', 'continuation', 'limit'} | ({'query'} if action == 'find' else {'conversation_ref'})
    if action not in ('find', 'open') or set(args) - allowed:
        raise Unavailable('recall_arguments_invalid')
    if action == 'find':
        if not isinstance(args.get('query'), str) or not 1 <= len(args['query']) <= 200:
            raise Unavailable('recall_arguments_invalid')
    elif not CID.fullmatch(args.get('conversation_ref', '')):
        raise Unavailable('recall_arguments_invalid')
    signature = hashlib.sha256(packed({k:args[k] for k in ('action','query','conversation_ref') if k in args})).hexdigest()
    cur = None
    if 'continuation' in args:
        try:
            value = args['continuation']
            if not isinstance(value,str) or len(value)>2048: raise ValueError()
            cur = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
            if set(cur) != {'s','c','b','h','n','p'} or cur['s'] != signature or not CID.fullmatch(cur['c']): raise ValueError()
            if any(type(cur[k]) is not int or cur[k]<0 for k in ('b','n','p')): raise ValueError()
            if not re.fullmatch('[0-9a-f]{64}',cur['h']): raise ValueError()
            if action=='open' and cur['c']!=args['conversation_ref']: raise ValueError()
        except (ValueError,TypeError,KeyError):
            raise Unavailable('recall_cursor_invalid') from None
    return signature, cur


def read(source, args, *, current_conversation, request_id, clock=time.monotonic):
    signature, cur = request(args)
    deadline = clock()+2
    source.begin(deadline)
    source.current(current_conversation,request_id)
    convs = source.conversations()
    if args['action']=='open':
        convs=[c for c in convs if c['id']==args['conversation_ref']]
        if not convs: raise Unavailable('recall_unavailable')
    elif cur:
        convs=[c for c in convs if c['id']>=cur['c']]
        if not convs or convs[0]['id']!=cur['c']: raise Unavailable('recall_source_changed')
    result={'source':'retained_mc_portal_notes','untrusted_data':True,'complete_native_history':False,
            'excluded':['archived','legacy','other_scopes','off_record','attachments','Rooms','CoS'],
            'items':[], 'complete':True, 'continuation':None, 'skipped_oversized':0}
    used=0
    def continuation(c,b,h,n,p):
        return token(dict(s=signature,c=c,b=b,h=h,n=n,p=p))
    for scanned,conv in enumerate(convs):
        if scanned>=100:
            result.update(complete=False,continuation=continuation(conv['id'],9223372036854775807,'0'*64,0,0));break
        if clock()>=deadline: raise Unavailable('recall_deadline')
        cid=conv['id']; prior=cur if cur and cur['c']==cid else None
        snap=source.snapshot(conv,current_conversation,request_id,prior['b'] if prior else None,MAX_WORK-used)
        if snap is None:
            if args['action']=='open' or prior: raise Unavailable('recall_snapshot_too_large')
            result['skipped_oversized']+=1;continue
        rows,boundary=snap
        if rows is None:
            result.update(complete=False,continuation=continuation(cid,boundary,'0'*64,0,0));break
        digest=hashlib.sha256(packed(rows)).hexdigest()
        if prior and prior['h']!='0'*64 and prior['h']!=digest: raise Unavailable('recall_source_changed')
        used+=sum(len(r['text'].encode()) for r in rows)
        n,p=(prior['n'],prior['p']) if prior else (0,0)
        if n>len(rows) or (n==len(rows) and p): raise Unavailable('recall_cursor_invalid')
        if used>MAX_WORK:
            result.update(complete=False,continuation=continuation(cid,boundary,digest,n,p));break
        while n<len(rows):
            if clock()>=deadline: raise Unavailable('recall_deadline')
            row=rows[n];value=row['text']
            if p>len(value): raise Unavailable('recall_cursor_invalid')
            if args['action']=='find':
                # Case-insensitive literal matching preserves original character offsets.
                match=re.search(re.escape(args['query']),value[p:],re.IGNORECASE)
                if not match:n+=1;p=0;continue
                start=p+match.start();end=p+match.end()
                text=value[max(0,start-60):min(len(value),max(0,start-60)+320)]
                following=(n,end) if end<len(value) else (n+1,0)
            else:
                text=value[p:p+1500];start=p;following=(n,p+len(text)) if p+len(text)<len(value) else (n+1,0)
            item={k:row[k] for k in ('id','author_kind','who','created_at')}
            item.update(conversation_ref=cid,text=text,character_offset=(max(0,start-60) if args['action']=='find' else start),
                        note_characters=len(value),partial_note=(len(text)!=len(value)),
                        snapshot_sha256=digest,boundary_note_id=boundary)
            if args['action']=='find':item['match_offset']=start
            else:item['segment_end']=start+len(text)
            candidate={**result,'items':result['items']+[item], 'complete':False,
                       'continuation':continuation(cid,boundary,digest,*following)}
            if len(packed(candidate))>MAX_OUTPUT or len(result['items'])>=args.get('limit',5 if args['action']=='find' else 10):
                result.update(complete=False,continuation=continuation(cid,boundary,digest,n,p));source.recheck();return result
            result['items'].append(item);n,p=following
        cur=None
    if result['skipped_oversized']:result['complete']=False
    source.recheck()
    return result
