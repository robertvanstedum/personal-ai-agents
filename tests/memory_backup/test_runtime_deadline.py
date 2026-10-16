import importlib.util,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[2]
s=importlib.util.spec_from_file_location('draft_runtime',ROOT/'scripts/vault/runtime_install.py');R=importlib.util.module_from_spec(s);s.loader.exec_module(R)
def clock_at(h):
 class Clock:
  @staticmethod
  def now(zone):return datetime(2026,10,14,h,59,tzinfo=ZoneInfo('America/Chicago'))
 return Clock

def test_runtime_sets_exact_deadline_for_batch(tmp_path,monkeypatch):
 monkeypatch.setattr(R,'datetime',clock_at(5));monkeypatch.setattr(R,'environment',lambda p,c:{})
 cfg={'jobs_root':str(tmp_path)};run=Mock(return_value=SimpleNamespace(returncode=0));monkeypatch.setattr(R.subprocess,'run',run)
 monkeypatch.setattr(R,'commands',lambda *a:['dummy'])
 assert R.run_job(cfg,'backup',pin=tmp_path)==0
 env=run.call_args.kwargs['env'];assert float(env['MOI_BATCH_DEADLINE_EPOCH'])==datetime(2026,10,14,6,tzinfo=ZoneInfo('America/Chicago')).timestamp()
def test_runtime_refuses_after_boundary_without_starting(tmp_path,monkeypatch):
 monkeypatch.setattr(R,'datetime',clock_at(6));run=Mock();monkeypatch.setattr(R.subprocess,'run',run)
 assert R.run_job({'jobs_root':str(tmp_path)},'backup',pin=tmp_path)==1;run.assert_not_called()
