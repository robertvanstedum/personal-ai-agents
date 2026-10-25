"""Version2 is opt-in; legacy receipts and exact message bytes remain authoritative."""
import hashlib,time,unittest
from copy import deepcopy
import test_agent_contact_postgres as A
from test_tool_path_postgres import Base,DSN,OWNER

@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class StatusProtocolTests(Base,unittest.TestCase):
    migrations=A.AgentContactTests.migrations+('core/mc_tools/sql/018_status_reply_protocol.sql',)
    next_turn=A.AgentContactTests.next_turn
    invoke=A.AgentContactTests.invoke
    worker=A.AgentContactTests.worker
    contact=A.AgentContactTests.contact

    def setUp(self):
        super().setUp()
        with self.connect() as c,c.cursor() as cur:cur.execute('TRUNCATE guild.mc_agent_contacts')
        self.allowed=True
        self.service.agent_contact=A.AgentContact({A.TARGET.alias:A.TARGET},conversation_allowed=lambda p,c:self.allowed and p==OWNER and c==A.CONV,dispatch_wait=0,status_protocol_version=2)
        self.next_turn('agent-note');self.owner_notes[(A.CONV,'agent-note')]='Report existing progress.'
        self.client=A.create_app(self.service,token_sha256=hashlib.sha256(A.EXECUTOR.encode()).hexdigest(),agent_worker_token_sha256=hashlib.sha256(A.TOKEN.encode()).hexdigest()).test_client()

    def output(self,d):
        p=d['reply_protocol'];now=time.time()
        return dict(state='status_report',request_id=p['request_id'],proof=dict(state='status_report',observed_at=now,first_observed_at=now-2,
            native_turn_status='inProgress',build_complete=False,native_dispatch_required=False,
            proof=dict(protocol_version=2,request_id=p['request_id'],correlation=p['correlation'],message_sha256=p['message_sha256'],
                       thread_id=A.TARGET.session_id,target_revision=A.TARGET.revision,turn_id='t',input_id='i',input_sha256='a'*64,
                       status_id='s',status_sha256='b'*64,report='Continuing the original authorized build.')))

    def test_new_protocol_persists_exact_message_and_replay(self):
        ref=self.contact();saved=self.one('SELECT reply_protocol FROM guild.mc_agent_contacts')[0]
        self.assertEqual(saved['version'],2)
        self.assertIn('then continue the already-authorized work',saved['message'])
        self.assertEqual(self.contact(),ref)
        self.assertEqual(self.one('SELECT reply_protocol FROM guild.mc_agent_contacts')[0],saved)
        self.assertEqual(self.worker('claim',ref).json['reply_protocol'],saved)

    def test_old_row_replay_after_opt_in_stays_legacy_forever(self):
        self.service.agent_contact.status_protocol_version=1
        ref=self.contact();self.service.agent_contact.status_protocol_version=2
        self.assertEqual(self.contact(),ref)
        self.assertIsNone(self.one('SELECT reply_protocol FROM guild.mc_agent_contacts')[0])
        self.assertNotIn('reply_protocol',self.worker('claim',ref).json)

    def test_status_notice_exact_once_and_not_completion_after_turn_ends(self):
        ref=self.contact();self.invoke('minimoi_agent_watch',dict(request_ref=ref,operation='watch'))
        d=self.worker('claim',ref).json;self.finish_turn(self.ledger_id)
        output=self.output(d)
        self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=output).status_code,200)
        self.assertEqual(len(self.page),1)  # listener inserts/readbacks the retained note
        self.assertEqual(self.service.agent_contact.deliver_notices(self.service),0)
        self.assertEqual(len(self.page),1)
        text=self.one('SELECT notice_text FROM guild.mc_agent_contacts')[0]
        self.assertIn('observed commentary status report',text)
        self.assertNotIn('completed its response',text)
        self.assertEqual(self.worker('poll').json['requests'],[])

    def test_wrong_version_message_target_and_completion_claim_refused(self):
        ref=self.contact();d=self.worker('claim',ref).json
        for field in ('version','message','target','complete','time','native_id'):
            with self.subTest(field=field):
                out=self.output(d)
                if field=='version':out['proof']['proof']['protocol_version']=1
                elif field=='message':out['proof']['proof']['message_sha256']='c'*64
                elif field=='target':out['proof']['proof']['thread_id']='other'
                elif field=='complete':out['proof']['build_complete']=True
                elif field=='time':out['proof']['observed_at']=float('inf')
                else:out['request_id']='r-'+'f'*24
                self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=out).status_code,409)

    def test_v1_cannot_accept_v2_status_and_v2_cannot_accept_v1_final(self):
        ref=self.contact();d=self.worker('claim',ref).json
        out=self.output(d);out['state']='result'
        self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=out).status_code,409)
        self.one('UPDATE guild.mc_agent_contacts SET reply_protocol=NULL RETURNING operation_id')
        self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=self.output(d)).status_code,409)

    def test_cancel_or_conversation_revocation_prevents_status_notice(self):
        ref=self.contact();self.invoke('minimoi_agent_watch',dict(request_ref=ref,operation='watch'))
        d=self.worker('claim',ref).json
        self.invoke('minimoi_agent_watch',dict(request_ref=ref,operation='cancel'))
        self.assertEqual(self.worker('complete',ref,nonce=d['nonce'],outcome=self.output(d)).status_code,200)
        self.assertEqual(self.service.agent_contact.deliver_notices(self.service),0)
