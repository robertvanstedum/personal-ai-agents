"""Opt-in CoS/MC configuration; existing German/PT bootstrap untouched."""
from .controlled_session import MODEL,SessionRefused
from core.realtime_voice.standards import conversation_turn_detection


def configuration(instructions):
    if not isinstance(instructions,str) or not 1 <= len(instructions.encode()) <= 24000:
        raise SessionRefused('Voice persona unavailable')
    vad = conversation_turn_detection('openai',create_response=False)
    vad['interrupt_response'] = True  # provider barge-in still cancels speech
    return {
        'type':'realtime','model':MODEL,'instructions':instructions,
        'max_output_tokens':1024,
        'audio':{'input':{'turn_detection':vad,
                          'transcription':{'model':'gpt-4o-transcribe','language':'en'}},
                 'output':{'voice':'cedar'}},
        'truncation':{'type':'retention_ratio','retention_ratio':0.8,'token_limits':{'post_instructions':8000}},
        'tools':[{'type':'function','name':name,'description':description,
                  'parameters':{'type':'object','properties':{},'additionalProperties':False}}
                 for name,description in (
                     ('consult_cos_agent','Transparently consult the shared CoS/MC agent for current facts, tools, Vault or substantive work on the owner\'s current spoken turn. Do not imply every spoken reply consulted it.'),
                     ('save_cos_note','Request the existing platform note operation for an actual explicit owner note command. No model-authored note or approval is accepted.'))],
        'tool_choice':'auto',
    }
