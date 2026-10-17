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


def parse(text):
    """Return literal payload only for a direct unquoted command, else None."""
    if not isinstance(text, str) or len(text) > 2000:
        return None
    if re.fullmatch(_PREFIX + r'(?:put|pin|save)\s+(?:this|that|it)\s+(?:on|to)\s+(?:the\s+|my\s+)?board[.!?]?', text.strip(), re.I):
        return ''  # no guessed referent: ask for the actual note text
    match = _COMMAND.fullmatch(text.strip()) or _GERMAN.fullmatch(text.strip())
    return match.group('text').strip() if match else None


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
