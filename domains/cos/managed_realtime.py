"""CoS owner-proxy integration; disabled unless explicit operator setup is ready.

The portal owns login and CSRF. X-Minimoi-* is stripped from browser input and
recreated there; the backend remains private to that existing trusted proxy.
"""
from flask import abort,request
from core.identity import resolve_user_id
from core.realtime_voice.controlled_host import create_host_blueprint
from core.realtime_voice.server_openai import OpenAIControlledProvider
from core.realtime_voice.providers.openai_realtime import _require_api_key
from core.realtime_voice import budget as shared_budget
from domains.cos.realtime_tools import confer_dispatch


def enabled(environ):
    return environ.get('MINIMOI_COS_MANAGED_REALTIME')=='1'


def owner_guard(req):
    uid=resolve_user_id(req)
    if uid is None or req.headers.get('X-Minimoi-User-Tier')!='owner':abort(403)
    if req.headers.get('X-Minimoi-Voice-Write-Verified')!='1':abort(403)
    return str(uid)


def conversation(principal,requested):
    # CoS currently has one owner-owned canonical conversation, no Private mode.
    # Do not silently accept another conversation or client-selected owner.
    from core.realtime_voice.controlled_session import SessionRefused
    if requested!='owner':raise SessionRefused('Voice conversation unavailable')
    return 'owner'


def install(app,*,environ,process_turn,instructions,budget=None,provider_factory=None):
    """No schema creation, provider calls or new credential storage at startup."""
    if not enabled(environ):return False
    raw=environ.get('MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO','')
    if not isinstance(raw,str) or not raw.isascii() or not raw.isdecimal() or not 1<=int(raw)<=20_000_000:return False
    ledger=budget if budget is not None else shared_budget.default_budget()
    try:
        # Operator must have prepared BOTH shared allowance and control schema.
        with ledger._tx() as run:
            for table in ('voice_control_owner','voice_control_session','voice_control_call','voice_control_usage'):
                run('SELECT * FROM '+table+' LIMIT 0')
            run('SELECT surface,provider_call_id,end_receipt FROM voice_control_session LIMIT 0')
    except Exception:return False
    if provider_factory is None:
        def provider_factory():
            from openai import OpenAI
            return OpenAIControlledProvider(OpenAI(api_key=_require_api_key(),max_retries=0,timeout=10))
    app.register_blueprint(create_host_blueprint(name='cos_managed_realtime',domain='cos',guard=owner_guard,
        conversation=conversation,instructions=instructions,dispatch=confer_dispatch(process_turn),
        provider_factory=provider_factory,environ=environ,budget=ledger),url_prefix='/api/realtime-voice/managed')
    return True
