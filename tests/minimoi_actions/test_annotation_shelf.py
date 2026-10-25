from dataclasses import replace
from datetime import timedelta
import json
import hashlib
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.mc_learning.annotation_shelf import AnnotationShelf
from core.mc_learning.corrections import CaptureCoverage, project_corrections
from core.mc_learning.statement_journal import StatementJournal
from core.mc_learning.statements import PreparedStatement
from core.memory_shelf import ulid
from core.memory_shelf.shelf import Shelf
from core.minimoi_actions.context import ActionContext
from core.vault_t1.reader import Vault, parse_edition
from core.workshop_journal.errors import CommitUnknown, Corrupt, IdConflict, InvalidInput, UnsafeRoot
from test_statement_journal import NOW, prepared


def context():
    return ActionContext('owner','mc','dev','conversation','current-turn','dispatch',1,
        NOW+timedelta(hours=1),'policy','role','recorded','budget')


def store(root):
    journal = StatementJournal(root,principal_id='owner',environment='dev',scope_id='robert',min_free_bytes=0)
    return AnnotationShelf(Shelf(root,min_free_bytes=0,clock=lambda:NOW),journal)


def competing_publish(root, payload, output):
    try:
        result = store(root).publish(payload,context=context(),now=NOW,
            can_read_statement=lambda *_:True,authorize_owner_annotation=lambda *_:True)
        output.put(('ok',result.sequence))
    except Exception as exc:
        output.put((type(exc).__name__,str(exc)))


class AnnotationShelfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = store(self.root)
        self.context = context()
        self.one = prepared(turn='one',kind='correction')
        self.two = prepared(turn='two',kind='correction')
        self.store.journal.append(self.one); self.store.journal.append(self.two)
        self.receipts = set()

    def snapshot(self, **kwargs):
        return self.store.snapshot(**(dict(context=self.context,now=NOW,can_read_statement=lambda *_:True)|kwargs))

    def payload(self, source=None, **overrides):
        p = (source or self.two).data
        data = dict(schema='mc-statement-annotation/1',annotation_id=ulid.new_ulid(),
            statement_id=p['statement_id'],span_key=p['span_key'],principal_id='owner',environment='dev',
            scope_id='robert',kind='correction',topic='whole product acceptance',repeat_of=[],confirmed_repeat_of=[],
            actor_id='owner',recorded_at=NOW.isoformat(),owner_receipt_id=str(uuid4()),
            base_revision=self.snapshot().revisions[p['statement_id']])
        return json.dumps(data|overrides).encode()

    def publish(self, payload, **kwargs):
        return self.store.publish(payload,**(dict(context=self.context,now=NOW,can_read_statement=lambda *_:True,
            authorize_owner_annotation=lambda *_:True)|kwargs))

    def project(self):
        start,end = NOW-timedelta(days=1),NOW
        return project_corrections(context=self.context,rows=self.store.journal.snapshot(),
            annotations=self.snapshot().annotations,start=start,end=end,now=NOW,
            coverage=CaptureCoverage(start,end),can_read_statement=lambda *_:True,
            verify_owner_annotation=lambda _,p,d,sequence:(p['owner_receipt_id'],d) in self.receipts)

    def test_shelf_record_preserves_source_and_only_completed_exact_receipt_changes_ledger(self):
        raw_before = self.store.journal.snapshot()
        result = self.publish(self.payload(repeat_of=[self.one.data['statement_id']],confirmed_repeat_of=[self.one.data['statement_id']]))
        self.assertEqual(self.project()['confirmed_repeats'],0)
        self.receipts.add((result.data['owner_receipt_id'],result.digest))
        self.assertEqual(self.project()['confirmed_repeats'],1)
        self.assertEqual(self.store.journal.snapshot(),raw_before)
        rec,_,edition = Vault(str(self.root)).open(result.data['annotation_id'])
        self.assertEqual(rec.meta['kind'],'owner-statement-annotation')
        self.assertEqual(rec.meta['events'],[])
        _,rows = parse_edition(edition)
        saved = json.loads(next(r['text'] for r in rows if r.get('type')=='ref'))
        self.assertEqual(saved['annotation'],result.data)
        self.assertEqual(saved['sequence'],1)
        self.assertEqual(self.context.grant_ids,())

    def test_retry_restart_and_provider_switch_keep_one_sequence_and_one_edition(self):
        payload = self.payload(); first = self.publish(payload)
        self.store = store(self.root)
        second = self.publish(payload,context=replace(self.context,actor_id='mc-different-provider'))
        self.assertEqual(second,first)
        self.assertEqual(len(self.snapshot().annotations),1)
        self.assertEqual(len(Vault(str(self.root)).records()[0].editions),1)

    def test_stale_relabel_and_reused_identity_or_receipt_conflict(self):
        payload = self.payload(); stale = self.payload()
        first = self.publish(payload)
        with self.assertRaisesRegex(IdConflict,'revision_changed'):
            self.publish(stale)
        for changes in ({'topic':'changed bytes'},{'annotation_id':ulid.new_ulid()}):
            with self.subTest(changes=changes),self.assertRaisesRegex(IdConflict,'intent_conflict'):
                self.publish(json.dumps(first.data|changes).encode())
        second = self.publish(self.payload(kind='direction'))
        self.receipts.update([(first.data['owner_receipt_id'],first.digest),(second.data['owner_receipt_id'],second.digest)])
        self.assertEqual(self.project()['corrections'],1)

    def test_failed_edition_publication_is_pending_and_same_intent_recovers(self):
        payload = self.payload()
        with patch('core.memory_shelf.shelf.record.write',side_effect=OSError('synthetic crash')):
            with self.assertRaisesRegex(CommitUnknown,'publication_pending'):
                self.publish(payload)
        snapshot = self.snapshot()
        self.assertEqual(snapshot.annotations,())
        self.assertEqual(snapshot.pending_annotation_ids,(json.loads(payload)['annotation_id'],))
        result = self.publish(payload)
        self.assertEqual(result.sequence,1)
        self.assertEqual(self.snapshot().pending_annotation_ids,())
        self.assertEqual(len(Vault(str(self.root)).records()),1)

    def test_checkpoint_fault_recovers_reserved_sequence_without_duplicate(self):
        payload = self.payload()
        with patch('core.mc_learning.annotation_shelf.fs.publish_replace',side_effect=OSError('synthetic fsync failure')):
            with self.assertRaisesRegex(CommitUnknown,'checkpoint_unknown'):
                self.publish(payload)
        self.assertEqual(len(self.snapshot().pending_annotation_ids),1)
        self.assertEqual(self.publish(payload).sequence,1)

    def test_short_writes_use_existing_full_write_helpers(self):
        from core.workshop_journal import fsutil
        original = fsutil._write
        with patch.object(fsutil,'_write',side_effect=lambda fd,view:original(fd,view[:7])):
            result = self.publish(self.payload())
        self.assertEqual(self.snapshot().annotations,(result,))

    def test_concurrent_stale_edits_have_one_winner(self):
        payloads = [self.payload(topic=f'choice {i}') for i in range(4)]
        ctx = multiprocessing.get_context('spawn'); output = ctx.Queue()
        workers = [ctx.Process(target=competing_publish,args=(str(self.root),p,output)) for p in payloads]
        for worker in workers: worker.start()
        results = [output.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(10); self.assertEqual(worker.exitcode,0)
        self.assertEqual(sum(r[0]=='ok' for r in results),1,results)
        self.assertEqual(sum(r==('IdConflict','annotation_revision_changed') for r in results),3,results)
        self.assertEqual(len(self.snapshot().annotations),1)
        output.close()

    def test_authority_scope_privacy_and_revocation_are_checked_before_effect(self):
        payload = self.payload()
        for changes in ({'principal_id':'foreign'},{'environment':'prod'},{'privacy_mode':'private'}):
            with self.subTest(changes=changes),self.assertRaises(InvalidInput):
                self.publish(payload,context=replace(self.context,**changes))
        with self.assertRaisesRegex(InvalidInput,'owner_action_refused'):
            self.publish(payload,authorize_owner_annotation=lambda *_:False)
        self.assertFalse((self.root/'sessions-raw').exists())
        answers = iter([True,False])
        with self.assertRaisesRegex(InvalidInput,'owner_action_refused'):
            self.publish(payload,authorize_owner_annotation=lambda *_:next(answers))
        with self.assertRaisesRegex(InvalidInput,'source_refused'):
            self.publish(payload,can_read_statement=lambda *_:False)
        self.assertEqual(self.snapshot().annotations,())
        self.publish(payload)
        self.assertEqual(self.snapshot(can_read_statement=lambda *_:False).annotations,())
        with self.assertRaisesRegex(InvalidInput,'source_refused'):
            self.publish(payload,can_read_statement=lambda *_:False)

    def test_no_owner_receipt_or_forged_attribution_is_never_an_owner_control(self):
        for changes in ({'actor_id':'mc'},{'owner_receipt_id':None},{'span_key':'0'*64},{'recorded_at':(NOW+timedelta(seconds=1)).isoformat()}):
            with self.subTest(changes=changes),self.assertRaises(InvalidInput):
                self.publish(self.payload(**changes))

    def test_self_cross_scope_future_and_cyclic_links_are_refused(self):
        for sid in (self.two.data['statement_id'],str(uuid4())):
            with self.assertRaisesRegex(InvalidInput,'repeat_refused'):
                self.publish(self.payload(repeat_of=[sid]))
        future = self.one.data|{'statement_id':str(uuid4()),'scope_id':'mandate:'+ulid.new_ulid()}
        foreign = PreparedStatement(json.dumps(future).encode())
        foreign_journal = StatementJournal(self.root,principal_id='owner',environment='dev',scope_id=future['scope_id'],min_free_bytes=0)
        foreign_journal.append(foreign)
        with self.assertRaisesRegex(InvalidInput,'repeat_refused'):
            self.publish(self.payload(repeat_of=[future['statement_id']]))
        later = prepared(turn='later',kind='correction').data
        later['occurred_at'] = NOW.isoformat()
        self.store.journal.append(PreparedStatement(json.dumps(later).encode()))
        with self.assertRaisesRegex(InvalidInput,'repeat_refused'):
            self.publish(self.payload(repeat_of=[later['statement_id']]))
        self.publish(self.payload(source=self.one,repeat_of=[self.two.data['statement_id']]))
        with self.assertRaisesRegex(InvalidInput,'cycle_or_limit'):
            self.publish(self.payload(repeat_of=[self.one.data['statement_id']]))

    def test_missing_acknowledged_tail_or_corrupt_reservation_never_looks_empty(self):
        self.publish(self.payload())
        folder = self.root/'_status'/'mc-statement-annotations'/self.store.journal.partition
        path = folder/'entry-00000001.json'; original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(Corrupt,'checkpoint_invalid'):
            self.snapshot()
        path.write_bytes(original); path.chmod(0o600)
        p = json.loads(original); p['payload']['topic']='tampered'
        path.write_text(json.dumps(p))
        with self.assertRaisesRegex(Corrupt,'reservation_invalid'):
            self.snapshot()

    def test_missing_edition_and_wrong_index_are_not_success(self):
        payload = self.payload(); result = self.publish(payload)
        rec = Vault(str(self.root)).find(result.data['annotation_id'])
        rec.editions[0].path.unlink()
        with self.assertRaisesRegex(Corrupt,'readback_failed'):
            self.snapshot()
        with self.assertRaisesRegex(Corrupt,'readback_failed'):
            self.publish(payload)

    def test_lost_reservation_folder_is_damage_not_a_fresh_ledger(self):
        self.publish(self.payload())
        folder = self.root/'_status'/'mc-statement-annotations'/self.store.journal.partition
        folder.rename(folder.with_name('saved-evidence'))
        with self.assertRaisesRegex(Corrupt,'reservations_missing'):
            self.snapshot()

    def test_existing_shelf_record_id_is_not_taken_over(self):
        from core.mc_learning.statement_shelf import normalize_statement
        result = normalize_statement(shelf=self.store.shelf,journal=self.store.journal,context=self.context,
            statement_id=self.one.data['statement_id'],now=NOW,can_read_statement=lambda *_:True)
        before = Vault(str(self.root)).open(result.record_id)[2]
        with self.assertRaisesRegex(IdConflict,'id_in_use'):
            self.publish(self.payload(annotation_id=result.record_id))
        self.assertEqual(Vault(str(self.root)).open(result.record_id)[2],before)
        self.assertEqual(self.snapshot().annotations,())

    def test_forged_index_and_unknown_ancestry_hold_without_an_effect(self):
        payload = self.payload(); p = json.loads(payload)
        identity = hashlib.sha256(json.dumps(['owner','dev','robert',p['annotation_id']],
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with patch.object(self.store.shelf,'index',return_value={'minimoi-owner-annotation:'+identity:{'id':'wrong','dir':'wrong'}}):
            with self.assertRaisesRegex(Corrupt,'identity_conflict'):
                self.publish(payload)
        self.assertEqual(len(self.snapshot().pending_annotation_ids),1)
        self.publish(payload)

        missing = str(uuid4())
        source = prepared(turn='missing-ancestor',kind='correction').data
        source['repeat_of'] = [missing]
        self.store.journal.append(PreparedStatement(json.dumps(source).encode()))
        with self.assertRaisesRegex(InvalidInput,'ancestry_unavailable'):
            self.publish(self.payload(repeat_of=[source['statement_id']]))

    def test_symlink_and_hardlink_reservations_are_refused(self):
        self.publish(self.payload())
        folder = self.root/'_status'/'mc-statement-annotations'/self.store.journal.partition
        target = folder/'entry-00000001.json'
        copy = self.root/'linked.json'; os.link(target,copy)
        with self.assertRaises(UnsafeRoot): self.snapshot()
        copy.unlink(); target.rename(copy); target.symlink_to(copy)
        with self.assertRaises(UnsafeRoot): self.snapshot()

    def test_disk_low_read_only_initial_snapshot_and_revision_in_receipt_digest(self):
        self.assertEqual(self.snapshot().annotations,())
        self.assertFalse((self.root/'_ledger').exists())
        payload = self.payload()
        with patch.object(self.store.shelf,'headroom',return_value='disk_low'):
            with self.assertRaisesRegex(InvalidInput,'disk_low'): self.publish(payload)
        self.assertFalse((self.root/'sessions-raw').exists())
        from core.mc_learning.corrections import Annotation
        one = Annotation(1,payload)
        changed = Annotation(1,json.dumps(one.data|{'base_revision':'0'*64}).encode())
        self.assertNotEqual(one.digest,changed.digest)


if __name__ == '__main__':
    unittest.main()
