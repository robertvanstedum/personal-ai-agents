from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import yaml

from core.mc_learning.statements import (PreparedStatement, StatementRequest,
                                         SourceDescriptor, prepare_statement)
from core.mc_learning.statement_journal import StatementJournal
from core.minimoi_actions.context import ActionContext
from core.memory_shelf import record
from core.workshop_journal import fsutil as fs
from core.workshop_journal.errors import (CommitUnknown, Corrupt, IdConflict,
                                         InvalidInput, LockBusy, Missing, UnsafeRoot)

NOW = datetime(2026,10,20,12,tzinfo=timezone.utc)


def prepared(*, turn='turn-one', now=NOW, statement_id=None, kind='direction', text='Gates 🙂\r\nnot dates.\n'):
    raw = text.encode()
    digest = hashlib.sha256(raw).hexdigest()
    context = ActionContext('owner','mc','dev','conversation','current-turn','dispatch',1,
        now+timedelta(minutes=1),'policy','role','recorded','budget')
    source = SourceDescriptor('owner','dev','conversation',turn,'native-source-id','robert',
        'owner','owner',digest,NOW-timedelta(minutes=1),True,True,'on_record')
    return prepare_statement(context=context,proposal=StatementRequest(json.dumps(dict(
        turn_id=turn,source_edition_hash=digest,start=0,end=len(text),kind=kind,
        topic='discipline',repeat_of=[])).encode()),statement_id=statement_id or str(uuid4()),
        now=now,model_id='synthetic-model',describe_source=lambda *_:source,
        read_source=lambda *_:raw,lookup_repeat=lambda *_:None)


def competing_append(root, payload, output):
    try:
        journal = StatementJournal(root,principal_id='owner',environment='dev',scope_id='robert',
                                   lock_timeout=5,min_free_bytes=0)
        result = journal.append(PreparedStatement(payload))
        output.put(('ok', result.statement_id, result.commit_sequence))
    except Exception as exc:
        output.put(('error', type(exc).__name__, str(exc)))


class StatementJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = self.new_journal()
        self.folder = self.root/'turns'/'.owner-statements'/self.journal.partition

    def new_journal(self, **kwargs):
        return StatementJournal(self.root,principal_id='owner',environment='dev',scope_id='robert',
                                min_free_bytes=0,**kwargs)

    def day_path(self, day='2026-10-20'):
        return self.folder/f'{day}.jsonl'

    def test_restart_provider_and_next_day_duplicate_keep_original_and_sidecar(self):
        first = prepared()
        result = self.journal.append(first)
        self.assertFalse(result.duplicate)
        original = self.day_path().read_bytes()
        second = prepared(now=NOW+timedelta(days=2),kind='correction')
        changed = second.data | {'proposing_model':'another-provider'}
        replay = self.new_journal().append(PreparedStatement(json.dumps(changed).encode()))
        self.assertEqual(replay.statement_id, result.statement_id)
        self.assertEqual(replay.commit_sequence,1)
        self.assertTrue(replay.duplicate)
        self.assertEqual(self.day_path().read_bytes(),original)
        self.assertEqual(len(list(self.folder.glob('*.jsonl'))),1)
        row = self.journal.snapshot()[0]
        self.assertEqual(row['payload']['quote'],first.data['quote'])
        self.assertEqual(row['payload']['kind'],'direction')
        meta = yaml.safe_load((self.folder/'2026-10-20.meta.yml').read_bytes())
        record.check(meta)
        self.assertEqual(meta['source_hash'],hashlib.sha256(original).hexdigest())
        self.assertEqual(meta['events'],[])
        self.assertEqual(meta['statement_count'],1)
        self.assertTrue(result.sidecar_ready)
        for path in self.root.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777,0o700 if path.is_dir() else 0o600)

    def test_concurrent_processes_record_one_span_once_and_distinct_spans_in_order(self):
        ctx = multiprocessing.get_context('fork')
        output = ctx.Queue()
        payloads = [prepared().encoded for _ in range(4)] + [prepared(turn=f'turn-{i}').encoded for i in range(3)]
        processes = [ctx.Process(target=competing_append,args=(str(self.root),p,output)) for p in payloads]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.kill(); process.join(); self.fail('fixture process timed out')
            self.assertEqual(process.exitcode,0)
        outcomes = [output.get(timeout=1) for _ in processes]
        self.assertTrue(all(x[0]=='ok' for x in outcomes),outcomes)
        rows = self.journal.snapshot()
        self.assertEqual([r['sequence'] for r in rows],[1,2,3,4])
        self.assertEqual(len({x[1] for x in outcomes}),4)
        output.close()

    def test_day_rotation_and_late_recovery_have_global_commit_sequence(self):
        one = self.journal.append(prepared())
        two = self.journal.append(prepared(turn='next',now=NOW+timedelta(days=1)))
        three = self.journal.append(prepared(turn='late',now=NOW+timedelta(hours=1)))
        self.assertEqual([one.commit_sequence,two.commit_sequence,three.commit_sequence],[1,2,3])
        self.assertEqual([x['sequence'] for x in self.journal.snapshot()],[1,2,3])
        self.assertEqual(len(self.day_path().read_bytes().splitlines()),2)

    def test_short_writes_are_completed(self):
        original = fs._write
        with patch.object(fs,'_write',side_effect=lambda fd,view:original(fd,view[:7])):
            result = self.journal.append(prepared())
        self.assertTrue(result.sidecar_ready)
        self.assertEqual(len(self.journal.snapshot()),1)

    def test_partial_append_is_quarantined_before_retry_without_rewriting_committed_prefix(self):
        self.journal.append(prepared())
        prefix = self.day_path().read_bytes()
        second = prepared(turn='second')
        original_write = fs.write_all
        def fail_on_entry(fd, raw):
            if b'"schema":"mc-statement-journal/1"' in raw:
                os.write(fd,raw[:47])
                raise OSError('injected partial append')
            return original_write(fd,raw)
        with patch.object(fs,'write_all',side_effect=fail_on_entry),self.assertRaises(CommitUnknown):
            self.journal.append(second)
        damaged = self.day_path().read_bytes()
        with self.assertRaisesRegex(Corrupt,'tail_pending_recovery'):
            self.journal.snapshot()
        retry = self.new_journal().append(prepared(turn='second',now=NOW+timedelta(days=1)))
        self.assertEqual(retry.statement_id,second.data['statement_id'])
        self.assertEqual(retry.commit_sequence,2)
        self.assertEqual(list(self.folder.glob('.torn-*'))[0].read_bytes(),damaged[len(prefix):])
        self.assertTrue(self.day_path().read_bytes().startswith(prefix))
        self.assertEqual(len(self.journal.snapshot()),2)

    def test_sync_failure_after_complete_line_is_unknown_and_retry_resyncs_same_id(self):
        first = prepared()
        # Journal opens append fd write-only; inject via write_all instead so
        # failure occurs immediately after complete bytes, before their sync.
        original_write = fs.write_all
        def complete_then_fail(fd, raw):
            original_write(fd,raw)
            if b'"schema":"mc-statement-journal/1"' in raw:
                raise OSError('complete but unsynced')
        with patch.object(fs,'write_all',side_effect=complete_then_fail),self.assertRaises(CommitUnknown):
            self.journal.append(first)
        before = self.day_path().read_bytes()
        result = self.new_journal().append(prepared())
        self.assertTrue(result.duplicate)
        self.assertEqual(result.statement_id,first.data['statement_id'])
        self.assertEqual(self.day_path().read_bytes(),before)

    def test_sidecar_failure_does_not_erase_committed_record_and_retry_repairs_same_ulid(self):
        self.journal.append(prepared())
        meta_path = self.folder/'2026-10-20.meta.yml'
        old_id = yaml.safe_load(meta_path.read_bytes())['id']
        second = prepared(turn='second')
        original_replace = fs.publish_replace
        def sidecar_fail(folder, name, raw, **kwargs):
            if name.endswith('.meta.yml'):
                raise OSError('sidecar unavailable')
            return original_replace(folder,name,raw,**kwargs)
        with patch.object(fs,'publish_replace',side_effect=sidecar_fail):
            result = self.journal.append(second)
        self.assertFalse(result.sidecar_ready)
        self.assertEqual(len(self.journal.snapshot()),2)
        result = self.journal.append(second)
        self.assertTrue(result.sidecar_ready)
        meta = yaml.safe_load(meta_path.read_bytes())
        self.assertEqual(meta['id'],old_id)
        self.assertEqual(meta['statement_count'],2)

    def test_interior_corruption_holds_without_discarding_any_bytes(self):
        self.journal.append(prepared())
        self.journal.append(prepared(turn='second'))
        path = self.day_path()
        raw = path.read_bytes().replace(b'"sequence":1',b'"sequence":9',1)+b'torn'
        path.write_bytes(raw)
        with self.assertRaisesRegex(Corrupt,'committed_line_invalid'):
            self.journal.append(prepared(turn='third'))
        self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(list(self.folder.glob('.torn-*')),[])

    def test_missing_intent_or_committed_day_holds_instead_of_duplicate(self):
        first = prepared()
        self.journal.append(first)
        intent = self.folder/f'.intent-{first.data["span_key"]}.json'
        intent.unlink()
        with self.assertRaisesRegex(Corrupt,'intent_missing'):
            self.journal.append(prepared())
        self.assertEqual(len(self.journal.snapshot()),1)

    def test_missing_last_committed_day_is_not_silently_replaced(self):
        self.journal.append(prepared())
        self.journal.append(prepared(turn='second',now=NOW+timedelta(days=1)))
        self.day_path('2026-10-21').unlink()
        with self.assertRaisesRegex(Corrupt,'head_mismatch'):
            self.journal.append(prepared(turn='third'))

    def test_head_sync_failure_retries_complete_record_without_second_append(self):
        first = prepared()
        original_replace = fs.publish_replace
        def head_fail(folder, name, raw, **kwargs):
            if name == '.head.json':
                raise OSError('head unavailable')
            return original_replace(folder,name,raw,**kwargs)
        with patch.object(fs,'publish_replace',side_effect=head_fail),self.assertRaises(CommitUnknown):
            self.journal.append(first)
        result = self.journal.append(prepared())
        self.assertTrue(result.duplicate)
        self.assertEqual(result.statement_id,first.data['statement_id'])
        self.assertEqual(len(self.journal.snapshot()),1)

    def test_headroom_loss_after_append_is_unknown_not_false_failure(self):
        first = prepared()
        with patch('core.mc_learning.statement_journal.fsio.check_headroom',
                   side_effect=[None,None,'disk_low']),self.assertRaises(CommitUnknown):
            self.journal.append(first)
        result = self.journal.append(first)
        self.assertTrue(result.duplicate)
        self.assertEqual(result.statement_id,first.data['statement_id'])

    def test_uuid_cannot_be_reused_for_another_span(self):
        first = prepared()
        self.journal.append(first)
        with self.assertRaises(IdConflict):
            self.journal.append(prepared(turn='other',statement_id=first.data['statement_id']))
        self.assertEqual(len(self.journal.snapshot()),1)

    def test_read_only_snapshot_creates_nothing_and_missing_is_not_zero(self):
        with self.assertRaises(Missing):
            self.journal.snapshot()
        self.assertEqual(list(self.root.iterdir()),[])
        self.journal.append(prepared())
        before = {str(p):p.stat().st_mtime_ns for p in self.root.rglob('*')}
        self.journal.snapshot()
        self.assertEqual(before,{str(p):p.stat().st_mtime_ns for p in self.root.rglob('*')})

    def test_lock_timeout_and_disk_low_do_not_append(self):
        self.journal.append(prepared())
        before = self.day_path().read_bytes()
        lock = os.open(self.folder/'.lock',os.O_RDWR)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX)
            with self.assertRaises(LockBusy):
                self.new_journal(lock_timeout=0.03).append(prepared(turn='second'))
        finally:
            os.close(lock)
        with patch('core.mc_learning.statement_journal.fsio.check_headroom',return_value='disk_low'):
            with self.assertRaisesRegex(InvalidInput,'disk_low'):
                self.journal.append(prepared(turn='second'))
        self.assertEqual(self.day_path().read_bytes(),before)

    def test_symlinks_hardlinks_and_public_file_permissions_are_refused(self):
        self.journal.append(prepared())
        path = self.day_path()
        backup = self.folder/'backup'
        path.rename(backup)
        path.symlink_to(backup)
        with self.assertRaises(UnsafeRoot):
            self.journal.snapshot()
        path.unlink(); os.link(backup,path)
        with self.assertRaises(UnsafeRoot):
            self.journal.snapshot()
        backup.unlink(); path.chmod(0o644)
        with self.assertRaises(UnsafeRoot):
            self.journal.snapshot()

    def test_refused_capacity_does_not_reserve_ids_or_accumulate_sidecars(self):
        self.journal.append(prepared())
        before = {p.name:p.read_bytes() for p in self.folder.iterdir()}
        refused = [prepared(turn=f'refused-{i}') for i in range(50)]
        with patch('core.mc_learning.statement_journal.MAX_TOTAL',len(self.day_path().read_bytes())):
            for item in refused:
                with self.assertRaisesRegex(InvalidInput,'statement_storage_limit'):
                    self.journal.append(item)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.folder.iterdir()})
        # Capacity restored: refusal did not bind this span to a rejected ID.
        fresh = prepared(turn='refused-0')
        result = self.journal.append(fresh)
        self.assertEqual(result.statement_id,fresh.data['statement_id'])

    def test_direct_payload_cannot_change_scope_quote_hash_privacy_or_add_approval(self):
        data = prepared().data
        for changes in ({'approved':True},{'privacy_mode':'private'},{'quote':'forged'},
                        {'scope_id':'guest'},{'range_hash':'0'*64},{'start':False},
                        {'principal_id':'another-owner'}):
            with self.subTest(changes=changes),self.assertRaises(InvalidInput):
                self.journal.append(PreparedStatement(json.dumps(data|changes).encode()))
        self.assertEqual(list(self.root.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
