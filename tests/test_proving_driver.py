import importlib.util
import tempfile
import unittest
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('driver', Path(__file__).resolve().parents[1]/'scripts/staging/mc_probe/proving_driver.py')
d=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(d)

class SafetyTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.j=d.Journal(self.temp.name)
  self.c=d.GuildClient('http://127.0.0.1:15001','mc-proving-20261019',self.j,lambda: {'admitted':False})
  self.c.csrf='synthetic';self.c.environment={'environment':'dev'}
 def tearDown(self):self.temp.cleanup()
 def test_production_rejected(self):
  for url in ['https://minimoi.ai','https://dev.minimoi.ai.evil.test','https://dev.minimoi.ai/path','https://user:pass@dev.minimoi.ai','http://127.0.0.1:5001']:
   with self.assertRaises(d.Refused):d.GuildClient(url,'mc-proving-20261019',self.j,None)
 def test_owner_rejected(self):
  with self.assertRaises(d.Refused):d.GuildClient('https://dev.minimoi.ai','robert',self.j,None)
 def test_budget_refusal_prevents_network(self):
  self.c._request=lambda *a: self.fail('network called')
  with self.assertRaises(d.Refused):self.c.write('/mc/turns',{},'test')
  self.assertEqual(list(Path(self.temp.name).glob('*.json')),[])
 def test_ambiguous_write_blocks_next_write(self):
  self.c._request=lambda *a: (_ for _ in ()).throw(TimeoutError())
  with self.assertRaises(TimeoutError):self.c.write('/notes',{},'first')
  self.assertTrue((Path(self.temp.name)/'first-failure.json').exists())
  self.c._request=lambda *a: self.fail('network retried')
  with self.assertRaises(d.Refused):self.c.write('/notes',{},'second')
 def test_error_response_remains_unreconciled(self):
  self.c._request=lambda *a: (500,{'error':'unknown'})
  with self.assertRaises(d.Refused):self.c.write('/notes',{},'first')
  with self.assertRaises(d.Refused):self.j.require_reconciled()
 def test_disallowed_write(self):
  with self.assertRaises(d.Refused):self.c.write('/postits',{},'test')
 def test_environment_required(self):
  self.c._request=lambda *a: self.fail('network called')
  with self.assertRaises(d.Refused):self.c.login('synthetic')
 def test_production_marker_refused(self):
  self.c.instance_id='a'*32
  self.c._request=lambda *a:(200,{'service':'portal','proving':{'environment':'prod','instance_id':'a'*32}})
  with self.assertRaises(d.Refused):self.c.login('synthetic')
 def test_authenticated_wrong_owner(self):
  self.c.verify_environment=lambda:None
  results=iter([(302,None),(200,{'user':{'username':'robert','tier':'owner'},'csrf_token':'x'})])
  self.c._request=lambda *a,**kw:next(results)
  with self.assertRaises(d.Refused):self.c.login('synthetic')
 def test_parallel_writer_refused_before_network(self):
  self.c._request=lambda *a: self.fail('concurrent network write')
  with self.j.exclusive():
   with self.assertRaisesRegex(d.Refused,'another_proving_write'):
    self.c.write('/notes',{},'parallel')
 def test_lock_released_after_failure(self):
  self.c._request=lambda *a: (_ for _ in ()).throw(TimeoutError())
  with self.assertRaises(TimeoutError):self.c.write('/notes',{},'failed')
  with self.j.exclusive():pass
 def test_events_are_exclusive(self):
  self.j.put('first',{})
  with self.assertRaises(FileExistsError):self.j.put('first',{})

if __name__=='__main__':unittest.main()
