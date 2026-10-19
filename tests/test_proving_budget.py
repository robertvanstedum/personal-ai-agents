import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/staging/mc_probe'))
from proving_budget import Budget, BudgetRefused, micro  # noqa: E402

class BudgetTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.b=Budget(Path(self.tmp.name)/'budget.sqlite')
 def tearDown(self): self.tmp.cleanup()
 def test_unknown_remains_counted(self):
  self.b.reserve('request-one',20);self.b.settle('request-one',None,[])
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',6)
 def test_receipt_releases_unused_reservation(self):
  self.b.reserve('request-one',20);self.b.settle('request-one',1,['receipt-one'])
  self.assertTrue(self.b.reserve('request-two',24)['admitted'])
 def test_replay_never_readmits(self):
  self.b.reserve('request-one',2)
  with self.assertRaises(BudgetRefused):self.b.reserve('request-one',2)
 def test_concurrent_admission_is_atomic(self):
  def run(i):
   try:self.b.reserve('request-'+str(i),15);return True
   except BudgetRefused:return False
  with ThreadPoolExecutor(2) as pool: self.assertEqual(sum(pool.map(run,[1,2])),1)
 def test_invalid_costs(self):
  for value in [None,True,float('nan'),float('inf'),-1]:
   with self.assertRaises(BudgetRefused):micro(value)
 def test_fractional_cost_rounds_up_without_undercharging(self):
  self.assertEqual(micro('0.00012345'),124)
  self.assertEqual(micro('0.0000001'),1)
  self.b.reserve('request-one',1)
  self.assertEqual(self.b.settle('request-one','0.00012345',['fractional'])['usd'],0.000124)
 def test_connection_is_closed(self):
  import sqlite3
  with self.b.connect() as db:db.execute('SELECT 1')
  with self.assertRaises(sqlite3.ProgrammingError):db.execute('SELECT 1')
 def test_unresolved_call_crossing_midnight_still_counts(self):
  # 23:58 Chicago, still unresolved at 00:05 the next Chicago day (D23).
  self.b.reserve('request-one',20,at=datetime(2026,10,19,4,58,tzinfo=timezone.utc))
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',6,at=datetime(2026,10,19,5,5,tzinfo=timezone.utc))
 def test_unknown_stays_on_its_own_day_not_every_later_day(self):
  # An unknown outcome is charged for good to the day it was admitted, never to later days.
  for i in range(5):
   self.b.reserve(f'request-day1-{i}',5,at=datetime(2026,10,19,18,tzinfo=timezone.utc));self.b.settle(f'request-day1-{i}',None,[])
  with self.assertRaises(BudgetRefused):self.b.reserve('request-day1-late',0.5,at=datetime(2026,10,19,23,tzinfo=timezone.utc))
  self.assertEqual(self.b.reserve('request-day3',0.5,at=datetime(2026,10,21,18,tzinfo=timezone.utc))['day_spent_before_usd'],0)
 def test_settled_cost_stays_on_admission_day(self):
  self.b.reserve('request-one',20,at=datetime(2026,10,19,4,50,tzinfo=timezone.utc))   # 23:50 Chicago, 18 Oct
  self.b.settle('request-one',20,['receipt-one'])
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',6,at=datetime(2026,10,19,4,55,tzinfo=timezone.utc))
  self.assertTrue(self.b.reserve('request-three',24,at=datetime(2026,10,19,5,30,tzinfo=timezone.utc))['admitted'])
 def test_platform_answer_releases_to_zero(self):
  self.b.reserve('request-one',20)
  out=self.b.settle_platform('request-one',{'status':'answered','backend_kind':'platform','message':'Saved'})
  self.assertEqual(out,{'usd':0.0,'source':'platform_answer','receipt_ids':['platform:request-one']})
  self.assertTrue(self.b.reserve('request-two',25)['admitted'])
  self.assertEqual(self.b.settle_platform('request-one',{'status':'answered','backend_kind':'platform'})['usd'],0.0)
 def test_only_a_platform_answer_releases(self):
  self.b.reserve('request-one',20)
  for answer in [None,{},{'status':'answered','backend_kind':'cos'},{'status':'job','job':{}},
                 {'status':'failed','backend_kind':'platform'},{'status':'answered','backend_kind':'platform','error':'x'}]:
   with self.assertRaises(BudgetRefused):self.b.settle_platform('request-one',answer)
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',6)
  with self.assertRaises(BudgetRefused):self.b.settle_platform('request-missing',{'status':'answered','backend_kind':'platform'})
 def test_platform_release_cannot_override_receipt_settlement(self):
  self.b.reserve('request-one',2);self.b.settle('request-one',1,['receipt-one'])
  with self.assertRaises(BudgetRefused):self.b.settle_platform('request-one',{'status':'answered','backend_kind':'platform'})
 def test_old_ledger_schema_refused(self):
  import sqlite3
  path=Path(self.tmp.name)/'old.sqlite'
  db=sqlite3.connect(path);db.execute('CREATE TABLE reservations (id TEXT PRIMARY KEY, day TEXT NOT NULL, bound INTEGER NOT NULL, charged INTEGER NOT NULL, state TEXT NOT NULL, receipts TEXT)');db.commit();db.close()
  path.chmod(0o600)
  with self.assertRaises(BudgetRefused):Budget(path)
 def test_receipt_cannot_be_reused(self):
  self.b.reserve('request-one',2);self.b.settle('request-one',1,['same'])
  self.b.reserve('request-two',2)
  with self.assertRaises(BudgetRefused):self.b.settle('request-two',1,['same'])
 def test_bound_breach_does_not_release_reservation(self):
  self.b.reserve('request-one',20)
  with self.assertRaises(BudgetRefused):self.b.settle('request-one',21,['receipt-one'])
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',0.1)

if __name__=='__main__':unittest.main()
