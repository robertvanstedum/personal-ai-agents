"""Fixed portal source for recall; no migration, source writes, or fallback store."""
import json
import os
import stat
import time
import select
import psycopg2
from psycopg2 import extensions
from core.mc_tools.recall import CID, Unavailable
from .conversations import CHAT_SCOPES, conversations_of

class Excluded(Exception):
    pass

class Source:
    def __init__(self,services,principal):
        self.services,self.principal=services,principal
        self.folder=conversations_of(services).dir
        self.checked={}
        self.conn=None
    def begin(self,deadline):self.deadline=deadline
    def check_time(self):
        if time.monotonic()>=self.deadline:raise Unavailable('recall_deadline')
    def wait(self):
        while True:
            self.check_time()
            state=self.conn.poll()
            if state==extensions.POLL_OK:return
            remaining=max(0,self.deadline-time.monotonic())
            if state==extensions.POLL_READ:select.select([self.conn.fileno()],[],[],remaining)
            elif state==extensions.POLL_WRITE:select.select([],[self.conn.fileno()],[],remaining)
            else:raise Unavailable('recall_unavailable')
    def run(self,work):
        try:
            self.check_time()
            if self.conn is None:
                self.conn=psycopg2.connect(self.services.floor._database_url(),async_=True)
                self.wait()
            def q(sql,params=()):
                self.check_time()
                with self.conn.cursor() as cur:
                    cur.execute(sql,params);self.wait()
                    return cur.fetchall() if cur.description else []
            return work(q)
        except Unavailable:raise
        except Exception:raise Unavailable('recall_unavailable') from None
    def close(self):
        if self.conn is not None:self.conn.close()
    def metadata(self,cid):
        self.check_time()
        try:
            directory=os.open(self.folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                fd=os.open(cid+'.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
                try:
                    s=os.fstat(fd)
                    if not stat.S_ISREG(s.st_mode) or s.st_size>16384:raise ValueError()
                    raw=os.read(fd,16385)
                    if len(raw)>16384:raise ValueError()
                    data=json.loads(raw)
                finally:os.close(fd)
            finally:os.close(directory)
        except (OSError,ValueError,TypeError):raise Unavailable('recall_unavailable') from None
        if not isinstance(data,dict) or data.get('id')!=cid or not isinstance(data.get('principal'),str):
            raise Unavailable('recall_unavailable')
        if data.get('principal')!=self.principal or data.get('legacy') or data.get('archived'):
            raise Excluded()
        if data.get('notes_floor')!=self.services.floor.floor+'/'+cid:
            raise Unavailable('recall_unavailable')
        scope=data.get('scope')
        if scope is not None and not isinstance(scope,str):
            raise Unavailable('recall_unavailable')
        if scope not in CHAT_SCOPES|{None}:
            raise Excluded()
        return data
    def conversations(self):
        try:
            fd=os.open(self.folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                with os.scandir(fd) as entries:
                    names=[]
                    for entry in entries:
                        self.check_time();names.append(entry.name)
                        if len(names)>1000:raise Unavailable('recall_directory_bound')
            finally:os.close(fd)
        except OSError:raise Unavailable('recall_unavailable') from None
        result=[]
        for name in sorted(names):
            if not name.endswith('.json') or not CID.fullmatch(name[:-5]):continue
            try:data=self.metadata(name[:-5])
            except Excluded:continue
            result.append(data)
        return result
    def required(self,cid):
        try:return self.metadata(cid)
        except Excluded:raise Unavailable('recall_unavailable') from None
    def current(self,cid,request_id):
        conv=self.required(cid)
        if cid in self.checked and self.checked[cid]!=conv:raise Unavailable('recall_source_changed')
        self.checked[cid]=conv
        def work(q):
            q("SELECT set_config('statement_timeout',%s,false)",[str(max(1,int((self.deadline-time.monotonic())*1000)))])
            return q("SELECT id FROM guild.floor_messages WHERE floor=%s AND request_id=%s AND author=%s AND author_kind='owner' AND record_mode='on_record'",[conv['notes_floor'],request_id,self.principal])
        try: rows=self.run(work)
        except Exception:raise Unavailable('recall_unavailable') from None
        if len(rows)!=1:raise Unavailable('recall_unavailable')
        self.current_scope=(cid,request_id)
    def snapshot(self,conv,current,request_id,boundary,max_bytes):
        self.check_time(); cid=conv['id']
        fresh=self.required(cid)
        if fresh!=conv:raise Unavailable('recall_source_changed')
        self.checked[cid]=conv
        floor=self.services.floor
        def work(q):
            remaining=max(1,int((self.deadline-time.monotonic())*1000))
            q("SELECT set_config('statement_timeout',%s,false)",[str(remaining)])
            # Bound current conversation by a fresh server-resolved kept owner request on every page.
            rows=q("""WITH cap AS (
                SELECT CASE WHEN %s THEN (SELECT id FROM guild.floor_messages
                  WHERE floor=%s AND request_id=%s AND author=%s AND author_kind='owner'
                  AND record_mode='on_record') ELSE 9223372036854775807 END AS id), scoped AS (
                SELECT guild.floor_messages.id,author,author_kind,text,created_at FROM guild.floor_messages,cap
                WHERE floor=%s AND record_mode='on_record' AND guild.floor_messages.id<=cap.id
                  AND guild.floor_messages.id<=coalesce(%s,9223372036854775807)), bounds AS (
                SELECT count(*) AS n,coalesce(sum(octet_length(text)),0) AS size,
                  coalesce(bool_or(author_kind='owner' AND author<>%s),false) AS foreign_owner,
                  coalesce(max(id),0) AS boundary FROM scoped)
                SELECT cap.id,bounds.n,bounds.size,bounds.foreign_owner,bounds.boundary,
                  CASE WHEN bounds.n<=4000 AND bounds.size<=least(1048576,%s) AND NOT bounds.foreign_owner THEN
                   (SELECT json_agg(json_build_object('id',id,'who',author,'author_kind',author_kind,
                         'text',text,'created_at',created_at) ORDER BY id) FROM scoped) ELSE NULL END
                FROM cap,bounds""",[cid==current,conv['notes_floor'],request_id,self.principal,
                       conv['notes_floor'],boundary,self.principal,max_bytes])
            return rows[0]
        try:cap,n,size,foreign,b,rows=self.run(work)
        except Exception:raise Unavailable('recall_unavailable') from None
        if cap is None or foreign:raise Unavailable('recall_unavailable')
        if n>4000 or size>1048576:return None
        if size>max_bytes:return None,int(b)
        rows=rows or []
        if any(r['author_kind'] not in ('owner','agent','platform') or not isinstance(r['text'],str) for r in rows):
            raise Unavailable('recall_unavailable')
        return rows,int(b)
    def recheck(self):
        self.check_time()
        self.current(*self.current_scope)
        if any(self.required(cid)!=conv for cid,conv in self.checked.items()):raise Unavailable('recall_source_changed')
