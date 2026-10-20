from dataclasses import replace
from datetime import timedelta
import copy
import json
import unittest
from uuid import uuid4

from core.mc_learning.corrections import Annotation, CaptureCoverage, project_corrections
from core.memory_shelf.ulid import new_ulid
from core.minimoi_actions.context import ActionContext
from core.workshop_journal.errors import InvalidInput
from test_statement_journal import NOW, prepared


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW+timedelta(days=4)
        self.start,self.end = NOW-timedelta(days=1),self.now
        self.context = ActionContext('owner','mc','dev','conversation','current-turn','dispatch',1,
            self.now+timedelta(minutes=1),'policy','role','recorded','budget')
        self.coverage = CaptureCoverage(self.start,self.end)
        self.rows, self.annotations, self.receipts = [], [], set()
        self.denied = set()

    def add(self, *, kind='correction', day=0, repeats=(), topic='same topic', scope='robert'):
        data = prepared(turn=f'turn-{len(self.rows)}',now=NOW+timedelta(days=day),kind=kind).data
        data.update(occurred_at=(NOW+timedelta(days=day,minutes=-1)).isoformat(),
                    repeat_of=list(repeats),topic=topic,scope_id=scope)
        self.rows.append(dict(sequence=len(self.rows)+1,payload=data))
        return data['statement_id']

    def annotate(self, target, *, approved=False, kind='correction', repeats=(), confirmed=(),topic='relabelled'):
        source = next(r['payload'] for r in self.rows if r['payload']['statement_id']==target)
        data = dict(schema='mc-statement-annotation/1',annotation_id=new_ulid(),statement_id=target,
            span_key=source['span_key'],base_revision='0'*64,principal_id='owner',environment='dev',scope_id=source['scope_id'],
            kind=kind,topic=topic,repeat_of=list(repeats),confirmed_repeat_of=list(confirmed),
            actor_id='owner' if approved else 'mc',recorded_at=(self.now-timedelta(minutes=1)).isoformat(),
            owner_receipt_id=str(uuid4()) if approved else None)
        annotation = Annotation(len(self.annotations)+1,json.dumps(data).encode())
        self.annotations.append(annotation)
        if approved:
            self.receipts.add((data['owner_receipt_id'],annotation.digest))
        return annotation

    def project(self, **kwargs):
        defaults = dict(context=self.context,rows=self.rows,annotations=self.annotations,
            start=self.start,end=self.end,now=self.now,coverage=self.coverage,
            can_read_statement=lambda _,p:p['statement_id'] not in self.denied,
            verify_owner_annotation=lambda _,p,digest,sequence:(p['owner_receipt_id'],digest) in self.receipts)
        return project_corrections(**(defaults|kwargs))

    def test_same_topic_is_not_a_repeat_and_no_audit_is_unknown(self):
        self.add(); self.add(day=2)
        view = self.project()
        self.assertEqual(view['corrections'],2)
        self.assertEqual(view['confirmed_repeats'],0)
        self.assertEqual(view['provisional_repeats'],0)
        self.assertEqual(view['coverage']['status'],'unavailable')
        self.assertIsNone(view['coverage']['missed_statements'])

    def test_two_days_later_repeat_is_provisional_until_exact_owner_review(self):
        first = self.add()
        second = self.add(day=2,repeats=[first])
        view = self.project()
        self.assertEqual((view['corrections'],view['confirmed_repeats'],view['provisional_repeats']),(2,0,1))
        annotation = self.annotate(second,approved=True,repeats=[first],confirmed=[first])
        view = self.project()
        self.assertEqual((view['confirmed_repeats'],view['provisional_repeats'],view['confirmed_repeat_fraction']),(1,0,0.5))
        self.assertEqual(view['entries'][1]['annotation_id'],annotation.data['annotation_id'])
        self.assertEqual(view['entries'][1]['source']['record_id'],'native-source-id')
        self.assertEqual(self.context.grant_ids,())

    def test_unverified_or_changed_receipt_never_confirms(self):
        first = self.add(); second = self.add(day=1,repeats=[first])
        annotation = self.annotate(second,approved=True,repeats=[first],confirmed=[first])
        self.receipts.clear()
        view = self.project()
        self.assertEqual((view['confirmed_repeats'],view['provisional_repeats']),(0,1))
        self.assertIn('annotation_owner_receipt_unverified',view['warning_codes'])
        self.receipts.add((annotation.data['owner_receipt_id'],annotation.digest))
        self.annotations[0] = Annotation(1,json.dumps(annotation.data|{'topic':'forged approved label'}).encode())
        self.assertEqual(self.project()['confirmed_repeats'],0)

    def test_quote_and_original_labels_survive_relabel_undo_and_provider_switch(self):
        target = self.add(kind='direction')
        original = copy.deepcopy(self.rows)
        self.annotate(target,kind='correction',topic='agent guess')
        self.assertEqual(self.project()['corrections'],0)
        self.assertEqual(len(self.project()['proposed_annotations']),1)
        self.annotate(target,approved=True,kind='correction')
        view = self.project(context=replace(self.context,actor_id='mc-other-provider'))
        self.assertEqual(view['corrections'],1)
        self.assertEqual(view['entries'][0]['original_kind'],'direction')
        self.assertEqual(view['entries'][0]['quote'],original[0]['payload']['quote'])
        self.annotate(target,approved=True,kind='direction')
        self.assertEqual(self.project()['corrections'],0)
        self.assertEqual(self.rows,original)

    def test_multiple_links_count_one_repeat_and_old_target_can_be_outside_window(self):
        one = self.add(); two = self.add(day=1); three = self.add(day=3,repeats=[one,two])
        self.annotate(three,approved=True,repeats=[one,two],confirmed=[one,two])
        start = NOW+timedelta(days=2)
        view = self.project(start=start,coverage=CaptureCoverage(start,self.end))
        self.assertEqual((view['corrections'],view['confirmed_repeats'],view['confirmed_repeat_fraction']),(1,1,1.0))

    def test_invalid_self_future_cross_scope_and_missing_links_are_not_counted(self):
        one = self.add()
        future = self.add(day=2)
        foreign = self.add(scope='mandate:'+new_ulid())
        self.rows[0]['payload']['repeat_of'] = [one,future,foreign,str(uuid4())]
        with self.assertRaises(InvalidInput):
            self.project()
        self.rows[0]['payload']['repeat_of'].remove(one)
        view = self.project()
        self.assertEqual(view['provisional_repeats'],0)
        self.assertIn('repeat_link_invalid',view['warning_codes'])

    def test_cycle_is_withheld_even_with_owner_reviewed_links(self):
        one = self.add(); two = self.add()
        self.annotate(one,approved=True,repeats=[two],confirmed=[two])
        self.annotate(two,approved=True,repeats=[one],confirmed=[one])
        view = self.project()
        self.assertEqual(view['confirmed_repeats'],0)
        self.assertIn('repeat_cycle_or_limit',view['warning_codes'])

    def test_relabelled_target_invalidates_confirmation_and_is_not_silently_replaced(self):
        one = self.add(); two = self.add(day=2,repeats=[one])
        self.annotate(two,approved=True,repeats=[one],confirmed=[one])
        self.annotate(one,approved=True,kind='decision')
        view = self.project()
        self.assertEqual(view['corrections'],1)
        self.assertEqual(view['confirmed_repeats'],0)
        self.assertIn('repeat_link_invalid',view['warning_codes'])

    def test_no_corrections_is_no_data_not_zero_success(self):
        self.add(kind='decision')
        view = self.project()
        self.assertIsNone(view['confirmed_repeat_fraction'])
        self.assertEqual(view['fraction_status'],'no_data')

    def test_revocation_hides_quote_and_counts_and_downgrades_coverage(self):
        one = self.add(); self.denied.add(one)
        self.coverage = CaptureCoverage(self.start,self.end,'complete',1,1,0)
        view = self.project()
        self.assertEqual(view['entries'],[])
        self.assertEqual(view['corrections'],0)
        self.assertEqual(view['coverage']['status'],'partial')
        self.assertNotIn(self.rows[0]['payload']['quote'],json.dumps(view))

    def test_proposed_annotations_do_not_expose_revoked_source_links(self):
        one = self.add(); two = self.add(day=1)
        self.annotate(two,repeats=[one])
        self.denied.add(one)
        view = self.project()
        self.assertEqual(view['proposed_annotations'][0]['repeat_of'],[])
        self.assertNotIn(one,json.dumps(view))

    def test_coverage_window_mismatch_missing_audit_and_invalid_counts_refused(self):
        with self.assertRaises(InvalidInput):
            self.project(coverage=CaptureCoverage(self.start-timedelta(days=1),self.end))
        for kwargs in ({'missed_statements':0},{'eligible_turns':1,'captured_turns':2},
                       {'status':'complete'},{'task_volume':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(InvalidInput):
                CaptureCoverage(self.start,self.end,**kwargs)

    def test_honest_missed_capture_and_task_volume_accompany_ratio(self):
        self.add()
        self.coverage = CaptureCoverage(self.start,self.end,'complete',3,3,0,2,3,7,new_ulid())
        view = self.project()
        self.assertEqual(view['coverage']['status'],'partial')
        self.assertEqual(view['coverage']['missed_statements'],2)
        self.assertEqual(view['coverage']['task_volume'],7)

    def test_foreign_private_malformed_and_duplicate_inputs_refused(self):
        self.add()
        for context in (replace(self.context,principal_id='other'),replace(self.context,environment='prod'),
                        replace(self.context,privacy_mode='private')):
            with self.assertRaises(InvalidInput):
                self.project(context=context)
        with self.assertRaises(InvalidInput):
            self.project(rows=[self.rows[0],self.rows[0]|{'sequence':2}])
        with self.assertRaises(InvalidInput):
            self.project(rows=[{}])

    def test_annotation_is_strict_and_cannot_contain_quote_or_unreviewed_confirmation(self):
        one = self.add(); two = self.add(day=1)
        good = self.annotate(two)
        for changes in ({'quote':'rewritten quote'},{'approved':True},{'confirmed_repeat_of':[one]},
                        {'scope_id':'guest'},{'annotation_id':str(uuid4())}):
            with self.subTest(changes=changes),self.assertRaises(InvalidInput):
                Annotation(1,json.dumps(good.data|changes).encode())


if __name__ == '__main__':
    unittest.main()
