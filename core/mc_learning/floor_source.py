"""Source adapter for kept owner notes, including directions with no reply/task.

Uses ConversationStore/FloorStores, never the capped answered-turn log. Policy
is an injected trusted server check of existing capture approval and current
scope. No model text can choose a floor, account, environment or filesystem path.
The SQL read filters owner/record mode and bounds UTF-8 bytes before returning
text. Only the exact already scrubbed stored text is eligible for a statement.
Missing, unsafe or changed sources are refused; nothing is rewritten here.
"""
from datetime import datetime, timezone
import re

from core.memory_shelf.render import scrub_text
from core.minimoi_actions.context import ActionContext, ActionContractError, _aware, _identifier
from minimoi_portal.guild_ui.conversations import ConversationStore
from minimoi_portal.guild_ui.stores import FloorStores
from utils.payment_scrub import REMOVED as PAYMENT_REMOVED
from utils.credential_scrub import REMOVED as CREDENTIAL_REMOVED
from .statements import SourceDescriptor, MAX_TURN_BYTES
from .statement_journal import _date, _encode, _hash, _scope

_MARKERS = re.compile('|'.join(re.escape(x) for x in (PAYMENT_REMOVED,CREDENTIAL_REMOVED)))


class FloorStatementSource:
    def __init__(self, *, conversations, floor_for, environment, instance_id, capture_scope, clock=None):
        if (type(conversations) is not ConversationStore or environment not in ('dev','prod')
                or not _identifier(instance_id) or not callable(floor_for) or not callable(capture_scope)):
            raise ActionContractError('statement_source_adapter_invalid')
        self.conversations, self.floor_for = conversations, floor_for
        self.environment, self.instance = environment, instance_id
        self.capture_scope = capture_scope
        self.clock = clock or (lambda:datetime.now(timezone.utc))

    def _load(self, context, turn_id):
        now = self.clock()
        if (type(context) is not ActionContext or not _aware(now) or context.has_expired(now)
                or context.environment != self.environment or context.privacy_mode not in ('on_record','recorded')
                or not _identifier(turn_id)):
            raise ActionContractError('statement_source_refused')
        try:
            conv = self.conversations.get(context.conversation_id,context.principal_id,migrate_legacy=False)
        except Exception:
            raise ActionContractError('statement_source_unavailable') from None
        if (conv.get('principal') != context.principal_id or conv.get('id') != context.conversation_id
                or conv.get('record_mode','on_record') != 'on_record' or not _identifier(conv.get('notes_floor'))):
            raise ActionContractError('statement_source_refused')
        # Site area (Build/Operate/etc.) is NOT a Shelf/mandate scope. Only the
        # trusted capture policy determines that mapping, before any note read.
        scope = self.capture_scope(context,conv)
        if not _scope(scope):
            raise ActionContractError('statement_capture_refused')
        try:
            floor = self.floor_for(conv['notes_floor'])
            if not isinstance(floor,FloorStores) or floor.floor != conv['notes_floor']:
                raise ActionContractError('statement_source_floor_mismatch')
            note = floor.get_owner_note(turn_id,context.principal_id,max_bytes=MAX_TURN_BYTES)
        except ActionContractError:
            raise
        except Exception:
            raise ActionContractError('statement_source_unavailable') from None
        if (type(note) is not dict or note.get('who') != context.principal_id or note.get('author_kind') != 'owner'
                or note.get('request_id') != turn_id or note.get('record_mode') != 'on_record'
                or type(note.get('id')) is not int or note['id'] < 1 or type(note.get('text')) is not str):
            raise ActionContractError('statement_source_unavailable')
        try:
            raw = note['text'].encode('utf-8')
            if len(raw) > MAX_TURN_BYTES or scrub_text(note['text']) != (note['text'],False):
                raise ActionContractError('statement_source_not_scrubbed_or_bounded')
            occurred = _date(note['created_at'])
            if occurred > now:
                raise ValueError()
        except (ValueError,TypeError,UnicodeError,KeyError):
            raise ActionContractError('statement_source_unavailable') from None
        # Counts are visible redaction groups in this stored edition, not an
        # invented count of original secrets that a scrubber may have coalesced.
        markers = tuple((m.start(),m.end(),1) for m in _MARKERS.finditer(note['text']))
        native = 'floor-note:'+_hash(_encode([self.instance,conv['notes_floor']]))+':'+str(note['id'])
        descriptor = SourceDescriptor(context.principal_id,self.environment,context.conversation_id,
            turn_id,native,scope,context.principal_id,'owner',_hash(raw),occurred,
            True,True,'on_record',markers)
        return descriptor,raw

    def describe_current(self, context, turn_id):
        """Trusted admission can include this exact source pointer in context."""
        return self._load(context,turn_id)[0]

    def describe_source(self, context, turn_id, edition_hash):
        descriptor,_ = self._load(context,turn_id)
        if descriptor.edition_hash != edition_hash:
            raise ActionContractError('statement_source_changed')
        return descriptor

    def read_source(self, context, descriptor, limit):
        if type(descriptor) is not SourceDescriptor or type(limit) is not int or not 0 < limit <= MAX_TURN_BYTES:
            raise ActionContractError('statement_source_unavailable')
        fresh,raw = self._load(context,descriptor.turn_id)
        if fresh != descriptor or len(raw) > limit:
            raise ActionContractError('statement_source_changed')
        return raw
