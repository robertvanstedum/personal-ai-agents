from pathlib import Path
from unittest import TestCase, main
from proving_tools import RepositoryTools, ToolRefused

class ToolsTests(TestCase):
 @classmethod
 def setUpClass(cls):cls.tool=RepositoryTools(Path(__file__).resolve().parents[3],{'baseline':'b884af11'})
 def test_pinned_read_returns_verifiable_source(self):
  value=self.tool.read_file('baseline','minimoi_portal/guild_ui/board_commands.py',1,5)
  self.assertEqual(len(value['revision']),40);self.assertEqual(len(value['sha256']),64)
  self.assertEqual(len(value['lines']),5)
 def test_path_traversal_and_private_data_refused(self):
  for path in ['../../etc/passwd','/etc/passwd','domains/cos/data/private.json','minimoi_portal/auth/users.json','.git/config','core/../data/private.json','core/x;echo.py']:
   with self.assertRaises(ToolRefused):self.tool.read_file('baseline',path)
 def test_unapproved_revision_refused(self):
  with self.assertRaises(ToolRefused):self.tool.read_file('main','core/agent_partner.py')
 def test_window_is_bounded(self):
  for start,count in [(0,3),(True,2),(1,201),(1,-1)]:
   with self.assertRaises(ToolRefused):self.tool.read_file('baseline','core/agent_partner.py',start,count)
 def test_diff_same_revision_has_no_invented_change(self):
  result=self.tool.diff('baseline','baseline','minimoi_portal/guild_ui/board_commands.py')
  self.assertEqual(result['diff'],'');self.assertFalse(result['truncated'])
  self.assertEqual(result['before_sha256'],result['after_sha256'])
 def test_diff_cannot_bypass_source_policy(self):
  with self.assertRaises(ToolRefused):self.tool.diff('baseline','baseline','minimoi_portal/auth/users.json')
  with self.assertRaises(ToolRefused):self.tool.diff('unapproved','baseline','core/agent_partner.py')
 def test_arbitrary_command_cannot_run(self):
  for suite in ['bash','board_commands; id','../../etc/passwd']:
   with self.assertRaises(ToolRefused):self.tool.run_suite(suite)
 def test_runner_cannot_start_without_reviewed_environment(self):
  with self.assertRaises(ToolRefused):self.tool.run_suite('board_commands')

class SnapshotTests(TestCase):
 def test_modified_snapshot_or_symlink_is_refused(self):
  import tempfile,hashlib
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);file=root/'test.py';file.write_text('pass\n')
   tool=RepositoryTools(Path(__file__).resolve().parents[3],{'baseline':'b884af11'},snapshot=root)
   tool.snapshot_manifest={'commit':tool.revisions['baseline'],'files':{'test.py':hashlib.sha256(file.read_bytes()).hexdigest()}}
   self.assertEqual(tool.verify_snapshot(),tool.revisions['baseline'])
   file.write_text('raise Exception()\n')
   with self.assertRaisesRegex(ToolRefused,'snapshot_changed'):tool.verify_snapshot()
   file.unlink();file.symlink_to('/etc/passwd')
   with self.assertRaisesRegex(ToolRefused,'snapshot_symlink'):tool.verify_snapshot()

if __name__=='__main__':main()
