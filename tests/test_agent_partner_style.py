from core.agent_partner import PARTNER_STYLE
from core.realtime_voice.controlled_config import configuration
from domains.cos.confer_service import ConferTurnService, ConferTurnRequest


def test_style_is_system_context_and_never_mutates_commands_or_saved_notes():
    calls=[];writes=[];resets=[]
    service=ConferTurnService(call_backend=lambda text,context,policy:calls.append((text,context)) or 'Synthetic reply',
        build_context=lambda:{'system_prompt':'Host authority'},increment_chat=lambda:None,
        backend_metadata=lambda:('Synthetic','test'),save_note=lambda text,key:writes.append(text) or {'saved':True},
        reset_conversation=lambda cid:resets.append(cid) or True)
    service.handle(ConferTurnRequest(text='Save a note: Keep this exact sentence.',channel='api_text'))
    assert writes==['Keep this exact sentence.'] and not calls
    service.handle(ConferTurnRequest(text='/new',channel='api_text',conversation_id='exact-session'))
    assert resets==['exact-session'] and not calls
    service.handle(ConferTurnRequest(text='Exact owner request',channel='api_text'))
    assert calls[0][0]=='Exact owner request'
    assert calls[0][1]['system_prompt']=='Host authority\n\n'+PARTNER_STYLE
    assert PARTNER_STYLE in configuration('Host authority')['instructions']
