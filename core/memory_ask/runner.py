"""Frozen saved-query Ask runner: retrieval only, private immutable evidence.
No model, graph, scheduler or new permission registry. Host owns profile and
confirmation verification. A COMPLETE marker means execution ended, not success.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import time
import uuid
from dataclasses import dataclass,field
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from core.memory_ask import consumer,context,citation

SCHEMA='minimoi.ask-query-suite.v1'
RUN_SCHEMA='minimoi.ask-query-run.v1'
STATES=('pass','fail','error','unsupported','not_run','needs_review')
MAX_SUITE=128*1024
MAX_CASES=25
RESERVE=5*1024**3
SLACK=16*1024**2
NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')
CHI=ZoneInfo('America/Chicago')
class Refused(ValueError):pass
class ReadBudgetExceeded(Refused):pass

class ReadBudget:
    """Conservative aggregate byte admission, shared across verified run loads."""
    def __init__(self,max_bytes):
        if type(max_bytes)is not int or max_bytes<0:raise Refused('invalid_read_budget')
        self.remaining=max_bytes
    def charge(self,size):
        if size>self.remaining:raise ReadBudgetExceeded('run_read_budget_exceeded')
        self.remaining-=size


def encoded(obj):return (json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n').encode()
def sha(data):return hashlib.sha256(data).hexdigest()
def fields(obj,required,optional=()):
    if not isinstance(obj,dict) or set(obj)-set(required)-set(optional) or not set(required)<=set(obj):raise Refused('invalid_fields')
def string(value,maxlen=1000):
    if not isinstance(value,str) or not value.strip() or len(value)>maxlen or any(ord(c)<32 for c in value):raise Refused('invalid_text')
    try:value.encode('utf-8')
    except UnicodeError:raise Refused('invalid_text') from None
    return value

def validate(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_SUITE:raise Refused('suite_size')
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out:raise Refused('duplicate_json_field')
            out[k]=v
        return out
    try:suite=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(Refused('invalid_number')))
    except (ValueError,UnicodeError):raise Refused('invalid_json') from None
    fields(suite,('schema','version','suite_id','profile_ref','confirmation_ref','cases'))
    if suite['schema']!=SCHEMA:raise Refused('unsupported_schema')
    for k in ('version','suite_id','profile_ref'):
        if not isinstance(suite[k],str) or not NAME.fullmatch(suite[k]):raise Refused('invalid_id')
    string(suite['confirmation_ref'],200)
    cases=suite['cases']
    if not isinstance(cases,list) or not 1<=len(cases)<=MAX_CASES:raise Refused('case_count')
    seen=set(); artifact_names={'manifest.json','suite.json','report.json','report.txt','execution-receipt.json'}
    for c in cases:
        fields(c,('case_id','question','purpose','processing','mode','window','expected','assertions'))
        if not isinstance(c['case_id'],str) or not NAME.fullmatch(c['case_id']) or c['case_id'] in seen:raise Refused('case_id')
        new_names={c['case_id']+'.json',c['case_id']+'.attempt.json'}
        if artifact_names & new_names:raise Refused('case_file_collision')
        artifact_names.update(new_names)
        seen.add(c['case_id']);string(c['question']);string(c['purpose'],500)
        if c['processing'] not in ('local_only','shareable'):raise Refused('processing')
        string(c['mode'],80)
        w=c['window'];fields(w,('kind','axis'),('start','end','days'))
        if w['kind']=='none':
            if set(w)!={'kind','axis'} or w['axis']!='none':raise Refused('window')
        elif w['kind']=='fixed':
            if set(w)!={'kind','axis','start','end'}:raise Refused('window')
            for k in ('start','end'):
                try:d=datetime.fromisoformat(w[k].replace('Z','+00:00'))
                except (ValueError,TypeError,AttributeError):raise Refused('window') from None
                if d.tzinfo is None:raise Refused('window_timezone')
            if datetime.fromisoformat(w['start'].replace('Z','+00:00'))>=datetime.fromisoformat(w['end'].replace('Z','+00:00')):raise Refused('window_order')
        elif w['kind']=='rolling_days':
            if set(w)!={'kind','axis','days'} or type(w['days']) is not int or not 1<=w['days']<=3660:raise Refused('window')
        else:raise Refused('window')
        string(w['axis'],80)
        if not isinstance(c['expected'],list) or len(c['expected'])>20:raise Refused('expected')
        for e in c['expected']:
            fields(e,('file','lines'),('record_id','edition'))
            if not isinstance(e['file'],str) or not re.fullmatch(r'[\w./@+-]+\.md',e['file']) or e['file'].startswith('/') or '..' in e['file'].split('/'):raise Refused('evidence_file')
            if not isinstance(e['lines'],list) or len(e['lines'])!=2 or any(type(n) is not int for n in e['lines']) or not 1<=e['lines'][0]<=e['lines'][1]<=100_000_000:raise Refused('evidence_range')
            for k in ('record_id','edition'):
                if k in e:string(e[k],200)
        fields(c['assertions'],('min_passages','require_meaning','max_unresolved'))
        if type(c['assertions']['min_passages']) is not int or not 0<=c['assertions']['min_passages']<=11 or type(c['assertions']['max_unresolved']) is not int or not 0<=c['assertions']['max_unresolved']<=100 or type(c['assertions']['require_meaning']) is not bool:raise Refused('assertions')
    return suite

@dataclass(frozen=True)
class Profile:
    reference:str
    config:consumer.Config=field(repr=False)
    local_only_allowed:bool=False
    permissions_fingerprint:str='unknown'
    metadata:dict=field(default_factory=dict)


def plan(suite,*,at=None):
    at=at or datetime.now(timezone.utc)
    if at.tzinfo is None:raise Refused('clock_timezone')
    end=at.astimezone(CHI).replace(hour=0,minute=0,second=0,microsecond=0)
    rows=[]
    for c in suite['cases']:
        w=c['window'];bounds=None
        if w['kind']=='rolling_days':
            bounds={'start':(end-timedelta(days=w['days'])).astimezone(timezone.utc).isoformat(),'end':end.astimezone(timezone.utc).isoformat(),'timezone':'America/Chicago','axis':w['axis']}
        elif w['kind']=='fixed':bounds={k:datetime.fromisoformat(w[k].replace('Z','+00:00')).astimezone(timezone.utc).isoformat() for k in ('start','end')};bounds['axis']=w['axis']
        unsupported='unsupported_mode' if c['mode']!='ask_curated' else ('unsupported_date_precision_or_axis' if w['kind']!='none' else '')
        rows.append({'case_id':c['case_id'],'window':bounds,'unsupported':unsupported})
    return rows


def ask_case(question,profile,*,post=consumer.default_post):
    """Existing bearer grant, curated API and endpoint safety; no include_raw.
    Return only response from Ask. No synthesis/hosted reference operation.
    """
    url=consumer.endpoint(profile.config)
    if url is None:raise Refused('bad_config')
    body=encoded({'query':question,'k':profile.config.k})
    status,raw=post(url,{'Authorization':'Bearer '+profile.config.token,'Content-Type':'application/json','Accept':'application/json'},body,profile.config.timeout_s)
    if type(status) is not int or not isinstance(raw,bytes) or len(raw)>consumer.MAX_RESPONSE_BYTES:raise Refused('invalid_response')
    if status!=200:raise Refused(consumer._HTTP_CODES.get(status,'unavailable'))
    try:return json.loads(raw)
    except (ValueError,UnicodeError):raise Refused('invalid_response') from None


def evaluate(case,result,*,verify_source=None):
    built=context.build(result,allowed_tiers=('curated',))
    if built.report['status']=='unavailable':return {'outcome':'error','code':'unavailable','retrieval':built.report}
    retained=built.citations['keyword']+built.citations['meaning']
    if built.citations['adjacent'].get('file'):retained.append(built.citations['adjacent'])
    checks=[]
    unsupported_assertions=[]
    for e in case['expected']:
        state=citation.check(f"This expected evidence range is retained {e['file']}:{e['lines'][0]}-{e['lines'][1]}.",built.citations)[0]['state']
        checks.append({'expected':e,'retained':state=='supported'})
    # Use actual context-prepared hits, not another raw hit in the same file.
    # The reviewed renderer JSON-escapes all source newlines, so its one DATA
    # line is stable; no source content is treated as instructions or executed.
    data=json.loads(built.block.splitlines()[2])
    hits=data['keyword']+data['meaning']
    if data['adjacent'].get('file'):hits.append(data['adjacent'])
    for check in checks:
        e=check['expected']
        if any(k in e for k in ('record_id','edition')):
            matching=[h for h in hits if h['file']==e['file'] and h['lines'][0]<=e['lines'][0]<=e['lines'][1]<=h['lines'][1]]
            if matching and any(not any(k in h for h in matching) for k in ('record_id','edition') if k in e):
                unsupported_assertions.append('identity_or_edition_unavailable')
                check['identity_check']='unsupported'
            else:
                check['retained']=check['retained'] and any(all(h.get(k)==e[k] for k in ('record_id','edition') if k in e) for h in matching)
    status=result.get('status',{})
    source_states=[]
    for check in checks:
        if not check['retained']:continue
        proof = verify_source(check['expected']) if verify_source else None
        verified = (isinstance(proof,dict) and proof.get('verified') is True and isinstance(proof.get('source_sha256'),str) and bool(re.fullmatch(r'[0-9a-f]{64}',proof['source_sha256'])))
        source_states.append(verified if proof is not None else None)
        check['source_verified'] = verified if proof is not None else None
        if verified:check['source_sha256']=proof['source_sha256']
    required=case['assertions']
    failures=[]
    if not all(c['retained'] for c in checks):failures.append('missing_expected_evidence')
    if False in source_states:failures.append('source_verification_failed')
    if built.report['passages_prepared']<required['min_passages']:failures.append('insufficient_passages')
    if built.report['unresolved_count']>required['max_unresolved']:failures.append('unresolved_sources')
    if required['require_meaning'] and status.get('meaning')!='ok':
        return {'outcome':'error','code':'meaning_unavailable','retrieval':built.report,'checks':checks,'evidence_block':built.block}
    return {'outcome':'fail' if failures else ('unsupported' if unsupported_assertions else ('pass' if checks and source_states and all(v is True for v in source_states) else 'needs_review')),
            'code':','.join(failures+unsupported_assertions),'source_resolution':'verified_by_host' if source_states and all(v is True for v in source_states) else 'not_verified','checks':checks,'retained_citations':built.citations,'retrieval':built.report,
            'evidence_block':built.block,'human_usefulness':'needs_review','cost':None,'query_id':result.get('query_id'),
            'watermark':{k:status.get(k) for k in ('index_built_at','index_updated_at','embedder')},
            'caller':result.get('caller'),'tier':result.get('tier')}


def private_root(path):
    path=os.path.abspath(os.fspath(path));fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for component in path.split('/')[1:]:
            if not component:continue
            child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        s=os.fstat(fd)
        if s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)!=0o700:raise Refused('result_root_not_private')
        return fd
    except BaseException:os.close(fd);raise


def publish(fd,name,obj,*,raw=None):
    data=encoded(obj) if raw is None else raw
    temporary='.'+uuid.uuid4().hex+'.tmp'
    f=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        view=memoryview(data)
        while view:
            n=os.write(f,view)
            if n<=0:raise OSError('short_write')
            view=view[n:]
        os.fsync(f)
    finally:os.close(f)
    # Atomic exclusive publication: no completed file is ever replaced.
    os.link(temporary,name,src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
    os.unlink(temporary,dir_fd=fd);os.fsync(fd)


def run(raw,*,profile,root,verify_confirmation,retrieve=ask_case,clock=None,free_bytes=None,verify_source=None,confirmation_status="host_verified"):
    if confirmation_status not in ('host_verified','operator_attested_not_gold_verified'):raise Refused('confirmation_status')
    suite=validate(raw);start=(clock or (lambda:datetime.now(timezone.utc)))()
    if suite['profile_ref']!=profile.reference or verify_confirmation(sha(raw),suite['confirmation_ref'],profile.reference) is not True:raise Refused('owner_confirmation_required')
    if consumer.endpoint(profile.config) is None or type(profile.config.k) is not int or not 1<=profile.config.k<=consumer.MAX_K:raise Refused('bad_profile')
    if type(profile.config.timeout_s) not in (int,float) or not 0<profile.config.timeout_s<=consumer.TIMEOUT_S:raise Refused('bad_profile')
    if type(profile.local_only_allowed) is not bool:raise Refused('bad_profile')
    rows=plan(suite,at=start);fd=private_root(root);runfd=None;rid='r-'+uuid.uuid4().hex
    try:
        measure=free_bytes or (lambda:os.fstatvfs(fd).f_bavail*os.fstatvfs(fd).f_frsize)
        available=measure()
        if type(available) is not int or available<RESERVE+SLACK:raise Refused('disk_low')
        os.mkdir(rid,0o700,dir_fd=fd);os.fsync(fd);runfd=os.open(rid,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        hashes={}
        def save(name,obj,*,raw=None):
            if measure()<RESERVE+SLACK:raise Refused('disk_low')
            publish(runfd,name,obj,raw=raw)
            hashes[name]=sha(encoded(obj) if raw is None else raw)
        manifest={'schema':RUN_SCHEMA,'run_id':rid,'suite_hash':sha(raw),'suite_id':suite['suite_id'],'version':suite['version'],'profile':profile.reference,'confirmation_ref':suite['confirmation_ref'],'permissions':profile.permissions_fingerprint,'metadata':profile.metadata,'local_only_allowed':profile.local_only_allowed,'started_at':start.isoformat(),'plan':rows,'cost':None}
        save('INCOMPLETE',{'reason':'run_in_progress'});save('manifest.json',manifest);save('suite.json',None,raw=raw)
        save('execution-receipt.json',{'suite_hash':sha(raw),'confirmation_ref':suite['confirmation_ref'],'profile':profile.reference,'confirmation_status':confirmation_status,'question_confirmation':'needs_review' if confirmation_status=='operator_attested_not_gold_verified' else 'host_verified','at':start.isoformat()})
        results=[]
        for case,row in zip(suite['cases'],rows):
            outcome={'case_id':case['case_id'],'window':row['window'],'human_usefulness':'needs_review','cost':None}
            if row['unsupported']:outcome.update(outcome='unsupported',code=row['unsupported'])
            elif case['processing']=='local_only' and not profile.local_only_allowed:outcome.update(outcome='not_run',code='processing_profile_not_permitted')
            else:
                save(case['case_id']+'.attempt.json',{'case_id':case['case_id'],'state':'attempting','at':datetime.now(timezone.utc).isoformat()})
                before=time.monotonic()
                try:outcome.update(evaluate(case,retrieve(case['question'],profile),verify_source=verify_source))
                except Exception as exc:outcome.update(outcome='error',code=str(exc) if isinstance(exc,Refused) and str(exc) in context.ERRORS|{'invalid_response','bad_config'} else 'unavailable',operation='uncertain_no_retry')
                outcome['latency_ms']=round((time.monotonic()-before)*1000,2)
            save(case['case_id']+'.json',outcome);results.append(outcome)
        report={'run_id':rid,'case_ids':[r['case_id'] for r in results],'counts':{state:sum(r['outcome']==state for r in results) for state in STATES},'human_usefulness':'needs_review','question_confirmation':'needs_review' if confirmation_status=='operator_attested_not_gold_verified' else 'host_verified','watermarks':[r.get('watermark') for r in results],'returned_identity':[{'caller':r.get('caller'),'tier':r.get('tier')} for r in results],'finished_at':datetime.now(timezone.utc).isoformat()}
        save('report.json',report)
        text='Saved Ask run '+rid+'\n'+'\n'.join(f"{r['case_id']}: {r['outcome']} ({r.get('code','')})" for r in results)+'\nHuman usefulness: needs_review. Gold question confirmation: '+('needs_review' if confirmation_status=='operator_attested_not_gold_verified' else 'host_verified')+'. Cost: unknown.\n'
        save('report.txt',None,raw=text.encode())
        hashes.pop('INCOMPLETE',None)
        if measure()<RESERVE+SLACK:raise Refused('disk_low')
        publish(runfd,'.COMPLETE.tmp',{'report_sha256':sha(encoded(report)),'files':dict(hashes)})
        os.rename('.COMPLETE.tmp','COMPLETE',src_dir_fd=runfd,dst_dir_fd=runfd);os.fsync(runfd)
        os.unlink('INCOMPLETE',dir_fd=runfd);os.fsync(runfd)
        return {'run_id':rid,'counts':report['counts'],'human_usefulness':'needs_review','question_confirmation':report['question_confirmation']}
    finally:
        if runfd is not None:os.close(runfd)
        os.close(fd)


def compare(first,second):
    warnings=[]
    if any(r['report'].get('question_confirmation')!='host_verified' for r in (first,second)):warnings.append('gold_question_confirmation_needs_review')
    if any(r['manifest'].get('permissions') in (None,'unknown') for r in (first,second)):warnings.append('unknown_permissions')
    for key in ('suite_hash','profile','permissions','metadata','plan'):
        if first['manifest'].get(key)!=second['manifest'].get(key):warnings.append('changed_window' if key=='plan' else 'changed_'+key)
    if first['report'].get('returned_identity')!=second['report'].get('returned_identity'):warnings.append('changed_returned_identity')
    if first['report'].get('watermarks')!=second['report'].get('watermarks'):warnings.append('changed_corpus_index_model')
    def known_watermarks(run):
        planned=run['manifest'].get('plan')
        values=run['report'].get('watermarks')
        if not isinstance(planned,list) or not 1<=len(planned)<=MAX_CASES or not isinstance(values,list) or len(values)!=len(planned):return False
        ids=[p.get('case_id') if isinstance(p,dict) else None for p in planned]
        if any(not isinstance(i,str) or not NAME.fullmatch(i) for i in ids) or len(set(ids))!=len(ids) or run['report'].get('case_ids')!=ids:return False
        keys=('index_built_at','index_updated_at','embedder')
        return all(isinstance(w,dict) and all(isinstance(w.get(k),str) and bool(w[k].strip()) for k in keys) for w in values)
    if not all(known_watermarks(r) for r in (first,second)):warnings.append('unknown_corpus_index_model')
    return {'warnings':warnings,'first':first['report']['counts'],'second':second['report']['counts'],'human_usefulness':'needs_review','comparable':not warnings}


def load_run(root,rid,*,include_details=False,max_bytes=None,read_budget=None):
    if not isinstance(rid,str) or not re.fullmatch(r'r-[0-9a-f]{32}',rid):raise Refused('run_id')
    local_budget=ReadBudget(max_bytes)if max_bytes is not None else None
    if read_budget is not None and not isinstance(read_budget,ReadBudget):raise Refused('invalid_read_budget')
    fd=private_root(root);child=None
    try:
        child=os.open(rid,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        st=os.fstat(child)
        if st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o700:raise Refused('run_not_private')
        def read(name):
            f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=child)
            try:
                st=os.fstat(f)
                if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>1024*1024:raise Refused('run_file_invalid')
                # Include the growth-detection byte before any read. A shared
                # budget exhaustion must escape rather than look like a bad run.
                read_size=st.st_size+1
                for budget in (local_budget,read_budget):
                    if budget is not None:budget.charge(read_size)
                data=os.read(f,read_size)
                if len(data)>1024*1024:raise Refused('run_file_invalid')
                return data
            finally:os.close(f)
        try:
            os.stat('INCOMPLETE',dir_fd=child,follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise Refused('run_incomplete')
        marker=json.loads(read('COMPLETE'))
        files=marker.get('files')
        static={'manifest.json','suite.json','report.json','report.txt','execution-receipt.json'}
        if not isinstance(files,dict) or not 5<=len(files)<=2*MAX_CASES+5 or not static<=set(files):raise Refused('run_hashes_invalid')
        for name,expected in files.items():
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',name) or not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):raise Refused('run_hashes_invalid')
        # Verify mandatory bytes before trusting their IDs, plans or counts.
        raw_static={name:read(name) for name in static}
        if any(sha(data)!=files[name] for name,data in raw_static.items()):raise Refused('run_hash_mismatch')
        raw_suite=raw_static['suite.json'];suite=validate(raw_suite)
        manifest=json.loads(raw_static['manifest.json']);report=json.loads(raw_static['report.json']);receipt=json.loads(raw_static['execution-receipt.json'])
        if manifest.get('schema')!=RUN_SCHEMA or manifest.get('run_id')!=rid or report.get('run_id')!=rid or manifest.get('suite_hash')!=sha(raw_suite):raise Refused('run_identity_mismatch')
        for key in ('suite_id','version','confirmation_ref'):
            if manifest.get(key)!=suite[key]:raise Refused('suite_manifest_mismatch')
        if manifest.get('profile')!=suite['profile_ref'] or any(receipt.get(k)!=manifest.get(k) for k in ('suite_hash','confirmation_ref','profile')):raise Refused('suite_receipt_mismatch')
        status=receipt.get('confirmation_status')
        if status not in ('host_verified','operator_attested_not_gold_verified'):raise Refused('confirmation_status')
        question='host_verified' if status=='host_verified' else 'needs_review'
        if receipt.get('question_confirmation')!=question or report.get('question_confirmation')!=question:raise Refused('question_confirmation_mismatch')
        start=datetime.fromisoformat(manifest['started_at'])
        planned=plan(suite,at=start)
        if manifest.get('plan')!=planned or type(manifest.get('local_only_allowed')) is not bool:raise Refused('run_plan_mismatch')
        expected=set(static);cases=[]
        for case,row in zip(suite['cases'],planned):
            name=case['case_id']+'.json';expected.add(name)
            attempted=not row['unsupported'] and not (case['processing']=='local_only' and not manifest['local_only_allowed'])
            if attempted:expected.add(case['case_id']+'.attempt.json')
        if set(files)!=expected or set(os.listdir(child))!=expected|{'COMPLETE'}:raise Refused('run_artifacts_mismatch')
        dynamic={}
        for name in expected-static:
            dynamic[name]=read(name)
            if sha(dynamic[name])!=files[name]:raise Refused('run_hash_mismatch')
        for case,row in zip(suite['cases'],planned):
            saved=json.loads(dynamic[case['case_id']+'.json'])
            if saved.get('case_id')!=case['case_id'] or saved.get('window')!=row['window'] or saved.get('outcome') not in STATES:raise Refused('case_identity_mismatch')
            cases.append(saved)
        ids=[c['case_id'] for c in cases]
        if report.get('case_ids')!=ids or report.get('counts')!={state:sum(c['outcome']==state for c in cases) for state in STATES}:raise Refused('run_counts_mismatch')
        if report.get('watermarks')!=[c.get('watermark') for c in cases] or report.get('returned_identity')!=[{'caller':c.get('caller'),'tier':c.get('tier')} for c in cases]:raise Refused('run_coverage_mismatch')
        if marker.get('report_sha256')!=sha(raw_static['report.json']):raise Refused('report_hash_mismatch')
        result={'manifest':manifest,'report':report}
        if include_details:result.update(suite=suite,cases=cases)
        return result
    except ReadBudgetExceeded:raise
    except (OSError,ValueError,KeyError,TypeError,AttributeError):raise Refused('run_incomplete_or_invalid') from None
    finally:
        if child is not None:os.close(child)
        os.close(fd)
