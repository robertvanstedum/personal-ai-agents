from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from core.mc_learning.annotation_actions import AnnotationActions, CAPABILITY
from core.mc_learning.annotation_shelf import AnnotationShelf
from core.mc_learning.corrections import Annotation, CaptureCoverage, project_corrections
from core.mc_learning.statement_journal import StatementJournal
from core.memory_shelf.shelf import Shelf
from core.memory_shelf.ulid import new_ulid
from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore
from core.minimoi_actions.receipts import EffectIntent, ReceiptStore
from core.minimoi_actions.requests import ToolRequest
from core.workshop_journal.errors import CommitUnknown
from postgres_fixture_support import DSN, PostgresFixture
from test_statement_journal import NOW, prepared


@unittest.skipUnless(DSN,'requires disposable MC_BINDING_TEST_DSN')
class AnnotationActionPostgresTests(PostgresFixture,unittest.TestCase):
    migrations = ('core/minimoi_actions/sql/004_action_receipts.sql',
                  'core/minimoi_actions/sql/005_action_grants.sql',
                  'core/minimoi_actions/sql/006_owner_annotation_intents.sql')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        self.journal = StatementJournal(self.root,principal_id='owner',environment='dev',scope_id='robert',min_free_bytes=0)
        self.source = prepared(now=self.now-timedelta(minutes=1),kind='direction')
        self.journal.append(self.source)
        self.shelf = AnnotationShelf(Shelf(self.root,min_free_bytes=0),self.journal)
        self.grants = GrantStore({CAPABILITY:CapabilityRule(1,True,True)})
        self.actions = AnnotationActions(self.grants,self.shelf)
        self.receipts = ReceiptStore()
        self.context = ActionContext('owner','mc','dev','conversation','owner-turn','dispatch',1,
            self.now+timedelta(minutes=10),'policy','role','recorded','budget')
        version = self.shelf.snapshot(context=self.context,now=self.now,can_read_statement=lambda *_:True).revisions[self.source.data['statement_id']]
        p = self.source.data
        self.template = dict(schema='mc-statement-annotation/1',annotation_id=new_ulid(),statement_id=p['statement_id'],
            span_key=p['span_key'],principal_id='owner',environment='dev',scope_id='robert',kind='correction',
            topic='whole product scope',repeat_of=[],confirmed_repeat_of=[],actor_id='owner',
            recorded_at=self.now.isoformat(),base_revision=version)
        request = ToolRequest(CAPABILITY,1,json.dumps(self.template).encode())
        self.operation,self.proposal,self.grant_id = str(uuid4()),str(uuid4()),str(uuid4())
        self.intent = EffectIntent(self.operation,request,grant_id=self.grant_id,proposal_id=self.proposal,expected_target_version=version)
        self.grant = GrantDefinition(self.grant_id,'owner','dev','conversation',('owner-turn',),('mc',),'policy',
            CAPABILITY,1,(request.arguments_sha256,),1,self.now+timedelta(minutes=10),proposal_id=self.proposal,
            approved_operation_id=self.operation,approval_expires_at=self.now+timedelta(minutes=5),expected_target_version=version)
        self.context = replace(self.context,grant_ids=(self.grant_id,))
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_action_grant_consumptions,guild.mc_action_grants,'
                        'guild.mc_action_events,guild.mc_action_receipts CASCADE')
            self.grants.issue(conn,self.grant)

    def prepare(self, **kwargs):
        with self.connect() as conn:
            return self.actions.prepare(conn,kwargs.get('context',self.context),kwargs.get('intent',self.intent),current_policy_version='policy')

    def begin(self):
        with self.connect() as conn:
            return self.actions.begin(conn,self.context,self.intent,'worker',current_policy_version='policy')

    def execute(self, lease, **kwargs):
        with self.connect() as conn:
            return self.actions.execute(conn,kwargs.pop('context',self.context),kwargs.pop('intent',self.intent),lease,
                current_policy_version=kwargs.pop('policy','policy'),can_read_statement=kwargs.pop('can_read_statement',lambda *_:True),**kwargs)

    def saved_annotation(self):
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('SELECT payload FROM guild.mc_owner_annotation_intents WHERE operation_id=%s',(self.operation,))
            return Annotation(1,json.dumps(cur.fetchone()[0]).encode())

    def verified(self, context=None, payload=None, sequence=1):
        a = payload or self.saved_annotation()
        with self.connect() as conn:
            return self.actions.verify_completed(conn,context or self.context,a.data,a.digest,sequence)

    def expire_lease(self):
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_action_receipts SET lease_until=clock_timestamp()-interval '1 second'")

    def claim_reconcile(self):
        with self.connect() as conn:
            return self.receipts.claim(conn,'owner','dev',self.operation,'reconciler',mode='reconcile')

    def test_real_grant_file_evidence_receipt_and_projection_roundtrip(self):
        first = self.prepare(); self.assertFalse(self.verified())
        result = self.execute(self.begin())
        self.assertEqual(result['state'],'verified')
        self.assertEqual(result['receipt_id'],first['receipt_id'])
        self.assertTrue(self.verified())
        # prepared() dates the source turn at its fixed NOW (20 Oct 2026), so the window reaches back to it;
        # a window of the last day stopped containing the statement on 21 Oct.
        start = NOW-timedelta(days=1)
        with self.connect() as conn:
            view = project_corrections(context=self.context,rows=self.journal.snapshot(),
                annotations=self.shelf.snapshot(context=self.context,now=datetime.now(timezone.utc),can_read_statement=lambda *_:True).annotations,
                start=start,end=self.now,now=datetime.now(timezone.utc),
                coverage=CaptureCoverage(start,self.now),can_read_statement=lambda *_:True,
                verify_owner_annotation=lambda c,p,d,s:self.actions.verify_completed(conn,c,p,d,s))
        self.assertEqual(view['corrections'],1)
        self.assertEqual(view['entries'][0]['original_kind'],'direction')
        self.assertEqual(self.journal.snapshot()[0]['payload']['quote'],self.source.data['quote'])
        self.assertEqual(self.prepare()['state'],'verified')

    def test_dispatch_must_commit_before_any_file_publication(self):
        self.prepare()
        with self.assertRaisesRegex(ActionContractError,'dispatch_commit_required'),self.connect() as conn:
            lease = self.actions.begin(conn,self.context,self.intent,'worker',current_policy_version='policy')
            self.actions.execute(conn,self.context,self.intent,lease,current_policy_version='policy',can_read_statement=lambda *_:True)
        self.assertFalse((self.root/'sessions-raw').exists())
        with self.connect() as conn:
            self.assertEqual(self.receipts.read(conn,'owner','dev',self.operation)['state'],'prepared')
        self.assertEqual(self.execute(self.begin())['state'],'verified')

    def test_file_before_receipt_crash_stays_unapplied_then_reconciles_without_mutation_or_new_grant(self):
        self.prepare(); lease = self.begin()
        with patch.object(self.actions.receipts,'finish',side_effect=RuntimeError('synthetic crash')):
            with self.assertRaisesRegex(RuntimeError,'crash'):
                self.execute(lease)
        self.assertFalse(self.verified())
        snapshot = self.shelf.snapshot(context=self.context,now=datetime.now(timezone.utc),can_read_statement=lambda *_:True)
        self.assertEqual(len(snapshot.annotations),1)
        self.expire_lease(); recovery = self.claim_reconcile()
        with self.connect() as conn:
            self.grants.revoke(conn,'owner','dev',self.grant_id)
        with patch.object(self.shelf,'publish',side_effect=AssertionError('reconcile must not write')):
            with self.connect() as conn:
                result = self.actions.reconcile(conn,replace(self.context,actor_id='mc-next-provider',grant_ids=()),recovery,can_read_statement=lambda *_:True)
        self.assertEqual(result['state'],'verified'); self.assertTrue(self.verified())
        self.assertEqual(len(self.shelf.snapshot(context=self.context,now=datetime.now(timezone.utc),can_read_statement=lambda *_:True).annotations),1)

    def test_incomplete_file_is_not_completed_or_automatically_redispatched(self):
        self.prepare(); lease = self.begin()
        with patch('core.memory_shelf.shelf.record.write',side_effect=OSError('synthetic crash')):
            with self.assertRaises(CommitUnknown): self.execute(lease)
        self.expire_lease(); recovery = self.claim_reconcile()
        with patch.object(self.shelf,'publish',side_effect=AssertionError('must not retry effect')):
            with self.assertRaisesRegex(ActionContractError,'publication_unverified'),self.connect() as conn:
                self.actions.reconcile(conn,self.context,recovery,can_read_statement=lambda *_:True)
        self.assertFalse(self.verified())
        with self.assertRaisesRegex(ActionContractError,'execution_lease_invalid'):
            self.execute(recovery)

    def test_concurrent_prepare_consumes_once_and_retains_exact_payload_and_evidence(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _:self.prepare(),range(4)))
        self.assertEqual(len({r['receipt_id'] for r in receipts}),1)
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('SELECT consumed_operations FROM guild.mc_action_grants'); self.assertEqual(cur.fetchone()[0],1)
            cur.execute('SELECT count(*) FROM guild.mc_owner_annotation_intents'); self.assertEqual(cur.fetchone()[0],1)

    def test_prepare_rollback_drops_intent_consumption_and_receipt_together(self):
        with self.assertRaises(RuntimeError),self.connect() as conn:
            self.actions.prepare(conn,self.context,self.intent,current_policy_version='policy')
            raise RuntimeError('synthetic crash')
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM guild.mc_owner_annotation_intents'); self.assertEqual(cur.fetchone()[0],0)
            cur.execute('SELECT count(*) FROM guild.mc_action_receipts'); self.assertEqual(cur.fetchone()[0],0)
            cur.execute('SELECT consumed_operations FROM guild.mc_action_grants'); self.assertEqual(cur.fetchone()[0],0)
        self.assertFalse((self.root/'sessions-raw').exists())

    def test_no_exact_owner_approval_forged_receipt_or_private_context_can_prepare(self):
        for intent in (replace(self.intent,proposal_id=None),replace(self.intent,expected_target_version='wrong'),
                       replace(self.intent,request=ToolRequest(CAPABILITY,1,json.dumps(self.template|{'owner_receipt_id':str(uuid4())}).encode()))):
            with self.assertRaises(ActionContractError): self.prepare(intent=intent)
        for change in ({'privacy_mode':'private'},{'principal_id':'other'},{'environment':'prod'},{'grant_ids':()}):
            with self.subTest(change=change),self.assertRaises(ActionContractError):
                self.prepare(context=replace(self.context,**change))
        self.assertFalse((self.root/'sessions-raw').exists())

    def test_revocation_policy_change_cancel_and_expired_lease_prevent_file_effect(self):
        self.prepare(); lease = self.begin()
        with self.assertRaisesRegex(ActionContractError,'policy_changed'):
            self.execute(lease,policy='changed')
        with self.connect() as conn:
            self.grants.revoke(conn,'owner','dev',self.grant_id)
        with self.assertRaisesRegex(ActionContractError,'revoked'):
            self.execute(lease)
        self.assertFalse((self.root/'sessions-raw').exists())

    def test_cancel_after_dispatch_is_not_permission_to_write(self):
        self.prepare(); lease = self.begin()
        with self.connect() as conn:
            self.receipts.cancel(conn,'owner','dev',self.operation)
        with self.assertRaisesRegex(ActionContractError,'dispatch_refused'):
            self.execute(lease)
        self.assertFalse((self.root/'sessions-raw').exists())

    def test_completed_receipt_is_bound_to_exact_payload_principal_and_persisted_evidence(self):
        self.prepare(); self.execute(self.begin()); self.assertTrue(self.verified())
        a = self.saved_annotation()
        self.assertFalse(self.verified(context=replace(self.context,principal_id='other')))
        self.assertFalse(self.verified(context=replace(self.context,environment='prod')))
        self.assertFalse(self.verified(payload=Annotation(1,json.dumps(a.data|{'topic':'different'}).encode())))
        self.assertFalse(self.verified(sequence=2))
        with self.connect() as conn:
            conn.set_session(readonly=True)
            self.assertTrue(self.actions.verify_completed(conn,self.context,a.data,a.digest,1))
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_owner_annotation_intents SET verified_payload_hash=%s",('0'*64,))
        self.assertFalse(self.verified())

    def test_expired_dispatch_lease_requires_reconciliation_and_cannot_write(self):
        self.prepare(); lease = self.begin(); self.expire_lease()
        with self.assertRaisesRegex(ActionContractError,'lease_stale'):
            self.execute(lease)
        self.assertFalse((self.root/'sessions-raw').exists())
        recovery = self.claim_reconcile()
        with self.assertRaisesRegex(ActionContractError,'publication_unverified'),self.connect() as conn:
            self.actions.reconcile(conn,self.context,recovery,can_read_statement=lambda *_:True)


if __name__ == '__main__':
    unittest.main()
