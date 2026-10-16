import importlib.util,json,os,shutil,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from scripts.agents import handoffs as h

class Handoffs(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.store=h.Store(self.base/'handoffs',durable_roots=(self.base/'artifacts',self.base/'memory'));self.store.init()
  self.req=dict(h.TEMPLATE,title='Review a change',request='Read the linked spec; return evidence.',recipient='builder',conversation_id='conversation-1',job_id='job-1')
  self.req={k:(list(v) if isinstance(v,list) else v) for k,v in self.req.items()}
  self.r=self.store.create('mc',self.req);self.id=self.r['id'];self.directory=self.store.root/'mc'/self.id
  self.file=self.base/'screen.png';self.file.write_bytes(b'\x89PNG\x00exact-snapshot')
 def tearDown(self):self.tmp.cleanup()
 def close_old(self):
  with patch.object(h,'stamp',return_value='2025-08-31T09:00:00+00:00'):
   self.store.mark('mc',self.id,'complete','actual-test-result')
   self.store.mark('mc',self.id,'ack','test-receipt','receiving-agent')
 def test_common_template_and_namespace(self):
  other=self.store.create('cos',self.req,self.id)
  self.assertEqual(other['request'],self.r['request']);self.assertEqual(self.store.get('mc',self.id)['role'],'mc');self.assertEqual(self.store.get('cos',self.id)['role'],'cos')
 def test_role_and_uuid_cannot_escape(self):
  for role,rid in [('..',self.id),('mc','../escape'),('mc','invalid')]:
   with self.subTest(role=role,rid=rid),self.assertRaises((ValueError,FileNotFoundError)):self.store.get(role,rid)
 def test_existing_id_not_overwritten(self):
  with self.assertRaises(h.Refused):self.store.create('mc',self.req,self.id)
  self.assertEqual(self.store.get('mc',self.id)['request'],self.req)
 def test_copy_bytes_and_original_untouched(self):
  row=self.store.add('mc',self.id,self.file);copy=self.directory/'files'/row['stored_name']
  self.assertEqual(copy.read_bytes(),self.file.read_bytes());self.assertEqual(row['sha256'],h.sha(self.file.read_bytes()));self.assertEqual(copy.stat().st_mode&511,0o600)
 def test_source_symlink_and_parent_symlink_refused(self):
  link=self.base/'link.png';link.symlink_to(self.file)
  with self.assertRaises(OSError):self.store.add('mc',self.id,link)
  folder=self.base/'alias';folder.symlink_to(self.base,target_is_directory=True)
  with self.assertRaises(OSError):self.store.add('mc',self.id,folder/'screen.png')
 def test_atomic_source_replacement_during_read_is_refused(self):
  real=h.os.fdopen;path=self.file
  class Handle:
   def __init__(self,inner):self.inner=inner
   def __enter__(self):return self
   def __exit__(self,*a):return self.inner.__exit__(*a)
   def fileno(self):return self.inner.fileno()
   def read(self,*args):
    data=self.inner.read(*args);replacement=path.with_name('replacement.png');replacement.write_bytes(b'new published content');os.replace(replacement,path);return data
  with patch.object(h.os,'fdopen',side_effect=lambda *a,**kw:Handle(real(*a,**kw))),self.assertRaises(h.Refused):h.source(path)
 def test_directory_publication_during_read_is_refused(self):
  published=self.base/'published';published.mkdir();path=published/'result.txt';path.write_bytes(b'old verified content')
  replacement=self.base/'published-next';replacement.mkdir();(replacement/'result.txt').write_bytes(b'new different content');oldparent=self.base/'published-old'
  real=h.os.fdopen
  class Handle:
   def __init__(self,inner):self.inner=inner
   def __enter__(self):return self
   def __exit__(self,*a):return self.inner.__exit__(*a)
   def fileno(self):return self.inner.fileno()
   def read(self,*args):
    data=self.inner.read(*args);os.rename(published,oldparent);os.rename(replacement,published);return data
  with patch.object(h.os,'fdopen',side_effect=lambda *a,**kw:Handle(real(*a,**kw))),self.assertRaises(h.Refused):h.source(path)
 def test_root_symlink_refused(self):
  link=self.base/'linked';link.symlink_to(self.store.root,target_is_directory=True)
  with self.assertRaises(OSError):h.Store(link).get('mc',self.id)
 def test_cross_platform_writer_refuses_busy(self):
  (self.store.root/'.writer').mkdir()
  with self.assertRaisesRegex(h.Refused,'busy'):self.store.get('mc',self.id)
  self.assertTrue((self.store.root/'.writer').exists());(self.store.root/'.writer').rmdir()
 def test_private_root_required(self):
  self.store.root.chmod(0o755)
  with self.assertRaises(h.Refused):self.store.get('mc',self.id)
 def test_oversize_source_no_copy(self):
  with patch.object(h,'FILE_MAX',2),self.assertRaises(h.Refused):self.store.add('mc',self.id,self.file)
  self.assertEqual(list((self.directory/'files').iterdir()),[])
 def test_low_disk_preserves_reserve(self):
  fake=shutil._ntuple_diskusage(10*h.RESERVE,10*h.RESERVE-1,1)
  with patch.object(h.shutil,'disk_usage',return_value=fake),self.assertRaises(h.Refused):self.store.add('mc',self.id,self.file)
  self.assertEqual(list((self.directory/'files').iterdir()),[])
 def test_calendar_months_end_clamp(self):
  self.assertEqual(h.six_months('2025-08-31T09:00:00+00:00'),'2026-02-28T09:00:00+00:00')
  self.assertEqual(h.six_months('2027-08-31T09:00:00+00:00'),'2028-02-29T09:00:00+00:00')
 def test_open_and_unacknowledged_never_expire(self):
  self.assertIn('open_or_unacknowledged',self.store.plan('mc',self.id,now='2030-01-01T00:00:00+00:00')['held'])
  self.store.mark('mc',self.id,'complete','result')
  self.assertFalse(self.store.plan('mc',self.id,now='2030-01-01T00:00:00+00:00')['eligible'])
 def test_later_ack_anchor(self):
  with patch.object(h,'stamp',return_value='2026-01-31T00:00:00+00:00'):self.store.mark('mc',self.id,'complete','result')
  with patch.object(h,'stamp',return_value='2026-03-31T00:00:00+00:00'):self.store.mark('mc',self.id,'ack','delivery','builder')
  p=self.store.plan('mc',self.id,now='2026-08-01T00:00:00+00:00');self.assertEqual(p['expires_at'],'2026-09-30T00:00:00+00:00');self.assertFalse(p['eligible'])
 def test_ack_freezes_content_until_reopen(self):
  self.store.mark('mc',self.id,'ack','delivery','builder')
  with self.assertRaises(h.Refused):self.store.add('mc',self.id,self.file)
  self.store.mark('mc',self.id,'reopen','additional input');self.store.add('mc',self.id,self.file)
  self.assertIsNone(self.store.get('mc',self.id)['acknowledged_at'])
 def test_kept_file_requires_permanent_copy(self):
  self.store.add('mc',self.id,self.file);self.close_old()
  self.assertIn('promotion_required',self.store.plan('mc',self.id)['held'])
 def test_promotion_in_scratch_rejected(self):
  row=self.store.add('mc',self.id,self.file)
  with self.assertRaises(h.Refused):self.store.promote('mc',self.id,row['stored_name'],self.file)
 def test_verified_promotion_expiry_preserves_original_and_receipt(self):
  row=self.store.add('mc',self.id,self.file);durable=self.base/'artifacts';durable.mkdir();copy=durable/'saved.png';copy.write_bytes(self.file.read_bytes())
  self.store.promote('mc',self.id,row['stored_name'],copy);self.close_old();p=self.store.plan('mc',self.id)
  self.assertTrue(p['eligible']);self.store.expire('mc',self.id,p['manifest_sha256'])
  self.assertTrue(copy.exists() and self.file.exists() and (self.directory/'handoff.json').exists());self.assertEqual(list((self.directory/'files').iterdir()),[])
  self.assertIn('already_expired',self.store.plan('mc',self.id)['held'])
 def test_changed_promoted_file_holds_expiry(self):
  row=self.store.add('mc',self.id,self.file);d=self.base/'artifacts';d.mkdir();saved=d/'saved.png';saved.write_bytes(self.file.read_bytes());self.store.promote('mc',self.id,row['stored_name'],saved);self.close_old();saved.write_bytes(b'changed')
  self.assertIn('promotion_unavailable_or_changed',self.store.plan('mc',self.id)['held'])
 def test_changed_transport_and_unknown_files_hold(self):
  row=self.store.add('mc',self.id,self.file,True);self.close_old();copy=self.directory/'files'/row['stored_name'];copy.write_bytes(b'changed')
  with self.assertRaises(h.Refused):self.store.plan('mc',self.id)
  copy.write_bytes(self.file.read_bytes());(self.directory/'files'/'extra').write_bytes(b'unknown')
  with self.assertRaises(h.Refused):self.store.plan('mc',self.id)
 def test_disposable_expiry_requires_current_manifest(self):
  self.store.add('mc',self.id,self.file,True);self.close_old()
  with self.assertRaises(h.Refused):self.store.expire('mc',self.id,'0'*64)
  p=self.store.plan('mc',self.id);self.store.expire('mc',self.id,p['manifest_sha256']);self.assertTrue(self.file.exists())
 def test_interrupted_expiry_leaves_explicit_hold(self):
  self.store.add('mc',self.id,self.file,True);other=self.base/'other.txt';other.write_bytes(b'second');self.store.add('mc',self.id,other,True);self.close_old();p=self.store.plan('mc',self.id)
  real=h.os.unlink
  def fail(name,*args,**kw):
   if name=='02-other.txt':raise OSError('injected')
   return real(name,*args,**kw)
  with patch.object(h.os,'unlink',side_effect=fail),self.assertRaises(OSError):self.store.expire('mc',self.id,p['manifest_sha256'])
  self.assertTrue((self.directory/'expiry-intent.json').exists());self.assertIn('expiry_uncertain_operator_review_required',self.store.plan('mc',self.id)['held']);self.assertTrue(other.exists() and self.file.exists())
  with self.assertRaises(h.Refused):self.store.mark('mc',self.id,'reopen','do not hide partial expiry')
 def test_temporary_cli_root_does_not_make_durable_home(self):
  self.assertRaises(h.Refused,h.Store(self.base/'handoffs').durable,self.base/'artifacts'/'copy.png')
 def test_expired_receipt_does_not_retain_request_body(self):
  self.store.add('mc',self.id,self.file,True);self.close_old();p=self.store.plan('mc',self.id);self.store.expire('mc',self.id,p['manifest_sha256'])
  m=self.store.get('mc',self.id);self.assertNotEqual(m['request']['request'],self.req['request']);self.assertEqual(len(m['expired_request_sha256']),64)
 def test_oversized_request_refused_before_directory_creation(self):
  req=dict(self.req)
  for k in ['acceptance','owner_decisions','proposals','open_questions','next_actions']:req[k]=['x'*2000]*30
  before=set((self.store.root/'mc').iterdir())
  with self.assertRaises(h.Refused):self.store.create('mc',req)
  self.assertEqual(set((self.store.root/'mc').iterdir()),before)

if __name__=='__main__':unittest.main()
