"""Owner notes and reminders, shared by typed and canonical spoken MC turns.

Parsed only from the owner's own words (the kept note text or the voice
transcript), never from context-enriched text or model arguments. The note
grammar is the Chief of Staff's own (``parse_explicit_note``), so a note command
cannot slip past here and be saved with context attached. A note or reminder
exists only when the Chief of Staff platform returns its receipt, and the reply
then names it; nothing is claimed, inferred or retried from prose.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import uuid
from zoneinfo import ZoneInfo

from .stores import GUILD_PLATFORM, FloorStoreUnavailable

CHICAGO = ZoneInfo('America/Chicago')
REMINDER_MAX = 500
HORIZON = timedelta(days=366)
WITH_CONTEXT = ('Send a note or reminder on its own, without attached files or topic context. '
                'Nothing was saved or scheduled.')
UNAVAILABLE = 'Notes and reminders need the Chief of Staff platform, which is not connected here. Nothing was saved or scheduled.'
# Reminders are delivered by Telegram to the owner's phone. Off unless this mount switches them on, so no test or
# Dev portal can send one by default. Off means: recognised, refused, and nothing sent to the Chief of Staff.
REMINDERS_FLAG = 'MINIMOI_MC_OWNER_REMINDERS'
REMINDERS_OFF = ('Reminders are not switched on here, so nothing was scheduled. '
                 'Nothing was sent to the Chief of Staff.')

# "okay, " / "okay ": a comma then spaces, or spaces; the same words "\s*,?\s+" read, without its quadratic backtracking on
# a long run of spaces (review M-3, A4 fix round).
_PREFIX = r'(?:(?:okay|ok|hey|hi)(?:\s*,\s+|\s+))?(?:please\s+|(?:can|could|would)\s+you\s+(?:please\s+)?)?'
_COUNT = r'(?:\d{1,4}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty[- ]five|half\s+an)'
_W_IN = r'in\s+' + _COUNT + r'\s+(?:minutes?|mins?|hours?|hrs?)'
_W_CLOCK = r'(?:noon|midnight|\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)?)'
_W_DAY = (r'(?:today|tonight|this\s+(?:morning|afternoon|evening)|tomorrow(?:\s+(?:morning|afternoon|evening|night))?'
          r'|(?:on\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day)')
_WHEN = rf'(?:{_W_IN}|(?:{_W_DAY}\s+)?at\s+{_W_CLOCK}|at\s+{_W_CLOCK}\s+{_W_DAY}|{_W_DAY})'
_END = r'[\s.!?]*'
# "remind me <when> to X", "remind me to X <when>", "set a reminder [for <when>] to X [<when>]".
_REMIND_WHEN_FIRST = re.compile(rf'{_PREFIX}remind\s+me\s+(?P<when>{_WHEN})\s*,?\s+(?P<link>to|that|about)\s+(?P<text>.+?){_END}', re.I | re.S)
_REMIND = re.compile(rf'{_PREFIX}remind\s+me\s+(?P<link>to|that|about)\s+(?P<text>.+?)(?:\s*,?\s+(?P<when>{_WHEN}))?{_END}', re.I | re.S)
_SET = re.compile(rf'{_PREFIX}set\s+(?:me\s+)?(?:a\s+)?reminder\s+(?:(?:for\s+)?(?P<when>{_WHEN})\s*,?\s+)?'
                  rf'(?P<link>to|that|about|:|[-–—])\s*(?P<text>.+?)(?:\s*,?\s+(?P<when2>{_WHEN}))?{_END}', re.I | re.S)
_SLASH_REMIND = re.compile(r'/remind(?:\s+|$)', re.I)
_CANONICAL_DUE = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)\s+(.+)', re.S)

_WORDS = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8,
          'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'fifteen': 15, 'twenty': 20, 'thirty': 30}
_PART = {'morning': 9, 'afternoon': 14, 'evening': 18, 'night': 20, 'tonight': 20}
_WEEKDAYS = ('mon', 'tues', 'wednes', 'thurs', 'fri', 'satur', 'sun')


@dataclass(frozen=True)
class Action:
    kind: str                       # 'note' or 'reminder'
    text: str | None = None         # the owner's literal words to keep
    due: datetime | None = None     # UTC, reminders only
    refusal: str | None = None      # a recognized command that cannot be done as asked


def _clock(spec, dated, part):
    """(hour, minute, ambiguous, next_day) from a spoken clock.

    ambiguous: no am/pm and no other day, so both am and pm are tried and the
    earliest future one wins ("today at 7" at 10:00 is 19:00). next_day: a
    late-night clock ("tonight at 12", "tonight at 1") falls after midnight."""
    spec = spec.casefold().strip()
    late = part in ('evening', 'night', 'tonight')
    if spec == 'noon':return 12, 0, False, False
    if spec == 'midnight':return 0, 0, False, late
    match = re.fullmatch(r'(\d{1,2})(?:[:.](\d{2}))?\s*(?:(a)\.?\s?m\.?|(p)\.?\s?m\.?)?', spec)
    if match is None:raise ValueError
    hour, minute = int(match[1]), int(match[2] or 0)
    if minute > 59 or hour > 23:raise ValueError
    if match[3] or match[4]:
        if not 1 <= hour <= 12:raise ValueError
        hour = (hour % 12) + (12 if match[4] else 0)
        return hour, minute, False, late and hour < 6
    if hour >= 13 or hour == 0 or spec.startswith('0'):
        return hour, minute, False, late and hour < 6
    if late:
        if hour == 12:return 0, minute, False, True
        if hour <= 5:return hour, minute, False, True
        return hour + 12, minute, False, False
    if part == 'morning':return hour, minute, False, False
    if part == 'afternoon':return (hour + 12 if hour < 12 else hour), minute, False, False
    if dated:
        return (hour + 12 if 1 <= hour <= 6 else hour), minute, False, False
    return hour, minute, True, False


def resolve(when, now):
    """UTC due time for one spoken WHEN phrase, or raise ValueError(sentence) to refuse."""
    text = ' '.join(when.casefold().split())
    local_now = now.astimezone(CHICAGO)
    match = re.fullmatch(r'in\s+(.+?)\s+(minutes?|mins?|hours?|hrs?)', text)
    if match:
        count = match[1]
        if count == 'half an':minutes = 30
        else:
            value = int(count) if count.isdigit() else _WORDS.get(count.replace('-', ' '), 45 if 'forty' in count else 0)
            minutes = value * (60 if match[2].startswith('h') else 1)
        if not 1 <= minutes <= 7 * 24 * 60:raise ValueError('Give a delay between one minute and seven days.')
        return (now + timedelta(minutes=minutes)).astimezone(timezone.utc).replace(microsecond=0)
    clock = re.search(r'\bat\s+(.+?)(?=\s+(?:today|tonight|this|tomorrow|on|mon|tues|wednes|thurs|fri|satur|sun)|$)', text)
    day = text[:clock.start()].strip() if clock else text
    if clock and not day:
        day = text[clock.end():].strip()
    part = None
    for name in ('morning', 'afternoon', 'evening', 'night', 'tonight'):
        if re.search(rf'\b{name}\b', day):part = name
    if day.startswith('today') or day.startswith('this') or day == 'tonight' or not day:
        date = local_now.date()
    elif day.startswith('tomorrow'):
        date = local_now.date() + timedelta(days=1)
    else:
        name = day.removeprefix('on ').strip()
        index = next((i for i, w in enumerate(_WEEKDAYS) if name == w + 'day'), None)
        if index is None:raise ValueError('Say when, for example "at 5 pm" or "in 20 minutes".')
        date = local_now.date() + timedelta(days=(index - local_now.weekday()) % 7)
    if clock is None:
        if part is None:raise ValueError('Say what time, for example "tomorrow at 9 am".')
        hour, minute, ambiguous, next_day = _PART[part], 0, False, False
    else:
        dated = bool(day) and not day.startswith(('today', 'this', 'tonight'))
        try:hour, minute, ambiguous, next_day = _clock(clock[1], dated, part)
        except ValueError:raise ValueError('That time is not one I can read. Say it like "5:30 pm".') from None
    if next_day:
        date = date + timedelta(days=1)
    def at(d, h):
        candidate = datetime(d.year, d.month, d.day, h, minute, tzinfo=CHICAGO)
        if candidate.astimezone(timezone.utc).astimezone(CHICAGO).replace(tzinfo=None) != candidate.replace(tzinfo=None):
            raise ValueError('That local time does not exist because the clocks change. Choose another time.')
        return candidate
    def options(d):
        if ambiguous and hour == 12:              # "at 12" late in the day is the coming midnight
            return [at(d, 12), at(d + timedelta(days=1), 0)]
        return [at(d, hour)] + ([at(d, hour + 12)] if ambiguous and hour < 12 else [])
    future = [c for c in options(date) if c > local_now]
    if not future:
        if day and not day.startswith(('on ',)) and day.split()[0] in ('today', 'tonight', 'this'):
            raise ValueError('That time today has already passed.')
        if day and day.split()[0] not in ('today', 'tonight', 'this', 'tomorrow') and date == local_now.date():
            date = date + timedelta(days=7)
        else:
            date = date + timedelta(days=1)
        future = options(date)
    due = min(future).astimezone(timezone.utc)
    if due - now > HORIZON:raise ValueError('Reminders can be set up to a year ahead.')
    return due


def _note(text):
    from domains.cos.confer_service import InvalidConferTurn, parse_explicit_note
    try:
        payload = parse_explicit_note(text)
    except InvalidConferTurn as exc:
        return Action('note', refusal=str(exc) + ' Nothing was saved.')
    return None if payload is None else Action('note', text=payload)


def parse(text, now=None):
    """Return an Action for a direct owner note or reminder command, else None. Never raises."""
    if not isinstance(text, str) or not text.strip():
        return None
    value = text.strip()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    note = _note(value)                 # any length: an over-long note is refused, never passed on
    if note is not None:
        return note
    if len(value) > 2000:
        return None
    if _SLASH_REMIND.match(value):
        canonical = _CANONICAL_DUE.fullmatch(value[_SLASH_REMIND.match(value).end():].strip())
        try:
            due = datetime.fromisoformat(canonical[1].replace('Z', '+00:00')) if canonical else None
        except ValueError:
            due = None
        if due is None:
            return Action('reminder', refusal='Use /remind YYYY-MM-DDTHH:MMZ then the text, or say "remind me at 5 pm to …". Nothing was scheduled.')
        return _reminder(canonical[2], due, now)
    for pattern in (_REMIND_WHEN_FIRST, _REMIND, _SET):
        match = pattern.fullmatch(value)
        if match is None:
            continue
        groups = match.groupdict()
        when = groups.get('when') or groups.get('when2')
        trailing = groups.get('when2') if pattern is _SET else (groups.get('when') if pattern is _REMIND else None)
        words = match['text']
        if pattern is _SET and groups.get('when') and groups.get('when2'):
            words = words + ' ' + groups['when2']    # "for 9 am to call Anna at noon": the second time is content
        if pattern is _REMIND_WHEN_FIRST and not _timed(when):
            tail = re.search(rf'\s*,?\s+(at\s+{_W_CLOCK}){_END}$', words, re.I)
            if tail:                               # "remind me tomorrow to call Anna at 5"
                when, words = when + ' ' + tail[1], words[:tail.start()]
        if trailing and match['link'].casefold() in ('that', 'about'):
            words = words + ' ' + trailing  # "remind me that the meeting is at 3 pm": the time is also the content
        if match['link'].casefold() in ('that', 'about') and not _timed(when):
            continue                        # "remind me about the plan / that we agreed on Tuesday" asks to recall
        if not when:
            return Action('reminder', refusal='Say the whole reminder with its time in one go, for example "remind me at 5 pm to call Anna". Nothing was scheduled.')
        try:
            due = resolve(when, now)
        except ValueError as exc:
            return Action('reminder', refusal=f'{exc} Nothing was scheduled.')
        return _reminder(words, due, now)
    return None


def _timed(when):
    """A WHEN with a clock or a delay ("at …", "in …"). A day or part of the day alone is not:
    "remind me about what we decided this morning" asks to recall."""
    if not when:
        return False
    value = ' '.join(when.casefold().split())
    return value.startswith('in ') or ' at ' in ' ' + value


def _reminder(words, due, now):
    clean = ' '.join(str(words).split()).strip(' ,;:')
    if not clean:
        return Action('reminder', refusal='Say what to remind you about. Nothing was scheduled.')
    if len(clean) > REMINDER_MAX:
        return Action('reminder', refusal=f'Keep a reminder to {REMINDER_MAX} characters. Nothing was scheduled.')
    if due <= now or due - now > HORIZON:
        return Action('reminder', refusal='A reminder needs a time in the future, within a year. Nothing was scheduled.')
    return Action('reminder', text=clean, due=due.replace(microsecond=0))


def local_label(due):
    local = due.astimezone(CHICAGO)
    return local.strftime('%a %-d %b, %-I:%M %p') + ' Chicago time'


def _verified(kind, receipt, operation_id, action):
    op = receipt.get('operation') if receipt.get('state') == 'ok' else None
    if not isinstance(op, dict) or op.get('operation_id') != operation_id or op.get('status') not in ('saved', 'deduplicated'):
        return None
    if kind == 'note':
        return op if op.get('type') == 'note_save' and op.get('storage') == 'cos_platform_memory' else None
    due = action.due.strftime('%Y-%m-%dT%H:%M:%SZ')
    if (op.get('type') == 'reminder_set' and op.get('storage') == 'cos_reminders' and op.get('delivery') == 'telegram'
            and op.get('due_utc') == due and isinstance(op.get('reminder_id'), str)
            and re.fullmatch(r'rem-[0-9a-f]{12}', op['reminder_id'])):
        return op
    return None


def reminders_enabled() -> bool:
    """This mount's switch (feature_environ: the request's mount, or the voice worker's bound config). No
    bound configuration, or any doubt, is off."""
    try:
        from . import feature_enabled
        return feature_enabled(REMINDERS_FLAG)
    except Exception:
        return False


def cos_for(services):
    """The Chief of Staff's operation client for ``execute`` (A4-3): notes and reminders are hers whichever runtime Master
    Craftsman chats through. MC on CoS: that backend, exactly as before. MC on another runtime (OpenClaw, on Dev and in
    his own production container): the services' own CoS client, built from COS_BACKEND (mc/cos.py
    operations_from_env), or None without one. execute then answers UNAVAILABLE as it always has; nothing else changes.
    Typed and spoken notes both come here (api._owner_action_command), so the same /note operation, receipt and
    idempotency apply on either runtime."""
    mc = getattr(services, 'mc', None)
    return mc if getattr(mc, 'kind', None) == 'cos' else getattr(services, 'cos_operations', None)


def execute(text, *, cos, conversation, principal, turn_key, now=None):
    """Only the caller's authenticated, kept owner turn may enter. Returns None for ordinary chat.

    A real Chief of Staff receipt is mandatory for success. The same turn retried
    reuses one operation id, so CoS returns "already saved" instead of a second copy.
    The typed and the spoken path both come here, so the reminders switch is checked once, for both.
    """
    first = parse(text, now)
    if first is None:
        return None
    if first.kind == 'reminder' and not reminders_enabled():
        # Not an outcome to pause on: nothing was attempted. No operation id, no Chief of Staff call, no model.
        return {'reply': REMINDERS_OFF, 'operation': {'type': 'reminder_set', 'saved': False, 'reason': 'reminders_off'}}
    # Same redaction as other kept owner text before it leaves for another store.
    from utils.credential_scrub import scrub as credentials
    from .payment_scrub import scrub as payments
    action = parse(payments(credentials(text)[0]), now)
    if action is None or action.kind != first.kind:
        return {'reply': 'That note or reminder could not be read after redaction. Nothing was saved or scheduled.',
                'operation': {'type': 'cos_note' if first.kind == 'note' else 'reminder_set', 'saved': False}}
    if not isinstance(principal, str) or not principal or not isinstance(turn_key, str) or not turn_key:
        raise ValueError('Authenticated owner turn required')
    kind = action.kind
    op_type = 'cos_note' if kind == 'note' else 'reminder_set'
    if action.refusal is not None:
        return {'reply': action.refusal, 'operation': {'type': op_type, 'saved': False}}
    scope = getattr(conversation, 'floor', None)
    if not isinstance(scope, str) or not scope:
        raise ValueError('Owned conversation floor required')
    if getattr(cos, 'kind', None) != 'cos' or not callable(getattr(cos, 'operation', None)):
        return {'reply': UNAVAILABLE, 'operation': {'type': op_type, 'saved': False}}
    digest = hashlib.sha256(('owner-action\0' + principal + '\0' + scope + '\0' + turn_key).encode()).hexdigest()
    operation_id = str(uuid.UUID(digest[:32]))
    if kind == 'note':
        canonical = '/note ' + action.text
    else:
        canonical = '/remind ' + action.due.strftime('%Y-%m-%dT%H:%M:%SZ') + ' ' + action.text
    receipt = cos.operation(canonical, conversation_id=scope, request_id=operation_id)
    state = receipt.get('state') if isinstance(receipt, dict) else None
    op = _verified(kind, receipt, operation_id, action) if state == 'ok' else None
    what = 'note' if kind == 'note' else 'reminder'
    if op is None:
        if state == 'refused':
            reply = f"{receipt.get('reply')} Nothing was {'saved' if kind == 'note' else 'scheduled'}."
        elif state == 'uncertain':
            reply = f'The Chief of Staff did not confirm the {what} in time, so its outcome is unknown. Do not repeat it yet; I am not claiming it was {"saved" if kind == "note" else "set"}.'
        else:
            reply = f'The Chief of Staff did not confirm the {what}. Nothing is known to be {"saved" if kind == "note" else "scheduled"}.'
        return {'reply': reply, 'operation': {'type': op_type, 'saved': False, 'state': state or 'failed'}}
    # The receipt text is the same on a retry: the kept conversation receipt is
    # idempotent by request id and refuses a different text under the same id.
    repeated = op['status'] == 'deduplicated'
    if kind == 'note':
        reply = f'Saved to Chief of Staff notes: {action.text}'
        operation = {'type': 'cos_note', 'saved': True, 'operation_id': operation_id, 'repeated': repeated}
    else:
        reply = (f"Reminder set for {local_label(action.due)}: {action.text}. "
                 f"It will arrive by Telegram. Receipt {op['reminder_id']}.")
        operation = {'type': 'reminder_set', 'saved': True, 'operation_id': operation_id, 'repeated': repeated,
                     'reminder_id': op['reminder_id'], 'due_utc': op['due_utc'], 'delivery': 'telegram'}
    try:
        kept = conversation.add_note('owner-action-' + digest + '-receipt', reply, GUILD_PLATFORM,
                                     area='Notes' if kind == 'note' else 'Reminders', page='mc')
    except FloorStoreUnavailable:
        kept = None
    saved = kept is not None and kept.outcome == 'kept'
    return {'reply': reply, 'reply_note': kept.value if saved else None, 'operation': operation, 'history_saved': saved}
