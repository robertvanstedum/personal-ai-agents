"""The same authenticated artifact lifecycle, now with exact synthetic binary outputs."""
import base64,os,unittest
import test_host_artifacts_postgres as lifecycle
from test_career_trial_helper import H,KIT
from core.mc_tools.career_trials import prepared

@unittest.skipUnless(KIT and lifecycle.DSN,'requires staged public kit and disposable PostgreSQL')
class CareerLifecycleTests(lifecycle.ArtifactLifecycleTests):
    def setUp(self):
        super().setUp();self.tool='minimoi_career_trial';self.args={'fixture':'synthetic-v1'}

    def helper(self,d,raw,start):
        if d.get('kind')!='career_trial':return super().helper(d,raw,start)
        return H.run({'mode':start['mode'],'operation_id':d['operation_id'],'expires_at':start['expires_at'],
            'payload':base64.b64encode(raw).decode(),'sha256':d['sha256']},self.service.artifact_root,KIT)

    def test_fixed_descriptor_and_server_expected_hashes(self):
        d=self.prepare();self.assertEqual(d['kind'],'career_trial');raw=self.fetch(d);self.assertEqual(raw,prepared()[0])
        start=self.start(d);output=self.helper(d,raw,start);receipt=self.bridge(d,'complete',result=output)
        self.assertEqual(receipt['code'],'artifact_verified');self.assertEqual(len(receipt['data']['files']),14)
        self.assertEqual(receipt['data']['fixture'],'synthetic-v1');self.assertTrue(receipt['data']['page_count'].startswith('not checked'))

    def test_arbitrary_fixture_refused_without_effect(self):
        self.args={'fixture':'real-career'};self.assertEqual(self.invoke()['status'],'refused');self.assertEqual(self.receipts(),[])

    def test_cross_conversation_recovery_cannot_find_operation(self):
        from test_tool_path_postgres import session_key
        d=self.prepare();other='other-career-conversation';ledger=self.claim('other-career-note',conv=other)
        self.bind(ledger,'other-career-note',conv=other)
        result=self.service.call('minimoi_artifact_recover',{'operation_ref':d['operation_id']},
            session_key=session_key(other),agent_id='mc-chat',run_id=self.runs[ledger],tool_call_id='other_recovery',
            instance_id=self.instance,offered_tools=['minimoi_artifact_recover'])
        self.assertEqual(result['code'],'artifact_not_found');self.assertEqual(self.receipts(),[])

    def test_unoffered_trial_is_refused_before_prepare(self):
        from test_tool_path_postgres import session_key
        result=self.service.call(self.tool,self.args,session_key=session_key(),agent_id='mc-chat',run_id=self.run,
            tool_call_id='unoffered',instance_id=self.instance,offered_tools=[])
        self.assertEqual(result['status'],'refused');self.assertEqual(self.receipts(),[])
