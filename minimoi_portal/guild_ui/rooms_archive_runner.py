"""Pure scheduled archive planning. No transport, identity, job or runtime grant.
Candidate means recheck through archive_room, NEVER permission to archive.
"""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
ZONE=ZoneInfo('America/Chicago')
MAX_ROOMS=200
class PlanRefused(ValueError):pass


def batch_deadline(now):
    if not isinstance(now,datetime) or now.tzinfo is None:raise PlanRefused('clock_unavailable')
    local=now.astimezone(ZONE)
    if local.hour>=6:raise PlanRefused('outside_batch_window')
    return local.replace(hour=6,minute=0,second=0,microsecond=0)


def plan(preview,*,now):
    """Dry run only; real per-room proof unavailable until authorized execution.
    Preview external eligibility is NOT a grant and may honestly be unavailable.
    """
    deadline=batch_deadline(now)
    if not isinstance(preview,dict) or preview.get('threshold_days')!=30:raise PlanRefused('preview_invalid')
    rows=preview.get('rooms')
    if not isinstance(rows,list) or len(rows)>MAX_ROOMS:raise PlanRefused('preview_not_bounded')
    from .rooms_archive_api import ROOM
    seen=set();result=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not ROOM.fullmatch(row['id']) or row['id'] in seen:raise PlanRefused('preview_invalid')
        seen.add(row['id'])
        if any(type(row.get(k)) is not int or row[k]<(1 if k=='version' else 0) for k in ('version','archive_version','latest_seq')):raise PlanRefused('preview_invalid')
        try:
            activity=datetime.fromisoformat(row['last_activity'].replace('Z','+00:00'))
            if activity.tzinfo is None or activity>now:raise ValueError
        except (TypeError,ValueError,KeyError,AttributeError):raise PlanRefused('activity_unavailable') from None
        reason=('already_archived' if row.get('archived_at') else 'recent_activity' if now-activity<timedelta(days=30) else
                'backend_work_not_clear' if row.get('backend_work')!='clear' else
                'external_work_pending' if row.get('external_work')=='pending' else
                'candidate_needs_locked_proof')
        result.append({'room':row['id'],'reason':reason,'candidate':reason=='candidate_needs_locked_proof',
            'precondition':{k:row[k] for k in ('version','archive_version','latest_seq','last_activity')}})
    return {'dry_run':True,'threshold_days':30,'batch_deadline':deadline.isoformat(),
        'scheduled_authorization':'not_configured','writes_performed':0,'rooms':result}


def run_once(*,caller,ledger_root,record,previous=None,clock=None):
    """Explicitly installed authorized caller only. No retry with alternate key.
    Caller/record are trusted host dependencies, never supplied by an HTTP body.
    The private receipt is persisted BEFORE each attempt; reconciliation uses the
    same key/precondition even next night, with no fabricated actor session.
    """
    import hashlib,json
    from datetime import timezone
    from .rooms_archive_api import archive_room,ROOM
    clock=clock or (lambda:datetime.now(timezone.utc))
    _,authority,_=caller.authorize()
    receipt={'kind':'automated_archive_job','authority':authority,'records_actor':'robert','attempts':[],'state':'running'}
    pending=[]
    if previous is not None:
        receipt['previous_receipt_sha256']=hashlib.sha256(json.dumps(previous,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if (not isinstance(previous,dict) or previous.get('kind')!='automated_archive_job'
                or not isinstance(previous.get('attempts'),list) or len(previous['attempts'])>MAX_ROOMS):raise PlanRefused('prior_receipt_invalid')
        for item in previous['attempts']:
            if not isinstance(item,dict) or item.get('state') not in {'attempting','uncertain','completed'}:raise PlanRefused('prior_receipt_invalid')
            if item['state']!='completed':
                if (not isinstance(item.get('room'),str) or not ROOM.fullmatch(item['room']) or not isinstance(item.get('key'),str)
                        or not isinstance(item.get('precondition'),dict) or not isinstance(item.get('authority'),dict)):raise PlanRefused('prior_receipt_invalid')
                pending.append({k:item[k] for k in ('room','key','precondition','authority')})
    receipt['attempts']=[{**item,'state':'uncertain'} for item in pending]
    record(receipt)
    seen=set()
    def attempt(item):
        batch_deadline(clock());caller.authorize()
        if item['room'] in seen:raise PlanRefused('duplicate_room')
        seen.add(item['room']);entry=next((e for e in receipt['attempts'] if e['room']==item['room']),None)
        if entry is None:
            entry={**item,'state':'attempting'};receipt['attempts'].append(entry)
        else:entry['state']='attempting'
        record(receipt)
        body,code=archive_room(root=ledger_root,call=caller,principal='robert',room=item['room'],key=item['key'],precondition=item['precondition'])
        if code!=200:
            entry.update(state='uncertain',status=code,error=body.get('error','unconfirmed'));receipt['state']='failed';record(receipt)
            return False
        entry.update(state='completed',status=code,records_receipt=body);record(receipt)
        return True
    for item in pending:
        if not attempt(item):return receipt
    code,preview=caller('GET','/api/v1/rooms/archive-preview')
    if code!=200:raise PlanRefused('preview_unavailable')
    planned=plan(preview,now=clock())
    candidates=[r for r in planned['rooms'] if r['candidate'] and r['room'] not in seen]
    if len(candidates)+len(pending)>MAX_ROOMS:raise PlanRefused('run_not_bounded')
    for row in candidates:
        canonical=json.dumps({'room':row['room'],'precondition':row['precondition']},sort_keys=True,separators=(',',':'))
        key='ra-'+hashlib.sha256(canonical.encode()).hexdigest()[:48]
        if not attempt({'room':row['room'],'key':key,'precondition':row['precondition'],'authority':authority}):return receipt
    receipt['state']='ok';record(receipt)
    return receipt
