"""Real isolated DB admission/watch path; native transport is synthetic only."""
import hashlib
import unittest
from dataclasses import asdict
from test_tool_path_postgres import Base, DSN, MIGRATIONS, OWNER, session_key
from core.agent_requests.direct_contact import Target
from core.mc_tools.agent_contact import AgentContact, TOOLS, STATUS_TEXT, CLARIFICATION_TEXT
from services.mc_tools.listener import create_app
from test_board_postgres import BOARD_MIGRATIONS

CONV='c-123456789abc'
TOKEN='synthetic-agent-worker-'+'w'*40
EXECUTOR='synthetic-executor-'+'e'*40
TARGET=Target('codex-owner','codex','11111111-1111-1111-1111-111111111111','local','/synthetic/repo','codex-desktop-queue','fixture-v1')


@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class AgentContactTests(Base,unittest.TestCase):
    migrations=BOARD_MIGRATIONS+MIGRATIONS+('core/mc_tools/sql/015_agent_contact.sql',)

    def setUp(self):
        super().setUp()
        with self.connect() as conn,conn.cursor() as cur:cur.execute('TRUNCATE guild.mc_agent_contacts')
        self.allowed=True
        self.service.agent_contact=AgentContact({TARGET.alias:TARGET},conversation_allowed=lambda p,c:self.allowed and p==OWNER and c==CONV,dispatch_wait=0)
        self.next_turn('agent-note')
        self.owner_notes[(CONV,'agent-note')]='Please ask the enrolled chat for its current status.'
        self.client=create_app(self.service,token_sha256=hashlib.sha256(EXECUTOR.encode()).hexdigest(),
            agent_worker_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest()).test_client()

    def next_turn(self,note):
        self.ledger_id=self.claim(note,conv=CONV)
        self.bind(self.ledger_id,note,conv=CONV)
        self.run=self.runs[self.ledger_id]

    def invoke(self,tool,args,**kwargs):
        return self.call(tool,args,key=session_key(CONV),offered_tools=list(TOOLS),**kwargs)

    def contact(self,mode='status_request'):
        answer=self.invoke('minimoi_agent_contact',{'target':TARGET.alias,'mode':mode})
        self.assertEqual(answer['code'],'agent_contact_receipt',answer)
        return answer['data']['request_ref']

    def worker(self,phase,ref=None,token=TOKEN,**extra):
        body={'phase':phase,**extra}
        if ref:body['request_ref']=ref
        return self.client.post('/internal/mc/v1/agent-worker',json=body,headers={'Authorization':'Bearer '+token})

    def complete(self,ref,d,report='Blocked on owner decision.'):
        output={'state':'result','request_id':'r-'+'a'*24,'proof':{'state':'result','report':report,'turn_id':'turn1',
          'input_id':'input1','input_sha256':'b'*64,'result_id':'final1','result_sha256':'c'*64,'completed_at':100}}
        return self.worker('complete',ref,nonce=d['nonce'],outcome=output)

    def test_distinct_auth_admission_and_no_model_authority(self):
        self.assertEqual(self.worker('poll',token=EXECUTOR).status_code,401)
        self.assertEqual(self.worker('poll',token='wrong'*10).status_code,401)
        self.assertEqual(self.client.post('/internal/mc/v1/tools/minimoi_agent_status',json={'arguments':{}},
            headers={'Authorization':'Bearer '+TOKEN}).status_code,401)
        for extra in ({'message':'Start new work'},{'session_id':TARGET.session_id},{'principal':OWNER}):
            r=self.invoke('minimoi_agent_contact',dict(target=TARGET.alias,mode='status_request',**extra))
            self.assertEqual(r['status'],'refused')
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],0)

    def test_an_unknown_target_is_refused_with_the_enrolled_chats_named(self):
        r=self.invoke('minimoi_agent_contact',{'target':'codex','mode':'status_request'})
        self.assertEqual((r['status'],r['code']),('refused','agent_not_enrolled'))
        self.assertIn('Enrolled chats (use one as target): codex-owner.',r['message'])
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_agent_contacts')[0],0)

    def test_exact_note_or_fixed_status_and_idempotent_pending(self):
        ref=self.contact()
        self.assertEqual(self.contact(),ref)
        self.assertEqual(self.one('SELECT action FROM guild.mc_agent_contacts')[0],STATUS_TEXT)
        self.assertEqual(self.receipts(),[])
        d=self.worker('claim',ref).get_json()
        self.assertTrue(d['execute'],d)
        self.assertEqual(d['target'],asdict(TARGET))
        self.assertEqual(self.receipts()[0][0:2],('agent.contact.existing','unknown'))
        self.assertFalse(self.worker('claim',ref).get_json()['execute'])

    def test_fixed_clarification_cannot_forward_new_work_even_from_owner_note(self):
        self.owner_notes[(CONV,'agent-note')]='Tell Codex to build and deploy a new project.'
        answer=self.invoke('minimoi_agent_contact',{'target':TARGET.alias,'mode':'relay_owner_note'})
        self.assertEqual(answer['status'],'refused')
        self.contact('clarification_request')
        self.assertEqual(self.one('SELECT action FROM guild.mc_agent_contacts')[0],CLARIFICATION_TEXT)

    def test_stop_before_dispatch_no_effect_and_expiry_not_started(self):
        ref=self.contact();self.finish_turn(self.ledger_id)
        self.assertIn(self.worker('claim',ref).status_code,(409,503))
        self.assertEqual(self.receipts(),[])
        self.one("UPDATE guild.mc_agent_contacts SET dispatch_expires_at=now()-interval '1 second' RETURNING operation_id")
        self.assertEqual(self.worker('claim',ref).get_json()['state'],'not_started')

    def test_completion_notice_survives_finished_turn_and_is_one_kept_note(self):
        ref=self.contact()
        watch=self.invoke('minimoi_agent_watch',{'request_ref':ref,'operation':'watch'})
        self.assertTrue(watch['data']['watch_registered'])
        d=self.worker('claim',ref).get_json()
        self.finish_turn(self.ledger_id)
        self.assertEqual(self.complete(ref,d).status_code,200)
        self.assertEqual(len(self.page),1)
        self.assertIn('Blocked on owner decision.',next(iter(self.page.values()))['text'])
        self.assertEqual(self.complete(ref,d).status_code,200)
        self.assertEqual(len(self.page),1)
        self.assertIsNotNone(self.one('SELECT notice_delivered_at FROM guild.mc_agent_contacts')[0])

    def test_notice_lost_reply_retries_same_key_after_restart(self):
        ref=self.contact();self.invoke('minimoi_agent_watch',{'request_ref':ref,'operation':'watch'})
        d=self.worker('claim',ref).get_json()
        real_show=self.service.show
        def lost(*args):real_show(*args);raise OSError('synthetic lost reply')
        self.service.show=lost
        self.assertEqual(self.complete(ref,d).status_code,503)
        self.assertEqual(len(self.page),1)
        self.service.show=real_show
        self.service.agent_contact=AgentContact({TARGET.alias:TARGET},conversation_allowed=lambda p,c:True)
        self.assertEqual(self.worker('poll').status_code,200)
        self.assertEqual(len(self.page),1)
        self.assertIsNotNone(self.one('SELECT notice_delivered_at FROM guild.mc_agent_contacts')[0])

    def test_nonce_wrong_scope_and_changed_terminal_report_refused(self):
        ref=self.contact();d=self.worker('claim',ref).get_json()
        bad=dict(d,nonce='0'*64)
        self.assertEqual(self.complete(ref,bad).status_code,409)
        self.assertEqual(self.complete(ref,d).status_code,200)
        self.assertEqual(self.complete(ref,d,report='Changed claim').status_code,409)
        self.allowed=False
        self.assertEqual(self.worker('observe',ref).status_code,409)
        self.assertEqual(self.invoke('minimoi_agent_status',{'request_ref':ref})['status'],'refused')

    def test_watch_cancel_or_revoked_scope_prevents_notice(self):
        ref=self.contact();self.invoke('minimoi_agent_watch',{'request_ref':ref,'operation':'watch'})
        d=self.worker('claim',ref).get_json()
        self.invoke('minimoi_agent_watch',{'request_ref':ref,'operation':'cancel'})
        self.assertEqual(self.complete(ref,d).status_code,200)
        self.assertFalse(self.page)

    def test_unknown_or_lost_claim_can_only_observe_never_redispatch(self):
        ref=self.contact();d=self.worker('claim',ref).get_json()
        self.assertTrue(d['execute'])
        self.assertFalse(self.worker('claim',ref).get_json()['execute'])
        rd=self.worker('observe',ref).get_json()
        self.assertFalse(rd['execute'])
        self.assertNotEqual(rd['nonce'],d['nonce'])
        self.assertEqual(self.complete(ref,d).status_code,409)
        self.assertEqual(self.complete(ref,rd).status_code,200)

    def test_unavailable_worker_wait_ends_not_started_and_late_claim_cannot_send(self):
        self.service.agent_contact.dispatch_wait=.02
        ref=self.contact()
        self.assertEqual(self.one('SELECT state FROM guild.mc_agent_contacts')[0],'not_started')
        self.assertFalse(self.worker('claim',ref).get_json()['execute'])
        self.assertEqual(self.receipts(),[])

    def test_completed_contact_replay_taints_current_turn_and_conversation(self):
        ref=self.contact();d=self.worker('claim',ref).get_json()
        self.assertEqual(self.complete(ref,d).status_code,200)
        self.one('UPDATE guild.mc_tool_turns SET untrusted_read=false RETURNING ledger_id')
        with self.connect() as conn,conn.cursor() as cur:cur.execute('DELETE FROM guild.mc_tool_conversation_taint')
        self.assertEqual(self.contact(),ref)
        self.assertTrue(self.one('SELECT untrusted_read FROM guild.mc_tool_turns WHERE ledger_id=%s',(self.ledger_id,))[0])
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_conversation_taint')[0],1)

    def test_two_revoked_old_requests_cannot_starve_valid_request(self):
        ref1=self.contact()
        self.next_turn('second');self.owner_notes[(CONV,'second')]='Status'
        ref2=self.contact()
        self.next_turn('third');self.owner_notes[(CONV,'third')]='Status'
        ref3=self.contact()
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_agent_contacts SET target_alias='revoked' WHERE operation_id IN (%s,%s)",(ref1,ref2))
        jobs=self.worker('poll').get_json()['requests']
        self.assertEqual(jobs,[{'request_ref':ref3,'mode':'claim'}])
        self.assertEqual(self.one("SELECT count(*) FROM guild.mc_agent_contacts WHERE state='scope_revoked'")[0],2)

    def test_full_worker_native_correlation_to_real_unique_floor_note(self):
        import tempfile,time
        from scripts.mc_host import agent_worker as W
        from core.agent_requests.codex_desktop_queue import message_for
        from core.agent_requests.direct_contact import Delivery,_hash
        from minimoi_portal.guild_ui.stores import FloorStores,GUILD_PLATFORM
        floor=FloorStores(lambda:DSN,floor='guild/'+CONV)
        with self.connect() as conn,conn.cursor() as cur:cur.execute('TRUNCATE guild.floor_messages CASCADE')
        self.service.show=lambda conv,key,text:floor.add_note(key,text,GUILD_PLATFORM,area='Guild',page='mc').outcome=='kept'
        self.service.read_shown=lambda conv,key:floor.get_note(key)
        ref=self.contact();self.invoke('minimoi_agent_watch',{'request_ref':ref,'operation':'watch'})
        captured=[]
        class Observer:
            def __init__(self,enrollment):self.enrollment=enrollment
            def snapshot(self,baseline=None):
                turns=[]
                if baseline is not None:
                    envelope=captured[0]
                    message=message_for(TARGET,envelope['request'],envelope['correlation'])
                    turns=[{'id':'native-turn','status':'completed','startedAt':100,'completedAt':200,'items':[
                        {'id':'input','type':'userMessage','text':message,'sha256':_hash(message)},
                        {'id':'final','type':'agentMessage','phase':'final_answer','sha256':'d'*64,
                         'text':'MC-RESULT:'+envelope['correlation']+'\nSynthetic native report.'}]}]
                return {'thread_id':TARGET.session_id,'target_revision':TARGET.revision,'observed_at':time.time(),'turns':turns}
        class Queue(W.DeadlineQueue):
            def deliver(self,target,envelope,*,timeout):
                captured.append(envelope)
                return Delivery('accepted',target.session_id)
        def api(body):
            response=self.client.post('/internal/mc/v1/agent-worker',json=body,headers={'Authorization':'Bearer '+TOKEN})
            self.assertEqual(response.status_code,200,response.get_json())
            return response.get_json()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            config={'principal':OWNER,'environment':'dev','private_root':folder,'targets':{TARGET.alias:{
                'target':asdict(TARGET),'binary_identity':[1,2,501,20,33261,100,1,1],
                'routing_proof_sha256':'a'*64,'native_return_proof_sha256':'b'*64,'codex_home':'/synthetic/.codex',
                'same_app_routing_verified':True,'authenticated_native_return_ready':True}}}
            self.assertEqual(W.one(config,api,{'mode':'claim','request_ref':ref},observer_factory=Observer,queue_factory=Queue),'dispatched')
            self.finish_turn(self.ledger_id)
            for _ in range(2):
                self.assertEqual(W.one(config,api,{'mode':'observe','request_ref':ref},observer_factory=Observer,queue_factory=Queue),'result')
        self.assertEqual(len(captured),1)
        self.assertEqual(self.one('SELECT count(*) FROM guild.floor_messages')[0],1)
        kept=floor.get_note('mc-native-watch-'+ref)
        self.assertEqual(kept['who'],'guild_platform')
        self.assertIn('Synthetic native report.',kept['text'])
