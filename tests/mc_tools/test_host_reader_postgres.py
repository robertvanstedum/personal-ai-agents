"""Real admission, taint, authenticated callback and cross-turn rejection for host reads."""
import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from test_tool_path_postgres import Base, DSN, MIGRATIONS, session_key
from test_host_reader import H
from services.mc_tools.listener import create_app

TOKEN='synthetic-executor-token-'+'x'*32


@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class HostBindingTests(Base,unittest.TestCase):
    migrations=MIGRATIONS+('core/mc_tools/sql/013_host_reads.sql',)

    def setUp(self):
        super().setUp()
        self.client=create_app(self.service,token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest()).test_client()
        self.args={'path':'/private/tmp/synthetic-ordinary.txt','limit':20}
        self.headers={'Authorization':'Bearer '+TOKEN,'X-MC-Session':session_key(),'X-MC-Agent':'mc-chat',
                      'X-MC-Run':self.run,'X-MC-Tool-Call':'host_call','X-MC-Instance':self.instance}

    def post(self,completion=None,**body):
        req={'arguments':self.args,'offered_tools':['minimoi_host_read'],**body}
        if completion is not None:req['host_completion']=completion
        return self.client.post('/internal/mc/v1/tools/minimoi_host_read',json=req,headers=self.headers)

    def descriptor(self):
        response=self.post();self.assertEqual(response.status_code,200)
        r=response.get_json();self.assertEqual(r['code'],'host_read_admitted',r)
        return r['data']['host_descriptor']

    def completion(self,d):
        return {'operation_id':d['operation_id'],'nonce':d['nonce'],
                'result':{'status':'done','code':'host_read','data':{'sha256':'a'*64,'text':'synthetic text','offset':0,'total':14,'next_offset':None,
                'hash_kind':'source_bytes','view':'utf8_text','source_bytes':14}}}

    def test_admit_commits_taint_callback_metadata_no_effect_receipt(self):
        d=self.descriptor()
        self.assertTrue(self.one('SELECT untrusted_read FROM guild.mc_tool_turns WHERE ledger_id=%s',(self.ledger_id,))[0])
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_conversation_taint')[0],1)
        self.assertEqual(self.post().get_json()['code'],'host_read_already_admitted')
        output=self.post(self.completion(d)).get_json()
        self.assertEqual(output['status'],'done');self.assertIn('synthetic text',output['message'])
        self.assertEqual(self.post(self.completion(d)).get_json(),output)
        row=self.one('SELECT row_to_json(r)::text FROM guild.mc_host_reads r')[0]
        self.assertNotIn('synthetic text',row);self.assertNotIn(d['nonce'],row);self.assertNotIn(self.args['path'],row)
        self.assertEqual(self.receipts(),[])

    def test_real_helper_result_returns_only_after_authenticated_completion(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            source=Path(folder)/'ordinary.txt';source.write_text('source outside model workspace')
            self.args={'path':str(source),'limit':10}
            d=self.descriptor()
            output=H.run({'operation':d['operation'],'arguments':d['arguments'],'expires_at':d['expires_at'],
                          'denied_paths':[],'forbidden_values':[TOKEN]})
            self.assertEqual(output['status'],'done')
            c={'operation_id':d['operation_id'],'nonce':d['nonce'],'result':output}
            answer=self.post(c).get_json()
            self.assertEqual(answer['status'],'done',answer)
            self.assertEqual(answer['data']['text'],'source out')
            self.assertEqual(answer['data']['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())

    def test_callback_cannot_add_fields_or_skip_admission(self):
        d=self.descriptor();c=self.completion(d);c['result']['data']['unexpected']='text'
        self.assertEqual(self.post(c).get_json()['code'],'tool_arguments_invalid')
        c=self.completion(d);c['operation_id']='00000000-0000-4000-8000-000000000000'
        self.assertEqual(self.post(c).get_json()['code'],'host_read_callback_refused')
        c=self.completion(d);self.headers['X-MC-Session']=session_key('other-conversation')
        self.assertEqual(self.post(c).get_json()['status'],'refused')

    def test_missing_offered_invalid_callback_and_unauthorized_never_admit(self):
        self.assertEqual(self.post(offered_tools=[]).get_json()['status'],'refused')
        self.headers['Authorization']='Bearer '+'wrong'*12
        self.assertEqual(self.post().status_code,401)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_host_reads')[0],0)

    def test_wrong_nonce_changed_arguments_result_replay_and_expiry(self):
        d=self.descriptor();c=self.completion(d);c['nonce']='b'*64
        self.assertEqual(self.post(c).get_json()['code'],'host_read_callback_refused')
        c=self.completion(d);self.args['path']='/private/tmp/other'
        self.assertEqual(self.post(c).get_json()['code'],'host_read_callback_refused')
        self.args['path']='/private/tmp/synthetic-ordinary.txt'
        self.assertEqual(self.post(c).get_json()['status'],'done')
        c['result']['data']['text']='different text'
        self.assertEqual(self.post(c).get_json()['code'],'host_read_callback_refused')
        self.one("UPDATE guild.mc_host_reads SET expires_at=now()-interval '1 second' RETURNING operation_id")
        self.assertEqual(self.post(self.completion(d)).get_json()['code'],'host_read_callback_refused')

    def test_new_turn_cannot_read_old_callback(self):
        d=self.descriptor();self.next_turn('next-owner-note')
        self.assertEqual(self.post(self.completion(d)).get_json()['status'],'refused')
        self.headers['X-MC-Run']=self.run
        self.assertEqual(self.post(self.completion(d)).get_json()['code'],'host_read_callback_refused')

    def test_stop_and_replaced_instance_refuse_callbacks(self):
        d=self.descriptor();self.finish_turn(self.ledger_id)
        self.assertEqual(self.post(self.completion(d)).get_json()['status'],'refused')
        self.next_turn('new-note');self.headers['X-MC-Run']=self.run
        d=self.descriptor();self.service.register_instance('c'*32)
        self.assertEqual(self.post(self.completion(d)).get_json()['status'],'refused')
