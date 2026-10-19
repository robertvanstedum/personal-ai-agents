"""Dev-only fixed-route proxy: conservative daily estimates and hard call/token limits.

Actual vendor reporting stays in the upstream gateway. This is a runaway guard,
not an invoice or a promise of an exact monetary ceiling. No automatic retries.
"""
import datetime, hashlib, hmac, http.server, json, os, sqlite3, threading, time
import urllib.request, urllib.error
from pathlib import Path
from zoneinfo import ZoneInfo

LOCK=threading.Lock()
DB='/state/model-admission.sqlite'
MAX_CALLS=100  # $0.25 conservative estimate per fixed-route call: $25/day.
MAX_SECONDS=900
MAX_BODY=400000
MODEL='minimoi-cos-agent'
UPSTREAM_MODEL='minimoi-mc-agent'  # Same Haiku model, no gateway fallback chain.

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def credentials():
    token=os.environ.get('PROVING_PROXY_TOKEN','')
    upstream=os.environ.get('UPSTREAM_KEY','')
    # LiteLLM generates sk- plus 22 random URL-safe characters (25 total).
    if len(token)<32 or len(upstream)<25:
        raise ValueError('strong_proxy_and_upstream_credentials_required')
    return token,upstream

def admit():
    day=datetime.datetime.now(ZoneInfo('America/Chicago')).date().isoformat()
    with LOCK:
        try:
            armed=json.loads(Path('/state/armed.json').read_text())
            expires=float(armed['expires_epoch'])
        except (OSError,ValueError,KeyError,TypeError):
            raise ValueError('operator_has_not_armed_run') from None
        if not 0 < expires-time.time() <= MAX_SECONDS:
            raise ValueError('run_time_limit')
        db=sqlite3.connect('file:'+DB+'?mode=rw',uri=True)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                count=db.execute('SELECT COUNT(*) FROM calls WHERE day=?',(day,)).fetchone()[0]
                if count>=MAX_CALLS: raise ValueError('daily_call_limit')
                db.execute('INSERT INTO calls(day,at) VALUES (?,?)',(day,datetime.datetime.now(datetime.timezone.utc).isoformat()))
        finally: db.close()
    return count+1

class Handler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(125)
    def log_message(self,*args): pass
    def answer(self,status,body):
        raw=json.dumps(body).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        if self.path!='/v1/chat/completions':return self.answer(404,{'error':'unsupported_route'})
        try: token,upstream_key=credentials()
        except ValueError:return self.answer(503,{'error':'guard_not_configured'})
        if not hmac.compare_digest(self.headers.get('Authorization','').encode('utf-8'),('Bearer '+token).encode('utf-8')):
            return self.answer(401,{'error':'not_authorized'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=MAX_BODY:raise ValueError('request_size')
            body=json.loads(self.rfile.read(n))
            if not isinstance(body,dict) or body.get('model')!=MODEL:raise ValueError('fixed_model_required')
            # Upstream destinations, API keys and background work cannot be selected by the caller.
            allowed={'model','messages','tools','tool_choice','temperature','top_p','stop','max_tokens','max_completion_tokens','stream','stream_options','user','parallel_tool_calls'}
            if set(body)-allowed:
                print(json.dumps({'event':'unsupported_parameters','names':sorted(set(body)-allowed)}),flush=True)
                raise ValueError('unsupported_parameter')
            if not isinstance(body.get('messages'),list):raise ValueError('messages_required')
            completion_limit=body.pop('max_completion_tokens',1024)
            body['max_tokens']=min(int(body.get('max_tokens',1024)),int(completion_limit),1024)
            if body['max_tokens']<1:raise ValueError('invalid_token_limit')
            admitted_call=admit()
            print(json.dumps({"event":"admitted","body_bytes":n,"daily_call":admitted_call}),flush=True)
        except (ValueError,TypeError,sqlite3.Error) as exc:
            known={'request_size','fixed_model_required','unsupported_parameter','messages_required','invalid_token_limit','operator_has_not_armed_run','run_time_limit','daily_call_limit'}
            print(json.dumps({'event':'refused','reason':str(exc) if str(exc) in known else type(exc).__name__}),flush=True)
            return self.answer(429,{'error':'proving_admission_refused'})
        headers_sent=False
        try:
            body.pop('user',None)
            body['model']=UPSTREAM_MODEL
            req=urllib.request.Request('http://minimoi-model-gateway:4000/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+upstream_key,'Content-Type':'application/json'})
            opener=urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({}))
            with opener.open(req,timeout=120) as response:
                raw=response.read(1000001)
                if len(raw)>1000000:raise ValueError('upstream_response_size')
                content_type=response.headers.get('Content-Type','application/json')
                if not body.get('stream'):json.loads(raw)
                self.send_response(200);headers_sent=True;self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except Exception:
            # Failed attempts remain admitted/charged estimates; never retry here.
            if headers_sent:self.close_connection=True
            else:self.answer(502,{'error':'upstream_failed_no_retry'})

if __name__=='__main__':
    credentials()
    with sqlite3.connect('file:'+DB+'?mode=rw',uri=True) as db:
        db.execute('SELECT COUNT(*) FROM calls').fetchone()
    http.server.ThreadingHTTPServer(('0.0.0.0',4000),Handler).serve_forever()
