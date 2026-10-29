import importlib.util,sys
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
ROOT=Path(__file__).resolve().parents[2]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
from core.jobs import batch_window as W
B=load('draft_backup','core/memory_backup/backup.py')
from core.memory_index import embedder as E
I=load('draft_indexer','core/memory_index/indexer.py')
V=load('draft_watchers','core/memory_shelf/watchers.py')
D=load('draft_daily','scripts/memory/daily_watch.py')
def expire(*args,**kwargs):raise W.BatchDeadline()
def test_backup_deadline_translation(monkeypatch):
 monkeypatch.setattr(W,'remaining',expire)
 with pytest.raises(B.BackupError,match='outside_window'):B.batch_timeout()
def test_backup_abort_cleans_plaintext_and_never_publishes(tmp_path,monkeypatch):
 shelf=tmp_path/'shelf';shelf.mkdir();work=tmp_path/'work';dest=tmp_path/'dest'
 monkeypatch.setattr(B,'check_age',lambda *a:'v1.3.2')
 monkeypatch.setattr(B,'batch_timeout',lambda *a:3600)
 def stop_verification(*a):raise B.BackupError('outside_window')
 monkeypatch.setattr(B.portable,'verify_shelf',stop_verification)
 with pytest.raises(B.BackupError,match='outside_window'):
  B.run_backup(shelf_root=str(shelf),dest=dest.as_uri(),recipient='age1'+'a'*58,workdir=str(work),enforce_window=False,usage=lambda p:SimpleNamespace(free=100<<30))
 assert list(work.iterdir())==[]
 assert not list(dest.rglob('COMPLETE.json'))
def test_index_keeps_progress_releases_and_omits_deletion_and_freshness(monkeypatch):
 fake=SimpleNamespace(record=Mock(),result=lambda code,t:{'code':code},drop_missing=Mock())
 monkeypatch.setattr(I,'_Run',lambda *a:fake);monkeypatch.setattr(I,'shelf_entries',lambda r:[('a','a',Path('a')),('b','b',Path('b'))])
 calls=[]
 def step(*a):
  calls.append(1)
  if len(calls)==2:raise W.BatchDeadline()
 monkeypatch.setattr(W,'remaining',step)
 c=SimpleNamespace(commit=Mock());e=SimpleNamespace(release=Mock());meta=Mock();monkeypatch.setattr(I.store,'set_meta',meta)
 assert I._sync(c,Path('/unused'),e,None,lambda:datetime.now(timezone.utc),None,None)=={'code':'outside_window'}
 assert fake.record.call_count==1 and c.commit.called and e.release.called
 fake.drop_missing.assert_not_called();meta.assert_not_called()
def test_watcher_partial_pass_not_fresh(monkeypatch):
 writes=[];monkeypatch.setattr(V.fsio,'read_json',lambda p:{'last_success_at':'old','last_new_capture_at':'old'})
 monkeypatch.setattr(V.fsio,'write_json',lambda p,d:writes.append(d))
 shelf=SimpleNamespace(status_dir=Path('/unused'));result=V._finish(shelf,'codex',{}, {'outside_window':1},datetime.now(timezone.utc))
 assert result['status']=='outside_window' and writes[0]['last_success_at']=='old'
def test_no_next_capture_source_or_report(monkeypatch):
 monkeypatch.setattr(W,'remaining',expire);watch=Mock();report=Mock()
 result=D.capture(lambda s:'approved',watch)
 assert set(result.values())=={'outside_window'};watch.assert_not_called()
 assert D.publish_report('/unused',report)=={'report':'outside_window'};report.assert_not_called()
def test_embedder_release_is_cleanup_exception_to_deadline(monkeypatch):
 monkeypatch.setattr(W,'remaining',expire);called=[]
 class Response:
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def read(self):return b'{}'
 monkeypatch.setattr(E.urllib.request,'urlopen',lambda req,timeout:(called.append(timeout) or Response()))
 emb=E.OllamaEmbedder(timeout=7);emb._used=True;emb.release()
 assert called==[7] and not emb._used
