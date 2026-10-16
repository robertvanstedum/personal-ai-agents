"""Shared CoS/MC handoff transport, not a Work/approval engine.

Private root: <data>/handoffs/{cos,mc}/<uuid>/handoff.json + files/.
Acknowledgement is delivery evidence, not owner approval. Original source files
are never moved/deleted. Six calendar months starts at the later of completion
and acknowledgement. Open/unacknowledged work never expires. Needed files must
have a still-verifiable durable copy; explicitly disposable copies may expire.
No automatic job or deletion is enabled by installing this module.
"""
from __future__ import annotations
import argparse, calendar, contextlib, hashlib, json, os, re, shutil, stat, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

ROLES = ('cos', 'mc')
FILE_MAX = 25 * 1024**2
HANDOFF_MAX = 100 * 1024**2
META_MAX = 128 * 1024
RESERVE = 5 * 1024**3
FORMAT = 'minimoi-handoff/1'
TEMPLATE = {'title':'', 'request':'', 'recipient':'', 'conversation_id':'', 'job_id':'',
            'record_refs':[], 'acceptance':[], 'owner_decisions':[], 'proposals':[],
            'open_questions':[], 'next_actions':[]}
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

class Refused(ValueError): pass

def need(ok, reason):
    if not ok: raise Refused(reason)

def stamp(): return datetime.now(timezone.utc).isoformat()
def dt(s):
    d = datetime.fromisoformat(s)
    need(d.tzinfo is not None, 'timezone_required')
    return d.astimezone(timezone.utc)
def six_months(s):
    d=dt(s); month=d.month-1+6; year=d.year+month//12; month=month%12+1
    return d.replace(year=year, month=month, day=min(d.day,calendar.monthrange(year,month)[1])).isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def ident(s): return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def hid(value):
    need(isinstance(value,str) and str(uuid.UUID(value))==value and uuid.UUID(value).version==4,'invalid_handoff_id')
    return value

def open_dir(path):
    p=Path(path); need(p.is_absolute() and '..' not in p.parts,'absolute_root_required')
    fd=os.open('/',DIR_FLAGS)
    try:
        for part in p.parts[1:]:
            nxt=os.open(part,DIR_FLAGS,dir_fd=fd);os.close(fd);fd=nxt
        return fd
    except BaseException: os.close(fd); raise

def private_dir(fd):
    s=os.fstat(fd);need(s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700,'private_directory_required')

def mkdir(fd,name):
    try: os.mkdir(name,0o700,dir_fd=fd)
    except FileExistsError: pass
    sub=os.open(name,DIR_FLAGS,dir_fd=fd)
    try:private_dir(sub)
    except BaseException:os.close(sub);raise
    return sub

def read(fd,name,maximum=META_MAX):
    f=os.open(name,FILE_FLAGS,dir_fd=fd)
    with os.fdopen(f,'rb') as h:
        s=os.fstat(h.fileno());need(stat.S_ISREG(s.st_mode) and s.st_nlink==1,'regular_single_link_required')
        need(s.st_size<=maximum,'file_too_large')
        data=h.read(maximum+1)
        need(len(data)<=maximum and ident(s)==ident(os.fstat(h.fileno()))==ident(os.stat(name,dir_fd=fd,follow_symlinks=False)),'file_changed_or_too_large')
        return data

def source(path,maximum=FILE_MAX):
    p=Path(path); need(p.is_absolute(),'absolute_source_required');fd=open_dir(p.parent)
    try:
        before=os.stat(p.name,dir_fd=fd,follow_symlinks=False)
        data=read(fd,p.name,maximum)
        fresh=open_dir(p.parent)
        try:
            need((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==(os.fstat(fresh).st_dev,os.fstat(fresh).st_ino),'source_parent_changed')
            need(ident(before)==ident(os.stat(p.name,dir_fd=fresh,follow_symlinks=False)),'source_path_changed')
        finally:os.close(fresh)
        return data
    finally:os.close(fd)

def write(fd,name,data):
    need(len(data)<=META_MAX,'metadata_too_large')
    tmp='.'+uuid.uuid4().hex+'.tmp'
    f=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        with os.fdopen(f,'wb') as h:h.write(data);h.flush();os.fsync(h.fileno())
        os.replace(tmp,name,src_dir_fd=fd,dst_dir_fd=fd);os.fsync(fd)
    finally:
        try:os.unlink(tmp,dir_fd=fd)
        except FileNotFoundError:pass

def validate_request(request):
    need(isinstance(request,dict) and set(request)==set(TEMPLATE),'invalid_template_fields')
    for k in ('title','request','recipient','conversation_id','job_id'):
        need(isinstance(request[k],str) and len(request[k])<=8000 and '\x00' not in request[k],'invalid_request_text')
    need(all(request[k].strip() for k in ('title','request','recipient','conversation_id','job_id')),'missing_request_identity')
    for k in set(TEMPLATE)-{'title','request','recipient','conversation_id','job_id'}:
        need(isinstance(request[k],list) and len(request[k])<=30 and all(isinstance(x,str) and len(x)<=2000 for x in request[k]),'invalid_request_list')

class Store:
    def __init__(self,root,durable_roots=None):
        self.root=Path(root)
        data=Path('/minimoi/data') if self.root.is_relative_to('/minimoi/data') else Path.home()/'minimoi-staging/data'
        self.durable_roots=tuple(Path(p) for p in durable_roots) if durable_roots is not None else (data/'artifacts',data/'memory')
    def durable(self,path):
        p=Path(path);need(p.is_absolute() and '..' not in p.parts,'absolute_durable_path_required')
        need(any(p.is_relative_to(r) for r in self.durable_roots) and not p.is_relative_to(self.root),'not_a_permanent_artifact_home')
        return p
    def init(self):
        parent=open_dir(self.root.parent)
        try:fd=mkdir(parent,self.root.name)
        finally:os.close(parent)
        try:
            for role in ROLES:
                sub=mkdir(fd,role);os.close(sub)
        finally:os.close(fd)
        return {'root':str(self.root),'roles':list(ROLES),'retention':'six_calendar_months_after_completion_and_ack','automatic_deletion':False}
    @contextlib.contextmanager
    def locked(self):
        fd=open_dir(self.root)
        try:
            private_dir(fd)
            try:os.mkdir('.writer',0o700,dir_fd=fd)
            except FileExistsError:raise Refused('busy_or_uncertain_writer_no_automatic_retry')
            try:yield fd
            finally:os.rmdir('.writer',dir_fd=fd)
        finally:os.close(fd)
    @contextlib.contextmanager
    def record(self,root,role,record_id):
        need(role in ROLES,'invalid_role');hid(record_id)
        r=os.open(role,DIR_FLAGS,dir_fd=root)
        try:
            private_dir(r);f=os.open(record_id,DIR_FLAGS,dir_fd=r)
            try:private_dir(f);yield f
            finally:os.close(f)
        finally:os.close(r)
    def load(self,fd,role,record_id):
        m=json.loads(read(fd,'handoff.json'))
        need(m.get('format')==FORMAT and m.get('id')==record_id and m.get('role')==role,'record_identity_mismatch')
        validate_request(m['request'])
        need(isinstance(m.get('files'),list) and len(m['files'])<=32,'invalid_files')
        return m
    def save(self,fd,m): write(fd,'handoff.json',(json.dumps(m,indent=2,ensure_ascii=False)+'\n').encode())
    def create(self,role,request,record_id=None):
        need(len(json.dumps(request,ensure_ascii=False).encode())+4096<=META_MAX,'metadata_too_large')
        validate_request(request);need(role in ROLES,'invalid_role');need(shutil.disk_usage(self.root).free>=RESERVE+2*META_MAX,'insufficient_space');record_id=hid(record_id or str(uuid.uuid4()))
        with self.locked() as root:
            r=os.open(role,DIR_FLAGS,dir_fd=root)
            try:
                try:os.mkdir(record_id,0o700,dir_fd=r)
                except FileExistsError:raise Refused('handoff_exists_no_overwrite')
                f=os.open(record_id,DIR_FLAGS,dir_fd=r)
                try:
                    sub=mkdir(f,'files');os.close(sub)
                    m={'format':FORMAT,'id':record_id,'role':role,'request':request,'created_at':stamp(),
                       'completed_at':None,'completion_receipt':None,'acknowledged_at':None,'acknowledged_by':None,
                       'ack_receipt':None,'files':[],'expired_at':None}
                    self.save(f,m)
                finally:os.close(f)
            finally:os.close(r)
        return m
    def get(self,role,record_id):
        with self.locked() as root,self.record(root,role,record_id) as fd:return self.load(fd,role,record_id)
    def add(self,role,record_id,path,disposable=False):
        p=Path(path);name=p.name
        need(name not in ('.','..') and not name.startswith('.') and len(name.encode())<=180 and '\\' not in name,'invalid_filename')
        need(p.suffix.lower() not in ('.key','.pem','.p12','.pfx'),'credential_file_refused')
        data=source(p,FILE_MAX)
        with self.locked() as root,self.record(root,role,record_id) as fd:
            m=self.load(fd,role,record_id);need(not m['completed_at'] and not m['acknowledged_at'] and not m['expired_at'],'handoff_closed_or_acknowledged')
            need(len(m['files'])<32 and sum(x['bytes'] for x in m['files'])+len(data)<=HANDOFF_MAX,'handoff_quota')
            need(shutil.disk_usage(self.root).free>=RESERVE+len(data)+2*META_MAX,'insufficient_space')
            leaf=f'{len(m["files"])+1:02d}-{name}'
            sub=os.open('files',DIR_FLAGS,dir_fd=fd)
            try:
                out=os.open(leaf,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=sub)
                with os.fdopen(out,'wb') as h:h.write(data);h.flush();os.fsync(h.fileno())
                need(sha(read(sub,leaf,FILE_MAX))==sha(data),'copy_mismatch')
                row={'name':name,'stored_name':leaf,'bytes':len(data),'sha256':sha(data),'source':str(p),
                     'retention':'disposable' if disposable else 'keep_until_promoted','promoted_to':None}
                m['files'].append(row);self.save(fd,m)
            finally:os.close(sub)
        return row
    def mark(self,role,record_id,action,receipt,actor=None):
        need(isinstance(receipt,str) and 0<len(receipt)<=2000,'receipt_required')
        need(action in ('complete','ack','reopen'),'invalid_action')
        with self.locked() as root,self.record(root,role,record_id) as fd:
            m=self.load(fd,role,record_id);need(not m['expired_at'],'already_expired');need('expiry-intent.json' not in os.listdir(fd),'expiry_uncertain_operator_review_required')
            if action=='complete':
                need(not m['completed_at'],'already_completed');m['completed_at']=stamp();m['completion_receipt']=receipt
            elif action=='ack':
                need(isinstance(actor,str) and 0<len(actor)<=200,'actor_required')
                need(not m['acknowledged_at'],'already_acknowledged');m['acknowledged_at']=stamp();m['acknowledged_by']=actor;m['ack_receipt']=receipt
            else:m['completed_at']=None;m['completion_receipt']=None;m['acknowledged_at']=None;m['ack_receipt']=None;m['acknowledged_by']=None
            self.save(fd,m);return m
    def promote(self,role,record_id,name,path):
        path=self.durable(path);data=source(path)
        with self.locked() as root,self.record(root,role,record_id) as fd:
            m=self.load(fd,role,record_id);need(not m['expired_at'],'already_expired');need('expiry-intent.json' not in os.listdir(fd),'expiry_uncertain_operator_review_required')
            rows=[x for x in m['files'] if x['stored_name']==name];need(len(rows)==1,'file_not_found')
            row=rows[0];need(len(data)==row['bytes'] and sha(data)==row['sha256'],'promotion_mismatch')
            row['promoted_to']=str(Path(path));self.save(fd,m);return row
    def _plan(self,fd,m,now):
        reasons=[];expires=None
        if m['expired_at']:
            return {'id':m['id'],'role':m['role'],'eligible':False,'expires_at':None,'held':['already_expired'],'manifest_sha256':sha(read(fd,'handoff.json')),'files':[]}
        if 'expiry-intent.json' in os.listdir(fd):
            return {'id':m['id'],'role':m['role'],'eligible':False,'expires_at':None,'held':['expiry_uncertain_operator_review_required'],'manifest_sha256':sha(read(fd,'handoff.json')),'files':[]}
        if not m['completed_at'] or not m['acknowledged_at']:reasons.append('open_or_unacknowledged')
        else:
            expires=six_months(max(dt(m['completed_at']),dt(m['acknowledged_at'])).isoformat())
            if dt(now)<dt(expires):reasons.append('retention_not_elapsed')
        sub=os.open('files',DIR_FLAGS,dir_fd=fd)
        try:
            need(set(os.listdir(sub))=={x['stored_name'] for x in m['files']},'unregistered_files_hold')
            for row in m['files']:
                need(isinstance(row['stored_name'],str) and '/' not in row['stored_name'] and row['stored_name'] not in ('.','..'),'invalid_stored_name')
                data=read(sub,row['stored_name'],FILE_MAX)
                need(len(data)==row['bytes'] and sha(data)==row['sha256'],'copy_changed_hold')
                if row['retention']=='keep_until_promoted':
                    if not row['promoted_to']:reasons.append('promotion_required')
                    else:
                        try:self.durable(row['promoted_to']);data=source(row['promoted_to']);need(len(data)==row['bytes'] and sha(data)==row['sha256'],'promotion_mismatch')
                        except (OSError,ValueError):reasons.append('promotion_unavailable_or_changed')
                else:need(row['retention']=='disposable','invalid_retention')
        finally:os.close(sub)
        return {'id':m['id'],'role':m['role'],'eligible':not reasons,'expires_at':expires,'held':sorted(set(reasons)),
                'manifest_sha256':sha(read(fd,'handoff.json')),'files':[x['stored_name'] for x in m['files']]}
    def plan(self,role,record_id,now=None):
        with self.locked() as root,self.record(root,role,record_id) as fd:return self._plan(fd,self.load(fd,role,record_id),now or stamp())
    def expire(self,role,record_id,expected):
        # Retain the small delivery receipt; delete only verified transport copies.
        with self.locked() as root,self.record(root,role,record_id) as fd:
            m=self.load(fd,role,record_id);p=self._plan(fd,m,stamp())
            need(p['manifest_sha256']==expected,'manifest_changed');need(p['eligible'],'retention_hold')
            need('expiry-intent.json' not in os.listdir(fd),'expiry_uncertain_no_automatic_retry')
            write(fd,'expiry-intent.json',(json.dumps({'at':stamp(),'plan':p,'source_files':m['files']})+'\n').encode())
            sub=os.open('files',DIR_FLAGS,dir_fd=fd)
            try:
                for name in p['files']:os.unlink(name,dir_fd=sub)
                os.fsync(sub)
            finally:os.close(sub)
            m['expired_at']=stamp();m['expired_request_sha256']=sha(json.dumps(m['request'],sort_keys=True,ensure_ascii=False).encode())
            old=m['request'];m['request']=dict(TEMPLATE,title='Expired handoff',request='Transport content expired under the six-month policy.',recipient=old['recipient'],conversation_id=old['conversation_id'],job_id=old['job_id'],record_refs=old['record_refs'])
            self.save(fd,m)
            return {'expired':True,'id':record_id,'originals_deleted':False,'receipt_retained':True}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default=os.environ.get('MINIMOI_HANDOFF_ROOT',str(Path('/minimoi/data/handoffs') if Path('/minimoi/data').is_dir() else Path.home()/'minimoi-staging/data/handoffs')))
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('template');sub.add_parser('init')
    c=sub.add_parser('create');c.add_argument('--role',choices=ROLES,required=True);c.add_argument('--request-json',required=True);c.add_argument('--id')
    for cmd in ('show','plan-expiry','add','complete','ack','reopen','promote','expire'):
        q=sub.add_parser(cmd);q.add_argument('--role',choices=ROLES,required=True);q.add_argument('--id',required=True)
        if cmd=='add':q.add_argument('--file',required=True);q.add_argument('--disposable',action='store_true')
        if cmd in ('complete','ack','reopen'):q.add_argument('--receipt',required=True)
        if cmd=='ack':q.add_argument('--actor',required=True)
        if cmd=='promote':q.add_argument('--name',required=True);q.add_argument('--durable-file',required=True)
        if cmd=='expire':q.add_argument('--execute',action='store_true');q.add_argument('--expected-manifest-sha',required=True)
    a=p.parse_args(argv);s=Store(a.root)
    try:
        if a.cmd=='template':result={'format':FORMAT,'request':TEMPLATE,'retention_months':6,'ack_is_approval':False}
        elif a.cmd=='init':result=s.init()
        elif a.cmd=='create':result=s.create(a.role,json.loads(source(a.request_json,META_MAX)),a.id)
        elif a.cmd=='show':result=s.get(a.role,a.id)
        elif a.cmd=='plan-expiry':result=s.plan(a.role,a.id)
        elif a.cmd=='add':result=s.add(a.role,a.id,a.file,a.disposable)
        elif a.cmd in ('complete','ack','reopen'):result=s.mark(a.role,a.id,a.cmd,a.receipt,getattr(a,'actor',None))
        elif a.cmd=='promote':result=s.promote(a.role,a.id,a.name,a.durable_file)
        else:
            need(a.execute,'execute_required');result=s.expire(a.role,a.id,a.expected_manifest_sha)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError) as e:
        print(json.dumps({'error':str(e) if isinstance(e,Refused) else type(e).__name__}),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
