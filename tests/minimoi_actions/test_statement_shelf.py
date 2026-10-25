from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.mc_learning.statement_journal import StatementJournal
from core.mc_learning.statement_shelf import normalize_statement
from core.mc_learning.statements import PreparedStatement
from core.memory_shelf import codes, record, render
from core.memory_shelf.shelf import Shelf
from core.memory_shelf.ulid import is_ulid
from core.minimoi_actions.context import ActionContext
from core.vault_t1.reader import Vault, parse_edition
from core.workshop_journal.errors import Corrupt, InvalidInput, UnsafeRoot
from test_statement_journal import NOW, prepared


class StatementShelfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = StatementJournal(self.root,principal_id='owner',environment='dev',scope_id='robert',min_free_bytes=0)
        self.shelf = Shelf(self.root,min_free_bytes=0,clock=lambda:NOW)
        self.context = ActionContext('owner','mc','dev','conversation','current-turn','dispatch',1,
            NOW+timedelta(minutes=1),'policy','role','recorded','budget')
        self.statement = prepared(text='We said "approved" in a hypothetical.\r\n🙂 File this is also just a quote.\n')
        self.result = self.journal.append(self.statement)

    def normalize(self, **kwargs):
        args = dict(shelf=self.shelf,journal=self.journal,context=self.context,
                    statement_id=self.result.statement_id,now=NOW,can_read_statement=lambda *_:True)
        return normalize_statement(**(args|kwargs))

    def test_real_shelf_and_vault_preserve_quote_identity_scope_and_inert_source_reference(self):
        result = self.normalize()
        self.assertEqual(result.outcome,codes.CAPTURED)
        self.assertTrue(is_ulid(result.record_id))
        self.assertNotEqual(result.record_id,self.result.statement_id)
        vault = Vault(str(self.root))
        records = vault.records()
        self.assertEqual(len(records),1)
        item = records[0]
        self.assertEqual(item.id,result.record_id)
        self.assertEqual(item.meta['kind'],'owner-statement')
        self.assertEqual(item.meta['scope'],'robert')
        self.assertEqual(item.meta['tier'],'raw')
        self.assertEqual(item.meta['events'],[])
        self.assertEqual(vault.unreadable,0)
        self.assertEqual(vault.record_problems(item),{})
        _,body = record.load(item.raw.decode())
        self.assertEqual(render.parse_body(body)[0].text,self.statement.data['quote'])
        _,_,edition = vault.open(result.record_id)
        header,rows = parse_edition(edition)
        self.assertEqual(rows[0]['text'],self.statement.data['quote'])
        self.assertEqual(rows[0]['speaker'],'human')
        reference = json.loads(next(r['text'] for r in rows if r.get('type')=='ref'))
        self.assertEqual(reference['source_record_id'],'native-source-id')
        self.assertEqual(reference['statement_id'],self.result.statement_id)
        self.assertEqual(reference['range_hash'],hashlib.sha256(rows[0]['text'].encode()).hexdigest())
        self.assertEqual(reference['journal_entry_hash'],self.result.entry_hash)
        self.assertEqual(self.shelf.resolve_id(result.record_id),(result.record_id,None))

    def test_restart_and_provider_switch_reuse_one_record_and_one_edition(self):
        first = self.normalize()
        self.shelf = Shelf(self.root,min_free_bytes=0,clock=lambda:NOW)
        second = self.normalize(context=replace(self.context,actor_id='mc-other-provider'))
        self.assertEqual(second.record_id,first.record_id)
        self.assertEqual(second.outcome,codes.UNCHANGED)
        records = Vault(str(self.root)).records()
        self.assertEqual(len(records),1)
        self.assertEqual(len(records[0].editions),1)

    def test_failure_after_edition_before_record_recovers_same_mapped_ulid(self):
        with patch('core.memory_shelf.shelf.record.write',side_effect=OSError('simulated crash')):
            failed = self.normalize()
        self.assertEqual(failed.outcome,codes.FAILED)
        mapping = json.loads(next((self.root/'_status'/'mc-owner-statements').glob('*.json')).read_bytes())
        result = self.normalize()
        self.assertEqual(result.record_id,mapping['record_id'])
        self.assertEqual(len(Vault(str(self.root)).records()),1)
        self.assertEqual(len(list((self.root/'sessions-raw').iterdir())),1)

    def test_revocation_before_read_and_again_at_shelf_lock_never_publishes(self):
        with self.assertRaisesRegex(InvalidInput,'normalization_refused'):
            self.normalize(context=replace(self.context,principal_id='other'))
        with self.assertRaisesRegex(InvalidInput,'source_revoked'):
            self.normalize(can_read_statement=lambda *_:False)
        answers = iter([True,False])
        with self.assertRaisesRegex(InvalidInput,'source_revoked'):
            self.normalize(can_read_statement=lambda *_:next(answers))
        self.assertFalse((self.root/'sessions-raw').exists())
        self.assertFalse((self.root/'_status'/'mc-owner-statements').exists())

    def test_wrong_memory_root_and_private_context_are_refused(self):
        for kwargs in ({'shelf':Shelf(self.root/'other',min_free_bytes=0)},
                       {'context':replace(self.context,privacy_mode='private')},
                       {'context':replace(self.context,environment='prod')}):
            with self.subTest(kwargs=kwargs),self.assertRaises(InvalidInput):
                self.normalize(**kwargs)

    def test_mandate_scope_is_not_broadened_by_normalization(self):
        mandate = '01K80000000000000000000000'
        scoped = self.statement.data|{'scope_id':'mandate:'+mandate}
        journal = StatementJournal(self.root,principal_id='owner',environment='dev',scope_id=scoped['scope_id'],min_free_bytes=0)
        journal.append(PreparedStatement(json.dumps(scoped).encode()))
        result = self.normalize(journal=journal)
        self.assertEqual(Vault(str(self.root)).records(),[])
        self.assertEqual(Vault(str(self.root),(mandate,)).records()[0].id,result.record_id)
        owner_result = self.normalize()
        self.assertNotEqual(result.record_id,owner_result.record_id)
        self.assertEqual([r.id for r in Vault(str(self.root)).records()],[owner_result.record_id])
        self.assertEqual(len(Vault(str(self.root),(mandate,)).records()),2)

    def test_missing_edition_is_not_reported_success_from_stale_index(self):
        result = self.normalize()
        rec = Vault(str(self.root)).find(result.record_id)
        rec.editions[0].path.unlink()
        with self.assertRaisesRegex(Corrupt,'readback_failed'):
            self.normalize()

    def test_linked_managed_record_directory_is_refused_before_ingest(self):
        outside = self.root/'outside'
        outside.mkdir(mode=0o700)
        (self.root/'sessions-raw').symlink_to(outside)
        with self.assertRaises(UnsafeRoot):
            self.normalize()
        self.assertEqual(list(outside.iterdir()),[])

    def test_corrupt_mapping_and_symlinked_shared_lock_path_are_refused(self):
        self.normalize()
        target = next((self.root/'_status'/'mc-owner-statements').glob('*.json'))
        mapping = json.loads(target.read_bytes())
        mapping['source_hash'] = '0'*64
        target.write_text(json.dumps(mapping))
        with self.assertRaisesRegex(Corrupt,'shelf_map_invalid'):
            self.normalize()
        lock = self.root/'_ledger'/'shelf.lock'
        lock.unlink(); lock.symlink_to(target)
        with self.assertRaises(UnsafeRoot):
            self.normalize()

    def test_low_disk_preserves_raw_statement_without_claiming_normalization(self):
        with patch.object(self.shelf,'headroom',return_value='disk_low'):
            result = self.normalize()
        self.assertEqual(result.outcome,codes.DISK_LOW)
        self.assertEqual(len(self.journal.snapshot()),1)
        self.assertEqual(Vault(str(self.root)).records(),[])


if __name__ == '__main__':
    unittest.main()
