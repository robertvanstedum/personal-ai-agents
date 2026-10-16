"""Scheduled archive-only caller within the existing trusted portal authority.
This is an API restriction, NOT cryptographic confinement of the paired key.
No Flask request, owner session, new secret or generic signing endpoint.
"""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import requests
import time
from . import auth,rooms_identity as RI
from .records_bridge import validate_backend,KEY_FILE_DEFAULT,ACCESS_DIR_DEFAULT
from .transport_profiles import DEV, PRODUCTION, select_records_profile
from .guild_ui.rooms_archive_api import _read,_private_dir
from .guild_ui.rooms_archive_runner import batch_deadline
PURPOSE='rooms.archive.inactive30.v1'
ID=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
PATH=re.compile(r'/api/v1/rooms/[0-9a-f-]{36}/(archive-check|archive|archive-reconcile)\Z')
class CallerRefused(RuntimeError):pass

class ArchiveCaller:
    def __init__(self,*,backend,access_dir,key_file,run_id,load_users=auth.load_users,transport=None,clock=None,profile=DEV):
        self.profile=profile
        self.backend=validate_backend(backend,profile=profile);self.access_dir=Path(access_dir);self.key_file=key_file
        self.run_id=run_id;self.load_users=load_users;self.transport=transport
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        if not isinstance(run_id,str) or not ID.fullmatch(run_id):raise CallerRefused('run_invalid')

    def authorize(self):
        now=self.clock();deadline=batch_deadline(now)
        try:
            policy=json.loads(_read(str(self.access_dir/'archive-schedule.json'),4096))
            if (not isinstance(policy,dict) or set(policy)!={'enabled','delegation_id','owner'} or policy['enabled'] is not True
                or policy['owner']!='robert' or not isinstance(policy['delegation_id'],str) or not ID.fullmatch(policy['delegation_id'])):raise ValueError
            users=self.load_users()
            if not isinstance(users,list) or any(not isinstance(u,dict) for u in users):raise ValueError
            owners=[u for u in users if isinstance(u.get('username'),str) and u['username'].lower()=='robert']
            if len(owners)!=1 or owners[0].get('username')!='robert' or owners[0].get('tier')!='owner':raise ValueError
            decision=RI.RoomsAccess(str(self.access_dir)).decide('account:robert',account_active=True)
            if not decision.ok or decision.principal!='robert':raise ValueError
            # Use the installed key, never one supplied by a suite/client. Bound
            # reader refuses symlinks/oversize/untrusted ownership and modes.
            raw=_read(str(self.key_file),4096).decode().strip()
            kid,sep,secret=raw.partition(':')
            if not sep or not kid or len(secret)<32:raise ValueError
        except Exception:raise CallerRefused('archive_authority_unavailable') from None
        return (kid,secret),{'purpose':PURPOSE,'delegation_id':policy['delegation_id'],'run_id':self.run_id,
            'deadline':deadline.isoformat()},now

    def __call__(self,method,path,payload=None,*,operation_key=None):
        match=PATH.fullmatch(path) if isinstance(path,str) else None
        allowed=(method=='GET' and path=='/api/v1/rooms/archive-preview') or (match and (method,match[1]) in {
            ('GET','archive-check'),('POST','archive'),('POST','archive-reconcile')})
        if not allowed:raise CallerRefused('route_refused')
        if method=='GET' and (payload is not None or operation_key is not None):raise CallerRefused('payload_refused')
        if method=='POST' and not isinstance(payload,dict):raise CallerRefused('payload_refused')
        if match and match[1]=='archive' and (not isinstance(operation_key,str) or not re.fullmatch(r'ra-[a-f0-9]{48}',operation_key)):
            raise CallerRefused('operation_refused')
        key,authority,now=self.authorize()
        if match and match[1]=='archive':
            if set(payload)!={'version','archive_version','latest_seq','last_activity','_portal_archive_proof'}:raise CallerRefused('payload_refused')
            proof=payload['_portal_archive_proof']
            if not isinstance(proof,dict) or 'scheduled' in proof:raise CallerRefused('proof_refused')
            payload={**payload,'_portal_archive_proof':{**proof,'scheduled':authority}}
        if match and match[1]=='archive-reconcile' and (set(payload)!={'operation_key','checked_at','payload'} or operation_key is not None):raise CallerRefused('payload_refused')
        raw=json.dumps(payload).encode() if payload is not None else b''
        if len(raw)>256*1024:raise CallerRefused('payload_refused')
        headers={'Accept':'application/json','X-Portal-Identity':RI.sign(key,principal='robert',method=method,target=path,body=raw,env=self.profile.assertion_env,now=now.timestamp())}
        if payload is not None:headers.update({'Content-Type':'application/json','Origin':self.backend})
        if operation_key:headers['Idempotency-Key']=operation_key
        remaining=max(0,(datetime.fromisoformat(authority['deadline'])-self.clock()).total_seconds())
        if not remaining:raise CallerRefused('outside_batch_window')
        read_deadline=time.monotonic()+min(8,remaining)
        try:
            if self.transport is not None:response=self.transport.request(method,self.backend+path,headers=headers,data=raw,timeout=min(8,remaining),allow_redirects=False)
            else:
                with requests.Session() as connection:
                    connection.trust_env=False
                    response=connection.request(method,self.backend+path,headers=headers,data=raw,timeout=min(8,remaining),allow_redirects=False,stream=True)
                    try:
                        if time.monotonic()>=read_deadline:raise CallerRefused('response_deadline')
                        content=bytearray()
                        for chunk in response.iter_content(1):
                            if time.monotonic()>=read_deadline:raise CallerRefused('response_deadline')
                            content.extend(chunk)
                            if len(content)>512*1024:raise CallerRefused('response_oversize')
                        return response.status_code,json.loads(content) if content else None
                    finally:response.close()
            if len(response.content)>512*1024:raise CallerRefused('response_oversize')
            return response.status_code,json.loads(response.content) if response.content else None
        except (requests.RequestException,OSError,ValueError):return 0,None


def from_env(environ,*,run_id):
    from urllib.parse import urlsplit
    origin=urlsplit(environ.get('BASE_URL',''))
    profile=select_records_profile(environ.get('BASE_URL',''),environ)
    if profile != PRODUCTION:
        if (environ.get('MINIMOI_ROLE')=='production'
                or environ.get('MINIMOI_RECORDS_PROFILE','dev')!='dev'
                or origin.hostname not in {'dev.minimoi.ai','localhost','127.0.0.1'}
                or origin.username or origin.password or origin.query or origin.fragment):
            raise CallerRefused('records_profile_required')
        profile=DEV
    return ArchiveCaller(backend=environ.get('RECORDS_BACKEND',''),
        access_dir=environ.get('MINIMOI_ROOMS_ACCESS_DIR',ACCESS_DIR_DEFAULT),
        key_file=environ.get('RECORDS_PORTAL_KEY_FILE',KEY_FILE_DEFAULT),run_id=run_id,profile=profile)
