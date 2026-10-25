"""Disposable ordinary host files outside the model workspace; no owner files or providers."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fixed_host_inspector', ROOT / 'scripts/mc_host/inspect.py')
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class HostReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=('/private/tmp' if os.path.isdir('/private/tmp') else None))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def read(self, name='ordinary.txt', op='read', **args):
        return H.run({'operation':op,'arguments':{'path':str(self.root / name),**args},
                      'expires_at':time.time()+20,'denied_paths':[], 'forbidden_values':['actual-executor-value']})

    def test_utf8_pages_have_whole_source_hash_and_reject_changed_source(self):
        p=self.root/'ordinary.txt';p.write_text('é🙂hello\n'*800)
        a=self.read(limit=7);self.assertEqual(a['status'],'done');d=a['data']
        self.assertEqual(d['sha256'],hashlib.sha256(p.read_bytes()).hexdigest())
        b=self.read(offset=d['next_offset'],limit=7,sha256=d['sha256'])
        self.assertEqual(d['text']+b['data']['text'],p.read_text()[:14])
        self.assertEqual(self.read(offset=1)['code'],'host_page_hash_required')
        p.write_text('changed')
        self.assertEqual(self.read(offset=1,sha256=d['sha256'])['code'],'host_source_changed')

    def test_secret_detection_is_whole_file_before_paging(self):
        for content in ['x'*3000+'\nAPI_TOKEN=abcdefghijk', '-----BEGIN OPENSSH PRIVATE KEY-----',
                        'password: xyz', '{"client_secret":"syntheticvalue"}', '{"nested":{"api_key":"value"}}', 'prefix actual-executor-value suffix', 'ghp_'+'a'*30]:
            with self.subTest(content_type=content[:10]):
                (self.root/'ordinary.txt').write_text(content)
                r=self.read(limit=1)
                self.assertEqual(r,{'status':'refused','code':'host_secret_detected','data':{}})

    def test_paths_and_special_files(self):
        p=self.root/'ordinary.txt';p.write_text('ordinary')
        (self.root/'alias').symlink_to(p)
        (self.root/'diralias').symlink_to(self.root, target_is_directory=True)
        for path in ['alias','diralias/ordinary.txt','../ordinary.txt','.env','credentials.json']:
            self.assertEqual(self.read(path)['status'],'refused',path)
        os.link(p,self.root/'hardlink');self.assertEqual(self.read()['code'],'host_file_refused')
        os.mkfifo(self.root/'pipe');self.assertEqual(self.read('pipe')['status'],'refused')

    def test_control_path_case_alias_and_enrolled_worktree(self):
        control=self.root/'OperatorControl.txt';control.write_text('must stay private')
        request={'operation':'read','arguments':{'path':str(control).lower()},'expires_at':time.time()+20,
                 'denied_paths':[str(control)],'forbidden_values':[]}
        self.assertEqual(H.run(request)['code'],'host_path_refused')
        # Inode denial remains effective even when spelling checks cannot identify an alias.
        request['arguments']['path']=str(control)
        real_safe=H.safe_path
        with patch.object(H,'safe_path',side_effect=lambda p,d=(): real_safe(p,())):
            self.assertEqual(H.run(request)['code'],'host_path_refused')
        work=self.root/'.codex/worktrees/test-repo';work.mkdir(parents=True)
        (work/'ordinary.py').write_text('print("ordinary")')
        self.assertEqual(self.read('.codex/worktrees/test-repo/ordinary.py')['status'],'done')
        (self.root/'.codex/auth.json').write_text('private')
        self.assertEqual(self.read('.codex/auth.json')['code'],'host_path_refused')

    def test_directory_paging_and_omissions(self):
        for name in ['a','b','c','.env','credentials.json','actual-executor-value']:
            (self.root/name).write_text('text')
        (self.root/'link').symlink_to(self.root/'a')
        a=self.read('',op='list',limit=2)['data']
        self.assertEqual([x['name'] for x in a['entries']],['a','b'])
        self.assertTrue(a['omitted_protected_entries'])
        b=self.read('',op='list',offset=2,sha256=a['sha256'])['data']
        self.assertEqual([x['name'] for x in b['entries']],['c'])
        self.assertIsNone(b['next_offset'])
        with patch.object(H,'MAX_ENTRIES',2):
            self.assertEqual(self.read('',op='list')['code'],'host_directory_too_large')

    def test_binary_large_limit_expiry_permissions_and_changed_during_read(self):
        p=self.root/'ordinary.txt';p.write_bytes(b'\x00binary')
        self.assertEqual(self.read()['code'],'host_binary_refused')
        p.write_bytes(b'\xff')
        self.assertEqual(self.read()['code'],'host_binary_refused')
        p.write_bytes(b'a'*(H.MAX_SOURCE+1))
        self.assertEqual(self.read()['code'],'host_file_too_large')
        self.assertEqual(self.read(limit=2001)['code'],'host_request_invalid')
        with patch.object(H.os,'open',side_effect=PermissionError):
            self.assertEqual(self.read()['code'],'host_read_unavailable')
        p.write_text('normal')
        real_read=H.os.read
        def change(fd,n):
            data=real_read(fd,n)
            if data:p.write_text('changed')
            return data
        with patch.object(H.os,'read',side_effect=change):
            self.assertEqual(self.read()['code'],'host_source_changed')
        with patch.object(H.time,'time',return_value=time.time()+100):
            request={'operation':'read','arguments':{'path':str(p)},'expires_at':0,'denied_paths':[],'forbidden_values':[]}
            self.assertEqual(H.run(request)['code'],'host_read_expired')

    def docx(self,xml,compression=zipfile.ZIP_STORED):
        with zipfile.ZipFile(self.root/'ordinary.docx','w',compression=compression) as z:
            z.writestr('word/document.xml',xml)
            z.writestr('word/_rels/document.xml.rels','external target never fetched')
        return self.read('ordinary.docx',format='docx')

    def test_docx_structural_only_and_bounds(self):
        xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello table</w:t></w:r></w:p><w:sectPr><w:pgSz w:w="100" w:h="80" w:orient="landscape"/></w:sectPr></w:body></w:document>'
        xml=xml.replace('<w:sectPr>', '<w:tbl><w:tblGrid>'+''.join('<w:gridCol w:w="'+str(w)+'"/>' for w in [14,28,20,20,18])+'</w:tblGrid><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc>' for _ in range(5))+'</w:tr></w:tbl><w:sectPr>')
        r=self.docx(xml);self.assertEqual(r['status'],'done');self.assertIn('Hello table',r['data']['text'])
        self.assertEqual(r['data']['view'],'docx_structure_not_rendered')
        self.assertIn('landscape',r['data']['text'])
        self.assertIn('"grid_widths_twips": ["14", "28", "20", "20", "18"]',r['data']['text'])
        self.assertIn('"cell_count": 5',r['data']['text'])
        self.assertEqual(self.docx('<!DOCTYPE x>'+xml)['code'],'host_docx_refused')
        self.assertEqual(self.docx('a'*100000,zipfile.ZIP_DEFLATED)['code'],'host_docx_bounds')
        self.assertEqual(self.docx(xml.replace('Hello table','password=secretvalue'))['code'],'host_secret_detected')


if __name__=='__main__':unittest.main()
