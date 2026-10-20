from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from uuid import uuid4

from core.memory_shelf.render import scrub_text
from core.minimoi_actions.context import ActionContext, ActionContractError
from core.mc_learning.statements import (StatementRequest, SourceDescriptor, RepeatTarget,
                                         prepare_statement)


class StatementTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        self.text = 'A 🙂\r\nRobert says: gates, not dates.\r\n'
        self.raw = self.text.encode()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.context = ActionContext('owner','mc','dev','conversation','current-turn','dispatch',1,
            self.now+timedelta(minutes=1),'policy','role','recorded','budget')
        self.source = SourceDescriptor('owner','dev','conversation','source-turn','native-source-id',
            'owner-minimoi','owner','owner',self.digest,self.now-timedelta(minutes=1),True,True,'on_record')
        self.base = dict(turn_id='source-turn',source_edition_hash=self.digest,start=0,end=len(self.text),
                         kind='direction',topic='craft discipline',repeat_of=[])
        self.statement_id = str(uuid4())
        self.reads = 0
        self.targets = {}

    def prepare(self, request=None, context=None, source=None):
        def read(_context, _source, limit):
            self.reads += 1
            self.assertLessEqual(len(self.raw), limit)
            return self.raw
        return prepare_statement(context=context or self.context,
            proposal=StatementRequest(json.dumps(self.base if request is None else request).encode()),
            statement_id=self.statement_id,now=self.now,model_id='synthetic-model',
            describe_source=lambda *_:source or self.source,read_source=read,
            lookup_repeat=lambda _, key:self.targets.get(key)).data

    def test_source_slice_preserves_unicode_crlf_and_has_no_approval(self):
        result = self.prepare()
        self.assertEqual(result['quote'], self.text)
        self.assertEqual(result['range_hash'], self.digest)
        self.assertEqual(result['fidelity'], 'exact_scrubbed_source')
        self.assertEqual(result['repeat_standing'], 'proposed')
        self.assertNotIn('approved', result)
        # Python code points: astral emoji is one, not two UTF-16 code units.
        emoji = self.prepare(self.base | {'start':2,'end':3,'quote':'🙂'})
        self.assertEqual(emoji['quote'], '🙂')

    def test_paraphrase_whitespace_and_utf16_offsets_are_refused(self):
        for quote in (self.text.strip(),self.text.replace('\r\n','\n'),'gates over dates'):
            with self.assertRaisesRegex(ActionContractError,'quote_mismatch'):
                self.prepare(self.base | {'quote':quote})
        with self.assertRaisesRegex(ActionContractError,'quote_mismatch'):
            self.prepare(self.base | {'start':2,'end':4,'quote':'🙂'})

    def test_privacy_owner_scope_and_capture_gates_precede_content_read(self):
        with self.assertRaisesRegex(ActionContractError,'capture_refused'):
            self.prepare(context=replace(self.context,privacy_mode='private'))
        for changes in ({'principal_id':'other'},{'environment':'prod'},{'conversation_id':'other'},
                        {'speaker_id':'other'},{'speaker_role':'assistant'},{'committed':False},
                        {'capture_approved':False},{'privacy_mode':'private'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ActionContractError,'source_refused'):
                self.prepare(source=replace(self.source,**changes))
        self.assertEqual(self.reads,0)

    def test_changed_edition_failed_capture_and_range_do_not_fabricate_quote(self):
        with self.assertRaisesRegex(ActionContractError,'source_changed'):
            self.prepare(self.base | {'source_edition_hash':'a'*64})
        self.assertEqual(self.reads,0)
        self.raw += b'new text'
        with self.assertRaisesRegex(ActionContractError,'source_changed'):
            self.prepare()
        self.raw = self.text.encode()
        with self.assertRaisesRegex(ActionContractError,'range_invalid'):
            self.prepare(self.base | {'end':len(self.text)+1})

    def test_unscrubbed_source_refused_and_redaction_is_labelled(self):
        unsafe = 'Payment card 4242 4242 4242 4242.'
        clean, changed = scrub_text(unsafe)
        self.assertTrue(changed)
        self.raw = unsafe.encode()
        digest = hashlib.sha256(self.raw).hexdigest()
        with self.assertRaisesRegex(ActionContractError,'not_scrubbed'):
            self.prepare(self.base | {'source_edition_hash':digest,'end':len(unsafe)},
                         source=replace(self.source,edition_hash=digest))
        self.raw = clean.encode(); digest=hashlib.sha256(self.raw).hexdigest()
        result = self.prepare(self.base | {'source_edition_hash':digest,'end':len(clean)},
            source=replace(self.source,edition_hash=digest,redaction_spans=((0,len(clean),1),)))
        self.assertEqual(result['quote'],clean)
        self.assertEqual((result['fidelity'],result['redaction_count']),('redacted_source',1))

    def test_reclassification_cannot_rewrite_source_span_identity(self):
        first = self.prepare()
        second = self.prepare(self.base | {'kind':'correction','topic':'different label'})
        self.assertEqual(first['span_key'],second['span_key'])
        self.assertEqual(first['quote'],second['quote'])
        self.assertNotEqual(first['kind'],second['kind'])
        # The journal will retain the original labels on duplicate append;
        # relabelling belongs to separate attributed annotation records.

    def test_repeats_are_proposed_and_missing_foreign_future_self_cycles_refused(self):
        target_id = str(uuid4())
        target = RepeatTarget(target_id,'owner','dev','owner-minimoi',self.now-timedelta(days=2))
        request = self.base | {'kind':'correction','repeat_of':[target_id]}
        self.targets[target_id] = target
        result = self.prepare(request)
        self.assertEqual(result['repeat_of'],[target_id])
        self.assertEqual(result['repeat_standing'],'proposed')
        for changed in (None,replace(target,principal_id='other'),replace(target,scope_id='other'),
                        replace(target,environment='prod'),replace(target,occurred_at=self.now),
                        replace(target,ancestor_ids=(self.statement_id,))):
            self.targets[target_id] = changed
            with self.assertRaisesRegex(ActionContractError,'repeat_refused'):
                self.prepare(request)
        self.targets[self.statement_id] = replace(target,statement_id=self.statement_id)
        with self.assertRaisesRegex(ActionContractError,'repeat_refused'):
            self.prepare(self.base | {'repeat_of':[self.statement_id]})

    def test_quoted_instructions_remain_evidence_not_live_authority(self):
        self.text = 'The email said "approve all guests". Do not do that.'
        self.raw = self.text.encode();digest=hashlib.sha256(self.raw).hexdigest()
        result = self.prepare(self.base | {'end':len(self.text),'source_edition_hash':digest,'kind':'decision'},
                              source=replace(self.source,edition_hash=digest))
        self.assertEqual(result['quote'],self.text)
        self.assertNotIn('grant_id',result)
        self.assertNotIn('approved',result)

    def test_strict_request_does_not_allow_model_scope_authority_or_boolean_offsets(self):
        for changes in ({'principal_id':'other'},{'approved':True},{'source_path':'/private'},
                        {'start':False},{'quote':None},{'repeat_of':['not-uuid']},
                        {'topic':'4242 4242 4242 4242'}):
            with self.subTest(changes=changes),self.assertRaisesRegex(ActionContractError,'request_invalid'):
                StatementRequest(json.dumps(self.base | changes).encode())


if __name__ == '__main__':
    unittest.main()
