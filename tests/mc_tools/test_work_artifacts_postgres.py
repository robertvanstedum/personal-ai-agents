"""Bound HTTP listener + real effect receipts for private draft uncertainty/stop."""
import hashlib
import unittest
from types import SimpleNamespace
from test_tool_path_postgres import Base, DSN, OWNER, CONV, NOTE, INSTANCE, session_key
from core.mc_tools import binding
from core.mc_tools.tasks_adapter import TasksAdapter
from services.mc_tools.listener import create_app


@unittest.skipUnless(DSN, 'requires disposable MC_BINDING_TEST_DSN')
class WorkArtifactReceipts(Base, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.owner_notes[(CONV,NOTE)]='Save this private working draft.'
        self.dispatched=[]
        def save(*a,**k):
            self.dispatched.append((a,k))
            return {'status':'saved_readback_unavailable','committed':True,
                    'artifact_id':'wa-'+'a'*32,'revision':1}
        self.service.tasks=TasksAdapter(SimpleNamespace(work_artifacts=save),environment='dev',
                                      card_ref='a'*40,platform=SimpleNamespace(submit=lambda c:{'outcome':'accepted'}))
        token='synthetic-token-'+'a'*32
        self.http=create_app(self.service,token_sha256=hashlib.sha256(token.encode()).hexdigest()).test_client()
        self.headers={'Authorization':'Bearer '+token,'X-MC-Session':session_key(),
                      'X-MC-Agent':'mc-chat','X-MC-Run':self.run,'X-MC-Tool-Call':'work-call-1','X-MC-Instance':INSTANCE}

    def save(self):
        return self.http.post('/internal/mc/v1/tools/minimoi_work_save',headers=self.headers,
                              json={'arguments':{'title':'Draft','content':'Kept draft',
                                                 'expected_revision':0,'unresolved':[]}}).get_json()

    def test_committed_unverified_is_unknown_in_durable_receipt(self):
        answer=self.save()
        self.assertEqual(answer['status'],'unknown',answer)
        self.assertTrue(answer['data']['committed'])
        self.assertEqual(len(self.dispatched),1)
        self.assertEqual([(r[0],r[1]) for r in self.receipts()],[('work.artifact.save','unknown')])

    def test_stop_before_dispatch_records_no_save(self):
        with self.connect() as conn:
            binding.revoke(conn,ledger_id=self.ledger_id,claim_generation=1)
        answer=self.save()
        self.assertEqual(answer['status'],'refused',answer)
        self.assertEqual(self.dispatched,[])
        self.assertEqual(self.receipts(),[])
