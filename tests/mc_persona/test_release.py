import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
def module(name):
    s=importlib.util.spec_from_file_location(name,ROOT/'scripts/mc_persona'/f'{name}.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
r=module('release'); log=module('outbound_log')
REL=ROOT/'services/mc_operations/personas/mc-chat/releases/0.1.0'
class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.p=Path(self.t.name);self.w=self.p/'workspace';self.w.mkdir();self.c=self.p/'config.json';self.out=self.p/'candidate'
        for n in (*r.TARGETS,'USER.md','IDENTITY.md','MEMORY.md'):(self.w/n).write_text('original '+n)
        self.tools={'profile':'minimal','fs':{'workspaceOnly':True},'alsoAllow':['read','dir_list','grep','find','minimoi-tools'],'deny':['group:runtime','write','edit','apply_patch','group:sessions','group:memory','gateway','cron','group:web']}
        self.cfg={'agents':{'entries':{'mc-chat':{'workspace':str(self.w),'tools':self.tools,'model':{'primary':'anthropic/claude-sonnet-4-6','fallbacks':[]}},'main':{'model':'unchanged'}}},'secret':'must never be in staged plan'}
        self.c.write_text(json.dumps(self.cfg));self.plan=r.prepare(REL,self.w,self.c,self.out)
    def activate(self):return r.activate(self.out,disposition='owner-waived-for-dev',reviewed=True)
    def test_round_trip_preserves_unrelated_runtime_and_memory(self):
        self.activate();cfg=json.loads(self.c.read_text());self.assertEqual(cfg['agents']['entries']['main'],self.cfg['agents']['entries']['main']);self.assertIn('web_fetch',cfg['agents']['entries']['mc-chat']['tools']['alsoAllow'])
        self.assertIn('Native OpenClaw binding',(self.w/'AGENTS.md').read_text())
        cfg['agents']['entries']['main']['model']='later-unrelated-change';self.c.write_text(json.dumps(cfg))
        r.activate(self.out,rollback=True)
        self.assertEqual((self.w/'AGENTS.md').read_text(),'original AGENTS.md');self.assertEqual((self.w/'MEMORY.md').read_text(),'original MEMORY.md');self.assertEqual(json.loads(self.c.read_text())['agents']['entries']['main']['model'],'later-unrelated-change')
        self.assertNotIn(self.cfg['secret'],(self.out/'plan.json').read_text())
    def test_rollback_preserves_later_memory_changes(self):
        self.activate();(self.w/'MEMORY.md').write_text('new governed memory')
        r.activate(self.out,rollback=True)
        self.assertEqual((self.w/'MEMORY.md').read_text(),'new governed memory')
    def test_model_rollout_and_rollback(self):
        out=self.p/'model-candidate';r.prepare(REL,self.w,self.c,out,model='anthropic/claude-opus-5')
        r.activate(out,disposition='owner-waived-for-dev',reviewed=True)
        self.assertEqual(json.loads(self.c.read_text())['agents']['entries']['mc-chat']['model']['primary'],'anthropic/claude-opus-5')
        r.activate(out,rollback=True)
        self.assertEqual(json.loads(self.c.read_text())['agents']['entries']['mc-chat']['model']['primary'],'anthropic/claude-sonnet-4-6')
    def test_model_drift_refused(self):
        cfg=json.loads(self.c.read_text());cfg['agents']['entries']['mc-chat']['model']['primary']='other';self.c.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'model drift'):self.activate()
    def test_no_disposition_no_activation(self):
        with self.assertRaises(ValueError):r.activate(self.out,reviewed=True)
        self.assertEqual((self.w/'SOUL.md').read_text(),'original SOUL.md')
    def test_live_persona_drift(self):
        (self.w/'AGENTS.md').write_text('another worker')
        with self.assertRaisesRegex(ValueError,'persona drift'):self.activate()
    def test_config_drift(self):
        self.c.write_text(self.c.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'configuration changed'):self.activate()
    def test_artifact_tamper(self):
        (self.out/'after/SOUL.md').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'artifact drift'):self.activate()
    def test_rollback_refuses_intervening_persona_edit(self):
        self.activate();(self.w/'AGENTS.md').write_text('later edit')
        with self.assertRaisesRegex(ValueError,'persona drift'):r.activate(self.out,rollback=True)
    def test_config_write_failure_restores_persona(self):
        original=r.atomic
        def failure(p,raw):
            if Path(p).resolve()==self.c.resolve():raise OSError('injected config failure')
            original(p,raw)
        with patch.object(r,'atomic',failure):
            with self.assertRaises(OSError):self.activate()
        self.assertEqual((self.w/'SOUL.md').read_text(),'original SOUL.md');self.assertEqual(json.loads(self.c.read_text()),self.cfg)
    def test_lock_refuses_concurrent_operator(self):
        (self.w/'.mc-persona-activation.lock').touch()
        with self.assertRaises(FileExistsError):self.activate()
    def test_preserved_user_drift(self):
        (self.w/'USER.md').write_text('updated preference')
        with self.assertRaisesRegex(ValueError,'preserved context drift'):self.activate()
    def test_bad_context_and_budget(self):
        p=self.p/'context.json';p.write_text(json.dumps({'as_of':'2020-01-01T00:00:00+00:00','expires_at':'2020-01-02T00:00:00+00:00','sources':['fixture'],'text':'stale'}))
        with self.assertRaisesRegex(ValueError,'context stale'):r.prepare(REL,self.w,self.c,self.p/'other',p)
    def test_staged_context_expires_before_activation(self):
        plan=json.loads((self.out/'plan.json').read_text());plan['context_expires_at']='2020-01-01T00:00:00+00:00';(self.out/'plan.json').write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError,'staged context expired'):self.activate()
    def test_filesystem_boundary_preserved(self):
        self.tools['fs']['workspaceOnly']=False
        with self.assertRaises(ValueError):r.candidate_tools(self.tools)
class LogTests(unittest.TestCase):
    def test_only_session_assistant_web_calls_no_results_or_private_text(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'db';c=sqlite3.connect(p);c.execute('CREATE TABLE transcript_events(session_id TEXT,seq INT,event_json TEXT,created_at INT)')
            def add(session,seq,role,content):c.execute('INSERT INTO transcript_events VALUES (?,?,?,?)',(session,seq,json.dumps({'message':{'role':role,'content':content}}),1000))
            call={'type':'toolCall','id':'x','name':'web_search','arguments':{'query':'public documentation','private_extra':'must not print'}}
            add('a',1,'assistant',[call,{'type':'text','text':'PRIVATE'}]);add('b',1,'assistant',[call]);add('a',2,'toolResult',[call]);add('a',3,'user',[call]);c.commit();c.close()
            connections=[];original_connect=sqlite3.connect
            def tracked(*args,**kwargs):
                con=original_connect(*args,**kwargs);connections.append(con);return con
            with patch.object(log.sqlite3,'connect',tracked):rows=list(log.calls(p,'a',0))
            with self.assertRaises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['query'],'public documentation');self.assertNotIn('PRIVATE',str(rows));self.assertNotIn('must not print',str(rows));self.assertEqual(list(log.calls(p,'a',2000)),[])
if __name__=='__main__':unittest.main()
