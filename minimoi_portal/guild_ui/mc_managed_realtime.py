"""MC managed realtime: existing authenticated conversation and turn boundary.
Off by default. Provider speaks directly; only explicit consults call the shared
CoS/MC backend. No model args become owner text/approval, no fabricated request.
"""
from __future__ import annotations
import hashlib
import threading
from flask import abort,current_app,jsonify,g
from . import cfg,owner_api,_worker_config
from .security import check_write,check_stop
from core.realtime_voice.controlled_host import create_host_blueprint
from core.realtime_voice.controlled_session import SessionRefused
from core.realtime_voice import budget as shared_budget
from .mc_voice_direct import INSTRUCTIONS  # MC's operating rules, kept by the direct path's adapter (Spec 165 §4.2)

ENV='MINIMOI_MC_MANAGED_REALTIME'


def install(app,mount,*,environ,budget=None,provider_factory=None):
    from .mc_voice import enabled
    config=app.extensions[mount]
    if environ.get(ENV)!='1' or not enabled(environ) or getattr(config['services'].mc,'kind',None)!='cos':return False
    raw=environ.get('MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO','')
    if not isinstance(raw,str) or not raw.isascii() or not raw.isdecimal() or not 1<=int(raw)<=20_000_000:return False
    ledger=budget if budget is not None else shared_budget.default_budget()
    try:
        with ledger._tx() as run:
            for table in ('voice_control_owner','voice_control_session','voice_control_call','voice_control_usage'):run('SELECT * FROM '+table+' LIMIT 0')
            run('SELECT surface,provider_call_id,end_receipt FROM voice_control_session LIMIT 0')
    except Exception:return False
    if provider_factory is None:
        def provider_factory():
            from openai import OpenAI
            from core.realtime_voice.server_openai import OpenAIControlledProvider
            from core.realtime_voice.providers.openai_realtime import _require_api_key
            return OpenAIControlledProvider(OpenAI(api_key=_require_api_key(),max_retries=0,timeout=10))
    bindings={};lock=threading.RLock()

    def conversation(principal,requested):
        from . import api
        from .conversations import ID_RE,ConversationNotFound,ConversationStoreUnavailable
        if not isinstance(requested,str) or not ID_RE.fullmatch(requested):raise SessionRefused('Name an owned conversation')
        store=api._conversations()
        try:conv=store.get(requested,principal)
        except (ConversationNotFound,ConversationStoreUnavailable):raise SessionRefused('Conversation unavailable') from None
        if conv.get('principal')!=principal or conv.get('archived') or conv.get('record_mode')=='off_record':raise SessionRefused('Conversation unavailable')
        # Capture actual authenticated objects while the real request exists.
        user=config['current_user']() or {}
        if user.get('username')!=principal:raise SessionRefused('Voice owner unavailable')
        captured={'app':app,'config':config,'principal':principal,'conversation':conv,'conversations':store,
                  'floor':api._conv_floor(conv),'board':api._floor(),'services':config['services'],'author':api._author()}
        with lock:bindings[requested]=captured
        g.mc_voice_startup_binding = captured
        return requested

    def voice_instructions(principal):
        from . import api
        from .mc_voice_transcripts import startup_context
        bound = getattr(g, 'mc_voice_startup_binding', None)
        if bound is None or bound['principal'] != principal:
            raise SessionRefused('Voice conversation unavailable')
        def scope():
            from .conversations import ConversationNotFound, ConversationStoreUnavailable
            try:
                fresh = bound['conversations'].get(bound['conversation']['id'], principal)
            except (ConversationNotFound, ConversationStoreUnavailable):
                raise SessionRefused('Voice conversation unavailable') from None
            if (fresh.get('principal') != principal or fresh.get('archived')
                    or fresh.get('record_mode') == 'off_record'
                    or fresh.get('notes_floor') != bound['conversation'].get('notes_floor')):
                raise SessionRefused('Voice conversation unavailable')
        return INSTRUCTIONS + '\n\n' + startup_context(bound['floor'], principal,
            scope_check=scope, scrub=api.scrub)

    def dispatch(name,text,cid,operation_id):
        from . import api
        from .stores import NOTE_MAX
        with lock:bound=bindings.get(cid)
        if bound is None or name not in {'consult_cos_agent','save_cos_note'} or not isinstance(text,str) or not text.strip() or len(text)>NOTE_MAX:raise SessionRefused('Spoken turn unavailable')
        # Re-read current owner scope/archive state before any persisted action.
        fresh=bound['conversations'].get(cid,bound['principal'])
        if (fresh.get('principal')!=bound['principal'] or fresh.get('archived') or fresh.get('record_mode')=='off_record'
                or fresh.get('notes_floor')!=bound['conversation'].get('notes_floor')):raise SessionRefused('Conversation unavailable')
        with bound['app'].app_context(),_worker_config(bound['app'],bound['config']):
            # The turn ledger (issue #346): one claim covers every action below. Off: turn is None.
            from . import feature_environ
            from .mc import ledger_gate
            try:
                turn=ledger_gate.gate(bound['services'],feature_environ(),principal=bound['principal'],conversation_id=cid,
                                      note_request_id=operation_id,request_id=operation_id,kind='voice')
            except ledger_gate.Refusal as refused:
                raise SessionRefused(refused.body.get('message') or 'Spoken turn unavailable') from None
            phase=['before']
            try:
                result=_voice_dispatch(bound,name,text,cid,operation_id,fresh,turn,phase)
            except BaseException:
                api._ledger_abort(turn,phase[0])
                raise
            return result

    def _voice_dispatch(bound,name,text,cid,operation_id,fresh,turn,phase):
        from . import api
        from .stores import NOTE_MAX
        if True:
            phase[0]='platform'
            from .board_commands import execute as board_command
            board_result=board_command(text,board=bound['board'],conversation=bound['floor'],principal=bound['principal'],turn_key=operation_id,author=bound['author'])
            if board_result is not None:
                if turn is not None:turn.finish_platform(board_result)
                return board_result
            # The trusted journal supplies one id per utterance/effect, including tool replays.
            from .owner_actions import execute as owner_action
            action=owner_action(text,cos=bound['services'].mc,conversation=bound['floor'],principal=bound['principal'],turn_key=operation_id)
            if action is not None:
                if turn is not None:turn.finish_platform(action)
                return action
            phase[0]='before'                     # neither command matched: nothing was done
            if name=='save_cos_note':
                if turn is not None:turn.finish_not_sent({'error':'not_a_command'})
                raise SessionRefused('Explicit owner note command required')
            clean,bad=api._clean_text(text,NOTE_MAX,'spoken note')
            if bad is not None:
                if turn is not None:turn.finish_not_sent({'error':'invalid'})
                raise SessionRefused('Spoken note refused')
            kept=bound['floor'].add_note(operation_id,clean,bound['author'],page='voice')
            if kept.outcome!='kept' or not kept.value:
                if turn is not None:turn.finish_not_sent({'error':'not_kept'})
                raise SessionRefused('Spoken note not kept')
            note=kept.value
            from .mc_voice_transcripts import copy_to_chat
            def copy_scope():
                current = bound['conversations'].get(cid, bound['principal'])
                if (current.get('principal') != bound['principal'] or current.get('archived')
                        or current.get('record_mode') == 'off_record'
                        or current.get('notes_floor') != bound['conversation'].get('notes_floor')):
                    raise SessionRefused('Conversation unavailable')
            copied = None
            from .mc_voice_transcripts import chat_copy_requested
            if chat_copy_requested(text):
                phase[0]='platform'
                copied = copy_to_chat(text, bound['floor'], operation_id,
                    session_id=bound.get('transcript_sid'), scope_check=copy_scope, scrub=api.scrub)
                phase[0]='before'
            if copied is not None:
                if turn is not None:turn.finish_platform({'status':'answered','backend_kind':'platform'})
                return {**copied, 'note_request_id': operation_id}
            from .jobs_api import job_command
            found=(bound['services'],bound['principal'],fresh,bound['floor'],note,operation_id,operation_id,[])
            phase[0]='platform'
            command=job_command(found)
            phase[0]='before'
            if command is not None:
                response,status=command if isinstance(command,tuple) else (command,command.status_code)
                body=response.get_json()
                if turn is not None:turn.finish_platform(body if isinstance(body,dict) else {})
                if status!=200:raise SessionRefused('Job command refused')
                return {'reply':body.get('message') or 'Job command processed.','job_command':body.get('job_command'),'job':body.get('job'),'note_request_id':operation_id}
            from .mc.cos import MANAGED_VOICE_CHANNEL
            phase[0]='model'
            body,status=api.run_mc_turn(bound['services'],conversations=bound['conversations'],floor=bound['floor'],conv=fresh,
                principal=bound['principal'],note=note,note_id=operation_id,request_id=operation_id,attachment_ids=[],
                host_channel=MANAGED_VOICE_CHANNEL,ledger_turn=turn)
            if turn is not None:turn.finish_model(body,status)
            reply=body.get('reply_note') if isinstance(body,dict) else None
            if status!=200 or not isinstance(body,dict) or body.get('status')!='answered' or not isinstance(reply,dict) or not isinstance(reply.get('text'),str):raise SessionRefused('Agent outcome unavailable; do not claim completion')
            return {'reply':reply['text'],'note_request_id':operation_id,'reply_note_id':reply.get('request_id'),'history_saved':body.get('history_saved')}

    def retain_transcript(sid, cid, role, item, index, text, partial):
        from .mc_voice_transcripts import retain
        with lock:
            bound = bindings.get(cid)
        if bound is None:
            raise SessionRefused('Voice transcript scope unavailable')
        result = retain(bound, cid, sid, role, item, index, text, partial)
        # Canonical ASR retention precedes dispatch, so the copy is bound to
        # this actual voice session, never a previous session's last phrase.
        with lock:
            bound['transcript_sid'] = sid
        return result

    def canonical_command_match(text,cid):
        from .jobs import interpret,ACTIVE
        from .board_commands import parse as board_text
        from .owner_actions import parse as owner_action_text
        command=interpret(text)
        from .mc_voice_transcripts import WANTED as transcript_wanted, chat_copy_requested
        if board_text(text) is not None or owner_action_text(text) is not None or transcript_wanted.search(text) or chat_copy_requested(text):return True
        if command is None:return False
        with lock:bound=bindings.get(cid)
        if bound is None:raise SessionRefused('Conversation unavailable')
        with bound['app'].app_context(),_worker_config(bound['app'],bound['config']):
            from .jobs_api import manager
            mgr=manager()
            if mgr is None:return False
            if command=='stop':
                return any(j.get('principal')==bound['principal'] and j.get('state') in ACTIVE
                           for j in mgr.store.list(cid))
            return True

    def guard(req):
        refusal=(check_stop if req.path.endswith('/end') else check_write)(config['base_url'])
        if refusal is not None:abort(current_app.make_response(refusal))
        if not req.path.endswith('/end') and not config['services'].mc_turns:abort(409)
        user=config['current_user']() or {};principal=user.get('username')
        if not isinstance(principal,str) or not principal:abort(403)
        return principal

    bpname=mount+'_managed_realtime'
    bp=create_host_blueprint(name=bpname,domain='mc',guard=guard,conversation=conversation,instructions=voice_instructions,
        dispatch=dispatch,provider_factory=provider_factory,environ=environ,budget=ledger,
        canonical_command_match=canonical_command_match, on_transcript=retain_transcript)
    # owner_api uses the existing real mount config for every new route.
    app.extensions[bpname]=config
    @owner_api
    def verified_owner():return jsonify(ok=True)
    @bp.before_request
    def verify_owner():
        response=verified_owner()
        return response if response.status_code!=200 else None
    @bp.after_request
    def private_response(response):
        response.headers['Cache-Control']='no-store'
        return response
    app.register_blueprint(bp,url_prefix=config['url_prefix']+'/api/v1/mc/voice/managed')
    config['mc_managed_realtime']={'session_url':config['url_prefix']+'/api/v1/mc/voice/managed/session','provider':'openai','model':'gpt-realtime-2.1'}
    return True
