"""Deployment boundary tests; no portal credentials or private records."""
from functools import wraps
from types import SimpleNamespace
from flask import Flask,session
import pytest
from dev_portal_bridge import install,rewrite_paths


class Upstream:
    def __init__(self): self.calls=[]; self.reply=SimpleNamespace(content=b'{}',status_code=200,headers={'Content-Type':'application/json'})
    def request(self,*args,**kwargs): self.calls.append((args,kwargs));return self.reply


@pytest.fixture
def bridge():
    portal=Flask(__name__);portal.secret_key='synthetic-test-key';upstream=Upstream()
    def login(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if not session.get('user'): return 'Sign in',401
            return fn(*a,**kw)
        return wrapped
    def owner(fn):
        @wraps(fn)
        def wrapped(*a,**kw):
            if session['user'].get('tier')!='owner': return 'Owner only',403
            return fn(*a,**kw)
        return wrapped
    install(portal,login,owner,transport=upstream)
    return portal.test_client(),upstream


def signin(client,tier='owner'):
    with client.session_transaction(base_url='https://dev.minimoi.ai') as state: state['user']={'tier':tier}


def test_owner_gate_and_dev_host_only(bridge):
    client,upstream=bridge
    assert client.get('/app/records/',base_url='https://dev.minimoi.ai').status_code==401
    signin(client,'guest')
    assert client.get('/app/records/',base_url='https://dev.minimoi.ai').status_code==403
    assert not upstream.calls
    signin(client)
    assert client.get('/app/records/',base_url='https://dev.minimoi.ai').status_code==200
    with client.session_transaction(base_url='https://minimoi.ai') as state: state['user']={'tier':'owner'}
    assert client.get('/app/records/',base_url='https://minimoi.ai').status_code==404


def test_csrf_identity_headers_and_cookie_isolation(bridge):
    client,upstream=bridge;signin(client)
    path='/app/records/api/v1/rooms'
    assert client.post(path,base_url='https://dev.minimoi.ai',json={}).status_code==403
    assert client.post(path,base_url='https://dev.minimoi.ai',headers={'Origin':'https://evil.example'},json={}).status_code==403
    client.set_cookie('minimoi_room_poc','records-cookie',domain='dev.minimoi.ai',path='/app/records/')
    response=client.post(path,base_url='https://dev.minimoi.ai',headers={'Origin':'https://dev.minimoi.ai','Authorization':'Bearer do-not-forward','X-User-Id':'robert','Idempotency-Key':'retain'},json={})
    assert response.status_code==200
    args,kw=upstream.calls[-1]
    assert args==('POST','http://127.0.0.1:18880/api/v1/rooms')
    assert kw['headers']['Cookie']=='minimoi_room_poc=records-cookie'
    assert kw['headers']['Origin']=='http://127.0.0.1:18880'
    assert kw['headers']['Idempotency-Key']=='retain'
    assert 'Authorization' not in kw['headers'] and 'X-User-Id' not in kw['headers']
    assert kw['allow_redirects'] is False


def test_paths_static_only_and_secure_scoped_cookie(bridge):
    client,upstream=bridge;signin(client)
    text='fetch(`/api/v1/rooms/${id}`); const url="/api/login"; <script src="/static/app.js">'
    upstream.reply=SimpleNamespace(content=text.encode(),status_code=200,headers={'Content-Type':'application/javascript','Set-Cookie':'minimoi_room_poc=value; HttpOnly; Path=/; SameSite=Strict'})
    result=client.get('/app/records/static/app.js',base_url='https://dev.minimoi.ai')
    assert result.text==rewrite_paths(text)
    cookie=result.headers['Set-Cookie'];assert 'Secure' in cookie and 'HttpOnly' in cookie and 'Path=/app/records/' in cookie
    result=client.get('/app/records/api/v1/documents/id',base_url='https://dev.minimoi.ai')
    assert result.text==text
    assert result.headers['Cache-Control']=='no-store'


def test_redirect_and_path_escape_refused(bridge):
    client,upstream=bridge;signin(client)
    assert client.get('/app/records/../api',base_url='https://dev.minimoi.ai').status_code==400
    upstream.reply=SimpleNamespace(content=b'',status_code=302,headers={'Location':'https://evil.example'})
    assert client.get('/app/records/',base_url='https://dev.minimoi.ai').status_code==502
