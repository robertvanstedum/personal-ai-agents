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

# Polite framing around a request: "okay,", "please", "could you (please)", "can you kindly", and a
# trailing ", please" / "for me" / "thanks". It changes nothing about the request itself.
_PREFIX = r"(?:(?:okay|ok)(?:\s*,\s+|\s+))?(?:please\s+|kindly\s+|(?:can|could|would|will)\s+you\s+(?:please\s+|kindly\s+)?)?"
# Each separator has one reading (review M-4): a comma with spaces around it, or spaces, or nothing, which is exactly what
# "\s*,?\s*" read; its two "\s*" split a run of spaces every way, doubling the work with each repeated "please".
_TAIL = r"(?:(?:\s*,\s*|\s+)?(?:please|for\s+me|thanks|thank\s+you))*\s*[.!?]*"
# A4-1 (29 Oct, Robert on dev: "Can you put a test note on the board, just a post-it note, say test?" was refused). Three
# natural parts of how he asks, none of which ever becomes the words saved:
# * a one-word kind before the noun ("a test note", "a quick post-it"): a closed list, so no other word is read as framing;
# * an aside naming the post-it after the Board (", just a post-it note");
# * "say X" or "that says X" introducing the words, beside the colon, dash, "that", "saying", "to" and "about" there
#   were. "that says" is tried before "that", so "says" is never saved as a word. A bare "say" introduces them only after
#   a comma or the aside, or with a colon (review S-3): "Add a post-it say thanks to Anna" keeps "say thanks to Anna".
_KIND = r"(?:(?:test|quick|short|little|small|new|sticky|simple)\s+)?"
_ASIDE = r"(?:\s*,\s*(?:just\s+)?(?:a\s+)?post[- ]?it(?:\s+note)?)?"
_SAYS = (r"(?::|[-–—]|that\s+says(?:\s*:)?\s+|that\s+|saying(?:\s*:)?\s+|to\s+|about\s+|say\s*:\s*"
         r"|,\s*(?:say|saying|that\s+says)(?:\s*:)?\s+)")
_COMMAND = re.compile(_PREFIX + r"(?:create|make|add|save|write|put|pin)\s+(?:me\s+)?(?:a\s+|this\s+)?" + _KIND + r"(?:post[- ]?it(?:\s+note)?\b|(?:board\s+note)|note\s+(?:on|to)\s+(?:the\s+|my\s+)?board)(?:\s+(?:on|to)\s+(?:the\s+|my\s+)?board)?" + _ASIDE + r"\b\s*(?P<says>" + _SAYS + r")?\s*(?P<text>.*)", re.I | re.S)
# German (A4-1, the same parts): "ein Post-it" as well as "einen"/"eine", the Board as "ans Board", "aufs Board" or "auf/an
# dem/das/den Board", the verbs one hangs, sticks or pins a note with, and "mit (dem Text)", "sag(e)", "auf dem steht" or
# "das/der sagt" introducing the words ("sag(e)" only after a comma or with a colon, as "say"). A "Notiz" or "Zettel" is a Board post-it only with the Board named; "mach eine
# Notiz: …" alone is not one. "Kannst du …" puts the verb last: "Kannst du ein Post-it ans Board hängen, mit dem Text X?"
_DE_ART = r"(?:(?:ein|eine|einen)\s+)?"
_DE_BOARD = r"(?:(?:auf|an)\s+(?:dem|das|den)\s+board|ans\s+board|aufs\s+board)"
_DE_NOUN = r"(?:post[- ]?it(?:\s+" + _DE_BOARD + r")?|(?:notiz|zettel)\s+" + _DE_BOARD + r")"
_DE_ASIDE = r"(?:\s*,\s*(?:nur\s+)?(?:ein\s+)?post[- ]?it)?"
_DE_SAYS = (r"(?::|[-–—]|,?\s*(?:mit\s+dem\s+text|mit|auf\s+dem\s+steht|das\s+sagt|der\s+sagt)(?:\s*:)?\s+"
            r"|sage?\s*:\s*|,\s*sage?(?:\s*:)?\s+)")
_GERMAN = re.compile(r"(?:bitte\s+)?(?:erstelle|schreib|schreibe|mach|mache|häng|hänge|kleb|klebe|pinn|pinne)\s+(?:mir\s+)?" + _DE_ART + _DE_NOUN + _DE_ASIDE + r"\b\s*(?P<says>" + _DE_SAYS + r")?\s*(?P<text>.*)", re.I | re.S)
_GERMAN_ASKED = re.compile(r"(?:kannst|könntest)\s+du\s+(?:bitte\s+)?(?:mir\s+)?" + _DE_ART + _DE_NOUN + r"\s+(?:machen|erstellen|schreiben|hängen|kleben|pinnen)" + _DE_ASIDE + r"\b\s*(?P<says>" + _DE_SAYS + r")?\s*(?P<text>.*)", re.I | re.S)
# A request asked as a question ("Can you … say test?", "Kannst du …?"): its question mark closes the question, never the
# words, and is not saved (A4-1). A quoted payload is literal and keeps it.
_ASKED = re.compile(r"(?:(?:okay|ok)(?:\s*,\s+|\s+))?(?:(?:can|could|would|will)\s+you|kannst\s+du|könntest\s+du)\b", re.I)


def _command(text):
    """The first board-command grammar that matches the whole text: English, German, German asked with the verb last."""
    return _COMMAND.fullmatch(text) or _GERMAN.fullmatch(text) or _GERMAN_ASKED.fullmatch(text)


# What the "no separator, take the rest" rule must not save (review S-3): words that start with a mark ("…ans Board,
# nicht jetzt" leaves ", nicht jetzt"), or a separator word left alone ("Schreib ein Post-it mit"). Asked for instead.
# Only without an explicit separator (verification S-5): after a colon or a dash ("Add a post-it: .env needs rotating")
# the words are his as typed, a leading mark included.
_NOT_WORDS = frozenset({"mit", "sag", "sage", "say", "saying", "that", "to", "about"})


def _his_postit(payload):
    """``payload``, or '' (ask for the words) when it starts with a mark or is only a separator word (_NOT_WORDS). String
    work only (review M-3: its regular expression was quadratic on a long run of spaces)."""
    words = (payload or "").lstrip()
    if words[:1] in (",", ";", ".", "!", "?") or words.rstrip(" \t\n.!?").casefold() in _NOT_WORDS:
        return ''
    return payload


def _explicit(match):
    """Whether the words were introduced by a colon or a dash (the named ``says`` group), not by a pause or nothing."""
    says = match.groupdict().get("says")
    return bool(says) and re.search(r"[:\-–—]", says) is not None


def _unasked(request, words):
    """``words`` without the closing question mark when the request itself is a question (_ASKED)."""
    if not words or not _ASKED.match(request):
        return words
    return without_question_mark(words, polite=("please", "bitte"))


# One explicit text-first directive may sit among conversational questions.
# Extract only its stated literal payload, never a previous message/referent.
#
# Review M-4: each lazy group here starts after the whitespace before it, all of it, never given back ("\s++"), and
# starts and ends with a non-space ("\S(?:.*?\S)?" for ".+?"); so across a long run of spaces each step is one character
# test, not a rescan of the run ("put" and 1,990 spaces took 165 ms, the old tail seconds). The first match found is the
# same: the old "\s+" before a group was greedy, so a match whose group starts with whitespace was found only when none
# starting at the next word existed, and a lazy group never ends in whitespace a shorter one could leave to what follows.
# That leaves one old reading the main pattern cannot have: a group that is a single whitespace character, inside a run
# of three or more ("put   on the board"). The _WS twin finds exactly that case, in one pass, and is tried second, as
# the old pattern reached it last; the group's value is whitespace either way, and only its emptiness is used.
_V = r"(?:write|put|pin|add)"
_ON_A = r"(?:on|onto|to)\s+(?:a\s+|the\s+|my\s+)?(?:post[- ]?it(?:\s+note)?|board)\b" + _TAIL
_TEXT_FIRST = re.compile(_PREFIX + _V + r"\s++(?P<text>\S(?:.*?\S)?)\s+" + _ON_A, re.I | re.S)
_TEXT_FIRST_WS = re.compile(_PREFIX + _V + r"\s(?=(?P<text>\s)\s)\s++" + _ON_A, re.I | re.S)


def _text_first(clause):
    """The text-first directive in one clause (_TEXT_FIRST, or its single-whitespace twin), or None."""
    return _TEXT_FIRST.fullmatch(clause) or _TEXT_FIRST_WS.fullmatch(clause)


# "Put these three on the Board as one post-it: a; b; c" / "Put the following on the Board: a, b":
# a pointer that announces the words, then the words after a colon or dash. The pointer and the words follow M-4's rule
# above; a pointer that is one whitespace character (a run of three or more, its last non-newline character in the old
# reading) is _POINTED_WS's. The words never need that case: the text is stripped, so a non-space reading always exists.
_POINTED_REST = (r"(?:on|onto|to)\s+(?:a\s+|the\s+|my\s+)?(?:board|post[- ]?it(?:\s+note)?)"
                 r"(?:\s+as\s+(?:one|a\s+single|a)\s+(?:post[- ]?it(?:\s+note)?|card|note))?"
                 r"(?:\s*(?::|\s[-–—])\s*+(?P<text>\S(?:.*?\S)?))?" + _TAIL)
_POINTED = re.compile(_PREFIX + _V + r"\s++(?P<pointer>[^:\n–—\s](?:[^:\n–—]*?[^:\n–—\s])?)\s+" + _POINTED_REST, re.I | re.S)
_POINTED_WS = re.compile(_PREFIX + _V + r"\s(?=\s*?(?P<pointer>[^\S\n])\s)\s++" + _POINTED_REST, re.I | re.S)


def _pointed(text):
    """The pointed form (_POINTED, or its single-whitespace pointer twin), or None."""
    return _POINTED.fullmatch(text) or _POINTED_WS.fullmatch(text)


_ANNOUNCING = re.compile(r"(?:all\s+)?(?:these|those|them|this|it|the\s+following|the\s+list|my\s+list|the\s+items?|the\s+points?)(?:\s+(?:\d+|two|three|four|five|six|seven|eight|nine|ten))?(?:\s+(?:items|things|points|tasks|notes|bullets|lines|ones|reminders))?", re.I)
_OUTSIDE_REFUSAL = re.compile(r"\b(?:not|do\s+not|don['’]t|never|cancel|revoke|no\s+need|not\s+now|if|unless|whether|maybe|perhaps|example|hypothetical|suppose|imagine|yesterday|previously|said|says|asked|told|wrote|reported|quoted|document|transcript|translate|summari[sz]e|analy[sz]e)\b", re.I)
# Code, block quotes and indented blocks are material to read, never a request.
_CODE_OR_BLOCK = re.compile(r'`|(?m:^[ \t]*(?:>|\t)|^ {4})')
# A quotation mark, but not an apostrophe inside or at the end of a word (don't, James').
_QUOTE = re.compile(r'["“”‘]|(?<!\w)[\'’]|[\'’](?![\w\s.,!?;:]|$)')
_QUOTE_PAIRS = {'"': '"', '“': '”', "'": "'", '‘': '’', '’': '’'}
# A balanced quotation: its inside is the owner's words, so sentence ends, line breaks, code
# marks and indentation inside it are content, never request structure.
# A single quotation mark followed by a letter is an apostrophe (Robert’s, don't), not its end.
_QUOTED_SPAN = re.compile(r'"[^"]*"|“[^”]*”|‘(?:[^’]|’(?=\w))*’(?!\w)|(?<!\w)\'(?:[^\']|\'(?=\w))*\'(?!\w)', re.S)
_WRAPPED = re.compile(r'(["“‘’\'])(.*)(["”’\'])\s*[.!?]*', re.S)

# A payload that only points at something ("this", "your last answer", "the summary above")
# or names a generic thing ("a reminder") is not the note's text. Ask for the words instead
# of saving the pointer. Quoted words are always literal. The check is shared with the note
# grammar (domains/cos/owner_words.py, review S-1), unchanged.
from domains.cos.owner_words import points_only as _points_only, without_question_mark  # noqa: E402


def _mask_quotes(text):
    """``text`` with the inside of each balanced quotation blanked out, at the same length."""
    chars = list(text)
    for span in _QUOTED_SPAN.finditer(text):
        chars[span.start() + 1:span.end() - 1] = 'x' * (span.end() - span.start() - 2)
    return ''.join(chars)


def _clauses(text):
    """Sentences and lines of a message, never split inside a quotation."""
    masked, parts, start = _mask_quotes(text), [], 0
    for sep in re.finditer(r'(?<=[.!?])\s+|[\r\n]+', masked):
        parts.append(text[start:sep.start()])
        start = sep.end()
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _unquote(text):
    """(words, True) when one quotation wraps the whole payload (sentence punctuation may follow
    the closing mark); otherwise (text, False)."""
    wrapped = _WRAPPED.fullmatch(text)
    if not wrapped or _QUOTE_PAIRS.get(wrapped[1]) != wrapped[3] or not wrapped[2].strip():
        return text, False
    inner = wrapped[2]
    if wrapped[1] in "'‘’" and re.search(r"(?<!\w)['’]|['’](?!\w)", inner):
        return text, False          # 'a' and 'b': two quotations, not one
    if wrapped[1] not in "'‘’" and (wrapped[3] in inner or wrapped[1] in inner):
        return text, False          # "a" and "b"
    return inner.strip(), True


def _payload(match):
    """(words, quoted) for one command clause, or None when quotation marks frame the command
    itself (reported or quoted speech is data). Quotes around the payload are removed; an
    opening quotation mark with no closing mark anywhere asks for the words."""
    start, end = match.span('text')
    if _QUOTE.search(match.string[:start] + match.string[end:]):
        return None
    words, quoted = _unquote(match.group('text').strip())
    if not quoted and words[:1] in ('"', '“', '‘') and _QUOTE_PAIRS[words[0]] not in words[1:]:
        return '', True
    return words, quoted


def _literal(payload):
    """'' for a pointer or generic placeholder (ask for the words), else the payload unchanged."""
    if payload is None:
        return None
    if _points_only(payload):
        return ''
    return payload


def _text_first_request(text):
    # Code or block-quoted material, and negative or reported framing, is DATA, not authorization.
    if _CODE_OR_BLOCK.search(_mask_quotes(text)):return None
    clauses=_clauses(text)
    payloads=[];outside=[];literal=[]
    for clause in clauses:
        match=_text_first(clause)or _command(clause)
        found=_payload(match)if match else None
        if found is None:outside.append(clause)
        else:payloads.append(found[0]);literal.append(found[1])
    if not any(_text_first(clause)and _payload(_text_first(clause))is not None for clause in clauses):return None
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
    match = _command(stripped)
    if match:
        if _CODE_OR_BLOCK.search(_mask_quotes(text)):
            return None
        found = _payload(match)
        if found is None:
            return None
        if found[1]:
            return found[0]
        words = _unasked(stripped, found[0])
        return _literal(words if _explicit(match) else _his_postit(words))
    pointed = _pointed(stripped)
    announced = bool(pointed) and _ANNOUNCING.fullmatch(pointed.group('pointer').strip()) is not None
    if pointed and (pointed.group('text') is not None
                    or (announced and re.search(r'\bas\s+(?:one|a\s+single|a)\s', stripped, re.I))):
        if _CODE_OR_BLOCK.search(_mask_quotes(text)):
            return None
        if not announced or pointed.group('text') is None:
            return ''  # no words after the pointer, or two different payloads: ask once
        found = _payload(pointed)
        if found is None:
            return None
        return found[0] if found[1] else _literal(found[0])
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
