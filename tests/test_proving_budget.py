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
 def test_uncertain_prior_day_carried_forward(self):
  self.b.reserve('request-one',20,at=datetime(2026,10,18,tzinfo=timezone.utc))
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',6,at=datetime(2026,10,19,tzinfo=timezone.utc))
 def test_receipt_cannot_be_reused(self):
  self.b.reserve('request-one',2);self.b.settle('request-one',1,['same'])
  self.b.reserve('request-two',2)
  with self.assertRaises(BudgetRefused):self.b.settle('request-two',1,['same'])
 def test_bound_breach_does_not_release_reservation(self):
  self.b.reserve('request-one',20)
  with self.assertRaises(BudgetRefused):self.b.settle('request-one',21,['receipt-one'])
  with self.assertRaises(BudgetRefused):self.b.reserve('request-two',0.1)

if __name__=='__main__':unittest.main()
