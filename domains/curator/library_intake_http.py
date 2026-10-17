"""Internal signed receiver; install explicitly, default disabled, no public auth substitute."""
import threading,time
from core import library_intake_transport as protocol

class Nonces:
    def __init__(self):self.lock=threading.Lock();self.seen={}
    def claim(self,nonce,now):
        with self.lock:
            self.seen={k:v for k,v in self.seen.items()if now-v<=32}
            protocol.need(nonce not in self.seen and len(self.seen)<4096,'library_replay_refused')
            self.seen[nonce]=now

def install_configured(app,*,environ,preferences_path,history_path,intake_factory=None,clock=time.time):
    profile=protocol.configured(environ)
    if profile is None:return False
    from flask import request,Response
    from .library_intake import LibraryIntake
    intake_factory=LibraryIntake if intake_factory is None else intake_factory
    nonces=Nonces()
    def receive():
        # No cookie/session/user header or forwarded assertion is authentication.
        if request.host!='curator:8766' or request.query_string:return Response(status=404)
        if request.mimetype!='application/json' or request.content_length is None or not 0<request.content_length<=protocol.MAX_BODY:return Response(status=413)
        if request.headers.get('Content-Encoding'):return Response(status=415)
        try:
            raw=request.get_data(cache=False);value,nonce=protocol.verify(profile,raw,request.headers,now=clock())
            nonces.claim(nonce,clock())
        except (ValueError,TypeError):return Response(status=403)
        try:
            receipt=intake_factory(preferences_path,history_path=history_path).capture(principal=value['principal'],on_record=True,operation_id=value['operation_id'],url=value['url'],comment=value['comment'])
            body=protocol.packed({'status':'saved','operation_id':value['operation_id'],'receipt':receipt})
            protocol.need(len(body)<=protocol.MAX_REPLY,'library_reply_bound')
            return Response(body,status=200,content_type='application/json',headers={'X-MiniMoi-Receipt':protocol.response_signature(profile,nonce,body),'Cache-Control':'no-store'})
        except Exception:
            # Could have published; never claim nothing saved, never retry here.
            return Response(protocol.packed({'status':'unconfirmed'}),status=503,content_type='application/json',headers={'Cache-Control':'no-store'})
    app.add_url_rule(protocol.PATH,'internal_library_intake',receive,methods=['POST'])
    return True
