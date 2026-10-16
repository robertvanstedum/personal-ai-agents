from pathlib import Path
import json,hashlib,os
import pytest
from core.vault_t1 import assets,portable,reader

def setup(tmp_path):
 root=tmp_path/'shelf';root.mkdir(mode=0o700);source=tmp_path/'original.pdf';source.write_bytes(b'%PDF synthetic native bytes\x00\xff')
 return root,source

def take(root,source,**kwargs):return assets.capture(root,source,source_instance='synthetic-owner-store',source_id='logical-original',**kwargs)

def test_capture_exact_original_and_immutable_editions(tmp_path):
 root,source=setup(tmp_path);first=take(root,source);assert first['added'] and source.read_bytes()==b'%PDF synthetic native bytes\x00\xff'
 assert take(root,source)=={**first,'added':False}
 source.write_bytes(b'new native version');second=take(root,source)
 assert second['asset_id']==first['asset_id'] and second['edition']==2
 rows=assets.inventory(root);assert len(rows)==2
 assert reader.read_path(root,assets.object_path(first['sha256']).split('/'),assets.MAX_BYTES)==b'%PDF synthetic native bytes\x00\xff'
 assert all((root/rel).stat().st_mode&0o077==0 for _,rel,_ in rows)

def test_asset_portable_roundtrip_and_legacy_verify_dispatch(tmp_path):
 root,source=setup(tmp_path);r=take(root,source);out=tmp_path/'export';m=assets.export(root,out,ids=[r['asset_id']])
 assert m['format']=='vault-assets-export/1' and portable.verify(out)['ok']
 target=tmp_path/'restored';result=portable.restore(out,target)
 assert result['target_verified'] and result['restored_assets']==1
 assert (target/assets.object_path(r['sha256'])).read_bytes()==source.read_bytes()
 assert portable.verify_shelf(target)['checked']['assets']==1

def test_backup_shelf_verifier_detects_asset_corruption(tmp_path):
 root,source=setup(tmp_path);r=take(root,source);assert portable.verify_shelf(root)['ok']
 (root/assets.object_path(r['sha256'])).write_bytes(b'damaged')
 assert not portable.verify_shelf(root)['ok']

def test_symlink_source_destination_and_unlisted_object_refused(tmp_path):
 root,source=setup(tmp_path);link=tmp_path/'link';link.symlink_to(source)
 with pytest.raises(reader.VaultError):take(root,link)
 root.chmod(0o755)
 with pytest.raises(reader.VaultError,match='asset_destination_insecure'):take(root,source)
 root.chmod(0o700);take(root,source)
 (root/'assets/objects'/('a'*64+'.bin')).write_bytes(b'unknown')
 assert not assets.verify(root)['ok']

def test_cross_scope_and_changed_scope_fail_closed(tmp_path):
 root,source=setup(tmp_path);r=take(root,source,scope='mandate:restricted')
 out=tmp_path/'out'
 with pytest.raises(reader.VaultError,match='asset_selection_unavailable'):assets.export(root,out,ids=[r['asset_id']])
 assert not out.exists()
 m=assets.export(root,out,ids=[r['asset_id']],mandates=('restricted',));assert m['counts']['assets']==1
 with pytest.raises(reader.VaultError,match='asset_scope_change_not_supported'):take(root,source,scope='robert')

def test_native_export_corruption_no_partial_restore(tmp_path):
 root,source=setup(tmp_path);r=take(root,source);out=tmp_path/'out';assets.export(root,out,ids=[r['asset_id']])
 (out/assets.object_path(r['sha256'])).write_bytes(b'bad')
 assert not portable.verify_export(out)['ok']
 target=tmp_path/'target'
 with pytest.raises(reader.VaultError):portable.restore(out,target)
 assert not target.exists()

def test_bounds_original_retained_and_no_export_destination(tmp_path,monkeypatch):
 root,source=setup(tmp_path);original=source.read_bytes();monkeypatch.setattr(assets,'MAX_BYTES',3)
 with pytest.raises(reader.VaultError):take(root,source)
 assert source.read_bytes()==original and not (root/'assets').exists()

def test_existing_restore_target_not_replaced(tmp_path):
 root,source=setup(tmp_path);r=take(root,source);out=tmp_path/'out';assets.export(root,out,ids=[r['asset_id']])
 target=tmp_path/'target';target.mkdir();(target/'keep').write_bytes(b'keep')
 with pytest.raises(reader.VaultError):portable.restore(out,target)
 assert (target/'keep').read_bytes()==b'keep'

def test_manifest_graph_unlisted_and_missing_objects_refused(tmp_path):
 root,source=setup(tmp_path);r=take(root,source);out=tmp_path/'out';assets.export(root,out,ids=[r['asset_id']])
 (out/'extra').write_text('extra');assert not assets.verify_export(out)['ok']
 (out/'extra').unlink();m=json.loads((out/'manifest.json').read_text());m['files']=[x for x in m['files'] if not x['path'].startswith('assets/objects/')]
 raw=assets.canonical(m);(out/'manifest.json').write_bytes(raw);(out/'manifest.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  manifest.json\n');(out/assets.object_path(r['sha256'])).unlink()
 assert not assets.verify_export(out)['ok']

def test_frozen_batch_source_hash_mismatch_publishes_nothing(tmp_path):
 root,source=setup(tmp_path)
 with pytest.raises(reader.VaultError,match='asset_source_hash_changed'):take(root,source,expected_sha256='0'*64)
 assert not (root/'assets').exists()

def test_secret_file_kinds_not_supported(tmp_path):
 root,source=setup(tmp_path);secret=tmp_path/'identity.agekey';secret.write_bytes(b'synthetic')
 with pytest.raises(reader.VaultError,match='asset_kind_not_supported'):take(root,secret)
 assert not (root/'assets').exists()

@pytest.mark.parametrize('failure',['metadata_error','ENOSPC','metadata_fsync','object_fsync'])
def test_append_fault_does_not_damage_old_asset_and_retry_recovers(tmp_path,monkeypatch,failure):
 import errno
 root,source=setup(tmp_path);first=take(root,source)
 old=(root/assets.object_path(first['sha256'])).read_bytes()
 source.write_bytes(b'changed edition')
 original_put=portable.Publisher.put
 original_fsync=assets.os.fsync
 fired=[]
 def fail_put(self,rel,data):
  if failure=='object_fsync' and rel.startswith('assets/objects/') and not fired:
   result=original_put(self,rel,data);fired.append(True);return result
  if rel.startswith('assets/captures/') and not fired:
   fired.append(True)
   if failure=='ENOSPC':raise OSError(errno.ENOSPC,'synthetic disk full')
   if failure=='metadata_error':raise reader.VaultError('synthetic_metadata_failure')
  return original_put(self,rel,data)
 def fail_fsync(fd):
  # Publication file fsync must succeed; inject at the capture directory fsync.
  if failure in ('metadata_fsync','object_fsync') and fired and __import__('stat').S_ISDIR(os.fstat(fd).st_mode) and not getattr(fail_fsync,'done',False):
   fail_fsync.done=True
   raise OSError(errno.EIO,'synthetic directory sync failure')
  return original_fsync(fd)
 monkeypatch.setattr(portable.Publisher,'put',fail_put)
 if failure in ('metadata_fsync','object_fsync'):monkeypatch.setattr(assets.os,'fsync',fail_fsync)
 with pytest.raises((reader.VaultError,OSError)):take(root,source)
 monkeypatch.setattr(portable.Publisher,'put',original_put);monkeypatch.setattr(assets.os,'fsync',original_fsync)
 check=assets.verify(root);assert check['ok']
 assert (root/assets.object_path(first['sha256'])).read_bytes()==old
 assert check['checked']['assets']==1
 if failure in ('metadata_error','ENOSPC','object_fsync'):assert check['warnings']['unreferenced_objects']==1 and check['checked']['asset_editions']==1
 retry=take(root,source);assert retry['edition']==2
 assert assets.verify(root)['warnings']['unreferenced_objects']==0
 assert assets.verify(root)['checked']['asset_editions']==2

def test_busy_lock_nonblocking_no_asset_write(tmp_path):
 import fcntl
 root,source=setup(tmp_path);lock=os.open(root/'.assets.lock',os.O_RDWR|os.O_CREAT,0o600)
 try:
  fcntl.flock(lock,fcntl.LOCK_EX)
  with pytest.raises(reader.VaultError,match='asset_busy'):take(root,source)
  assert not (root/'assets').exists()
 finally:os.close(lock)

def test_disk_low_before_source_copy(tmp_path,monkeypatch):
 from types import SimpleNamespace
 from unittest.mock import Mock
 root,source=setup(tmp_path);reader_mock=Mock();monkeypatch.setattr(assets,'read_stable',reader_mock)
 monkeypatch.setattr(assets.shutil,'disk_usage',lambda p:SimpleNamespace(free=(5<<30)-1))
 with pytest.raises(reader.VaultError,match='asset_disk_low'):take(root,source)
 reader_mock.assert_not_called();assert not (root/'assets').exists()

def test_root_replacement_refused_before_append(tmp_path,monkeypatch):
 root,source=setup(tmp_path);take(root,source);source.write_bytes(b'changed')
 original=assets.inventory
 def replaced(path):
  rows=original(path);root.rename(tmp_path/'old-shelf');root.mkdir(mode=0o700);return rows
 monkeypatch.setattr(assets,'inventory',replaced)
 with pytest.raises(reader.VaultError,match='asset_root_changed'):take(root,source)
 assert not (root/'assets').exists()
 assert len(list((tmp_path/'old-shelf/assets/captures').rglob('*.json')))==1

def test_numeric_sha_type_fixed_refusal(tmp_path):
 root,source=setup(tmp_path);take(root,source);doc,rel,raw=assets.inventory(root)[0];doc['sha256']=int('1'*64)
 raw=assets.canonical(doc)
 with pytest.raises(reader.VaultError,match='asset_capture_schema'):assets.check_capture(doc,assets.capture_path(doc,raw),raw)

def test_metadata_limit_before_publication(tmp_path,monkeypatch):
 root,source=setup(tmp_path);monkeypatch.setattr(assets,'MAX_METADATA_BYTES',10)
 with pytest.raises(reader.VaultError,match='asset_metadata_limit'):take(root,source)
 assert not (root/'assets').exists()

def test_export_bound_before_output_creation(tmp_path,monkeypatch):
 root,source=setup(tmp_path);r=take(root,source);monkeypatch.setattr(assets,'MAX_EXPORT_FILES',1)
 with pytest.raises(reader.VaultError,match='asset_export_batch_too_large'):assets.export(root,tmp_path/'out',ids=[r['asset_id']])
 assert not (tmp_path/'out').exists()

def test_invalid_utf8_sidecar_fixed_failure(tmp_path):
 root,source=setup(tmp_path);r=take(root,source);out=tmp_path/'out';assets.export(root,out,ids=[r['asset_id']])
 (out/'manifest.sha256').write_bytes(b'\xff')
 result=portable.verify(out)
 assert not result['ok'] and result['problems'][0]['code']=='manifest_unreadable'

def test_orphan_object_limit_refuses_new_blob_but_allows_reuse(tmp_path,monkeypatch):
 root,source=setup(tmp_path);first=take(root,source)
 orphan=b'verified orphan';digest=hashlib.sha256(orphan).hexdigest();(root/assets.object_path(digest)).write_bytes(orphan)
 monkeypatch.setattr(assets,'MAX_CAPTURES',2)
 source.write_bytes(b'third distinct object')
 with pytest.raises(reader.VaultError,match='asset_object_limit'):take(root,source)
 assert len(list((root/'assets/objects').iterdir()))==2 and len(assets.inventory(root))==1
 source.write_bytes(orphan);reuse=take(root,source)
 assert reuse['edition']==2 and len(list((root/'assets/objects').iterdir()))==2
 assert assets.verify(root)['warnings']['unreferenced_objects']==0

def test_export_requires_allocated_file_and_directory_headroom(tmp_path,monkeypatch):
 from types import SimpleNamespace
 root,source=setup(tmp_path);r=take(root,source)
 files=[{'path':f'assets/objects/{i:064x}.bin','bytes':1} for i in range(8193)]
 assert assets.allocation_estimate(files,manifest_bytes=1000)>8193*4096
 out=tmp_path/'out'
 # Enough for old logical-byte budget, insufficient for per-file/dir blocks.
 monkeypatch.setattr(assets.shutil,'disk_usage',lambda p:SimpleNamespace(free=assets.RESERVE_BYTES+(66<<10)))
 with pytest.raises(reader.VaultError,match='asset_disk_low'):assets.export(root,out,ids=[r['asset_id']])
 assert not out.exists()
