"""Synthetic original-owner authorization; no real source, sender or native call."""
import hashlib
import json
import time
import unittest
from copy import deepcopy
from dataclasses import asdict
from test_tool_path_postgres import Base,DSN,OWNER
import test_agent_contact_postgres as A
CONV=A.CONV
from core.agent_requests.direct_contact import Target
from core.mc_tools.approved_handoff import ApprovedHandoffs,digest

TARGET=Target('claude-career','claude-code','22222222-2222-2222-2222-222222222222','local','/synthetic/repo','claude-fixed-owner-handoff-v1','fixture-v1')

@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class ApprovedHandoffTests(Base,unittest.TestCase):
    migrations=A.AgentContactTests.migrations+('core/mc_tools/sql/017_approved_agent_handoff.sql',)
    next_turn=A.AgentContactTests.next_turn
    invoke=A.AgentContactTests.invoke
    worker=A.AgentContactTests.worker

    def setUp(self):
        super().setUp()
        with self.connect() as conn,conn.cursor() as cur:cur.execute('TRUNCATE guild.mc_agent_contacts')
        self.allowed=True
        self.service.agent_contact=A.AgentContact({A.TARGET.alias:A.TARGET},conversation_allowed=lambda p,c:self.allowed and p==OWNER and c==CONV,dispatch_wait=0)
        self.next_turn('agent-note')
        self.owner_notes[(CONV,'agent-note')]='Please invoke the approved original handoff.'
        self.client=A.create_app(self.service,token_sha256=hashlib.sha256(A.EXECUTOR.encode()).hexdigest(),
            agent_worker_token_sha256=hashlib.sha256(A.TOKEN.encode()).hexdigest()).test_client()
        self.service.agent_contact.targets[TARGET.alias]=TARGET
        text='Ask the existing worker to update the already-authorized fixture spec.'
        self.owner_notes[(CONV,'original-request')]=text
        self.record=dict(version=1,principal=OWNER,environment=self.service.environment,conversation_id=CONV,
            note_request_id='original-request',owner_text=text,owner_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            target=asdict(TARGET),constraints=['Hold planned release; no merge or production.'],expires_at=time.time()+3600)
        self.records={'original-career':self.record}
        self.service.agent_contact.approved_handoffs=ApprovedHandoffs(lambda:deepcopy(self.records))

    def handoff(self):
        return self.invoke('minimoi_agent_contact',{'target':TARGET.alias,'mode':'approved_owner_handoff','approved_handoff_ref':'original-career'})

    def test_original_authority_survives_fresh_invoking_note_without_duplicate(self):
        first=self.handoff();self.assertEqual(first['code'],'agent_contact_receipt',first)
        ref=first['data']['request_ref']
        row=self.one('SELECT source,note_request_id,authorization_sha256 FROM guild.mc_agent_contacts')
        self.assertEqual(row[0]['note_id'],'original-request');self.assertEqual(row[1],'agent-note')
        self.assertEqual(row[2],digest(self.record))
        self.next_turn('later-request');self.owner_notes[(CONV,'later-request')]='Check that original handoff.'
        second=self.handoff()
        self.assertEqual(second['data']['request_ref'],ref)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],1)

    def test_arbitrary_body_missing_ref_wrong_mode_refused(self):
        for args in ({'target':TARGET.alias,'mode':'status_request'},
                     {'target':TARGET.alias,'mode':'approved_owner_handoff'},
                     {'target':TARGET.alias,'mode':'approved_owner_handoff','approved_handoff_ref':'original-career','message':'new work'}):
            self.assertEqual(self.invoke('minimoi_agent_contact',args)['status'],'refused')
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],0)

    def test_original_source_changed_missing_foreign_or_expired_refused(self):
        for change in ('edited','missing','foreign','expired','target'):
            with self.subTest(change=change):
                old=deepcopy(self.record);note=self.owner_notes.get((CONV,'original-request'))
                if change=='edited':self.owner_notes[(CONV,'original-request')]='Different'
                elif change=='missing':self.owner_notes.pop((CONV,'original-request'))
                elif change=='foreign':self.record['conversation_id']='c-ffffffffffff'
                elif change=='expired':self.record['expires_at']=time.time()-1
                else:self.record['target']['revision']='changed'
                self.assertEqual(self.handoff()['status'],'refused')
                self.record.clear();self.record.update(old);self.owner_notes[(CONV,'original-request')]=note
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],0)

    def test_claim_descriptor_and_final_confirmation_bind_current_turn(self):
        ref=self.handoff()['data']['request_ref']
        response=self.worker('claim',ref);self.assertEqual(response.status_code,200,response.json)
        d=response.json
        self.assertEqual(d['approved_handoff']['sha256'],digest(self.record))
        self.assertEqual(d['source']['note_id'],'original-request')
        self.assertEqual(self.worker('confirm',ref,nonce=d['nonce']).json,dict(admitted=True,expires_at=d['expires_at']))
        self.assertEqual(self.worker('confirm',ref,nonce='wrong').status_code,409)
        self.record['constraints'].append('changed')
        self.assertEqual(self.worker('confirm',ref,nonce=d['nonce']).status_code,409)

    def test_owner_stop_before_confirm_prevents_launch_admission(self):
        ref=self.handoff()['data']['request_ref'];d=self.worker('claim',ref).json
        self.finish_turn(self.ledger_id)
        self.assertNotEqual(self.worker('confirm',ref,nonce=d['nonce']).status_code,200)

    def test_claude_reply_is_reply_not_completed_task_and_replay_no_send(self):
        ref=self.handoff()['data']['request_ref'];d=self.worker('claim',ref).json
        proof=dict(state='reply_observed',receipt_uuid='native-receipt',receipt_sha256='a'*64,receipt_offset=100,
            msg_id='native-msg',reply_uuid='native-reply',reply_sha256='b'*64,report='Release hold remains.',
            task_complete=False,clean_native_turn_complete=False,provenance_mode='owned-live-pid-v1')
        outcome=dict(state='result',request_id='r-'+'c'*24,proof=proof)
        self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=outcome).status_code,200)
        self.assertEqual(self.worker('claim',ref).json['execute'],False)
        self.assertEqual(self.handoff()['data']['state'],'result')

    def test_forged_completion_claim_refused(self):
        ref=self.handoff()['data']['request_ref'];d=self.worker('claim',ref).json
        proof=dict(state='reply_observed',receipt_uuid='r',receipt_sha256='a'*64,msg_id='m',reply_sha256='b'*64,
                   report='Claim',task_complete=True,clean_native_turn_complete=True)
        outcome=dict(state='result',request_id='r-'+'c'*24,proof=proof)
        self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=outcome).status_code,409)

    def test_second_registry_alias_cannot_repeat_same_original_owner_note(self):
        first=self.handoff()['data']['request_ref']
        self.records['second-alias']=deepcopy(self.record)
        self.next_turn('alias-note');self.owner_notes[(CONV,'alias-note')]='Check original handoff alias.'
        answer=self.invoke('minimoi_agent_contact',{'target':TARGET.alias,'mode':'approved_owner_handoff','approved_handoff_ref':'second-alias'})
        self.assertEqual(answer['data']['request_ref'],first)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],1)

    def test_revoked_original_authority_does_not_starve_poll(self):
        self.handoff()
        self.owner_notes.pop((CONV,'original-request'))
        response=self.worker('poll')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['requests'],[])
        self.assertEqual(self.one('SELECT state FROM guild.mc_agent_contacts')[0],'scope_revoked')
