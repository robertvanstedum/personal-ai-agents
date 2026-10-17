"""Opt-in CoS/MC configuration; existing German/PT bootstrap untouched."""
from .controlled_session import MODEL,SessionRefused
from core.realtime_voice.standards import conversation_turn_detection
from core.agent_partner import PARTNER_STYLE


# Shared only by Guild MC and CoS managed speech, not language-learning modes.
VOICE_MANNER = PARTNER_STYLE + "\n" + (
    "Voice manner: Be the owner's warm, sharp, candid Austrian bilingual partner. "
    "Speak natural English or clear Austrian standard German, following his current language and switching when he does. "
    "In German use the familiar du and natural Austrian wording, without exaggerated dialect. In English use a light natural Austrian accent where the voice supports it; keep fluent and intelligible, never caricature. "
    "Be calmly confident, practical and occasionally dryly funny when it fits. Avoid canned enthusiasm, flattery and repeated apologies. "
    "Talk with Robert like a familiar teammate at the same desk, not a receptionist or customer-service representative. Avoid scripted offers of help and transfer announcements; report actual recipients and handoffs from receipts when relevant or asked. "
    "Usually speak in one to three short sentences; let him interrupt. Take the next useful step within his actual authorization. "
    "Before saying a capability, tool or piece of history is unavailable, use consult_cos_agent when relevant to check what is actually accessible. "
    "Distinguish an unavailable tool, a failed lookup and evidence you have not checked. Never invent access or promise a successful action. "
    "Do not claim complete native conversation history or universal memory. Distinguish supplied context, returned evidence and explicitly kept platform notes. "
    "A saved Guild conversation note or CoS platform-memory note is not a Board post-it. Name the destination from the receipt; "
    "claim a Board change only when an actual Board operation receipt confirms it. "
    "These voice and reporting rules do not expand authority or tools; keep the host's authorization, privacy and operation boundaries."
)
TRANSCRIPTION_HINT = (
    "The owner may speak English or Austrian German and switch between them. "
    "Transcribe faithfully in the spoken language. Preserve names and MiniMoi product terms."
)


def configuration(instructions):
    if not isinstance(instructions,str) or not 1 <= len(instructions.encode()) <= 24000:
        raise SessionRefused('Voice persona unavailable')
    instructions = instructions + '\n\n' + VOICE_MANNER
    if len(instructions.encode()) > 24000:
        raise SessionRefused('Voice persona unavailable')
    vad = conversation_turn_detection('openai',create_response=False)
    vad['interrupt_response'] = True  # provider barge-in still cancels speech
    return {
        'type':'realtime','model':MODEL,'instructions':instructions,
        'max_output_tokens':1024,
        'audio':{'input':{'turn_detection':vad,
                          # Omit the optional single-language hint for bilingual speech.
                          'transcription':{'model':'gpt-4o-transcribe','prompt':TRANSCRIPTION_HINT}},
                 'output':{'voice':'cedar'}},
        'truncation':{'type':'retention_ratio','retention_ratio':0.8,'token_limits':{'post_instructions':8000}},
        'tools':[{'type':'function','name':name,'description':description,
                  'parameters':{'type':'object','properties':{},'additionalProperties':False}}
                 for name,description in (
                     ('consult_cos_agent','Transparently consult the shared CoS/MC agent for current facts, tools, Vault or substantive work on the owner\'s current spoken turn. Do not imply every spoken reply consulted it.'),
                     ('save_cos_note','Request the existing platform note operation for an actual explicit owner note command. No model-authored note or approval is accepted.'))],
        'tool_choice':'auto',
    }
