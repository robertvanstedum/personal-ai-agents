"""Explicit owner Board creation, shared by typed and canonical spoken turns.
No generated arguments, implicit previous-message extraction or inference. Uses
existing Board store and idempotency; a conversation note is not a post-it.

A request that only points at something ("this", "your last answer") or names a
generic thing ("a reminder") is answered with one short question for the exact
words: nothing is guessed and nothing placeholder-like is saved.
"""
import hashlib
import re
from .stores import Author, GUILD_PLATFORM, POSTIT_MAX, FloorStoreUnavailable
from .payment_scrub import scrub

# Polite framing around a request: "please", "could you (please)", "can you kindly", and a
# trailing ", please" / "for me" / "thanks". It changes nothing about the request itself.
_PREFIX = r"(?:please\s+|kindly\s+|(?:can|could|would|will)\s+you\s+(?:please\s+|kindly\s+)?)?"
_TAIL = r"(?:\s*,?\s*(?:please|for\s+me|thanks|thank\s+you))*\s*[.!?]*"
_COMMAND = re.compile(_PREFIX + r"(?:create|make|add|save|write|put|pin)\s+(?:me\s+)?(?:a\s+|this\s+)?(?:post[- ]?it(?:\s+note)?\b|(?:board\s+note)|note\s+(?:on|to)\s+(?:the\s+|my\s+)?board)(?:\s+(?:on|to)\s+(?:the\s+|my\s+)?board)?\b\s*(?::|[-–—]|that\s+|saying\s+|to\s+|about\s+)?\s*(?P<text>.*)", re.I | re.S)
_GERMAN = re.compile(r"(?:bitte\s+)?(?:erstelle|schreibe|mach|mache)\s+(?:mir\s+)?(?:einen?\s+)?(?:post[- ]?it|notiz\s+(?:auf|an)\s+(?:dem|das)\s+board)\b\s*:?\s*(?P<text>.*)", re.I | re.S)


# One explicit text-first directive may sit among conversational questions.
# Extract only its stated literal payload, never a previous message/referent.
_TEXT_FIRST = re.compile(_PREFIX + r"(?:write|put|pin|add)\s+(?P<text>.+?)\s+(?:on|onto|to)\s+(?:a\s+|the\s+|my\s+)?(?:post[- ]?it(?:\s+note)?|board)\b" + _TAIL, re.I)
_OUTSIDE_REFUSAL = re.compile(r"\b(?:not|do\s+not|don['’]t|never|cancel|revoke|no\s+need|not\s+now|if|unless|whether|maybe|perhaps|example|hypothetical|suppose|imagine|yesterday|previously|said|says|asked|told|wrote|reported|quoted|document|transcript|translate|summari[sz]e|analy[sz]e)\b", re.I)
# Code, block quotes and indented blocks are material to read, never a request.
_CODE_OR_BLOCK = re.compile(r'`|(?m:^[ \t]*(?:>|\t)|^ {4})')
# A quotation mark, but not an apostrophe inside or at the end of a word (don't, James').
_QUOTE = re.compile(r'["“”‘]|(?<!\w)[\'’]|[\'’](?![\w\s.,!?;:]|$)')
_QUOTE_PAIRS = {'"': '"', '“': '”', "'": "'", '‘': '’', '’': '’'}

# A payload that only points at something ("this", "your last answer", "the summary above")
# or names a generic thing ("a reminder") is not the note's text. Ask for the words instead
# of saving the pointer. Quoted words are always literal.
_REFERENT = re.compile(r"""(?:
    this|that|it|these|those|same|the\s+same|above|the\s+above|below|the\s+below
  | (?:all\s+of\s+)?(?:this|that|it)(?:\s+(?:above|below|again))?
  | everything(?:\s+(?:above|you\s+said))?
  | (?:(?:the|that|this|your|my|our)\s+)?(?:(?:last|previous|latest|recent|earlier|above|current|full)\s+)?
      (?:answer|reply|response|message|summary|text|note|list|plan|points?|ideas?|suggestions?|output|results?|draft|one|thing|stuff)
      (?:\s+(?:above|below|earlier|again|you\s+(?:just\s+)?(?:gave|wrote|sent|made)))?
  | what\s+(?:you|i|we)\s+(?:just\s+)?(?:said|wrote|suggested|mentioned|proposed|sent|described|discussed)(?:\s+(?:above|earlier))?
)""", re.I | re.X)
_GENERIC = re.compile(r"(?:(?:a|an|another|some|one|new|a\s+new)\s+)?(?:reminder|note|post[- ]?it|task|to-?do|item|card|entry|message|thing)s?|something|anything", re.I)


def _payload(match):
    """The stated words of one command clause, or None when quotation marks frame the command
    itself (reported or quoted speech is data). Quotes around the payload are removed."""
    start, end = match.span('text')
    if _QUOTE.search(match.string[:start] + match.string[end:]):
        return None
    text = match.group('text').strip()
    close = _QUOTE_PAIRS.get(text[:1])
    if close and len(text) > 2 and text.endswith(close):
        return text[1:-1].strip()
    return text


def _literal(payload):
    """'' for a pointer or generic placeholder (ask for the words), else the payload unchanged."""
    if payload is None:
        return None
    bare = payload.rstrip('.!?').strip()
    if _REFERENT.fullmatch(bare) or _GENERIC.fullmatch(bare):
        return ''
    return payload


def _quoted(match):
    text = match.group('text').strip()
    return bool(text) and _QUOTE_PAIRS.get(text[:1]) is not None and len(text) > 2 and text.endswith(_QUOTE_PAIRS[text[:1]])


def _text_first_request(text):
    # Code or block-quoted material, and negative or reported framing, is DATA, not authorization.
    if _CODE_OR_BLOCK.search(text):return None
    clauses=[value.strip()for value in re.split(r'(?<=[.!?])\s+|[\r\n]+',text)if value.strip()]
    payloads=[];outside=[];literal=[]
    for clause in clauses:
        match=_TEXT_FIRST.fullmatch(clause)or _COMMAND.fullmatch(clause)or _GERMAN.fullmatch(clause)
        payload=_payload(match)if match else None
        if payload is None:outside.append(clause)
        else:payloads.append(payload);literal.append(_quoted(match))
    if not any(_TEXT_FIRST.fullmatch(clause)and _payload(_TEXT_FIRST.fullmatch(clause))is not None for clause in clauses):return None
    if _OUTSIDE_REFUSAL.search(' '.join(outside)):return None
    # Two differing explicit requests cannot be collapsed to an inferred one.
    if len(set(payloads))!=1:return ''
    payload=payloads[0] if all(literal) else _literal(payloads[0])
    if not payload:return ''

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
    """None for ordinary chat; '' for a Board request whose words are missing, only point at
    something ("this", "your last answer") or are ambiguous, so the caller asks once; otherwise
    the literal words to save. Quotes around the words are removed; a quoted command is data."""
    if not isinstance(text, str) or len(text) > 2000:
        return None
    stripped = text.strip()
    if re.fullmatch(_PREFIX + r'(?:put|pin|save)\s+(?:this|that|it)\s+(?:on|to)\s+(?:the\s+|my\s+)?board' + _TAIL, stripped, re.I):
        return ''  # no guessed referent: ask for the actual note text
    match = _COMMAND.fullmatch(stripped) or _GERMAN.fullmatch(stripped)
    if match:
        if _CODE_OR_BLOCK.search(text):
            return None
        payload = _payload(match)
        if payload is None:
            return None
        return payload if _quoted(match) else _literal(payload)
    return _text_first_request(text)


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
    if not clean:
        return {'reply': 'Which words should go on the Board? Send them exactly, for example: put "call the bank" on the board. Nothing was added to the Board.',
                'operation': {'type': 'board_postit', 'saved': False, 'reason': 'needs_text'}}
    if len(clean) > POSTIT_MAX:
        return {'reply': 'Please give me the Board note text in at most 280 characters. Nothing was added to the Board.',
                'operation': {'type': 'board_postit', 'saved': False, 'reason': 'too_long'}}
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
