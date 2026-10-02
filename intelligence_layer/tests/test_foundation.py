"""Synthetic acceptance fixtures only; never read sessions or application data."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from intelligence_layer.access import FixtureAuthority, Principal, Verified
from intelligence_layer.adapters import FixtureSessionAdapter, UNAVAILABLE
from intelligence_layer.contracts import VERSION, ContractError, Unavailable, canonical, digest, seal, stable_id, timestamp, validate
from intelligence_layer.retrieval import Retrieval, ModelInterface, VectorInterface, capabilities
from intelligence_layer.store import Store

EARLY='2026-10-01T10:00:00Z'
CAPTURE='2026-10-02T10:00:00Z'
LATE='2026-10-03T10:00:00Z'


def event(index,**updates):
    value=dict(schema_version='minimoi.fixture-session/1.0',synthetic=True,provider='fixture',thread_id='thread-a',
               event_id=f'event-{index}',index=index,author='owner' if index==5 else 'agent',
               origin='owner' if index==5 else 'agent',private=False,event_time=EARLY,text=f'canary_event_{index}',attachments=[])
    return {**value,**updates}


def designation(**updates):
    value=dict(schema_version=VERSION,id=stable_id('designation'),provider='fixture',thread_id='thread-a',topic='topic-a',
               disclosure_class='team',start=5,stop=9,owner_event_id='event-5',designated_by='agent',created_at=CAPTURE,attachments=[])
    return seal({**value,**updates})


def derived(source,kind='candidate',**updates):
    data={'candidate':dict(extractor='fixture',extractor_version='1',model='none'),
          'decision':dict(scope='fixture',supersedes=None,superseded_by=None),
          'practice':dict(version=1,in_force_from=CAPTURE,in_force_to=None,agents_md_ref='fixture://rule')}.get(kind,{})
    value=dict(schema_version=VERSION,id=stable_id([kind,source['id']]),type=kind,created_at=CAPTURE,event_time=EARLY,
               author='agent',disclosure_class=source['disclosure_class'],topics=['topic-a'],sources=[anchor(source)],
               provenance=dict(provider='fixture',thread_id='thread-a',event_id='derived',capture_method='fixture',
                               extractor_version='1',model=None,fidelity='inferred',gaps=['synthetic_evidence_only']),
               text='Proposal: choose amber for explicit reasons',authority='pending' if kind in ('lesson','practice') else 'agent_proposal',data=data)
    return seal({**value,**updates})


def anchor(source):
    return dict(record_id=source['id'],record_hash=source['hash'],start=0,end=len(source['text']))


class Foundation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='intelligence-test-')
        self.base=Path(self.tmp.name).resolve()
        self.auth=FixtureAuthority({'fixture-owner':Principal('owner','owner-only',frozenset({'topic-a','secret-topic'}),True),
                                    'fixture-agent':Principal('agent','owner-local',frozenset({'topic-a'})),
                                    'fixture-team':Principal('team','team',frozenset({'topic-a'}))})
        self.owner=self.auth.authenticate('fixture-owner')
        self.agent=self.auth.authenticate('fixture-agent')
        self.team=self.auth.authenticate('fixture-team')
        self.store=Store(self.base/'vault',self.auth)
        self.store.initialize()
        self.source=self.base/'fixture.jsonl'
        self.events=[event(i) for i in range(13)]
        self.write_events()
        self.query=Retrieval(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def write_events(self):
        self.source.write_bytes(b''.join(canonical(e)+b'\n' for e in self.events))

    def capture(self,d=None,**kwargs):
        d=d or designation()
        return self.store.capture(self.source,d,self.agent,self.auth.issue(self.owner,'designate',d['hash']),captured_at=CAPTURE,**kwargs)

    def records(self):
        with self.store._lock():
            return deepcopy(self.store._load()[0]['records'])

    def first(self):
        self.capture(apply=True)
        return sorted(self.records().values(),key=lambda r:r['data']['event_index'])[0]

    def bytes_in_store(self):
        return b''.join(p.read_bytes() for p in self.store.root.rglob('*') if p.is_file())

    def test_exact_range_source_immutability_and_idempotency(self):
        before=self.source.read_bytes()
        first=self.capture(apply=True)
        self.assertEqual(first['new_records'],4)
        self.assertEqual(self.capture(apply=True)['new_records'],0)
        self.assertEqual(before,self.source.read_bytes())
        data=self.bytes_in_store()
        for i in list(range(5))+list(range(9,13)):
            self.assertNotIn(f'canary_event_{i}"'.encode(),data)
        with self.store._lock():
            state,blobs,manifest=self.store._load()
        self.assertEqual(set(blobs.values()),{canonical(e)+b'\n' for e in self.events[5:9]})
        self.assertEqual(len(manifest['dependencies']),4)
        self.assertTrue(all(r['data']['original_file_hash']==digest(before) for r in state['records'].values()))

    def test_default_capture_dry_run_has_no_writes(self):
        before=self.bytes_in_store()
        self.assertEqual(self.capture()['new_records'],4)
        self.assertEqual(before,self.bytes_in_store())

    def test_private_and_foreign_thread_excluded(self):
        self.events[6]['private']=True
        self.events[7]['thread_id']='other-thread'
        self.write_events()
        self.assertEqual(self.capture(apply=True)['new_records'],2)
        self.assertNotIn(b'canary_event_6',self.bytes_in_store())
        self.assertNotIn(b'canary_event_7',self.bytes_in_store())

    def test_quote_tool_relay_cannot_designate(self):
        for origin in ('quote','tool','relay','agent'):
            self.events[5]['origin']=origin
            self.write_events()
            with self.assertRaises(ContractError):
                self.capture(apply=True)
        self.assertEqual(self.store.inspect()['records'],0)

    def test_designation_widening_requires_new_receipt_and_identity(self):
        d=designation()
        receipt=self.auth.issue(self.owner,'designate',d['hash'])
        changed=seal({**d,'start':0})
        with self.assertRaises(ContractError):
            self.store.capture(self.source,changed,self.agent,receipt,apply=True)
        self.capture(d,apply=True)
        with self.assertRaises(ContractError):
            self.capture(changed,apply=True)

    def test_invalid_version_and_float_fail_without_replacing_snapshot(self):
        self.capture(apply=True)
        original=self.bytes_in_store()
        self.events[6]['schema_version']='future'
        self.write_events()
        with self.assertRaises(ContractError):
            self.capture(apply=True)
        self.assertEqual(original,self.bytes_in_store())
        for bad in (1.25,float('nan'),float('inf'),{1:'x'}):
            with self.assertRaises(ContractError):
                canonical(bad)

    def test_golden_serialization_and_timestamp_contract(self):
        self.assertEqual(canonical({'z':'café','hash':'omit','a':[True,None,12]}),b'{"a":[true,null,12],"hash":"omit","z":"caf\xc3\xa9"}')
        self.assertEqual(seal({'z':'café','a':[True,None,12]})['hash'],'a152d76f2bf76571ed44af0c31df1afbb81f70505a6fdec8ab6cacd8b0ff6e18')
        for value in ('2026-01-01','2026-01-01T00:00:00+00:00','2026-99-01T00:00:00Z'):
            with self.assertRaises(ContractError):
                timestamp(value)

    def test_atomic_failure_leaves_previous_snapshot(self):
        self.capture(apply=True)
        before=self.store.inspect()['generation']
        other=designation(id=stable_id('another'),start=9,stop=10)
        def fail(stage):
            if stage=='before_publish':
                raise RuntimeError('synthetic crash')
        with self.assertRaises(RuntimeError):
            self.capture(other,apply=True,fault=fail)
        self.assertEqual(self.store.inspect()['generation'],before)
        self.assertFalse(list(self.store.root.glob('.pending-*')))
        self.assertNotIn(b'canary_event_9',self.bytes_in_store())

    def test_body_identity_and_forged_receipts_do_not_grant_access(self):
        with self.assertRaises(ContractError):
            self.auth.principal(Verified(self.owner.principal,self.agent.proof))
        with self.assertRaises(ContractError):
            self.auth.issue(self.agent,'designate',designation()['hash'])
        with self.assertRaises(ContractError):
            self.store.capture(self.source,designation(),self.agent,{'principal':'owner'},apply=True)

    def test_exact_citation_and_tamper_rejection(self):
        self.first()
        result=self.query.search('canary_event_5',self.agent,topic='topic-a')
        c=result['results'][0]['citations'][0]
        self.assertEqual(self.query.resolve(c,self.agent)['text'],'canary_event_5')
        for key,value in [('text','fabricated'),('event_id','other'),('record_hash','0'*64),('start',999)]:
            with self.assertRaises(ContractError):
                self.query.resolve({**c,key:value},self.agent)

    def test_event_and_assertion_clocks_both_filter(self):
        self.first()
        self.assertFalse(self.query.search('canary_event_5',self.agent,topic='topic-a',as_of=EARLY)['results'])
        self.assertTrue(self.query.search('canary_event_5',self.agent,topic='topic-a',as_of=CAPTURE)['results'])
        self.assertFalse(self.query.search('canary_event_5',self.agent,topic='topic-a',event_from=CAPTURE)['results'])
        self.assertFalse(self.query.search('canary_event_5',self.agent,topic='topic-a',event_to=EARLY)['results'])

    def test_unknown_event_time_stays_unknown(self):
        self.events[5]['event_time']=None
        self.write_events()
        self.first()
        result=self.query.search('canary_event_5',self.agent,topic='topic-a')
        self.assertIsNone(result['results'][0]['event_time'])
        self.assertFalse(self.query.search('canary_event_5',self.agent,topic='topic-a',as_of=LATE)['results'])

    def test_owner_only_ingestion_disabled_no_canary_in_index(self):
        self.events[5]['text']='OWNER_ONLY_CANARY'
        self.write_events()
        with self.assertRaises(Unavailable):
            self.capture(designation(disclosure_class='owner-only'),apply=True)
        self.query.rebuild()
        self.assertNotIn(b'OWNER_ONLY_CANARY',self.bytes_in_store())

    def test_local_only_route_and_scoped_withheld_counts(self):
        self.capture(designation(disclosure_class='owner-local'),apply=True)
        self.assertTrue(self.query.search('canary_event_5',self.agent,topic='topic-a')['results'])
        cloud=self.query.search('canary_event_5',self.agent,topic='topic-a',route='team-cloud')
        self.assertEqual(cloud['results'],[])
        self.assertEqual(cloud['withheld_in_topic'],4)
        with self.assertRaises(ContractError):
            self.query.search('anything',self.agent,topic='secret-topic')
        self.assertEqual(self.query.search('anything',self.owner,topic='secret-topic')['withheld_in_topic'],0)

    def test_derivative_requires_strictest_class_and_exact_source(self):
        self.capture(designation(disclosure_class='owner-local'),apply=True)
        source=next(iter(self.records().values()))
        r=derived(source,disclosure_class='team')
        with self.assertRaises(ContractError):
            self.store.add_record(r,self.agent)
        r=derived(source)
        self.store.add_record(r,self.agent)
        self.assertEqual(self.records()[r['id']]['disclosure_class'],'owner-local')

    def test_proposals_promotions_and_receipt_revision_binding(self):
        source=self.first()
        r=derived(source,kind='decision')
        self.store.add_record(r,self.agent)
        self.assertEqual(self.query.search('amber',self.agent,topic='topic-a')['results'][0]['authority'],'agent_proposal')
        promoted=derived(source,kind='decision',id=stable_id('promoted'),authority='owner')
        receipt=self.auth.issue(self.owner,'promote',promoted['hash'])
        changed=seal({**promoted,'text':'Changed revision'})
        with self.assertRaises(ContractError):
            self.store.add_record(changed,self.agent,receipt=receipt)
        self.store.add_record(promoted,self.agent,receipt=receipt)
        # Approval today cannot be represented as knowledge available yesterday.
        self.assertNotIn(promoted['id'],[r['id'] for r in self.query.search('amber',self.agent,topic='topic-a',as_of=EARLY)['results']])

    def test_temporal_relations_pending_vs_owner_and_citations(self):
        source=self.first()
        other=derived(source,kind='outcome',text='amber outcome')
        self.store.add_record(other,self.agent)
        edge=seal(dict(schema_version=VERSION,id=stable_id('edge'),kind='outcome_of',from_id=other['id'],to_id=source['id'],
                       author='agent',authority='pending',created_at=LATE,event_time=EARLY,disclosure_class='team'))
        self.store.add_relation(edge,self.agent)
        self.assertEqual(self.query.relations(other['id'],self.agent,topic='topic-a'),[])
        self.assertEqual(self.query.relations(other['id'],self.agent,topic='topic-a',include_pending=True,as_of=CAPTURE),[])
        answer=self.query.relations(other['id'],self.agent,topic='topic-a',include_pending=True,as_of=LATE)
        self.assertEqual(len(answer),1)
        self.assertTrue(answer[0]['to_citations'])

    def test_class_raise_blocks_existing_derivatives_and_old_citations(self):
        source=self.first()
        r=derived(source)
        self.store.add_record(r,self.agent)
        citation=self.query.search('amber',self.team,topic='topic-a')['results'][0]['citations'][0]
        target=digest(canonical(dict(id=source['id'],hash=source['hash'],disclosure_class='owner-local')))
        affected=self.store.change_class(source['id'],'owner-local',self.owner,self.auth.issue(self.owner,'classify',target))
        self.assertIn(r['id'],affected)
        self.assertFalse((self.store.root/'catalog.sqlite').exists())
        self.assertEqual(self.query.search('amber',self.team,topic='topic-a')['results'],[])
        self.assertEqual(self.query.search('amber',self.agent,topic='topic-a',route='team-cloud')['results'],[])
        with self.assertRaises(ContractError):
            self.query.resolve(citation,self.team)

    def test_shared_source_deletion_removes_derivative_preserves_other_source(self):
        source=self.first()
        other=sorted(self.records().values(),key=lambda r:r['data']['event_index'])[1]
        r=derived(source,sources=[anchor(source),anchor(other)],text='shared derivative canary')
        self.store.add_record(r,self.agent)
        self.query.rebuild()
        plan=self.store.deletion_plan(source['id'])
        result=self.store.delete(source['id'],self.owner,self.auth.issue(self.owner,'delete',plan['hash']))
        self.assertEqual(result['backup_erasure'],'pending')
        self.assertNotIn(source['id'],self.records())
        self.assertNotIn(r['id'],self.records())
        self.assertIn(other['id'],self.records())
        for path in plan['paths']:
            self.assertFalse((self.store.root/path).exists())
        self.assertNotIn(b'canary_event_5',self.bytes_in_store())
        self.assertNotIn(b'shared derivative canary',self.bytes_in_store())
        self.query.rebuild()
        self.assertFalse(self.query.search('canary_event_5',self.agent,topic='topic-a')['results'])
        with self.assertRaises(ContractError):
            self.capture(apply=True)

    def test_interrupted_erasure_blocks_retrieval_then_recovers(self):
        source=self.first()
        r=derived(source)
        self.store.add_record(r,self.agent)
        plan=self.store.deletion_plan(source['id'])
        def fail(stage):
            raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError):
            self.store.delete(source['id'],self.owner,self.auth.issue(self.owner,'delete',plan['hash']),fault=fail)
        self.assertEqual(self.query.search('amber',self.agent,topic='topic-a')['results'],[])
        self.store.recover_erasure(self.owner)
        self.assertNotIn(b'canary_event_5',self.bytes_in_store())

    def test_source_corruption_fails_closed(self):
        self.first()
        generation=(self.store.root/'CURRENT').read_text()
        raw=next((self.store.root/'generations'/generation/'blobs').iterdir())
        raw.write_bytes(b'corruption')
        with self.assertRaises(ContractError):
            self.query.search('canary',self.agent,topic='topic-a')

    def test_attachment_is_gap_and_not_followed(self):
        self.events[5]['attachments']=['a'*64]
        self.write_events()
        result=self.capture(designation(attachments=['a'*64]),apply=True)
        self.assertIn('attachments_unavailable',result['gaps'])

    def test_no_network_or_optional_fallback(self):
        with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')):
            self.first()
            self.query.search('canary_event_5',self.agent,topic='topic-a')
            for obj,method in [(ModelInterface(),'generate'),(VectorInterface(),'embed')]:
                with self.assertRaises(Unavailable):
                    getattr(obj,method)('fixture')

    def test_root_and_symlink_boundaries(self):
        with self.assertRaises(ContractError):
            Store('relative',self.auth)
        with self.assertRaises(Unavailable):
            Store('/Users/vanstedum/Projects/mini-moi-private/vault',self.auth)
        link=self.base/'link.jsonl'
        link.symlink_to(self.source)
        with self.assertRaises(ContractError):
            FixtureSessionAdapter().read(link)

    def test_duplicate_capture_paths_deleted_together(self):
        source=self.first()
        self.capture(designation(id=stable_id('duplicate'),start=5,stop=6),apply=True)
        self.assertEqual(sum(r['provenance']['event_id']=='event-5' for r in self.records().values()),2)
        plan=self.store.deletion_plan(source['id'])
        self.assertEqual(len(plan['records']),2)
        self.store.delete(source['id'],self.owner,self.auth.issue(self.owner,'delete',plan['hash']))
        self.assertNotIn(b'canary_event_5',self.bytes_in_store())

    def test_reingest_cannot_downgrade_source_class(self):
        source=self.first()
        target=digest(canonical(dict(id=source['id'],hash=source['hash'],disclosure_class='owner-local')))
        self.store.change_class(source['id'],'owner-local',self.owner,self.auth.issue(self.owner,'classify',target))
        self.capture(designation(id=stable_id('new-designation'),start=5,stop=6),apply=True)
        self.assertEqual(self.query.search('canary_event_5',self.agent,topic='topic-a',route='team-cloud')['results'],[])

    def test_metadata_only_restricted_stub_and_scoped_count(self):
        r=seal(dict(schema_version=VERSION,id=stable_id('restricted-stub'),type='source',created_at=CAPTURE,event_time=EARLY,
                    author='owner',disclosure_class='owner-only',topics=['secret-topic'],sources=[],text='',authority='evidence',
                    data={'stub':True},provenance=dict(provider='restricted-fixture',thread_id='withheld',event_id='withheld',
                    capture_method='stub-only',extractor_version='1',model=None,fidelity='observed',gaps=[])))
        with self.assertRaises(ContractError):
            self.store.add_fixture_stub(seal({**r,'text':'OWNER_ONLY_CANARY'}),self.owner)
        self.store.add_fixture_stub(r,self.owner)
        self.query.rebuild()
        self.assertNotIn(b'OWNER_ONLY_CANARY',self.bytes_in_store())
        self.assertEqual(self.query.search('anything',self.agent,topic='topic-a')['withheld_in_topic'],0)
        self.assertEqual(self.query.search('anything',self.owner,topic='secret-topic')['withheld_in_topic'],1)

    def test_detector_only_tool_inputs_metadata_and_explicit_exception(self):
        from intelligence_layer.detector import inspect_fixture
        calls=[{'tool_name':'read','tool_input':'read /fixture/vault/secret','message':'ignored'},
               {'tool_name':'shell','tool_input':'cat /fixture/mount/x','output':'ignored'},
               {'tool_name':'read','tool_input':'other','message':'/fixture/vault'}]
        results=inspect_fixture(calls,{'/fixture/vault':'vault','/fixture/mount':'mount'},synthetic=True)
        self.assertEqual(len(results),2)
        self.assertNotIn('secret',str(results))
        self.assertNotIn('tool_input',str(results))
        with self.assertRaises(ContractError):
            inspect_fixture(calls,{'/fixture/vault':'vault'})
        calls[0]['trusted_receipt_id']='trusted-fixture-receipt'
        self.assertEqual(len(inspect_fixture(calls,{'/fixture/vault':'vault','/fixture/mount':'mount'},synthetic=True,
                                              trusted_adapter_receipts=['trusted-fixture-receipt'])),1)

    def test_capability_and_source_availability_honest(self):
        self.assertTrue(capabilities()['fts5'])
        self.assertEqual(len(UNAVAILABLE),6)
        self.assertIn('turn_index',UNAVAILABLE['cos_turns'])


if __name__=='__main__':
    unittest.main()
