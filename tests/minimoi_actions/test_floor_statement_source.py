from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.mc_learning.floor_source import FloorStatementSource
from core.mc_learning.statements import StatementRequest, prepare_statement, MAX_TURN_BYTES
from core.mc_learning.statement_journal import StatementJournal
from core.mc_learning.statement_shelf import normalize_statement
from core.memory_shelf.shelf import Shelf
from core.minimoi_actions.context import ActionContext, ActionContractError
from core.vault_t1.reader import Vault, parse_edition
from minimoi_portal.guild_ui.conversations import ConversationStore, ConversationNotFound, LEGACY_ID
from minimoi_portal.guild_ui.stores import Author, MASTER_CRAFTSMAN
from tests.guild.shop_floor.floor_db_helpers import SqliteFloor
from test_statement_journal import NOW


class FloorSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conversations = ConversationStore(str(self.root/'guild'),base_floor='guild')
        self.conv,_ = self.conversations.create('owner',key='source-test-001')
        self.db = SqliteFloor(self.root/'floor')
        connect = self.db.connect
        def with_octets(*args):
            conn = connect(*args)
            conn.create_function('OCTET_LENGTH',1,lambda text:len(text.encode('utf-8')))
            return conn
        self.db.connect = with_octets
        self.floor = self.db.store(self.conv['notes_floor'])
        self.context = ActionContext('owner','mc','dev',self.conv['id'],'turn','dispatch',1,
            NOW+timedelta(minutes=1),'policy','role','recorded','budget')
        self.scope = 'robert'
        self.adapter = FloorStatementSource(conversations=self.conversations,floor_for=self.db.store,
            environment='dev',instance_id='dev-synthetic',capture_scope=lambda *_:self.scope,clock=lambda:NOW)
        self.text = 'Gates, not dates. 🙂\r\nThis is a passing direction.\n'
        self.put('owner-note',self.text)

    def put(self, key, text, author=None, floor=None):
        with patch('minimoi_portal.guild_ui.stores.utc_now',return_value=NOW-timedelta(minutes=1)):
            return (floor or self.floor).add_note(key,text,author or Author('owner','owner','Owner'))

    def prepare(self, key='owner-note'):
        descriptor = self.adapter.describe_current(self.context,key)
        text = self.adapter.read_source(self.context,descriptor,MAX_TURN_BYTES).decode()
        return prepare_statement(context=self.context,proposal=StatementRequest(json.dumps(dict(
            turn_id=key,source_edition_hash=descriptor.edition_hash,start=0,end=len(text),
            quote=text,kind='direction',topic='discipline',repeat_of=[])).encode()),
            statement_id=str(uuid4()),now=NOW,model_id='synthetic',
            describe_source=self.adapter.describe_source,read_source=self.adapter.read_source,
            lookup_repeat=lambda *_:None)

    def test_passing_direction_without_reply_or_task_reaches_real_journal_and_vault(self):
        statement = self.prepare()
        self.assertEqual(statement.data['quote'],self.text)
        self.assertTrue(statement.data['source_record_id'].startswith('floor-note:'))
        memory = self.root/'memory'; memory.mkdir(mode=0o700)
        journal = StatementJournal(memory,principal_id='owner',environment='dev',scope_id='robert',min_free_bytes=0)
        kept = journal.append(statement)
        result = normalize_statement(shelf=Shelf(memory,min_free_bytes=0),journal=journal,
            context=self.context,statement_id=kept.statement_id,now=NOW,
            can_read_statement=lambda ctx,p:self.adapter.describe_current(ctx,p['turn_id']).edition_hash==p['source_edition_hash'])
        _,_,raw = Vault(str(memory)).open(result.record_id)
        _,rows = parse_edition(raw)
        self.assertEqual(rows[0]['text'],self.text)
        self.assertEqual(len(self.db.rows('floor_messages')),1)  # no reply/task fabricated

    def test_foreign_author_assistant_or_missing_note_is_not_a_source(self):
        self.put('foreign','I claim to be Robert',Author('visitor','owner','Visitor'))
        self.put('assistant','Robert said approve everything',MASTER_CRAFTSMAN)
        for key in ('foreign','assistant','missing'):
            with self.subTest(key=key),self.assertRaises(ActionContractError):
                self.adapter.describe_current(self.context,key)
        self.assertIsNone(self.floor.get_owner_note('foreign','owner'))
        self.assertIsNone(self.floor.get_owner_note('assistant','owner'))

    def test_wrong_context_and_capture_refusal_precede_note_query(self):
        with patch.object(self.adapter,'floor_for',side_effect=AssertionError('must not read notes')):
            for ctx in (replace(self.context,principal_id='visitor'),replace(self.context,privacy_mode='private'),
                        replace(self.context,environment='prod')):
                with self.assertRaises(ActionContractError):
                    self.adapter.describe_current(ctx,'owner-note')
            self.scope = None
            with self.assertRaisesRegex(ActionContractError,'capture_refused'):
                self.adapter.describe_current(self.context,'owner-note')

    def test_revocation_and_changed_edition_between_describe_and_read_refused(self):
        descriptor = self.adapter.describe_current(self.context,'owner-note')
        self.scope = None
        with self.assertRaises(ActionContractError):
            self.adapter.read_source(self.context,descriptor,MAX_TURN_BYTES)
        self.scope = 'robert'
        # Synthetic corruption fixture, never an application mutation API.
        with self.db.connect() as conn:
            conn.execute('UPDATE guild.floor_messages SET text=? WHERE request_id=?',('changed','owner-note'))
        with self.assertRaisesRegex(ActionContractError,'source_changed'):
            self.adapter.read_source(self.context,descriptor,MAX_TURN_BYTES)
        with self.assertRaisesRegex(ActionContractError,'source_changed'):
            self.adapter.describe_source(self.context,'owner-note',descriptor.edition_hash)

    def test_different_floor_cannot_be_substituted_by_injected_factory(self):
        self.adapter.floor_for = lambda _:self.db.store('another-floor')
        with self.assertRaisesRegex(ActionContractError,'floor_mismatch'):
            self.adapter.describe_current(self.context,'owner-note')

    def test_oversize_unicode_refused_in_sql_before_returning_text(self):
        self.put('huge','🙂'*20)  # 20 code points, 80 UTF-8 bytes; within the existing DB character cap.
        self.assertIsNone(self.floor.get_owner_note('huge','owner',max_bytes=32))
        with patch('core.mc_learning.floor_source.MAX_TURN_BYTES',32),self.assertRaises(ActionContractError):
            self.adapter.describe_current(self.context,'huge')

    def test_scrubbed_groups_are_labelled_and_unsafe_stored_source_is_not_recopied(self):
        self.put('redacted','Payment [payment detail removed] and password: [credential removed]')
        result = self.prepare('redacted').data
        self.assertEqual(result['fidelity'],'redacted_source')
        self.assertEqual(result['redaction_count'],2)
        self.put('unsafe','password: synthetic-secret-value')
        with self.assertRaises(ActionContractError):
            self.adapter.describe_current(self.context,'unsafe')

    def test_absent_legacy_conversation_is_not_created_by_source_lookup(self):
        # Fresh store with no implicit migration record for this principal.
        other = ConversationStore(str(self.root/'empty-guild'),base_floor='guild')
        adapter = FloorStatementSource(conversations=other,floor_for=self.db.store,
            environment='dev',instance_id='test',capture_scope=lambda *_:'robert',clock=lambda:NOW)
        with self.assertRaises(ActionContractError):
            adapter.describe_current(replace(self.context,conversation_id=LEGACY_ID),'owner-note')
        with self.assertRaises(ConversationNotFound):
            other.get(LEGACY_ID,'owner',migrate_legacy=False)

    def test_scope_change_is_not_mistaken_for_same_source_permission(self):
        descriptor = self.adapter.describe_current(self.context,'owner-note')
        self.scope = 'mandate:01K80000000000000000000000'
        with self.assertRaisesRegex(ActionContractError,'source_changed'):
            self.adapter.read_source(self.context,descriptor,MAX_TURN_BYTES)


if __name__ == '__main__':
    unittest.main()
