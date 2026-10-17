import json, os, subprocess, sys
from pathlib import Path
import pytest
from core.memory_ask import runner as r, regression as g, consumer

@pytest.fixture
def runs(tmp_path):
    root=tmp_path/'runs';root.mkdir(mode=0o700)
    case={'case_id':'tiny','question':'Synthetic?','purpose':'bounded fixture','processing':'local_only','mode':'unsupported_fixture','window':{'kind':'none','axis':'none'},'expected':[],'assertions':{'min_passages':0,'require_meaning':False,'max_unresolved':0}}
    suite=json.dumps({'schema':r.SCHEMA,'version':'v1','suite_id':'tiny','profile_ref':'fixture','confirmation_ref':'synthetic','cases':[case]}).encode()
    p=r.Profile('fixture',consumer.Config('http://127.0.0.1:8771','synthetic'),True,'synthetic')
    def make():
        return r.run(suite,profile=p,root=root,verify_confirmation=lambda *a:True,retrieve=lambda *a:pytest.fail('retrieval unexpected'),free_bytes=lambda:r.RESERVE+r.SLACK)['run_id']
    return root,make

def used(root,rid):
    budget=r.ReadBudget(1000000);r.load_run(root,rid,read_budget=budget);return 1000000-budget.remaining

def test_per_run_exact_budget_and_default_integrity(runs):
    root,make=runs;rid=make();size=used(root,rid)
    assert r.load_run(root,rid,max_bytes=size)==r.load_run(root,rid)
    with pytest.raises(r.ReadBudgetExceeded):r.load_run(root,rid,max_bytes=size-1)
    (root/rid/'report.txt').write_text('changed')
    with pytest.raises(r.Refused,match='run_incomplete_or_invalid'):r.load_run(root,rid,max_bytes=1000000)

def test_aggregate_exhaustion_is_not_an_invalid_run_row(runs):
    root,make=runs;a=make();b=make();total=used(root,a)+used(root,b)
    assert len(g.history(root,max_bytes=total))==2
    with pytest.raises(r.ReadBudgetExceeded):g.history(root,max_bytes=total-1)
    assert len(g.history(root))==2

def test_oversized_artifact_is_denied_before_read(runs,monkeypatch):
    root,make=runs;rid=make();seen=[];original=r.os.read
    def observe(fd,n):seen.append(n);return original(fd,n)
    monkeypatch.setattr(r.os,'read',observe)
    with pytest.raises(r.ReadBudgetExceeded):r.load_run(root,rid,max_bytes=0)
    assert seen==[]

@pytest.mark.parametrize('value',[True,-1,1.5])
def test_invalid_budget_refused(value):
    with pytest.raises(r.Refused):r.ReadBudget(value)

def test_fifo_marker_refuses_with_bounded_child_timeout(runs):
    root,make=runs;rid=make();marker=root/rid/'COMPLETE';marker.unlink();os.mkfifo(marker,0o600)
    # Load the exact selected runner path in the child (private draft or integrated).
    code="import importlib.util,sys; p=importlib.util.spec_from_file_location('checked_runner',sys.argv[1]); m=importlib.util.module_from_spec(p);sys.modules[p.name]=m;p.loader.exec_module(m)\ntry:m.load_run(sys.argv[2],sys.argv[3],max_bytes=100000)\nexcept m.Refused:sys.exit(0)\nsys.exit(2)"
    result=subprocess.run([sys.executable,'-B','-c',code,r.__file__,str(root),rid],capture_output=True,timeout=3)
    assert result.returncode==0,result.stderr
