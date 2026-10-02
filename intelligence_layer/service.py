"""Standalone loopback dev service. Synthetic data only; no portal dependency.

Bearer credentials are generated locally, never sent to models. Browser sign-in
uses an HttpOnly, SameSite=Strict in-memory session and same-origin POST checks.
"""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time

from .access import FixtureAuthority, Principal
from .adapters import UNAVAILABLE, read_file
from .contracts import VERSION, ContractError, Unavailable, canonical, digest, now, seal, stable_id, strict_json
from .demo import seed
from .retrieval import Retrieval, capabilities
from .store import Store

MAX_BODY=128*1024


class DevApplication:
    def __init__(self,root,credential_file):
        path=Path(credential_file)
        if path.stat().st_mode & 0o077:
            raise ContractError('Credential file must be mode 0600')
        token=read_file(path).decode().strip()
        if len(token)<32:
            raise ContractError('Dev credential must be randomly generated and at least 32 characters')
        self.authority=FixtureAuthority({token:Principal('dev-owner','owner-only',frozenset({'demo'}),True)})
        self.store=Store(root,self.authority)
        if not Path(root).exists():
            self.store.initialize()
        self.store.inspect()
        self.query=Retrieval(self.store)
        self.sessions={}
        self.lock=threading.Lock()

    def authenticate(self,headers):
        bearer=headers.get('Authorization','')
        if bearer.startswith('Bearer '):
            return self.authority.authenticate(bearer[7:])
        cookies={}
        for part in headers.get('Cookie','').split(';'):
            key,sep,value=part.strip().partition('=')
            if sep:
                cookies[key]=value
        with self.lock:
            item=self.sessions.get(cookies.get('intelligence_session'))
            if item and item[1]>time.time():
                return item[0]
        raise ContractError('Authentication required')

    def login(self,token):
        context=self.authority.authenticate(token)
        session=secrets.token_urlsafe(32)
        with self.lock:
            self.sessions={k:v for k,v in self.sessions.items() if v[1]>time.time()}
            self.sessions[session]=(context,time.time()+3600)
        return session

    @staticmethod
    def fields(body,allowed,required=()):
        if not isinstance(body,dict) or set(body)-set(allowed) or set(required)-set(body):
            raise ContractError('Invalid request fields')

    def call(self,path,body,context):
        principal=self.authority.principal(context)
        if path=='/api/status':
            self.fields(body,())
            return {'store':self.store.inspect(),'capabilities':capabilities(),'adapters':UNAVAILABLE,
                    'principal':principal.name,'topics':sorted(principal.topics),'classification':'synthetic dev only',
                    'limits':['L0 is a same-user convention','No real transcript ingestion','No model, encryption, backup or restore claim']}
        if path=='/api/demo':
            self.fields(body,())
            return seed(self.store,context)
        if path=='/api/search':
            self.fields(body,('query','topic','route','as_of','event_from','event_to'),('query','topic'))
            return self.query.search(context=context,**body)
        if path=='/api/resolve':
            self.fields(body,('citation','route'),('citation',))
            return self.query.resolve(context=context,**body)
        if path=='/api/relations':
            self.fields(body,('start_id','topic','route','as_of','include_pending'),('start_id','topic'))
            return {'relations':self.query.relations(context=context,**body)}
        if path=='/api/rebuild':
            self.fields(body,())
            return self.query.rebuild()
        if path=='/api/candidate':
            self.fields(body,('text','sources','topic','type'),('text','sources','topic'))
            if body.get('type','candidate') not in ('candidate','decision','observation','outcome') or body['topic'] not in principal.topics or not isinstance(body['text'],str):
                raise ContractError('Invalid candidate')
            with self.store._lock():
                state,_,_=self.store._load()
                sources=[self.store._anchor(state,a) for a in body['sources']]
                if not sources:
                    raise ContractError('Exact citations required')
                from .contracts import CLASSES
                cls=max((r['disclosure_class'] for r in sources),key=CLASSES.index)
            kind=body.get('type','candidate')
            data={'candidate':dict(extractor='manual-dev',extractor_version='1',model='none'),
                  'decision':dict(scope='demo',supersedes=None,superseded_by=None)}.get(kind,{})
            record=seal(dict(schema_version=VERSION,id=stable_id(secrets.token_hex(16)),type=kind,created_at=now(),event_time=None,
                             author=principal.name,disclosure_class=cls,topics=[body['topic']],sources=body['sources'],text=body['text'],
                             authority='agent_proposal' if kind=='decision' else 'assertion',data=data,
                             provenance=dict(provider='dev-owner-entry',thread_id='demo',event_id='manual',capture_method='manual',
                                             extractor_version='1',model=None,fidelity='observed',gaps=['manual assertion; source event time unknown'])))
            self.store.add_record(record,context)
            return {'record':record}
        if path=='/api/promote':
            self.fields(body,('id','hash'),('id','hash'))
            with self.store._lock():
                state,_,_=self.store._load()
                prior=state['records'].get(body['id'])
                if not prior or prior['hash']!=body['hash'] or prior['type'] not in ('decision','candidate','lesson'):
                    raise ContractError('Promotion revision mismatch')
                record=deepcopy(prior)
            record.update(id=stable_id(secrets.token_hex(16)),author=principal.name,created_at=now(),authority='owner')
            record['provenance']['fidelity']='reviewed'
            if record['type']=='candidate':
                record['type']='lesson'
            record=seal(record)
            receipt=self.authority.issue(context,'promote',record['hash'])
            self.store.add_record(record,context,receipt=receipt)
            return {'record':record,'receipt':receipt.payload}
        if path=='/api/delete-plan':
            self.fields(body,('id',),('id',))
            return self.store.deletion_plan(body['id'])
        if path=='/api/delete':
            self.fields(body,('id','plan_hash'),('id','plan_hash'))
            plan=self.store.deletion_plan(body['id'])
            if plan['hash']!=body['plan_hash']:
                raise ContractError('Deletion plan changed; inspect again')
            return self.store.delete(body['id'],context,self.authority.issue(context,'delete',plan['hash']))
        raise ContractError('Unknown API endpoint')


class DevServer(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=16

    def __init__(self,address,app):
        self.app=app
        self.slots=threading.BoundedSemaphore(16)
        super().__init__(address,Handler)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self,request,address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version='MiniMoiIntelligenceDev'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self,*args):
        pass  # No tokens, URLs, source text or query bodies in debug logs.

    def send(self,status,data,*,html=False,cookie=None):
        raw=data if html else canonical(data)
        self.send_response(status)
        self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(raw)

    def host(self):
        port=self.server.server_port
        # Tests can use an ephemeral port; deploy uses fixed loopback port.
        return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')

    def do_GET(self):
        if not self.host():
            self.send(403,{'error':'host refused'})
        elif self.path=='/health':
            self.send(200,{'status':'ok','mode':'synthetic-dev','revision':os.environ.get('INTELLIGENCE_REVISION','working-tree')})
        elif self.path=='/':
            self.send(200,(Path(__file__).parent/'static/index.html').read_bytes(),html=True)
        elif self.path in ('/app.js','/style.css'):
            data=(Path(__file__).parent/'static'/self.path[1:]).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/javascript' if self.path.endswith('.js') else 'text/css')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send(404,{'error':'not found'})

    def do_POST(self):
        if not self.host():
            return self.send(403,{'error':'host refused'})
        origin=self.headers.get('Origin')
        if origin is not None and origin!='http://'+self.headers['Host']:
            return self.send(403,{'error':'origin refused'})
        if not self.headers.get('Authorization','').startswith('Bearer ') and origin is None:
            return self.send(403,{'error':'browser origin required'})
        try:
            length=int(self.headers.get('Content-Length','-1'))
            if not 0<=length<=MAX_BODY or self.headers.get('Transfer-Encoding'):
                return self.send(413,{'error':'body limit'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.send(415,{'error':'JSON required'})
            body=strict_json(self.rfile.read(length))
            if self.path=='/api/login':
                self.server.app.fields(body,('token',),('token',))
                session=self.server.app.login(body['token'])
                return self.send(200,{'authenticated':True},cookie='intelligence_session='+session+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
            try:
                context=self.server.app.authenticate(self.headers)
            except ContractError:
                return self.send(401,{'error':'authentication required'})
            result=self.server.app.call(self.path,body,context)
            self.send(200,result)
        except Unavailable:
            self.send(503,{'error':'optional capability unavailable; no fallback'})
        except (ContractError,ValueError,TypeError,KeyError,OSError):
            self.send(400,{'error':'invalid or unauthorized request; source content omitted'})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--credential-file',required=True)
    parser.add_argument('--bind',default='127.0.0.1',choices=['127.0.0.1','0.0.0.0'])
    parser.add_argument('--port',type=int,default=18882)
    args=parser.parse_args()
    app=DevApplication(args.root,args.credential_file)
    server=DevServer((args.bind,args.port),app)
    server.serve_forever()


if __name__=='__main__':
    main()
