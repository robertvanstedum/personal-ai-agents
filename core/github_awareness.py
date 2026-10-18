"""Fixed-public-repository observations, never a task store or execution authority.

One atomic cache/cursor snapshot; existing work registers and request ledger are
unchanged. Public REST polling is bounded, not a promise of every transient event.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import copy, fcntl, hashlib, json, math, os, re, shutil, stat, time
from pathlib import Path
from core import work_registers as files

REPO='robertvanstedum/personal-ai-agents'
PREFIX='/repos/'+REPO
MAX_FILE=2<<20
PAGE=100
PAGES=3
MAX_ITEMS=128
MAX_REQUESTS=40
STREAMS=('events','issues','issue_comments','review_comments','commits','runs','deployments')
LIMITATION=('Public REST observations only. Deleted/transient events and edits outside endpoint windows may be missing; '
 'events are limited to 300/30 days and may arrive 30 seconds to six hours late; workflow/deployment lists are bounded current windows, not complete historical journals. '
 'Texts are bounded excerpts; full source remains on GitHub. GitHub CI/deployment claims are not actual MiniMoi production image, data or feature acceptance evidence.')
class Refused(ValueError):pass
class FetchError(Refused):
 def __init__(self,code,retry_at=None):super().__init__(code);self.retry_at=retry_at

def need(value,code):
 if not value:raise Refused(code)
def stamp(value):
 need(isinstance(value,str),'invalid_timestamp');dt=datetime.fromisoformat(value.replace('Z','+00:00'));need(dt.tzinfo is not None,'invalid_timestamp');return dt.astimezone(timezone.utc)
def iso(value):return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def encoded(value):return (json.dumps(value,sort_keys=True,ensure_ascii=False)+'\n').encode()
def clean(value,n=200):return re.sub(r'[\x00-\x1f\x7f]',' ',str(value or ''))[:n]
def root_check(root):
 root=Path(root);need(root.is_absolute(),'absolute_root_required')
 for p in (root,*root.parents):
  s=p.lstat();need(stat.S_ISDIR(s.st_mode)and s.st_uid in {0,os.geteuid()}and not stat.S_ISLNK(s.st_mode)and not s.st_mode&0o022,'unsafe_cache_parent')
 s=root.lstat();need(s.st_uid==os.geteuid()and stat.S_IMODE(s.st_mode)==0o700,'private_cache_owner_required');return root

def validate(doc):
 need(type(doc)is dict and set(doc)=={'format','repo','writer_domain','generation','last_attempt','streams'}and doc['format']=='minimoi.github-observations/1'and doc['repo']==REPO,'cache_scope_invalid')
 if doc['last_attempt']is not None:stamp(doc['last_attempt'])
 need(type(doc['generation'])is int and doc['generation']>=0 and type(doc['writer_domain'])is str,'cache_generation_invalid')
 need(set(doc['streams'])==set(STREAMS),'cache_streams_invalid')
 for row in doc['streams'].values():
  need(type(row)is dict and set(row)=={'cursor','last_success','last_attempt','health','retry_at','items','evicted','coverage','gap'},'cache_row_invalid')
  need(row['gap']in {'initial_window','possible_poll_gap'},'cache_gap_invalid')
  need(row['health']in {'never_synced','ok','truncated','rate_limited','unavailable','invalid_response','request_budget','deadline'},'cache_health_invalid')
  for k in ('cursor','last_success','last_attempt','retry_at'):
   if row[k]is not None:stamp(row[k])
  need(type(row['items'])is list and len(row['items'])<=MAX_ITEMS and type(row['evicted'])is int and row['evicted']>=0,'cache_items_invalid')
  for item in row['items']:
   need(type(item)is dict and set(item)=={'identity','revision','kind','updated_at','title','state','url','excerpt','source'},'cache_item_invalid')
   need(all(type(v)is str for v in item.values())and len(encoded(item))<=10000,'cache_item_bound')
   need(item['kind']in STREAMS and item['source']=='GitHub public REST metadata'and re.fullmatch('[a-f0-9]{64}',item['revision'])and re.fullmatch('[A-Za-z0-9_.:-]{1,200}',item['identity']),'cache_item_scope')
   stamp(item['updated_at']);need(item['url'].startswith('https://github.com/'+REPO+'/'),'cache_url_scope')
 return doc

def load(root):
 root=root_check(root);path=root/'state.json';s=path.lstat();need(s.st_uid==os.geteuid()and stat.S_IMODE(s.st_mode)==0o600,'private_cache_file_required')
 return validate(json.loads(files.read(path,MAX_FILE)))

def initialize(root,*,now):
 """Explicit host preparation only; never invoked by a turn or failed poll."""
 root=Path(root);need(not os.path.lexists(root),'cache_already_exists');parent=root_check(root.parent)
 need(shutil.disk_usage(parent).free>=5*(1<<30)+MAX_FILE,'disk_low')
 doc={'format':'minimoi.github-observations/1','repo':REPO,'writer_domain':files.writer_domain(),'generation':0,'last_attempt':None,'streams':{}}
 for name in STREAMS:
  doc['streams'][name]={'cursor':iso(now-timedelta(hours=24)),'last_success':None,'last_attempt':None,'health':'never_synced','retry_at':None,'items':[],'evicted':0,'gap':'initial_window','coverage':'rolling_window'if name in {'events','runs','deployments'}else'updated_since_cursor'}
 root.mkdir(mode=0o700);files.sync(parent);files.atomic(root/'state.json',encoded(validate(doc)),exclusive=True)

def project(name,row,now):
 need(type(row)is dict,'invalid_response');ident=row.get('id')or row.get('sha');need(type(ident)in (str,int),'invalid_response')
 updated=row.get('updated_at')or row.get('created_at')or ((row.get('commit')or{}).get('committer')or{}).get('date')or iso(now);stamp(updated)
 url=row.get('html_url')or 'https://github.com/'+REPO+('/commit/'+str(ident)if name=='commits'else'/activity')
 need(type(url)is str and url.startswith('https://github.com/'+REPO+'/'),'invalid_response')
 title=row.get('title')or row.get('name')or row.get('type')or ((row.get('commit')or{}).get('message'))or name
 body=row.get('body')or ''
 # Event payload is never stored wholesale. Capture bounded descriptive context.
 if name=='events':
  payload=row.get('payload')or{};body=payload.get('action')or''
  for key in ('issue','pull_request','comment','review','release'):
   obj=payload.get(key)
   if isinstance(obj,dict):title=str(title)+' '+str(obj.get('title')or'');body=str(body)+' '+str(obj.get('body')or'')
 body='author='+clean((row.get('user')or row.get('actor')or{}).get('login'),80)+'; '+str(body)
 if name=='issues'and row.get('pull_request'):title='PR #'+str(row.get('number','?'))+' '+str(title)
 if name=='deployments':title='GitHub deployment '+clean(row.get('environment')or row.get('title'),100)+' '+str(title)
 if name=='runs':body+='; head_sha='+clean(row.get('head_sha'),40)+'; branch='+clean(row.get('head_branch'),80)
 state=row.get('conclusion')or row.get('status')or row.get('state')or ''
 item={'identity':name+':'+str(ident),'revision':'','kind':name,'updated_at':iso(stamp(updated)),'title':clean(title,240),'state':clean(state,60),'url':clean(url,350),'excerpt':clean(body,1000),'source':'GitHub public REST metadata'}
 item['revision']=hashlib.sha256(encoded(item)).hexdigest();return item

def route(name,cursor):
 since=iso(stamp(cursor)-timedelta(minutes=2))
 return {
  'events':(PREFIX+'/events',{}),
  'issues':(PREFIX+'/issues',{'state':'all','sort':'updated','direction':'desc','since':since}),
  'issue_comments':(PREFIX+'/issues/comments',{'since':since,'sort':'updated','direction':'desc'}),
  'review_comments':(PREFIX+'/pulls/comments',{'since':since,'sort':'updated','direction':'desc'}),
  'commits':(PREFIX+'/commits',{'since':since}),
  'runs':(PREFIX+'/actions/runs',{}),
  'deployments':(PREFIX+'/deployments',{})}[name]

def poll(doc,client,*,now,request_limit=3):
 """Pure injectable collector. Cursor advances only after complete bounded paging.
 Rolling-window cursors label poll freshness, not an assertion of full coverage.
 """
 need(type(request_limit)is int and 1<=request_limit<=MAX_REQUESTS,'request_limit_invalid')
 result=copy.deepcopy(validate(doc));result['last_attempt']=iso(now);result['generation']+=1;requests=0
 def get(path,params):
  nonlocal requests
  need(requests<request_limit,'request_budget');requests+=1
  status,data,headers=client.get(path,params);headers={str(k).lower():v for k,v in headers.items()}
  if status in (403,429)or str(headers.get('x-ratelimit-remaining','unknown'))in {str(i)for i in range(11)}:
   retry=None
   try:
    delay=int(headers.get('retry-after',0));reset=int(headers.get('x-ratelimit-reset',0));retry=iso(max(now+timedelta(seconds=min(3600,max(300,delay))),min(now+timedelta(hours=1),datetime.fromtimestamp(reset,timezone.utc))))
   except (TypeError,ValueError,OverflowError,OSError):retry=iso(now+timedelta(minutes=15))
   raise FetchError('rate_limited',retry)
  if status!=200:raise FetchError('unavailable')
  interval=headers.get('x-poll-interval')
  if interval is not None:
   try:need(0<int(interval)<=86400,'invalid_response')
   except (TypeError,ValueError):raise FetchError('invalid_response')from None
   stream_wait[0]=max(stream_wait[0],int(interval))
  return data
 for name in sorted(STREAMS,key=lambda n:(doc['streams'][n]['last_attempt']or'',n)):
  previous=doc['streams'][name];out=result['streams'][name]
  if previous['retry_at']and stamp(previous['retry_at'])>now:continue
  if requests>=request_limit:continue
  out['last_attempt']=iso(now);out['retry_at']=None;found=[];stream_wait=[300]
  if previous['last_success']is not None and(now-stamp(previous['last_success'])).total_seconds()>1800:out['gap']='possible_poll_gap'
  try:
   path,params=route(name,previous['cursor']);complete=False
   # Rolling-window streams (events, runs, deployments) have no since filter, so
   # older pages only repeat history: read the newest page and call it complete.
   # A busy repository otherwise spends the whole budget and never succeeds.
   rolling=previous['coverage']=='rolling_window'
   since=stamp(previous['cursor'])-timedelta(minutes=2)
   for page in range(1,PAGES+1):
    data=get(path,{**params,'page':page,'per_page':PAGE})
    if name=='runs':need(type(data)is dict,'invalid_response');data=data.get('workflow_runs')
    need(type(data)is list and len(data)<=PAGE,'invalid_response')
    page_items=[project(name,row,now)for row in data];found.extend(page_items)
    if rolling:
     # A full newest page entirely after the last success may have skipped items.
     if(len(data)==PAGE and previous['last_success']is not None
        and min(i['updated_at']for i in page_items)>previous['last_success']):out['gap']='possible_poll_gap'
    if name=='deployments':
     # Exact numeric IDs only; never follow provider-returned URL with a token.
     # Statuses only for deployments changed since the cursor; older ones are kept.
     for row in data:
      need(type(row.get('id'))is int and row['id']>0,'invalid_response')
      if stamp(row.get('updated_at')or row.get('created_at')or iso(now))<since:continue
      statuses=get(PREFIX+'/deployments/'+str(row['id'])+'/statuses',{'per_page':100,'page':1})
      need(type(statuses)is list,'invalid_response');need(len(statuses)<100,'truncated')
      for status in statuses:
       child=dict(status);child['id']='deployment-'+str(row['id'])+'-status-'+str(status['id']);child['html_url']=row.get('html_url')or 'https://github.com/'+REPO+'/deployments';child['body']=status.get('description','');child['environment']=row.get('environment','');found.append(project(name,child,now))
    if rolling or len(data)<PAGE:complete=True;break
   merged={item['identity']:item for item in previous['items']}
   for item in found:merged[item['identity']]=item
   ordered=sorted(merged.values(),key=lambda item:(item['updated_at'],item['identity']),reverse=True)
   out['evicted']+=max(0,len(ordered)-MAX_ITEMS);out['items']=ordered[:MAX_ITEMS]
   out['health']='ok'if complete else'truncated'
   if complete:out['cursor']=iso(now);out['last_success']=iso(now)
   if stream_wait[0]>300:out['retry_at']=iso(now+timedelta(seconds=stream_wait[0]))
  except Exception as error:
   code=str(error)if isinstance(error,Refused)else'unavailable'
   if code=='rate_limited':
    for pending in result['streams'].values():pending['retry_at']=getattr(error,'retry_at',None)
    requests=request_limit
   out['health']=code if code in {'invalid_response','rate_limited','request_budget','deadline','truncated'}else'unavailable';out['retry_at']=getattr(error,'retry_at',None)
 return validate(result)

class PublicClient:
 """Fixed origin, GET only, no redirects/proxies, bounded bytes/time; no SDK retry."""
 def __init__(self,*,token=None,clock=time.monotonic):
  self.token=token;self.clock=clock;self.deadline=clock()+60;self.bytes=0
 def get(self,path,params):
  import urllib.parse,urllib.request,urllib.error
  need(path.startswith(PREFIX+'/')and re.fullmatch('/[A-Za-z0-9_./-]+',path),'fixed_github_path_required')
  remain=self.deadline-self.clock();need(remain>0,'deadline')
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):return None
  headers={'Accept':'application/vnd.github+json','User-Agent':'MiniMoi-repository-awareness','X-GitHub-Api-Version':'2022-11-28'}
  if self.token:headers['Authorization']='Bearer '+self.token
  request=urllib.request.Request('https://api.github.com'+path+'?'+urllib.parse.urlencode(params),headers=headers,method='GET')
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
  try:
   with opener.open(request,timeout=min(5,remain))as response:
    raw=bytearray()
    while True:
     need(self.clock()<self.deadline,'deadline');chunk=response.read(65536)
     if not chunk:break
     raw.extend(chunk);self.bytes+=len(chunk);need(len(raw)<=1<<20 and self.bytes<=8<<20,'invalid_response')
    return response.status,json.loads(raw),dict(response.headers)
  except urllib.error.HTTPError as error:return error.code,None,dict(error.headers)

def _lock(root):
 """The cache's one writer lock (sync and rebind); nonblocking, so a second writer refuses."""
 fd=os.open(root/'sync.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
 try:
  attrs=os.fstat(fd);need(stat.S_ISREG(attrs.st_mode)and attrs.st_uid==os.geteuid()and stat.S_IMODE(attrs.st_mode)==0o600 and attrs.st_nlink==1,'unsafe_sync_lock')
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise Refused('sync_busy')from None
 except BaseException:os.close(fd);raise
 return fd

def health(root,*,now):
 """Read-only per-stream health for an operator or the jobs tile. No network, no write."""
 doc=load(root);streams={}
 for name,row in doc['streams'].items():
  age=None if row['last_success']is None else int((now-stamp(row['last_success'])).total_seconds())
  streams[name]={'health':row['health'],'age_seconds':age,'gap':row['gap'],'retry_at':row['retry_at'],'items':len(row['items'])}
 return {'repo':REPO,'generation':doc['generation'],'last_attempt':doc['last_attempt'],
         'writer_bound_to_this_boot':doc['writer_domain']==files.writer_domain(),'streams':streams}

def rebind(root,*,now):
 """Supervised operator step after a kernel restart: adopt this boot as the writer.

 Writes are held across a restart (writer_domain_changed) until this runs. It never
 polls. Every stream is marked possible_poll_gap: nothing was observed meanwhile.
 """
 root=root_check(root);fd=_lock(root)
 try:
  doc=load(root);current=files.writer_domain();need(doc['writer_domain']!=current,'writer_domain_unchanged')
  doc['writer_domain']=current;doc['generation']+=1
  for row in doc['streams'].values():row['gap']='possible_poll_gap'
  files.atomic(root/'state.json',encoded(validate(doc)));return doc
 finally:os.close(fd)

class Admission:
 """One writer admission for the cache: held across a whole run (its job-status start,
 the poll and its job-status finish), so a rejected competitor touches nothing."""
 def __init__(self,root):self.root=root_check(root);self.fd=None
 def __enter__(self):self.fd=_lock(self.root);return self
 def __exit__(self,*exc):os.close(self.fd);self.fd=None

def due(admission,*,now):
 """Inside an admission: whether a poll may run now (minimum 300 s cadence)."""
 need(admission.fd is not None,'admission_required')
 doc=load(admission.root);return doc['last_attempt']is None or(now-stamp(doc['last_attempt'])).total_seconds()>=300

def sync_admitted(admission,client,*,now):
 """The poll itself, for a caller that already holds the admission."""
 need(admission.fd is not None,'admission_required');root=admission.root
 need(shutil.disk_usage(root).free>=5*(1<<30)+MAX_FILE,'disk_low')
 doc=load(root);need(doc['writer_domain']==files.writer_domain(),'writer_domain_changed')
 # No concurrent polling before next five-minute interval; readers never poll.
 if doc['last_attempt']is not None:need((now-stamp(doc['last_attempt'])).total_seconds()>=300,'poll_not_due')
 updated=poll(doc,client,now=now,request_limit=40 if getattr(client,'token',None)else 3);raw=encoded(updated);need(len(raw)<=MAX_FILE,'cache_capacity');files.atomic(root/'state.json',raw);return updated

def sync(root,client,*,now):
 with Admission(root)as admission:return sync_admitted(admission,client,now=now)
