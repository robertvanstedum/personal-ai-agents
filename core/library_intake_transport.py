"""Fixed internal Library transport. No app/bootstrap, key fallback or URL fetch."""
from dataclasses import dataclass,field
import hashlib,hmac,json,re,secrets,time

PATH='/internal/library-intake'
TARGET='http://curator:8766'
PURPOSE=b'minimoi.library-intake.v1'
MAX_BODY=12000
MAX_REPLY=8192
ORIGINS={'dev':'https://dev.minimoi.ai','production':'https://minimoi.ai'}
class IntakeTransportRefused(ValueError):pass
class IntakeTransportUnconfirmed(IntakeTransportRefused):pass

def need(ok,code):
    if not ok:raise IntakeTransportRefused(code)
def packed(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def valid(value,limit):return isinstance(value,str)and 0<len(value)<=limit and not any(ord(c)<32 for c in value)
@dataclass(frozen=True)
class Profile:
    environment:str
    owner:str
    key:bytes=field(repr=False)
    @property
    def origin(self):return ORIGINS[self.environment]

def configured(environ):
    if environ.get('MINIMOI_LIBRARY_INTAKE')!='1':return None
    environment=environ.get('MINIMOI_LIBRARY_INTAKE_ENV');owner=environ.get('MINIMOI_LIBRARY_INTAKE_OWNER');secret=environ.get('PORTAL_SECRET_KEY')
    need(environment in ORIGINS and owner=='robert','library_profile_required')
    # Curator may hold ONLY the purpose-derived capability, never the portal
    # master Flask session-signing secret. No new key is generated here.
    derived=environ.get('MINIMOI_LIBRARY_INTAKE_DERIVED_KEY')
    master=None
    if secret is not None:
        need(isinstance(secret,str)and 32<=len(secret)<=4096,'library_shared_secret_required')
        master=hmac.digest(secret.encode(),PURPOSE,'sha256')
    if derived is not None:
        need(isinstance(derived,str)and re.fullmatch('[a-f0-9]{64}',derived)is not None,'library_derived_key_invalid')
        key=bytes.fromhex(derived)
        need(master is None or hmac.compare_digest(key,master),'library_key_sources_disagree')
    else:
        need(master is not None,'library_shared_secret_required')
        key=master
    return Profile(environment,owner,key)

def operation(owner,conversation,note):
    need(all(valid(v,160)for v in (owner,conversation,note)),'library_source_identity_required')
    return 'guild-library-'+hashlib.sha256((owner+'\0'+conversation+'\0'+note).encode()).hexdigest()

def payload(profile,conversation,note,url,comment):
    value={'environment':profile.environment,'origin':profile.origin,'principal':profile.owner,'record_mode':'on_record','conversation':conversation,'note':note,'operation_id':operation(profile.owner,conversation,note),'url':url,'comment':comment}
    validate(value,profile);return value

def validate(value,profile):
    need(type(value)is dict and set(value)=={'environment','origin','principal','record_mode','conversation','note','operation_id','url','comment'},'library_payload_invalid')
    need(value['environment']==profile.environment and value['origin']==profile.origin and value['principal']==profile.owner and value['record_mode']=='on_record','library_scope_refused')
    need(value['operation_id']==operation(profile.owner,value['conversation'],value['note']),'library_operation_invalid')
    need(valid(value['url'],3000)and isinstance(value['comment'],str)and len(value['comment'])<=4000 and '\x00'not in value['comment'],'library_metadata_invalid')

def signature(profile,raw,timestamp,nonce):
    return hmac.new(profile.key,b'POST\n'+PATH.encode()+b'\n'+timestamp.encode()+b'\n'+nonce.encode()+b'\n'+raw,hashlib.sha256).hexdigest()

def headers(profile,raw,*,now,nonce):
    timestamp=str(int(now));need(re.fullmatch('[a-f0-9]{32}',nonce)is not None,'library_nonce_invalid')
    return {'Content-Type':'application/json','X-MiniMoi-Time':timestamp,'X-MiniMoi-Nonce':nonce,'X-MiniMoi-Signature':signature(profile,raw,timestamp,nonce)}

def decode(raw):
    def pairs(rows):
        result={}
        for k,v in rows:need(k not in result,'library_duplicate_key');result[k]=v
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(IntakeTransportRefused('library_json_invalid')))
    except (ValueError,UnicodeError):raise IntakeTransportRefused('library_json_invalid')from None

def verify(profile,raw,header,*,now):
    need(isinstance(raw,bytes)and 0<len(raw)<=MAX_BODY,'library_body_bound')
    ts=header.get('X-MiniMoi-Time','');nonce=header.get('X-MiniMoi-Nonce','');sig=header.get('X-MiniMoi-Signature','')
    need(re.fullmatch('[0-9]{1,12}',ts)is not None and re.fullmatch('[a-f0-9]{32}',nonce)is not None and re.fullmatch('[a-f0-9]{64}',sig)is not None,'library_signature_required')
    need(-2<=now-int(ts)<=30,'library_signature_expired')
    need(hmac.compare_digest(sig,signature(profile,raw,ts,nonce)),'library_signature_refused')
    value=decode(raw);validate(value,profile);return value,nonce

def response_signature(profile,nonce,raw):
    return hmac.new(profile.key,b'RESPONSE\n'+PATH.encode()+b'\n'+nonce.encode()+b'\n'+raw,hashlib.sha256).hexdigest()

def send(profile,value,*,session_factory=None,clock=time.time,monotonic=time.monotonic):
    validate(value,profile);raw=packed(value);need(len(raw)<=MAX_BODY,'library_body_bound');nonce=secrets.token_hex(16)
    if session_factory is None:
        import requests
        session_factory=requests.Session
    try:
        started=monotonic()
        with session_factory()as session:
            session.trust_env=False
            with session.post(TARGET+PATH,data=raw,headers=headers(profile,raw,now=clock(),nonce=nonce),timeout=(1,3),allow_redirects=False,stream=True)as reply:
                need(reply.status_code==200,'library_response_unconfirmed')
                body=bytearray()
                for part in reply.iter_content(chunk_size=1):
                    body.extend(part);need(len(body)<=MAX_REPLY and monotonic()-started<=5,'library_reply_bound')
                body=bytes(body)
                need(hmac.compare_digest(reply.headers.get('X-MiniMoi-Receipt',''),response_signature(profile,nonce,body)),'library_receipt_unconfirmed')
                result=decode(body)
                need(type(result)is dict and set(result)=={'status','operation_id','receipt'}and result['status']=='saved'and result['operation_id']==value['operation_id'],'library_receipt_unconfirmed')
                return result['receipt']
    except Exception:raise IntakeTransportUnconfirmed('library_filing_unconfirmed_check_before_retry')from None
