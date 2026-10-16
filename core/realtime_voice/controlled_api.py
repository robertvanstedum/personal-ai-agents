"""Opt-in authenticated bridge; never registered by this draft.
Host guard must enforce existing owner identity + CSRF + on-record + conversation
ownership. No browser tool route exists: canonical events come from sideband.
"""
import threading
from flask import Blueprint,jsonify,request
from .controlled_session import SessionRefused, UnresolvedOperation
from .budget import VoiceBudgetError


def blueprint(*,name,guard,build_session,build_config,journal=None,surface=None):
    bp=Blueprint(name,__name__)
    sessions={}
    lock=threading.Lock()

    @bp.post('/session')
    def create():
        # guard is the existing host owner/write guard, not a client UID/header.
        principal=guard(request)
        if request.content_length is None or request.content_length > 70000:
            return jsonify(ok=False,error="Realtime request too large"),413
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or set(body)-{'provider','conversation_id','sdp'} or body.get('provider')!='openai':
            return jsonify(ok=False,error='Invalid realtime request'),400
        try:
            session=build_session(principal,body.get('conversation_id'))
            answer=session.start(body.get('sdp'),build_config(principal))
            with lock: sessions[session.sid]=session
            def watch():
                while not session.closed or getattr(session,"end_receipt",None) is None:
                    threading.Event().wait(1)
                    session.tick()
                with lock: sessions.pop(session.sid,None)
            threading.Thread(target=watch,name='voice-duration',daemon=True).start()
            return jsonify(ok=True,**answer),201
        except UnresolvedOperation as exc:
            return jsonify(ok=False,error=str(exc),code=exc.code),409
        except (SessionRefused,VoiceBudgetError):
            return jsonify(ok=False,error='Voice could not start; check the session allowance or unresolved operation.'),503

    @bp.post('/session/<sid>/end')
    def end(sid):
        principal=guard(request)
        with lock: session=sessions.get(sid)
        try:
            if session is not None:
                session.journal.check(sid,principal,active=False)
                receipt=session.end('user_ended')
            elif journal is not None:
                # Exact owner/surface/session readback survives pruning/restart.
                # Never construct a provider or retry a hangup during recovery.
                receipt=journal.end_receipt(sid,principal,surface)
            else:
                return jsonify(ok=False,error='Session unavailable'),404
            return jsonify(ok=True,**receipt)
        except (SessionRefused,VoiceBudgetError):
            return jsonify(ok=False,error='Session unavailable'),403
    return bp
