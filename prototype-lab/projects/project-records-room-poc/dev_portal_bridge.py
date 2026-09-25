"""Owner-gated dev-only route to the isolated loopback Records application.

Install into the existing portal app. Never inject a Records owner credential:
portal login and Records login remain separate. No production route is registered.
"""
from http.cookies import SimpleCookie
import re
from urllib.parse import urlsplit

from flask import Response, request
import requests

PREFIX = '/app/records'
COOKIE = 'minimoi_room_poc'


def rewrite_paths(text):
    # Only absolute application URL literals, not user response bodies. Applied
    # exclusively to trusted static HTML/JS/CSS responses below.
    return re.sub(r'([\'"`])/(api|static)(?=/)',
                  lambda match: match.group(1)+PREFIX+'/'+match.group(2), text)


def install(portal, require_login, require_owner, *, backend='http://127.0.0.1:18880', transport=None, local_port=5001):
    origin=urlsplit(backend)
    if origin.scheme!='http' or origin.hostname!='127.0.0.1' or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        raise ValueError('Records backend must be a fixed loopback HTTP origin')
    sender=transport

    def forward(path=''):
        if request.host not in {'dev.minimoi.ai',f'127.0.0.1:{local_port}',f'localhost:{local_port}'}:
            return Response('Records is available only on dev.minimoi.ai',status=404)
        if request.method not in {'GET','HEAD'}:
            expected='https://dev.minimoi.ai' if request.host=='dev.minimoi.ai' else 'http://'+request.host
            if request.headers.get('Origin')!=expected:
                return Response('Same-origin request required',status=403)
        if request.content_length and request.content_length>3_000_000:
            return Response('Request too large',status=413)
        # Do not pass the portal cookie, bearer authorization, forwarded identity,
        # or any client-selected backend host. Records has its own sign-in.
        headers={'Accept':request.headers.get('Accept','*/*')}
        if request.headers.get('Content-Type'): headers['Content-Type']=request.headers['Content-Type']
        if request.headers.get('Idempotency-Key'): headers['Idempotency-Key']=request.headers['Idempotency-Key']
        if request.cookies.get(COOKIE): headers['Cookie']=COOKIE+'='+request.cookies[COOKIE]
        if request.method not in {'GET','HEAD'}: headers['Origin']=backend
        try:
            # Encode each component: decoded traversal and URL delimiters cannot
            # select another backend path or origin.
            from urllib.parse import quote
            if any(part in {'.','..'} for part in path.split('/')):
                return Response('Invalid path',status=400)
            target=backend+'/'+quote(path,safe='/')
            if request.query_string: target+='?'+request.query_string.decode('ascii')
            payload=request.stream.read(3_000_001)
            if len(payload)>3_000_000: return Response('Request too large',status=413)
            if sender is not None:
                upstream=sender.request(request.method,target,headers=headers,data=payload,timeout=20,allow_redirects=False)
            else:
                # Per-request session: never retain one browser's Records cookie
                # and accidentally apply it to another portal request.
                with requests.Session() as connection:
                    connection.trust_env=False
                    upstream=connection.request(request.method,target,headers=headers,data=payload,timeout=20,allow_redirects=False)
        except (requests.RequestException,UnicodeError):
            return Response('Records dev service unavailable',status=502)
        if 300<=upstream.status_code<400:
            return Response('Unexpected Records redirect',status=502)
        content=upstream.content
        content_type=upstream.headers.get('Content-Type','application/octet-stream')
        # Never rewrite stored contributions, exported HTML or user uploads.
        if (not path or path.startswith('static/')) and any(t in content_type for t in ('text/html','javascript','text/css')):
            content=rewrite_paths(content.decode('utf-8')).encode('utf-8')
        response=Response(content,status=upstream.status_code,content_type=content_type)
        for name in ('Content-Disposition','Content-Security-Policy','X-Content-Type-Options','Referrer-Policy'):
            if name in upstream.headers: response.headers[name]=upstream.headers[name]
        response.headers['Cache-Control']='no-store'
        response.headers['X-Records-Environment']='dev'
        # requests combines Set-Cookie headers; Records issues one named cookie.
        cookies=SimpleCookie();cookies.load(upstream.headers.get('Set-Cookie',''))
        if COOKIE in cookies:
            cookie=cookies[COOKIE]
            response.set_cookie(COOKIE,cookie.value,path=PREFIX+'/',httponly=True,samesite='Strict',
                                secure=request.host=='dev.minimoi.ai',max_age=cookie['max-age'] or None,
                                expires=cookie['expires'] or None)
        return response

    wrapped=require_login(require_owner(forward))
    portal.add_url_rule(PREFIX,endpoint='records_dev_root',view_func=wrapped,methods=['GET','HEAD'],defaults={'path':''})
    portal.add_url_rule(PREFIX+'/',endpoint='records_dev_slash',view_func=wrapped,methods=['GET','HEAD'],defaults={'path':''})
    portal.add_url_rule(PREFIX+'/<path:path>',endpoint='records_dev_proxy',view_func=wrapped,methods=['GET','HEAD','POST','PUT','PATCH','DELETE'])
