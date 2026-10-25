"""Disposable real PostgreSQL + real exclusive writer; no production or owner records."""
import base64
import json
import tempfile
from pathlib import Path
import unittest
from test_tool_path_postgres import Base, DSN, MIGRATIONS, NOTE, CONV, OWNER, session_key
from test_board_postgres import BOARD_MIGRATIONS
from test_host_artifacts import H, note
from core.mc_tools import binding
from minimoi_portal.guild_ui.mc_conversation_export import render
from minimoi_portal.guild_ui.stores import FloorStores, Author


@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class ArtifactLifecycleTests(Base,unittest.TestCase):
    migrations=BOARD_MIGRATIONS+MIGRATIONS+('core/mc_tools/sql/014_host_artifacts.sql',)

    def setUp(self):
        super().setUp()
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('TRUNCATE guild.mc_host_artifacts CASCADE')
            cur.execute('TRUNCATE guild.floor_messages CASCADE')
        self.artifact_tmp=tempfile.TemporaryDirectory(dir='/private/tmp');self.addCleanup(self.artifact_tmp.cleanup)
        self.service.artifact_root=self.artifact_tmp.name
        self.args={'text':'Exact Unicode artifact\n🙂'};self.tool='minimoi_working_write';self.cid='artifact_call'

    def invoke(self,bridge=None):
        return self.service.call(self.tool,self.args,session_key=session_key(),agent_id='mc-chat',run_id=self.run,
            tool_call_id=self.cid,instance_id=self.instance,offered_tools=[self.tool],artifact_request=bridge)

    def prepare(self):
        r=self.invoke();self.assertEqual(r['code'],'artifact_prepared',r);return r['data']['artifact_descriptor']

    def bridge(self,d,phase,**extra):
        return self.invoke({'phase':phase,'challenge_id':d['challenge_id'],'nonce':d['nonce'],**extra})

    def fetch(self,d):
        chunks=[]
        for offset in range(0,d['bytes'],4096):
            r=self.bridge(d,'chunk',offset=offset);self.assertEqual(r['code'],'artifact_chunk',r)
            chunks.append(base64.b64decode(r['data']['chunk']))
        return b''.join(chunks)

    def start(self,d):
        r=self.bridge(d,'start',sha256=d['sha256']);self.assertEqual(r['code'],'artifact_execute',r);return r['data']

    def helper(self,d,raw,start):
        return H.run({'mode':start['mode'],'operation_id':d['operation_id'],'expires_at':start['expires_at'],
            'payload':base64.b64encode(raw).decode(),'sha256':d['sha256']},self.service.artifact_root)

    def expire_effect(self):
        self.one("UPDATE guild.mc_action_receipts SET lease_until=now()-interval '1 second' RETURNING operation_id")

    def recover(self,operation):
        self.next_turn('recover-note');self.tool='minimoi_artifact_recover';self.args={'operation_ref':operation};self.cid='recover_call'
        return self.prepare()

    def test_create_verified_duplicate_never_creates_again(self):
        d=self.prepare();raw=self.fetch(d);self.assertEqual(self.receipts(),[])
        start=self.start(d);self.assertEqual(self.receipts()[0][1],'dispatched')
        out=self.helper(d,raw,start);self.assertTrue(out['verified'])
        r=self.bridge(d,'complete',result=out);self.assertEqual(r['code'],'artifact_verified',r)
        replay=self.bridge(d,'complete',result=out)
        self.assertEqual(replay['code'],r['code'])
        self.assertEqual(replay['data']['files'],r['data']['files'])
        self.assertFalse(replay['data']['fresh_filesystem_check'])
        self.cid='repeat_call';repeat=self.invoke();self.assertEqual(repeat['code'],'artifact_verified')
        self.assertEqual(repeat['data']['operation_id'],d['operation_id'])
        self.assertTrue(r['data']['fresh_filesystem_check'])
        self.assertFalse(repeat['data']['fresh_filesystem_check'])
        self.assertEqual(repeat['data']['verification_provenance'],'retained_receipt')
        self.assertEqual(len(list((Path(self.artifact_tmp.name)/'mc-artifacts').iterdir())),1)
        self.assertEqual(len(self.receipts()),1)

    def test_cached_recovery_is_retained_evidence_not_fresh_filesystem_claim(self):
        d=self.prepare();raw=self.fetch(d);start=self.start(d)
        out=self.helper(d,raw,start);self.bridge(d,'complete',result=out)
        # A later external deletion must not turn cached receipt into a fresh check.
        folder=Path(self.artifact_tmp.name)/'mc-artifacts'/d['operation_id']
        for item in folder.iterdir():item.unlink()
        self.next_turn('cached-recovery');self.tool='minimoi_artifact_recover'
        self.args={'operation_ref':d['operation_id']};self.cid='cached-recovery'
        answer=self.invoke()
        self.assertEqual(answer['code'],'artifact_verified')
        self.assertFalse(answer['data']['fresh_filesystem_check'])
        self.assertIn('No fresh filesystem check',answer['message'])
        self.assertNotIn('artifact_descriptor',answer['data'])
        self.assertEqual(list(folder.iterdir()),[])

    def test_lost_prepare_and_chunk_are_explicitly_not_started(self):
        d=self.prepare();raw=self.fetch(d);self.assertTrue(raw)
        self.cid='repeat';self.assertEqual(self.invoke()['code'],'artifact_not_started')
        self.next_turn('recovery');self.tool='minimoi_artifact_recover';self.args={'operation_ref':d['operation_id']};self.cid='recovery'
        self.assertEqual(self.invoke()['code'],'artifact_not_started')
        self.assertEqual(self.receipts(),[]);self.assertFalse((Path(self.artifact_tmp.name)/'mc-artifacts').exists())

    def test_cancel_during_fetch_prevents_effect(self):
        d=self.prepare();self.fetch(d)
        with self.connect() as conn:binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        self.assertEqual(self.bridge(d,'start',sha256=d['sha256'])['status'],'refused')
        self.assertEqual(self.receipts(),[])

    def test_crash_after_write_recovers_readonly_after_lease(self):
        d=self.prepare();raw=self.fetch(d);start=self.start(d);self.helper(d,raw,start)
        self.service._host_effects.clear() # Process lost its unpersisted Effect handle.
        rd=self.recover(d['operation_id']);rr=self.fetch(rd)
        busy=self.bridge(rd,'start',sha256=rd['sha256']);self.assertEqual(busy['status'],'unknown')
        self.expire_effect();rs=self.start(rd);self.assertEqual(rs['mode'],'verify')
        out=self.helper(rd,rr,rs);self.assertTrue(out['verified'])
        recovered=self.bridge(rd,'complete',result=out);self.assertEqual(recovered['code'],'artifact_verified',recovered)
        self.assertEqual(self.receipts()[0][1],'verified')
        self.assertEqual(len(list((Path(self.artifact_tmp.name)/'mc-artifacts').iterdir())),1)

    def test_missing_after_dispatch_stays_unknown_never_creates_on_recovery(self):
        d=self.prepare();self.fetch(d);self.start(d);self.service._host_effects.clear();self.expire_effect()
        rd=self.recover(d['operation_id']);raw=self.fetch(rd);start=self.start(rd)
        out=self.helper(rd,raw,start);self.assertFalse(out['verified'])
        self.assertEqual(self.bridge(rd,'complete',result=out)['code'],'artifact_unknown')
        self.assertFalse((Path(self.artifact_tmp.name)/'mc-artifacts').exists())

    def test_forged_readback_nonce_chunk_and_cross_turn_refused(self):
        d=self.prepare()
        self.assertEqual(self.bridge(d,'chunk',offset=1)['status'],'refused')
        self.assertEqual(self.bridge(dict(d,nonce='0'*64),'chunk',offset=0)['status'],'refused')
        self.fetch(d);self.start(d)
        self.assertEqual(self.bridge(d,'complete',result={'verified':True,'files':[]})['code'],'artifact_readback_mismatch')
        self.next_turn('different')
        self.assertEqual(self.bridge(d,'complete',result={'verified':False,'files':[]})['status'],'refused')
        self.assertEqual(self.receipts()[0][1],'dispatched')

    def test_export_snapshot_is_frozen_and_not_model_supplied(self):
        captured=[]
        def exporter(conv,note_id):
            captured.append((conv,note_id))
            return render([note(1,'Earlier owner'),note(2,'Reply','agent','master_craftsman'),note(3,'Export',request_id=note_id)],boundary_request=note_id,principal=OWNER)
        exporter.authorize=lambda cid: True
        self.service.export_conversation=exporter;self.tool='minimoi_conversation_export';self.args={}
        d=self.prepare();raw=self.fetch(d);self.assertEqual(captured,[(CONV,NOTE)])
        self.service.export_conversation=lambda *a:(_ for _ in ()).throw(AssertionError('must not recapture'))
        self.service.export_conversation.authorize=lambda cid: True
        start=self.start(d);out=self.helper(d,raw,start)
        r=self.bridge(d,'complete',result=out);self.assertEqual(r['code'],'artifact_verified')
        self.assertEqual(r['data']['note_count'],3);self.assertEqual(r['data']['boundary_note_id'],3)
        files=json.loads(raw)['files'];canonical=json.loads(files[1]['text'])
        self.assertEqual(canonical['notes'][0]['text'],'Earlier owner')
        self.cid='again';self.assertEqual(self.invoke()['code'],'artifact_verified');self.assertEqual(len(captured),1)

    def test_export_scope_changed_after_fetch_refuses_before_effect(self):
        def exporter(conv,note_id):
            return render([note(1,'Export',request_id=note_id)],boundary_request=note_id,principal=OWNER)
        exporter.authorize=lambda cid: True
        self.service.export_conversation=exporter;self.tool='minimoi_conversation_export';self.args={}
        d=self.prepare();self.fetch(d)
        def unavailable(cid):raise ValueError('off_record')
        exporter.authorize=unavailable
        self.assertEqual(self.bridge(d,'start',sha256=d['sha256'])['code'],'export_snapshot_unavailable')
        self.assertEqual(self.receipts(),[])

    def test_cancel_and_policy_swap_allow_only_fresh_readonly_recovery(self):
        d=self.prepare();raw=self.fetch(d);start=self.start(d);self.helper(d,raw,start)
        with self.connect() as conn:binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        self.assertEqual(self.bridge(d,'complete',result={'verified':False,'files':[]})['status'],'refused')
        self.install_policy(revision='replacement-policy')
        self.service._admitting_policy('minimoi_artifact_recover')
        self.expire_effect();self.service._host_effects.clear()
        rd=self.recover(d['operation_id']);raw=self.fetch(rd);start=self.start(rd)
        self.assertEqual(start['mode'],'verify')
        self.assertEqual(self.bridge(rd,'complete',result=self.helper(rd,raw,start))['code'],'artifact_verified')

    def test_chunk_bridge_requires_listener_auth_and_correct_tool(self):
        import hashlib
        from services.mc_tools.listener import create_app
        client=create_app(self.service,token_sha256=hashlib.sha256(('a'*40).encode()).hexdigest()).test_client()
        body={'arguments':self.args,'artifact_request':{'phase':'chunk'},'offered_tools':[self.tool]}
        self.assertEqual(client.post('/internal/mc/v1/tools/'+self.tool,json=body).status_code,401)
        self.assertEqual(client.post('/internal/mc/v1/tools/minimoi_guest_requests',json=body,
                                    headers={'Authorization':'Bearer '+'a'*40}).status_code,400)
        self.assertEqual(self.receipts(),[])

    def test_real_floor_snapshot_boundary_onrecord_and_cross_owner(self):
        floor=FloorStores(lambda:DSN,floor='guild/'+CONV)
        for key,text,author in [('earlier','Earlier',Author(OWNER,'owner','Owner')),('request','Export now',Author(OWNER,'owner','Owner')),('later','Later reply',Author('master_craftsman','agent','MC'))]:
            self.assertEqual(floor.add_note(key,text,author).outcome,'kept')
        rows=floor.export_notes_snapshot('request',OWNER)
        self.assertEqual([r['text'] for r in rows],['Earlier','Export now'])
        with self.assertRaises(Exception):floor.export_notes_snapshot('request','other-owner')
        # Production schema forbids retaining off-record rows at all; the snapshot also filters defensively.
        import psycopg2
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.connect() as conn,conn.cursor() as cur:
                cur.execute("UPDATE guild.floor_messages SET record_mode='off_record' WHERE request_id='earlier'")
        self.assertEqual([r['text'] for r in floor.export_notes_snapshot('request',OWNER)],['Earlier','Export now'])
