"""Default-off production machine ingress for existing Records credentials.
This proxy mints no assertion/credential. Records remains the authority for work,
membership, revocation, idempotency and receipts. Browser identity never passes.
"""
from __future__ import annotations
import json,re,time
from urllib.parse import parse_qs,quote,urlencode
import requests
from flask import Response,request
from .transport_profiles import PRODUCTION,select_records_profile
PREFIX='/internal/records'
FLAG='MINIMOI_RECORDS_MACHINE_BRIDGE'
MAX_BODY=1_000_000
MAX_RESPONSE=8*1024*1024
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
TOKEN=re.compile(r'Bearer [A-Za-z0-9_-]{32,256}\Z')
KEY=re.compile(r'[A-Za-z0-9_.:-]{1,200}\Z')
ROUTES=(
 ('GET',re.compile(r'/api/v1/hosted-teammates\Z')),
 ('POST',re.compile(r'/api/v1/hosted-teammates/(?:mc|codex|claude-code)/reachable\Z')),
 ('POST',re.compile(r'/api/v1/turns/claim\Z')),
 ('POST',re.compile(r'/api/v1/turns/'+UUID+r'/(?:start|heartbeat|fail|cancel-ack|recover|reconciled)\Z')),
 ('POST',re.compile(r'/api/v1/rooms/'+UUID+r'/(?:rsvp|events)\Z')),
 ('GET',re.compile(r'/api/v1/documents/'+UUID+r'\Z')),
 ('GET',re.compile(r'/api/v1/operations/[A-Za-z0-9_.:-]{1,200}\Z')),
)
def refuse(status,code):return Response(json.dumps({'error':code}),status=status,content_type='application/json',headers={'Cache-Control':'no-store'})
def target(method,path,query):
 if not any(method==m and pattern.fullmatch(path)for m,pattern in ROUTES):return None
 if '/operations/'in path:
  try:values=parse_qs(query.decode('ascii'),keep_blank_values=True,strict_parsing=True)
  except (ValueError,UnicodeError):return None
  if set(values)!={'destination'}or len(values['destination'])!=1 or not re.fullmatch(UUID,values['destination'][0]):return None
  return path+'?'+urlencode({'destination':values['destination'][0]})
 return path if not query else None

def install_configured(portal,*,base_url,environ,transport=None,clock=time.monotonic):
 profile=select_records_profile(base_url,environ)
 if (profile is not PRODUCTION or environ.get(FLAG)!='1'
     or environ.get('RECORDS_BACKEND')!=PRODUCTION.backend):return False
 def forward(path):
  if request.host!=profile.host:return refuse(404,'unavailable')
  # Both browser and assertion channels are separate. No trusted identity can
  # be obtained by supplying their headers, even alongside a valid bearer.
  if any(request.headers.get(k)is not None for k in ('Origin','Cookie','X-Portal-Identity')):return refuse(403,'machine_channel_required')
  auth=request.headers.get('Authorization','')
  if not TOKEN.fullmatch(auth):return refuse(401,'unauthorized')
  canonical=target(request.method,'/'+path,request.query_string)
  if canonical is None:return refuse(403,'route_refused')
  if request.content_length is not None and request.content_length>MAX_BODY:return refuse(413,'too_large')
  body=request.stream.read(MAX_BODY+1)
  if len(body)>MAX_BODY:return refuse(413,'too_large')
  headers={'Authorization':auth,'Accept':'application/json'}
  if request.method=='GET':
   if body:return refuse(400,'body_refused')
  else:
   if request.mimetype!='application/json':return refuse(415,'json_required')
   try:
    value=json.loads(body)
    if not isinstance(value,dict):raise ValueError
   except (ValueError,UnicodeError):return refuse(400,'invalid_json')
   key=request.headers.get('Idempotency-Key','')
   if not KEY.fullmatch(key):return refuse(400,'operation_key_required')
   headers.update({'Content-Type':'application/json','Idempotency-Key':key})
  # No cookies/proxies/redirects/Origin/assertion headers or caller-controlled
  # upstream hosts. A session per call cannot retain a previous caller's key.
  session=transport or requests.Session()
  if transport is None:session.trust_env=False
  try:
   start=clock()
   with session.request(request.method,profile.backend+quote(canonical.split('?',1)[0],safe='/')+('?' + canonical.split('?',1)[1]if'?'in canonical else''),
       headers=headers,data=body,timeout=(2,8),allow_redirects=False,stream=True)as upstream:
    if upstream.status_code not in {200,201,202,204,400,401,403,404,409,410,413,415,422,429,500,502,503}:return refuse(502,'records_unavailable')
    content=bytearray()
    # Match the existing Vault ingress: expose every received byte to the
    # elapsed guard; a large buffered chunk can hide a dribbling response.
    for chunk in upstream.iter_content(1):
     if clock()-start>10:return refuse(502,'records_timeout')
     content.extend(chunk)
     if len(content)>MAX_RESPONSE:return refuse(502,'records_response_too_large')
    if clock()-start>10:return refuse(502,'records_timeout')
    is_document=request.method=='GET'and canonical.startswith('/api/v1/documents/')
    ctype=upstream.headers.get('Content-Type','application/octet-stream').split(';',1)[0].strip().lower()
    if not is_document and ctype!='application/json'and upstream.status_code!=204:return refuse(502,'records_response_invalid')
    response=Response(bytes(content),status=upstream.status_code,content_type=ctype,
      headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})
    if is_document:response.headers['Content-Disposition']='attachment'
    return response
  except (requests.RequestException,OSError,ValueError):return refuse(502,'records_unavailable')
  finally:
   if transport is None:session.close()
 portal.add_url_rule(PREFIX+'/<path:path>','records_machine_proxy',forward,methods=['GET','POST'])
 portal.extensions['records_machine_bridge']={'state':'on','profile':profile.name}
 return True
