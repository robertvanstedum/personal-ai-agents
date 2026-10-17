"""Explicit owner Board creation, shared by typed and canonical spoken turns.
No generated arguments, implicit previous-message extraction or inference. Uses
existing Board store and idempotency; a conversation note is not a post-it.
"""
import hashlib
import re
from .stores import Author, GUILD_PLATFORM, POSTIT_MAX, FloorStoreUnavailable
from .payment_scrub import scrub

_PREFIX = r"(?:please\s+|(?:can|could|would)\s+you\s+)?"
_COMMAND = re.compile(_PREFIX + r"(?:create|make|add|save|write|put|pin)\s+(?:me\s+)?(?:a\s+|this\s+)?(?:post[- ]?it(?:\s+note)?\b|(?:board\s+note)|note\s+(?:on|to)\s+(?:the\s+|my\s+)?board)(?:\s+(?:on|to)\s+(?:the\s+|my\s+)?board)?\b\s*(?::|[-–—]|that\s+|saying\s+|to\s+|about\s+)?\s*(?P<text>.*)", re.I | re.S)
_GERMAN = re.compile(r"(?:bitte\s+)?(?:erstelle|schreibe|mach|mache)\s+(?:mir\s+)?(?:einen?\s+)?(?:post[- ]?it|notiz\s+(?:auf|an)\s+(?:dem|das)\s+board)\b\s*:?\s*(?P<text>.*)", re.I | re.S)


# One explicit text-first directive may sit among conversational questions.
# Extract only its stated literal payload, never a previous message/referent.
_TEXT_FIRST_PREFIX = r"(?:please\s+|(?:can|could|would)\s+you\s+(?:please\s+)?)?"
_TEXT_FIRST = re.compile(_TEXT_FIRST_PREFIX + r"(?:write|put|pin|add)\s+(?P<text>.+?)\s+(?:on|onto|to)\s+(?:a\s+|the\s+|my\s+)?(?:post[- ]?it(?:\s+note)?|board)\b\s*[.!?]*", re.I)
_OUTSIDE_REFUSAL = re.compile(r"\b(?:not|do\s+not|don['’]t|never|cancel|revoke|no\s+need|not\s+now|if|unless|whether|maybe|perhaps|example|hypothetical|suppose|imagine|yesterday|previously|said|says|asked|told|wrote|reported|quoted|document|transcript|translate|summari[sz]e|analy[sz]e)\b", re.I)
_QUOTED_OR_CODE = re.compile(r'["`“”]|(?<!\w)[\'‘]|[\'’](?!\w)|(?m:^[ \t]*(?:>|\t)|^ {4})')


def _text_first_request(text):
    # Unsupported quoted/code/negative framing is DATA, not authorization.
    if _QUOTED_OR_CODE.search(text):return None
    clauses=[value.strip()for value in re.split(r'(?<=[.!?])\s+|[\r\n]+',text)if value.strip()]
    payloads=[];outside=[]
    for clause in clauses:
        match=_TEXT_FIRST.fullmatch(clause)
        if match:payloads.append(match.group('text').strip())
        else:
            other=_COMMAND.fullmatch(clause)or _GERMAN.fullmatch(clause)
            if other:payloads.append(other.group('text').strip())
            else:outside.append(clause)
    if not any(_TEXT_FIRST.fullmatch(clause)for clause in clauses):return None
    if _OUTSIDE_REFUSAL.search(' '.join(outside)):return None
    # Two differing explicit requests cannot be collapsed to an inferred one.
    if len(set(payloads))!=1:return ''
    payload=payloads[0]
    # Do not reinterpret unmarked story/document paragraphs as fresh commands.
    # The observed turn surrounds its explicit write with ordinary questions
    # and a matching 'Just say ...' confirmation, not arbitrary source prose.
    for clause in outside:
        if re.fullmatch(r'(?:how|what|where|when|why|can|could|would|is|are|do|does)\b[^\n]{0,299}\?',clause,re.I):continue
        if re.fullmatch(r'(?:okay|ok|yes|please|thank you)[.!]*',clause,re.I):continue
        say=re.fullmatch(r'(?:just\s+)?say\s+(.+)',clause,re.I)
        if say:
            normalize=lambda value:' '.join(value.rstrip('.!?').casefold().split())
            if normalize(say[1])==normalize(payload):continue
            return ''
        return None
    return payload


def parse(text):
    """Return literal payload only for a direct unquoted command, else None."""
    if not isinstance(text, str) or len(text) > 2000:
        return None
    if re.fullmatch(_PREFIX + r'(?:put|pin|save)\s+(?:this|that|it)\s+(?:on|to)\s+(?:the\s+|my\s+)?board[.!?]?', text.strip(), re.I):
        return ''  # no guessed referent: ask for the actual note text
    match = _COMMAND.fullmatch(text.strip()) or _GERMAN.fullmatch(text.strip())
    return match.group('text').strip() if match else _text_first_request(text)


def execute(text, *, board, conversation, principal, turn_key, author=None):
    """Only caller's authenticated, kept owner turn may enter this boundary.

    Returns None for normal chat. A real Board ID is mandatory for success.
    Retrying the same turn cannot create a second post-it, even if saving the
    conversation receipt previously failed. No completion is inferred from prose.
    """
    payload = parse(text)
    if payload is None:
        return None
    if not isinstance(principal, str) or not principal or not isinstance(turn_key, str) or not turn_key:
        raise ValueError('Authenticated owner turn required')
    if author is not None and (author.id != principal or author.kind != 'owner'):
        raise ValueError('Authenticated owner required')
    clean = scrub(payload).strip()
    if not clean or len(clean) > POSTIT_MAX:
        return {'reply': 'Please give me the Board note text in at most 280 characters. Nothing was added to the Board.', 'operation': {'type': 'board_postit', 'saved': False}}
    scope = getattr(conversation, 'floor', None)
    if not isinstance(scope, str) or not scope:
        raise ValueError('Owned conversation floor required')
    key = 'owner-board-' + hashlib.sha256((principal + '\0' + scope + '\0' + turn_key).encode()).hexdigest()
    result = board.add_postit(clean, author or Author(principal, 'owner', principal), idempotency_key=key)
    row = result.value
    if result.outcome != 'added' or not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
        return {'reply': 'The Board did not confirm a saved post-it. Check the Board before trying again.', 'operation': {'type': 'board_postit', 'saved': False}}
    reply = f"Saved Board post-it #{row['id']}: {clean}"
    try:
        kept = conversation.add_note(key + '-receipt', reply, GUILD_PLATFORM, area='Board', page='board')
    except FloorStoreUnavailable:
        kept = None
    return {'reply': reply, 'reply_note': kept.value if kept is not None and kept.outcome == 'kept' else None,
            'operation': {'type': 'board_postit', 'saved': True, 'id': row['id'], 'repeated': bool(result.repeated)},
            'history_saved': kept is not None and kept.outcome == 'kept'}
