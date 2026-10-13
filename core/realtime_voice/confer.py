"""Authenticated transcription bootstrap for the platform's own CoS agent.

Speech only transcribes; ui/send owns reasoning/actions and ui/speech reads the
server-owned reply. The durable daily allowance is required before minting.
"""
import os
from flask import Blueprint, jsonify, request
from core.identity import resolve_user_id
from core.realtime_voice import budget
from core.realtime_voice.bootstrap import check_voice_rate_limit
from core.realtime_voice.capabilities import AGENT_CONVERSATION_MODE, ProviderUnavailableError, providers_for_mode
from core.realtime_voice.duration_guard import DurationGuard
from core.realtime_voice.providers import openai_realtime


def _duration_guard():
    return DurationGuard(warning_minutes=int(os.environ.get('VOICE_CONFER_WARNING_MINUTES',13)),max_minutes=int(os.environ.get('VOICE_CONFER_MAX_MINUTES',15)))


def create_confer_voice_blueprint(*, locale='en-US', build_voice_instructions=None):
    # Preserve the host factory signature; spoken persona comes from the
    # selected CoS agent and server-owned TTS, not a second reasoning model.
    bp=Blueprint('realtime_voice_confer',__name__)

    @bp.route('/api/realtime-voice/confer/capabilities',methods=['GET'])
    def capabilities():
        if resolve_user_id(request) is None:
            return jsonify({'ok':False,'error':'identity required'}),401
        providers=[p.public_dict() for p in providers_for_mode(AGENT_CONVERSATION_MODE,locale) if p.provider=='openai']
        return jsonify({'ok':True,'mode':AGENT_CONVERSATION_MODE,'default_provider':'openai','providers':providers,'budget':budget.public_status()})

    @bp.route('/api/realtime-voice/confer/bootstrap',methods=['POST'])
    def bootstrap():
        uid=resolve_user_id(request)
        if uid is None:
            return jsonify({'ok':False,'error':'identity required'}),401
        if not check_voice_rate_limit(uid):
            return jsonify({'ok':False,'error':'rate limited'}),429
        body=request.get_json(silent=True) or {}
        if body.get('provider','openai')!='openai':
            return jsonify({'ok':False,'error':'This voice mode uses OpenAI transcription and the selected CoS agent.'}),400
        try:
            guard=_duration_guard()
            with budget.admit(kind='transcription_session',domain='cos',principal=str(uid),required=True,amount_micro=budget.session_allowance_micro()):
                credential=openai_realtime.mint_confer_transcription_credential(transcription_language='en',user_id_for_safety_identifier=str(uid),transcription_prompt='Transcribe Robert speaking to MiniMoi Chief of Staff. Preserve names and product terms.')
        except budget.VoiceBudgetError as e:
            return jsonify({'ok':False,'error':e.public}),e.status
        except ValueError as e:
            return jsonify({'ok':False,'error':str(e)}),400
        except ProviderUnavailableError as e:
            return jsonify({'ok':False,'error':str(e)}),503
        except openai_realtime.OpenAIRealtimeError as e:
            return jsonify({'ok':False,'error':str(e)}),502
        return jsonify({'ok':True,**credential,'warning_minutes':guard.warning_minutes,'max_minutes':guard.max_minutes})
    return bp
