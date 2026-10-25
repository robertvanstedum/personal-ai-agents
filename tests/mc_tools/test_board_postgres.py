"""MC Board admission on disposable PostgreSQL; no real provider or owner records."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_tool_path_postgres import Base, DSN, NOTE, CONV, OWNER, MIGRATIONS
from core.mc_tools import binding, policy, fence
from core.mc_tools.board_adapter import BoardAdapter
from minimoi_portal.guild_ui.stores import FloorStores, Author

BOARD_MIGRATIONS = tuple('minimoi_portal/guild_ui/sql/'+n for n in (
    '001_floor_b1.sql','002_board.sql','003_media.sql','004_board_place.sql','005_board_size.sql',
    '006_agent_reply_size.sql','008_board_operations.sql'))


def board_rules(doc):
    for name,h in policy.HANDLERS.items():
        if not (h.tool.startswith('minimoi_board_') or h.tool=='minimoi_clarify_action'): continue
        if any(row['handler']==name for row in doc['rules']): continue
        tier=min(h.tiers)
        doc['rules'].append(dict(capability=h.capability,version=1,actors=['mc'],origins=['owner_turn'],tier=tier,
            approval='none' if tier==1 else 'standing',source_requirement='current_owner_turn',handler=name,
            verify=h.verify,undo=h.undo,egress=h.egress,max_operations_per_turn=10))


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class BoardTests(Base,unittest.TestCase):
    migrations=BOARD_MIGRATIONS+MIGRATIONS+('core/mc_tools/sql/012_board_conversation.sql',)

    def install_policy(self,revision='board-test-1',change=None):
        def extend(doc):
            board_rules(doc)
            if change: change(doc)
        return super().install_policy(revision,extend)

    def setUp(self):
        super().setUp()
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_board_slots,guild.mc_board_observations,guild.board_operations,'
                        'guild.floor_postits,guild.floor_board_meta,guild.floor_requests CASCADE')
        self.service.board=BoardAdapter(cursor_key=b'only-for-disposable-tests'*2,item_allowed=lambda p,i:i==12)
        self.store=FloorStores(lambda:DSN)
        self.owner_notes[(CONV,NOTE)]='Bitte eine Notiz auf mein Board: Morgen anrufen.'

    def next(self,text='Please update that note.',note=None):
        note=note or 'note-'+uuid4().hex
        self.next_turn(note);self.owner_notes[(CONV,note)]=text
        return note

    def create(self,text='Morgen anrufen. ',**extra):
        return self.call('minimoi_board_note_create',{'text':text,**extra})

    def count(self):
        return self.one('SELECT count(*) FROM guild.floor_postits')[0]

    def read(self,ref):
        return self.call('minimoi_board_note_read',{'note_ref':str(ref)})

    def test_natural_text_unicode_newline_and_receipt(self):
        text='Morgen prüfen.\nDaran denken 👨‍👩‍👧‍👦'
        out=self.create(text)
        self.assertEqual(out['status'],'done',out)
        self.assertEqual(self.store.board().data['active'][0]['text'],text)
        self.assertIn('note_link',out['data'])
        self.assertEqual(self.receipts()[0][:2],('guild.board.note.create','verified'))

    def test_one_slot_across_duplicate_payload_and_capabilities(self):
        one=self.create('One')
        two=self.create('One')
        self.assertEqual(one['data']['operation_ref'],two['data']['operation_ref'])
        self.assertTrue(two['data']['replayed'])
        changed=self.create('One!')
        self.assertEqual(changed['code'],'board_slot_conflict')
        ref=one['data']['current']['id'];self.read(ref)
        update=self.call('minimoi_board_note_update',{'note_ref':ref,'changes':{'done':True}})
        self.assertEqual(update['code'],'board_slot_conflict')
        self.assertEqual(self.count(),1)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_board_slots')[0],1)

    def test_atomic_update_and_next_turn_undo(self):
        first=self.create('Original');ref=first['data']['current']['id']
        self.next();self.read(ref)
        out=self.call('minimoi_board_note_update',{'note_ref':ref,'changes':{'text':'Changed','label':'idea','done':True}})
        self.assertEqual(out['status'],'done',out)
        self.assertEqual(out['data']['current']['state'],'done')
        self.next('Undo that change.')
        undo=self.call('minimoi_board_operation_undo',{'operation_ref':out['data']['operation_ref']})
        self.assertEqual(undo['status'],'done',undo)
        self.assertEqual(undo['data']['current']['text'],'Original')
        self.assertEqual(undo['data']['current']['state'],'active')

    def test_intervening_ui_edit_is_not_overwritten_by_refreshed_read(self):
        first=self.create('Original');ref=first['data']['current']['id']
        self.next();self.read(ref)
        self.store.set_text(int(ref),'Newer owner edit',Author(OWNER,'owner','Owner'),expect_version=1,idempotency_key='ui-edit')
        self.read(ref) # immutable first observation prevents using this to force a write
        out=self.call('minimoi_board_note_update',{'note_ref':ref,'changes':{'text':'Stale intent'}})
        self.assertEqual(out['status'],'needs_clarification',out)
        self.assertEqual(self.store.board().data['active'][0]['text'],'Newer owner edit')

    def test_target_needs_current_turn_observation(self):
        first=self.create('Keep');ref=first['data']['current']['id']
        self.next()
        out=self.call('minimoi_board_note_update',{'note_ref':ref,'changes':{'done':True}})
        self.assertEqual(out['code'],'board_read_required')
        self.assertEqual(self.store.board().data['active'][0]['state'],'active')

    def test_late_old_run_and_stopped_turn_cannot_write(self):
        self.call('minimoi_board_notes_query',{})
        old=self.run;self.next()
        out=self.call('minimoi_board_note_create',{'text':'late'},run=old)
        self.assertEqual(out['status'],'refused',out)
        with self.connect() as conn: binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        self.assertEqual(self.create('stopped')['status'],'refused')
        self.assertEqual(self.count(),0)

    def test_clarification_immediate_answer_uses_new_authority(self):
        a=self.store.add_postit('Interview one',Author(OWNER,'owner','Owner'),idempotency_key='a').value
        b=self.store.add_postit('Interview two',Author(OWNER,'owner','Owner'),idempotency_key='b').value
        self.call('minimoi_board_notes_query',{'query':'Interview'})
        question=self.call('minimoi_clarify_action',{'capability':'guild.board.note.update','question':'Which interview note?',
                                                  'candidate_refs':[str(a['id']),str(b['id'])]})
        self.assertEqual(question['status'],'needs_clarification',question)
        self.assertEqual(self.count(),2)
        self.assertEqual(self.receipts(),[])
        self.next('The second one.');self.read(b['id'])
        out=self.call('minimoi_board_note_update',{'note_ref':str(b['id']),'changes':{'done':True},
                                                   'clarification_ref':question['data']['clarification_ref']})
        self.assertEqual(out['status'],'done',out)
        self.assertEqual(self.receipts()[0][2],f'ledger:{self.ledger_id}:1')

    def question(self):
        return self.call('minimoi_clarify_action',{'capability':'guild.board.note.create','question':'What text should I save?',
                                                 'candidate_refs':[]})['data']['clarification_ref']

    def test_expired_question_never_writes(self):
        ref=self.question()
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_board_slots SET expires_at=now()-interval '1 second'")
        self.next('The answer')
        out=self.create('Answer',clarification_ref=ref)
        self.assertEqual(out['code'],'board_clarification_expired',out)
        self.assertEqual(self.count(),0)

    def test_unrelated_turn_and_stop_expire_clarification(self):
        ref=self.question();self.next('Unrelated weather question');self.next('Answer to old question')
        self.assertEqual(self.create('answer',clarification_ref=ref)['code'],'board_clarification_expired')
        self.next();ref=self.question()
        with self.connect() as conn: binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        self.next('Answer after Stop')
        self.assertEqual(self.create('answer',clarification_ref=ref)['code'],'board_clarification_expired')
        self.assertEqual(self.count(),0)

    def test_external_text_and_missing_owner_note_cannot_authorize(self):
        self.owner_notes.clear()
        self.assertEqual(self.create()['code'],'owner_note_unavailable')
        self.owner_notes[(CONV,NOTE)]='Save a note'
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('UPDATE guild.mc_tool_turns SET untrusted_read=true')
        self.assertEqual(self.create()['code'],'board_external_text')
        self.assertEqual(self.count(),0)

    def test_response_lost_after_commit_recovers_without_new_effect(self):
        original=self.service._tx
        lose=[True]
        @contextmanager
        def lost():
            committed=False
            with original() as conn:
                yield conn
                with conn.cursor() as cur:
                    cur.execute('SELECT count(*) FROM guild.board_operations')
                    committed=cur.fetchone()[0]>0
            if committed and lose[0]:
                lose[0]=False
                raise ConnectionError('synthetic lost commit acknowledgement')
        with patch.object(self.service,'_tx',lost):
            out=self.create('Exactly once')
        self.assertEqual(out['status'],'unknown',out)
        self.next('Did that save?')
        recovered=self.call('minimoi_board_operation_read',{'operation_ref':'latest'})
        self.assertEqual(recovered['status'],'done',recovered)
        self.assertEqual(self.count(),1)
        self.assertEqual(len(self.receipts()),1)

    def test_concurrent_duplicates_share_one_uuid(self):
        def one(n):return self.call('minimoi_board_note_create',{'text':'One only'},call_id=f'parallel-{n}')
        with ThreadPoolExecutor(max_workers=4) as pool: answers=list(pool.map(one,range(4)))
        self.assertTrue(all(a['status']=='done' for a in answers),answers)
        self.assertEqual(len({a['data']['operation_ref'] for a in answers}),1)
        self.assertEqual(self.count(),1)

    def test_intentional_identical_note_on_new_turn_is_new_operation(self):
        one=self.create('Identical');self.next('Create another identical note.');two=self.create('Identical')
        self.assertEqual(two['status'],'done',two)
        self.assertNotEqual(one['data']['operation_ref'],two['data']['operation_ref'])
        self.assertEqual(self.count(),2)

    def test_overflow_does_not_truncate_or_prepare_slot(self):
        out=self.create('😀'*281)
        self.assertEqual(out['code'],'board_text_invalid',out)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_board_slots')[0],0)
        self.assertEqual(self.count(),0)

    def test_stop_after_preparation_cancels_without_dispatch_and_stays_cancelled(self):
        from core.mc_tools.service import Caller
        from core.mc_tools.board_adapter import OPS
        from test_tool_path_postgres import session_key
        name='minimoi_board_note_create'
        compiled,rule,_=self.service._admitting_policy(name)
        caller=Caller(session_key(),'mc-chat',self.run,'prepare-only',self.instance,name,rule,compiled.version,frozenset(compiled.tools))
        op,refusal=self.service.board.prepare(self.service,OPS[name],{'text':'Never dispatch'},caller)
        self.assertIsNone(refusal)
        self.assertEqual(self.count(),0)
        with self.connect() as conn: binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        self.assertEqual(self.create('Never dispatch')['status'],'refused')
        self.next('Did it save?')
        for _ in range(2):
            out=self.call('minimoi_board_operation_read',{'operation_ref':op})
            self.assertEqual(out['data']['state'],'not_applied',out)
        self.assertEqual(self.count(),0)
        self.assertEqual(self.receipts(),[])

    def test_clarification_from_another_conversation_is_not_authority(self):
        ref=self.question();self.next('Answer')
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_board_slots SET conversation_id='other-conversation' WHERE operation_id=%s",[ref])
        out=self.create('Not authorized continuation',clarification_ref=ref)
        self.assertEqual(out['code'],'board_clarification_expired',out)
        self.assertEqual(self.count(),0)

    def test_stop_during_admitted_transaction_preserves_verified_effect(self):
        from threading import Event
        from minimoi_portal.guild_ui.board_actions import BoardActions
        entered,release=Event(),Event()
        original=BoardActions._effect
        def held(svc,*args,**kwargs):
            entered.set()
            if not release.wait(5): raise RuntimeError('fixture release timeout')
            return original(svc,*args,**kwargs)
        def stop():
            fence.fence(self.ledger_id,1)
            with self.connect() as conn: binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        with patch.object(BoardActions,'_effect',held),ThreadPoolExecutor(max_workers=2) as pool:
            write=pool.submit(self.create,'Saved before Stop completes')
            try:
                self.assertTrue(entered.wait(5))
                stopping=pool.submit(stop)
            finally: release.set()
            out=write.result(5);stopping.result(5)
        self.assertEqual(out['status'],'done',out)
        self.assertEqual(self.count(),1)
        self.next('What saved before Stop?')
        result=self.call('minimoi_board_operation_read',{'operation_ref':'latest'})
        self.assertEqual(result['data']['state'],'verified',result)
        self.assertEqual(self.count(),1)

    def test_runtime_replacement_refuses_board_effect(self):
        self.service.register_instance(uuid4().hex)
        out=self.create('Must not save from old runtime')
        self.assertEqual(out['status'],'refused',out)
        self.assertEqual(self.count(),0)
