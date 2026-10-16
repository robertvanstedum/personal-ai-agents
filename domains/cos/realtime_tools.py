"""No separate agent implementation; exact canonical Confer host adapter.
MC host must supply its existing kept-note + MC CoS transport boundary instead.
Do not call this adapter with model-generated arguments or synthesized requests.
"""
from core.realtime_voice.controlled_session import SessionRefused
from domains.cos.confer_service import parse_explicit_note


def confer_dispatch(process_turn):
    def dispatch(name,canonical_text,conversation_id,operation_id):
        if name not in {'consult_cos_agent','save_cos_note'}:
            raise SessionRefused('Unknown voice function')
        if name=='save_cos_note' and parse_explicit_note(canonical_text) is None:
            raise SessionRefused('Explicit owner note command required')
        result=process_turn(canonical_text,channel='html_voice',conversation_id=conversation_id,request_id=operation_id)
        if name=='save_cos_note' and not result.operation:
            raise SessionRefused('Note receipt unavailable')
        return result.public_dict()
    return dispatch
