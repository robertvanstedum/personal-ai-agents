"""Real concurrent native read admissions; synthetic DB and external reader only."""
import threading
import time
import unittest
from unittest.mock import patch
from test_tool_path_postgres import Base, DSN, MIGRATIONS, session_key
from core.mc_tools import binding


@unittest.skipUnless(DSN,'requires disposable PostgreSQL')
class ParallelReadTests(Base,unittest.TestCase):
    migrations=MIGRATIONS+('core/mc_tools/sql/013_host_reads.sql',)

    def test_host_and_github_read_share_one_turn_without_lock_upgrade_deadlock(self):
        original=binding.resolve
        first_ready,second_resolved,release=threading.Event(),threading.Event(),threading.Event()
        errors=[];answers={}
        def synchronized(conn,**kwargs):
            result=original(conn,**kwargs)
            if kwargs['tool_call_id']=='parallel_host':
                first_ready.set()
                # Old SHARE locks let the second call resolve, then both upgrade.
                # Correct admission owns the turn's write lock, so this bounded
                # wait expires before the second resolve can return.
                second_resolved.wait(.4);release.set()
            else:
                second_resolved.set();release.wait(2)
            return result
        def call(key,name,args):
            try:
                answers[key]=self.service.call(name,args,session_key=session_key(),agent_id='mc-chat',
                    run_id=self.run,tool_call_id='parallel_'+key,instance_id=self.instance,
                    **({'offered_tools':['minimoi_host_read']} if key=='host' else {}))
            except Exception as exc:errors.append(type(exc).__name__)
        with patch.object(binding,'resolve',synchronized):
            first=threading.Thread(target=call,args=('host','minimoi_host_read',{'path':'/private/tmp/synthetic.txt','limit':20}))
            second=threading.Thread(target=call,args=('github','minimoi_github_issues',{'number':69}))
            first.start();self.assertTrue(first_ready.wait(3));second.start()
            first.join(8);second.join(8)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors,[],f'parallel calls failed: {errors}')
        self.assertEqual(answers['host']['code'],'host_read_admitted')
        self.assertEqual(answers['github']['code'],'read')
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_admissions')[0],2)
        self.assertTrue(self.one('SELECT untrusted_read FROM guild.mc_tool_turns WHERE ledger_id=%s',(self.ledger_id,))[0])
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_conversation_taint')[0],1)
        self.assertEqual(self.service.github.asked,[('issue',69)])

    def test_expiry_is_rechecked_after_waiting_for_turn_lock(self):
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET lease_until=clock_timestamp()+interval '1 second' WHERE id=%s",(self.ledger_id,))
        holder=self.connect();answers=[];errors=[]
        with holder.cursor() as cur:
            cur.execute('SELECT ledger_id FROM guild.mc_tool_turns WHERE ledger_id=%s FOR NO KEY UPDATE',(self.ledger_id,))
        original=self.service._connect
        def connection():
            conn=original()
            with conn.cursor() as cur:cur.execute("SET application_name='parallel_expiry_fixture'")
            return conn
        self.service._connect=connection
        def call():
            try:answers.append(self.call('minimoi_guest_requests'))
            except Exception as exc:errors.append(type(exc).__name__)
        worker=threading.Thread(target=call);worker.start()
        try:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                if self.one("SELECT count(*) FROM pg_stat_activity WHERE application_name='parallel_expiry_fixture' AND wait_event_type='Lock'")[0]:break
                time.sleep(.01)
            else:self.fail('call never waited on held turn lock')
            time.sleep(1.1)
        finally:holder.commit();holder.close();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertEqual(answers[0]['code'],'tool_turn_not_live')
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_admissions')[0],0)

    def test_parallel_same_tool_cannot_exceed_one_call_quota(self):
        from test_policy_postgres import quota
        self.install_policy(change=quota('minimoi_guest_requests',1))
        start=threading.Barrier(2);answers=[];errors=[]
        def call(index):
            try:
                start.wait(2)
                answers.append(self.call('minimoi_guest_requests',call_id='quota_parallel_'+str(index)))
            except Exception as exc:errors.append(type(exc).__name__)
        workers=[threading.Thread(target=call,args=(i,)) for i in range(2)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(5)
        self.assertFalse(any(w.is_alive() for w in workers));self.assertEqual(errors,[])
        self.assertEqual(sum(a['status']=='done' for a in answers),1)
        self.assertEqual(sum(a['code']=='tool_quota_exceeded' for a in answers),1)
        self.assertEqual(self.one('SELECT count(*) FROM guild.mc_tool_admissions')[0],1)

    def test_run_attached_after_empty_lookup_still_holds_the_ledger_lock(self):
        import psycopg2
        from test_tool_path_postgres import OWNER
        with self.connect() as conn,conn.cursor() as cur:
            cur.execute('UPDATE guild.mc_tool_turns SET runtime_run_id=NULL WHERE ledger_id=%s',(self.ledger_id,))
        actual=self.connect();fixture=self;attached=[]
        class Cursor:
            def __init__(self):self.inner=actual.cursor();self.ledger_lookup=False
            def __enter__(self):return self
            def __exit__(self,*args):self.inner.close()
            def execute(self,sql,args=None):
                self.ledger_lookup='SELECT id, claim_generation FROM guild.mc_turn_ledger WHERE id =' in sql
                return self.inner.execute(sql,args)
            def fetchone(self):
                row=self.inner.fetchone()
                if self.ledger_lookup and row is None and not attached:
                    fixture.assertTrue(fixture.attach(fixture.ledger_id,fixture.run));attached.append(True)
                return row
        class Connection:
            def cursor(self):return Cursor()
            def commit(self):actual.commit()
        try:
            binding.resolve(Connection(),session_key=session_key(),agent_id='mc-chat',allowed_agents={'mc-chat'},
                principal=OWNER,environment='dev',run_id=self.run,tool_call_id='attach_race',instance_id=self.instance)
            self.assertEqual(attached,[True])
            with self.connect() as other,other.cursor() as cur:
                with self.assertRaises(psycopg2.errors.LockNotAvailable):
                    cur.execute('SELECT id FROM guild.mc_turn_ledger WHERE id=%s FOR UPDATE NOWAIT',(self.ledger_id,))
                other.rollback()
        finally:actual.rollback();actual.close()
