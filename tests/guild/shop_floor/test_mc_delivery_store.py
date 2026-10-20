import hashlib,json,os
from pathlib import Path
import pytest
from minimoi_portal.guild_ui.mc_artifact_bundle import prepare
from minimoi_portal.guild_ui.mc_delivery_store import DeliveryStore,DeliveryMissing,DeliveryUnavailable,DeliveryFull,RETENTION_S

def bundle(text='hello'):
 return prepare(json.dumps({'files':[{'name':'brief.md','content':text}]}),scrub=lambda s:s)

def test_exact_bytes_private_files_and_idempotent_original(tmp_path):
 store=DeliveryStore(tmp_path/'deliveries');a=bundle()
 m,data=store.put('owner','c-1','n-1',a)
 assert data==a.data and hashlib.sha256(data).hexdigest()==m['sha256']
 assert store.get('owner','c-1',m['id'])==(m,data)
 assert store.put('owner','c-1','n-1',bundle('changed'))==(m,data)
 assert len(list((tmp_path/'deliveries').glob('*.zip')))==1
 assert all((p.stat().st_mode & 0o777)==0o600 for p in (tmp_path/'deliveries').iterdir())
 assert m['author']=='master_craftsman' and m['source_note']=='n-1'

@pytest.mark.parametrize('owner,cid',[('someone','c-1'),('owner','c-other')])
def test_other_scope_never_reads(tmp_path,owner,cid):
 store=DeliveryStore(tmp_path/'deliveries');m,_=store.put('owner','c-1','n-1',bundle())
 with pytest.raises(DeliveryMissing):store.get(owner,cid,m['id'])

def test_corrupt_bytes_cannot_be_reported_ready(tmp_path):
 store=DeliveryStore(tmp_path/'deliveries');m,_=store.put('owner','c-1','n-1',bundle())
 (store.root/(m['id']+'.zip')).write_bytes(b'corrupt')
 with pytest.raises(DeliveryUnavailable):store.get('owner','c-1',m['id'])
 with pytest.raises(DeliveryUnavailable):store.put('owner','c-1','n-1',bundle())

def test_partial_write_is_not_retried(tmp_path,monkeypatch):
 store=DeliveryStore(tmp_path/'deliveries');original=os.open
 def fail(path,flags,*a,**kw):
  if str(path).endswith('.json') and flags & os.O_CREAT:raise OSError('simulated full disk')
  return original(path,flags,*a,**kw)
 monkeypatch.setattr(os,'open',fail)
 with pytest.raises(DeliveryUnavailable):store.put('owner','c-1','n-1',bundle())
 monkeypatch.setattr(os,'open',original)
 with pytest.raises(DeliveryUnavailable,match='unconfirmed_previous_write'):store.put('owner','c-1','n-1',bundle())
 assert len(list(store.root.glob('*.zip')))==1

def test_count_cap_and_expiry_do_not_delete(tmp_path):
 now=[1000];store=DeliveryStore(tmp_path/'deliveries',clock=lambda:now[0])
 for i in range(10):m,_=store.put('owner','c-1',str(i),bundle())
 with pytest.raises(DeliveryFull):store.put('owner','c-1','extra',bundle())
 now[0]+=RETENTION_S
 with pytest.raises(DeliveryMissing):store.get('owner','c-1',m['id'])
 assert len(list(store.root.glob('*.zip')))==10

def test_symlink_bytes_and_insecure_root_refused(tmp_path):
 store=DeliveryStore(tmp_path/'deliveries');m,_=store.put('owner','c-1','n-1',bundle())
 zipfile=store.root/(m['id']+'.zip');zipfile.unlink();zipfile.symlink_to(tmp_path/'missing')
 with pytest.raises(DeliveryUnavailable):store.get('owner','c-1',m['id'])
 store.root.chmod(0o755)
 with pytest.raises(DeliveryUnavailable,match='private_directory_required'):store.get('owner','c-1',m['id'])
