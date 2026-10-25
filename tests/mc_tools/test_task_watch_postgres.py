"""Disposable DB, synthetic native reader; no live native or owner data."""
import hashlib
import json
import time
import unittest
from copy import deepcopy
from dataclasses import asdict
from test_tool_path_postgres import Base, DSN, MIGRATIONS, OWNER
from test_board_postgres import BOARD_MIGRATIONS
from test_agent_contact_postgres import TARGET, CONV, TOKEN, EXECUTOR
from core.mc_tools.agent_contact import AgentContact
from core.mc_tools.task_watch import TaskWatch
from core.agent_requests.task_watch import encoded, sha, CHUNK
from services.mc_tools.listener import create_app

SID='11111111-1111-4111-8111-111111111111'


@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class TaskWatchTests(Base,unittest.TestCase):
    migrations=BOARD_MIGRATIONS+MIGRATIONS+('core/mc_tools/sql/015_agent_contact.sql','core/mc_tools/sql/016_task_watch.sql')

    def setUp(self):
        super().setUp()
        with self.connect() as conn,conn.cursor() as cur:cur.execute('TRUNCATE guild.mc_agent_contacts,guild.mc_task_subscriptions')
        self.allowed=True
        self.service.agent_contact=AgentContact({TARGET.alias:TARGET},conversation_allowed=lambda p,c:self.allowed and p==OWNER and c==CONV,dispatch_wait=0)
        self.owner_notes[(CONV,'watch-owner-note')]='Notify me when the synthetic build is done.'
        scope={'synthetic':'test only'}
        self.d=dict(subscription_id=SID,principal=OWNER,environment=self.service.environment,conversation_id=CONV,
                    note_request_id='watch-owner-note',owner_note_sha256=hashlib.sha256(self.owner_notes[(CONV,'watch-owner-note')].encode()).hexdigest(),
                    target=asdict(TARGET),scope=scope,spec=dict(task_id='synthetic-build',generation='test-1',scope_sha256=sha(scope),correlation='b'*64),
                    expires_at=time.time()+3600)
        self.entries=[self.d]
        self.service.task_watch=TaskWatch(lambda:deepcopy(self.entries),self.service.agent_contact)
        self.client=create_app(self.service,token_sha256=hashlib.sha256(EXECUTOR.encode()).hexdigest(),
            agent_worker_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest()).test_client()

    def api(self,phase,token=TOKEN,**kwargs):
        body=dict(phase=phase,**kwargs)
        if phase!='poll':body['subscription_ref']=SID
        return self.client.post('/internal/mc/v1/agent-worker',json=body,headers={'Authorization':'Bearer '+token})

    def begin(self):
        self.assertEqual(self.api('poll').json['requests'][0],dict(mode='task_observe',subscription_ref=SID))
        r=self.api('task_begin');self.assertEqual(r.status_code,200,r.json)
        return r.json

    def ready(self):
        d=self.begin();out=self.output(None,'baseline');out['checkpoint']['items']={}
        self.assertEqual(self.commit(d,out).status_code,200)
        self.due()
        return self.begin()

    def output(self,outcome=None,turn='t1'):
        spec=self.d['spec']
        checkpoint=dict(thread_id=TARGET.session_id,revision=TARGET.revision,spec_sha256=sha(spec),
            anchor=dict(id=turn,status='completed',startedAt=100,completedAt=200),items={turn+'-final':None},excluded=[],active=None,batch_head=None,pending_done=None)
        proof=None if outcome is None else dict(report=dict(format='minimoi.task-terminal.v1',**spec,outcome=outcome,evidence_refs=['synthetic:receipt'],remaining='Owner UAT pending.'),
            turn_id=turn,item_id=turn+'-final',item_sha256='c'*64,completed_at=200)
        return dict(checkpoint=checkpoint,proof=proof,state='armed')

    def commit(self,d,output):
        if self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0] is None:
            output['checkpoint']['excluded']=sorted(output['checkpoint']['items'])
        raw=encoded(output)
        for offset in range(0,len(raw),CHUNK):
            r=self.api('task_stage',nonce=d['nonce'],offset=offset,data=raw[offset:offset+CHUNK].decode())
            self.assertEqual(r.status_code,200,r.json)
        return self.api('task_commit',nonce=d['nonce'],sha256=hashlib.sha256(raw).hexdigest())

    def due(self):
        self.one("UPDATE guild.mc_task_subscriptions SET next_observe_at=now()-interval '1 second' RETURNING subscription_id")

    def test_auth_and_observer_only_registration(self):
        self.assertEqual(self.api('poll',token=EXECUTOR).status_code,401)
        self.begin()
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],0)
        self.assertEqual(self.api('claim',request_ref=SID).status_code,409)
        self.assertEqual(self.api('task_begin',action='run arbitrary').status_code,409)

    def test_blocked_refused_stay_armed_then_one_done_notice(self):
        for index,outcome in enumerate(('blocked','refused','ready_for_owner_uat')):
            d=self.ready() if index==0 else self.begin()
            r=self.commit(d,self.output(outcome,'turn'+str(index)))
            self.assertEqual(r.status_code,200,r.json)
            if outcome!='ready_for_owner_uat':
                self.assertEqual(r.json['state'],'armed');self.assertEqual(len(self.shown),0);self.due()
        self.assertEqual(len(self.shown),1)
        self.assertIn('acceptance is pending',self.shown[0][2])
        self.assertEqual(self.shown[0][1],'mc-task-terminal-'+SID)
        self.assertIsNotNone(self.one('SELECT notice_delivered_at FROM guild.mc_task_subscriptions')[0])
        self.api('poll');self.api('poll');self.assertEqual(len(self.shown),1)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_conversation_taint WHERE conversation_id=%s',(CONV,))[0],1)

    def test_checkpoint_and_proof_commit_atomic_and_stale_nonce_refused(self):
        d=self.ready();out=self.output('blocked')
        self.assertEqual(self.commit(d,out).status_code,200)
        row=self.one('SELECT checkpoint,last_proof,checkpoint_version FROM guild.mc_task_subscriptions')
        self.assertEqual(row[0],out['checkpoint']);self.assertEqual(row[1],out['proof']);self.assertEqual(row[2],2)
        self.assertEqual(self.api('task_commit',nonce=d['nonce'],sha256='d'*64).status_code,409)

    def test_chunk_loss_restart_preserves_checkpoint_and_no_native_action(self):
        d=self.begin()
        self.api('task_stage',nonce=d['nonce'],offset=0,data='{"incomplete":')
        self.assertIsNone(self.one('SELECT checkpoint FROM guild.mc_task_subscriptions')[0])
        self.assertEqual(self.api('task_begin').json['state'],'busy')
        self.one("UPDATE guild.mc_task_subscriptions SET challenge_expires_at=now()-interval '1 second' RETURNING subscription_id")
        fresh=self.api('task_begin').json
        self.assertNotEqual(fresh['nonce'],d['nonce'])
        self.assertEqual(self.api('task_chunk',nonce=fresh['nonce'],offset=0).json['data'],'null')
        self.assertEqual(self.api('task_stage',nonce=d['nonce'],offset=0,data='{}').status_code,409)
        self.assertEqual(self.commit(fresh,self.output()).status_code,200)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],0)

    def test_wrong_digest_offset_and_partial_upload_refuse(self):
        d=self.begin()
        self.assertEqual(self.api('task_stage',nonce=d['nonce'],offset=6000,data='{}').status_code,409)
        self.assertEqual(self.api('task_stage',nonce=d['nonce'],offset=0,data='{}').status_code,200)
        self.assertEqual(self.api('task_stage',nonce=d['nonce'],offset=0,data='[]').status_code,409)
        self.assertEqual(self.api('task_commit',nonce=d['nonce'],sha256='d'*64).status_code,409)
        self.assertIsNone(self.one('SELECT checkpoint FROM guild.mc_task_subscriptions')[0])

    def test_revoke_cancel_expiry_scope_and_owner_changed_no_notice(self):
        for change in ('remove','expiry','scope','owner','conversation','target'):
            with self.subTest(change=change):
                with self.connect() as conn,conn.cursor() as cur:cur.execute('TRUNCATE guild.mc_task_subscriptions')
                original=deepcopy(self.d);self.entries=[self.d];self.allowed=True
                self.owner_notes[(CONV,'watch-owner-note')]='Notify me when the synthetic build is done.'
                d=self.begin()
                if change=='remove':self.entries=[]
                elif change=='expiry':self.d['expires_at']=time.time()-1
                elif change=='scope':self.d['scope']={'changed':True};self.d['spec']['scope_sha256']=sha(self.d['scope'])
                elif change=='owner':self.owner_notes[(CONV,'watch-owner-note')]='Changed owner note'
                elif change=='conversation':self.allowed=False
                else:self.d['target']['revision']='changed'
                self.assertEqual(self.api('task_stage',nonce=d['nonce'],offset=0,data='{}').status_code,409)
                self.api('poll');self.assertEqual(len(self.shown),0)
                self.d=original

    def test_lost_note_readback_retries_same_exact_key_not_second_notice(self):
        d=self.ready();read=self.service.read_shown;self.service.read_shown=lambda c,k:None
        self.assertEqual(self.commit(d,self.output('complete')).status_code,200)
        self.assertIsNone(self.one('SELECT notice_delivered_at FROM guild.mc_task_subscriptions')[0])
        self.assertEqual(len(self.shown),1)
        self.service.read_shown=read
        self.api('poll');self.assertEqual(len(self.shown),1)
        self.assertIsNotNone(self.one('SELECT notice_delivered_at FROM guild.mc_task_subscriptions')[0])

    def test_foreign_note_readback_never_delivery(self):
        self.service.read_shown=lambda c,k:{'who':'other','text':'wrong'}
        d=self.ready();self.commit(d,self.output('complete'))
        self.assertIsNone(self.one('SELECT notice_delivered_at FROM guild.mc_task_subscriptions')[0])
        self.assertEqual(len(self.shown),0)

    def test_test_generation_cannot_close_real_spec(self):
        d=self.ready();out=self.output('complete');out['proof']['report']['generation']='real-build'
        r=self.commit(d,out);self.assertEqual(r.status_code,409)
        self.assertEqual(self.one('SELECT state FROM guild.mc_task_subscriptions')[0],'armed')
        self.assertEqual(len(self.shown),0)

    def test_pending_done_is_durable_but_cannot_notify_until_batch_complete(self):
        d=self.ready();candidate=self.output('complete','earlier')
        candidate['checkpoint']['pending_done']=candidate['proof'];candidate['proof']=None
        candidate['checkpoint']['batch_head']='head'
        self.assertEqual(self.commit(d,candidate).status_code,200)
        self.assertEqual(len(self.shown),0)
        self.assertEqual(self.one('SELECT state FROM guild.mc_task_subscriptions')[0],'armed')
        self.due();d=self.begin()
        last=self.output(None,'head');last['proof']=candidate['checkpoint']['pending_done']
        self.assertEqual(self.commit(d,last).status_code,200)
        self.assertEqual(len(self.shown),1)
        initial=self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0]
        self.assertEqual(initial['anchor']['id'],'baseline')

    def test_pending_done_cannot_be_changed_or_silently_dropped(self):
        d=self.ready();candidate=self.output('complete','earlier')
        candidate['checkpoint']['pending_done']=candidate['proof'];candidate['proof']=None
        candidate['checkpoint']['batch_head']='head'
        self.assertEqual(self.commit(d,candidate).status_code,200)
        self.due();d=self.begin()
        self.assertEqual(self.commit(d,self.output(None,'head')).status_code,409)
        self.assertEqual(len(self.shown),0)

    def test_first_callback_cannot_invent_completed_baseline(self):
        d=self.begin()
        self.assertEqual(self.commit(d,self.output('complete')).status_code,409)
        self.assertIsNone(self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0])
        self.assertEqual(len(self.shown),0)

    def test_post_baseline_active_final_is_eligible_in_callback(self):
        d=self.ready()
        active=self.output(None,'baseline')
        active['checkpoint']['items']={}
        active['checkpoint']['active']=dict(id='streaming',startedAt=100,items={'streaming-final':None})
        active['checkpoint']['batch_head']='streaming'
        self.assertEqual(self.commit(d,active).status_code,200)
        self.due();d=self.begin()
        self.assertEqual(self.commit(d,self.output('ready_for_owner_uat','streaming')).status_code,200)
        self.assertEqual(len(self.shown),1)

    def test_original_baseline_exclusion_cannot_be_changed_or_reported(self):
        d=self.begin();baseline=self.output(None,'baseline')
        self.assertEqual(self.commit(d,baseline).status_code,200)
        self.due();d=self.begin()
        out=self.output('complete','baseline')
        out['checkpoint']['excluded']=['baseline-final']
        self.assertEqual(self.commit(d,out).status_code,409)
        self.assertEqual(len(self.shown),0)
        self.assertEqual(self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0]['excluded'],['baseline-final'])

    def test_provisional_anchor_reconciles_without_resetting_registration(self):
        d=self.begin();baseline=self.output(None,'baseline')
        baseline['checkpoint']['anchor'].update(status='interrupted',completedAt=None)
        baseline['checkpoint']['items']={'owner':'a'*64}
        self.assertEqual(self.commit(d,baseline).status_code,200)
        initial=self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0]
        self.due();d=self.begin();out=self.output('ready_for_owner_uat','baseline')
        out['checkpoint']['items']['owner']='a'*64;out['checkpoint']['excluded']=['owner']
        self.assertEqual(self.commit(d,out).status_code,200)
        self.assertEqual(self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0],initial)
        self.assertEqual(len(self.shown),1)

    def test_post_registration_final_seen_provisional_can_complete_same_turn(self):
        d=self.begin();baseline=self.output(None,'baseline')
        baseline['checkpoint']['anchor'].update(status='inProgress',completedAt=None)
        baseline['checkpoint']['items']={}
        self.assertEqual(self.commit(d,baseline).status_code,200)
        self.due();d=self.begin();partial=self.output(None,'baseline')
        partial['checkpoint']['anchor'].update(status='interrupted',completedAt=None)
        self.assertEqual(self.commit(d,partial).status_code,200)
        self.due();d=self.begin()
        self.assertEqual(self.commit(d,self.output('complete','baseline')).status_code,200)
        self.assertEqual(len(self.shown),1)
        self.assertEqual(self.one('SELECT initial_checkpoint FROM guild.mc_task_subscriptions')[0]['items'],{})

    def test_reconciled_same_anchor_cannot_change_user_hash(self):
        d=self.begin();baseline=self.output(None,'baseline')
        baseline['checkpoint']['anchor'].update(status='interrupted',completedAt=None)
        baseline['checkpoint']['items']={'owner':'a'*64}
        self.assertEqual(self.commit(d,baseline).status_code,200)
        self.due();d=self.begin();out=self.output('complete','baseline')
        out['checkpoint']['items']['owner']='b'*64;out['checkpoint']['excluded']=['owner']
        self.assertEqual(self.commit(d,out).status_code,409)
        self.assertEqual(len(self.shown),0)
