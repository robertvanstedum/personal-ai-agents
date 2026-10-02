from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from intelligence_layer.contracts import seal,stable_id,VERSION
from intelligence_layer.service import DevApplication,DevServer


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='intelligence-service-')
        self.base=Path(self.tmp.name).resolve()
        self.token='synthetic-token-only-'+('f'*32)
        credential=self.base/'credential'
        credential.write_text(self.token)
        credential.chmod(0o600)
        self.app=DevApplication(self.base/'store',credential)
        self.server=DevServer(('127.0.0.1',0),self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self,path,body=None,*,auth=True,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        h={'Content-Type':'application/json'}
        if auth:
            h['Authorization']='Bearer '+self.token
        h.update(headers or {})
        conn.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),h)
        response=conn.getresponse()
        raw=response.read()
        result=(response.status,dict(response.getheaders()),raw)
        conn.close()
        return result

    def post(self,path,body={}):
        code,_,raw=self.request(path,body)
        self.assertEqual(code,200,raw)
        return json.loads(raw)

    def test_auth_host_origin_and_body_identity(self):
        self.assertEqual(self.request('/api/status',{},auth=False,headers={'Origin':f'http://127.0.0.1:{self.server.server_port}'})[0],401)
        self.assertEqual(self.request('/api/status',{},headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/status',{},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/search',{'query':'x','topic':'demo','principal':'dev-owner'})[0],400)

    def test_cookie_signin_and_static_ui(self):
        code,headers,_=self.request('/api/login',{'token':self.token},auth=False,headers={'Origin':f'http://127.0.0.1:{self.server.server_port}'})
        self.assertEqual(code,200)
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';')[0]
        code,_,_=self.request('/api/status',{},auth=False,headers={'Cookie':cookie,'Origin':f'http://127.0.0.1:{self.server.server_port}'})
        self.assertEqual(code,200)
        code,headers,raw=self.request('/')
        self.assertEqual(code,200)
        self.assertIn(b'Ask the preserved record',raw)
        self.assertEqual(headers['Cache-Control'],'no-store')

    def test_demo_search_citation_rebuild_idempotency(self):
        self.assertEqual(self.post('/api/demo')['new_records'],4)
        self.assertEqual(self.post('/api/demo')['new_records'],0)
        result=self.post('/api/search',{'query':'citations','topic':'demo'})
        self.assertTrue(result['results'])
        self.assertIn('practice',{r['type'] for r in result['results']})
        c=result['results'][0]['citations'][0]
        self.assertTrue(self.post('/api/resolve',{'citation':c})['source_bytes_verified'])
        self.post('/api/rebuild')
        self.assertEqual(result['results'],self.post('/api/search',{'query':'citations','topic':'demo'})['results'])

    def test_learning_cycle_relations_have_exact_citations(self):
        self.post('/api/demo')
        result=self.post('/api/search',{'query':'outcome','topic':'demo'})
        outcome=next(r for r in result['results'] if r['type']=='outcome')
        relations=self.post('/api/relations',{'start_id':outcome['id'],'topic':'demo'})['relations']
        self.assertEqual(relations[0]['kind'],'outcome_of')
        self.assertTrue(relations[0]['from_citations'])
        self.assertTrue(relations[0]['to_citations'])

    def test_owner_promotion_and_exact_delete_plan(self):
        self.post('/api/demo')
        result=self.post('/api/search',{'query':'graph','topic':'demo'})
        c=result['results'][0]['citations'][0]
        a={k:c[k] for k in ('record_id','record_hash','start','end')}
        proposal=self.post('/api/candidate',{'text':'Retain exact citations','sources':[a],'topic':'demo','type':'decision'})['record']
        self.assertEqual(proposal['authority'],'agent_proposal')
        self.assertEqual(self.request('/api/promote',{'id':proposal['id'],'hash':'0'*64})[0],400)
        approved=self.post('/api/promote',{'id':proposal['id'],'hash':proposal['hash']})['record']
        self.assertEqual(approved['authority'],'owner')
        plan=self.post('/api/delete-plan',{'id':proposal['id']})
        self.assertEqual(self.request('/api/delete',{'id':proposal['id'],'plan_hash':'0'*64})[0],400)
        self.assertEqual(self.post('/api/delete',{'id':proposal['id'],'plan_hash':plan['hash']})['backup_erasure'],'pending')

    def test_topic_denial_and_no_secret_in_error(self):
        code,_,raw=self.request('/api/search',{'query':self.token,'topic':'forbidden'})
        self.assertEqual(code,400)
        self.assertNotIn(self.token.encode(),raw)
        self.assertEqual(self.post('/api/status')['topics'],['demo'])

    def test_restart_preserves_sources_and_receipts(self):
        self.post('/api/demo')
        before=self.app.store.inspect()['generation']
        other=DevApplication(self.base/'store',self.base/'credential')
        self.assertEqual(other.store.inspect()['generation'],before)


if __name__=='__main__':
    unittest.main()
