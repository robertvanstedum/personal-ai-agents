"""A refused host read says why, so MC can fix the call in one step (Dev check, 14 Nov 2026).

MC followed NOW.md's relative links ("../../operations/...") and paged a folder without the first page's hash; each
call came back as a bare refusal, and MC reported that the file "did not come back". The code stays
tool_arguments_invalid; only the words change. Both refusals happen before the database is touched.
"""
from types import SimpleNamespace
import unittest

from core.mc_tools import host_files


class RefusalWordsTests(unittest.TestCase):
    def admit(self, args, tool='minimoi_host_read'):
        caller = SimpleNamespace(offered_tools={tool}, tool=tool)
        return host_files.admit(None, args, caller)

    def test_relative_or_dotted_path_names_the_fix(self):
        for path in ('initiatives/x/NOW.md', '/minimoi/planning/initiatives/x/../../operations/a.md', '/a/./b', '/a//b'):
            r = self.admit({'path': path})
            self.assertEqual((r['status'], r['code']), ('refused', 'tool_arguments_invalid'), path)
            self.assertIn('absolute path', r['message'])
            self.assertIn('relative link', r['message'])

    def test_offset_without_hash_names_the_fix_for_reads_and_lists(self):
        for tool in ('minimoi_host_read', 'minimoi_host_list'):
            r = self.admit({'path': '/minimoi/planning/initiatives', 'offset': 50}, tool)
            self.assertEqual((r['status'], r['code']), ('refused', 'tool_arguments_invalid'))
            self.assertIn('sha256', r['message'])

    def test_a_tool_not_offered_keeps_the_plain_refusal(self):
        caller = SimpleNamespace(offered_tools=set(), tool='minimoi_host_read')
        r = host_files.admit(None, {'path': 'relative'}, caller)
        self.assertEqual(r['code'], 'tool_arguments_invalid')
        self.assertNotIn('absolute path', r['message'])


if __name__ == '__main__':
    unittest.main()
