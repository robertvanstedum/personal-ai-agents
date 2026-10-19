import unittest
from proving_tool_queue import dispatch, serve
from proving_tools import ToolRefused
class Fake:
 def read_file(self,**kwargs):return kwargs
class QueueTests(unittest.TestCase):
 def request(self,**kwargs):return {'id':'12345678-1234-1234-1234-123456789abc','method':'mc_repo_read','args':{'revision':'baseline','path':'core/foo.py'},**kwargs}
 def test_allowed_read(self):self.assertEqual(dispatch(Fake(),self.request())['revision'],'baseline')
 def test_shell_not_exposed(self):
  with self.assertRaises(ToolRefused):dispatch(Fake(),self.request(method='exec'))
 def test_unknown_arguments_refused(self):
  with self.assertRaises(ToolRefused):dispatch(Fake(),self.request(args={'revision':'baseline','path':'core/foo.py','command':'whoami'}))
 def test_missing_required_refused(self):
  with self.assertRaises(ToolRefused):dispatch(Fake(),self.request(args={'path':'core/foo.py'}))
 def test_queue_completes_once_and_writes_atomic_result(self):
  import tempfile,json
  from pathlib import Path
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);request=self.request();key=request['id']
   (root/(key+'.request')).write_text(json.dumps(request))
   serve(Fake(),root,max_requests=1,max_seconds=1)
   self.assertTrue(json.loads((root/(key+'.response')).read_text())['ok'])
   self.assertTrue((root/(key+'.completed')).exists())
   self.assertFalse(list(root.glob('*.writing')))
 def test_unresolved_request_blocks_worker_restart(self):
  import tempfile
  from pathlib import Path
  with tempfile.TemporaryDirectory() as directory:
   (Path(directory)/'pending.claimed').write_text('{}')
   with self.assertRaisesRegex(ToolRefused,'unresolved_tool_request'):serve(Fake(),directory,max_seconds=.1)
 def test_refusal_and_malformed_input_do_not_strand_later_requests(self):
  import tempfile,json
  from pathlib import Path
  for raw in ('{bad','[]',json.dumps(self.request(method='exec'))):
   with self.subTest(raw=raw),tempfile.TemporaryDirectory() as directory:
    root=Path(directory);first=self.request()['id'];second='22345678-1234-1234-1234-123456789abc'
    (root/(first+'.request')).write_text(raw)
    (root/(second+'.request')).write_text(json.dumps(self.request(id=second)))
    serve(Fake(),root,max_requests=2,max_seconds=1)
    self.assertFalse(json.loads((root/(first+'.response')).read_text())['ok'])
    self.assertTrue(json.loads((root/(second+'.response')).read_text())['ok'])
 def test_tool_oserror_has_definite_response(self):
  import tempfile,json
  from pathlib import Path
  class Broken:
   def read_file(self,**kwargs):raise OSError('private detail')
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);key=self.request()['id']
   (root/(key+'.request')).write_text(json.dumps(self.request()))
   serve(Broken(),root,max_requests=1,max_seconds=1)
   self.assertEqual(json.loads((root/(key+'.response')).read_text()),{'ok':False,'error':'tool_unavailable'})
if __name__=='__main__':unittest.main()

