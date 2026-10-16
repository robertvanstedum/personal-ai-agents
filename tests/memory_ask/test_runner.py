import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest
from core.memory_ask import consumer
spec=importlib.util.spec_from_file_location('draft_runner',Path(__file__).parents[2]/'core/memory_ask/runner.py')
r=importlib.util.module_from_spec(spec);sys.modules[spec.name]=r;spec.loader.exec_module(r)

def suite(**changes):
    c={'case_id':'decision-1','question':'What was decided?','purpose':'synthetic supporting evidence','processing':'local_only','mode':'ask_curated','window':{'kind':'none','axis':'none'},'expected':[{'file':'record.md','lines':[3,4],'record_id':'record1'}],'assertions':{'min_passages':1,'require_meaning':True,'max_unresolved':0}}
    c.update(changes)
    return json.dumps({'schema':r.SCHEMA,'version':'v1','suite_id':'synthetic','profile_ref':'local-curated','confirmation_ref':'owner-review-synthetic-1','cases':[c]}).encode()
def result():
    h={'file':'record.md','lines':[2,5],'passage':'A synthetic retained decision.','speaker':'robert','provenance':'synthetic_fixture','tier':'curated','record_id':'record1'}
    return {'query_id':'synthetic-query','caller':'synthetic-grant','tier':'curated','keyword':[h],'meaning':[],'adjacent':{'none':'not_found'},'unresolved':[],'status':{'meaning':'ok','index_built_at':'a','index_updated_at':'b','embedder':'fixture@v1'}}
@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'runs';root.mkdir(mode=0o700)
    profile=r.Profile('local-curated',consumer.Config('http://127.0.0.1:8771','synthetic-token'),True,'grant-v1')
    return root,profile

def execute(raw,setup,**kw):
    root,p=setup
    return r.run(raw,profile=p,root=root,verify_confirmation=lambda *a:True,retrieve=lambda *a:result(),free_bytes=lambda:r.RESERVE+r.SLACK,**kw)
def test_twice_immutable_private_history(setup):
    a=execute(suite(),setup);b=execute(suite(),setup);root,_=setup
    assert a['run_id']!=b['run_id'];assert a['counts']['needs_review']==1
    for rid in (a['run_id'],b['run_id']):
        assert (root/rid/'COMPLETE').exists() and not (root/rid/'INCOMPLETE').exists()
        assert os.stat(root/rid).st_mode&0o777==0o700
        assert all(p.stat().st_mode&0o777==0o600 for p in (root/rid).iterdir())
        assert 'synthetic retained decision' in (root/rid/'decision-1.json').read_text()
    assert r.compare(r.load_run(root,a['run_id']),r.load_run(root,b['run_id']))['warnings']==[]
def test_source_format_never_claims_resolved():
    out=r.evaluate(r.validate(suite())['cases'][0],result())
    assert out['checks'][0]['retained'] and out['checks'][0]['source_verified'] is None
    assert out['outcome']=='needs_review' and out['source_resolution']=='not_verified'
def test_injected_trusted_source_proof():
    case=r.validate(suite())['cases'][0]
    assert r.evaluate(case,result(),verify_source=lambda e:{'verified':True,'source_sha256':'a'*64})['outcome']=='pass'
    assert r.evaluate(case,result(),verify_source=lambda e:False)['outcome']=='fail'
def test_expected_range_must_be_retained():
    case=r.validate(suite(expected=[{'file':'record.md','lines':[1,7]}]))['cases'][0]
    assert r.evaluate(case,result())['outcome']=='fail'
def test_malformed_or_unavailable_never_pass():
    case=r.validate(suite())['cases'][0]
    assert r.evaluate(case,{'error':'unavailable'})['outcome']=='error'
    bad=result();bad['status']['meaning']='embedder_unavailable';assert r.evaluate(case,bad)['outcome']=='error'
def test_time_axis_resolved_once_but_unsupported(setup):
    from datetime import datetime,timezone
    raw=suite(window={'kind':'rolling_days','axis':'event_time','days':2})
    planned=r.plan(r.validate(raw),at=datetime(2026,3,9,12,tzinfo=timezone.utc))
    assert planned[0]['window']['start']=='2026-03-07T06:00:00+00:00'
    assert planned[0]['window']['end']=='2026-03-09T05:00:00+00:00'
    out=execute(raw,setup);assert out['counts']['unsupported']==1
@pytest.mark.parametrize('change',[{'url':'https://bad.example'},{'approved':True},{'token':'inline-secret'},{'filters':{'raw':True}}])
def test_suite_cannot_grant_or_route(change):
    with pytest.raises(r.Refused):r.validate(suite(**change))
def test_confirmation_before_artifacts_or_call(setup):
    root,p=setup;calls=[]
    with pytest.raises(r.Refused):r.run(suite(),profile=p,root=root,verify_confirmation=lambda *a:False,retrieve=lambda *a:calls.append(a),free_bytes=lambda:10**12)
    assert not calls and list(root.iterdir())==[]
def test_disk_floor_before_resources(setup):
    root,p=setup
    with pytest.raises(r.Refused):r.run(suite(),profile=p,root=root,verify_confirmation=lambda *a:True,free_bytes=lambda:r.RESERVE)
    assert not list(root.iterdir())
def test_interruption_leaves_visible_incomplete_no_redispatch(setup):
    root,p=setup;calls=[]
    def stop(*a):calls.append(a);raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):r.run(suite(),profile=p,root=root,verify_confirmation=lambda *a:True,retrieve=stop,free_bytes=lambda:10**12)
    run=next(root.iterdir());assert (run/'INCOMPLETE').exists() and not (run/'COMPLETE').exists() and len(calls)==1
    assert (run/'decision-1.attempt.json').exists()
    with pytest.raises(r.Refused):r.load_run(root,run.name)
def test_timeout_error_no_raw_error_or_retry(setup):
    root,p=setup;calls=[]
    def fail(*a):calls.append(a);raise TimeoutError('secret-provider-response')
    out=r.run(suite(),profile=p,root=root,verify_confirmation=lambda *a:True,retrieve=fail,free_bytes=lambda:10**12)
    data=(root/out['run_id']/'decision-1.json').read_text()
    assert out['counts']['error']==1 and len(calls)==1 and 'secret-provider-response' not in data
def test_profile_narrows_local_only(setup):
    root,p=setup
    p=r.Profile(p.reference,p.config,False,'grant-v1');out=execute(suite(),(root,p));assert out['counts']['not_run']==1
def test_root_symlink_and_public_refused(setup,tmp_path):
    root,p=setup;link=tmp_path/'linked';link.symlink_to(root)
    with pytest.raises(OSError):execute(suite(),(link,p))
    root.chmod(0o755)
    with pytest.raises(r.Refused):execute(suite(),setup)
def test_changed_inputs_warn(setup):
    root,p=setup;a=execute(suite(),setup);b=execute(suite(question='Which evidence?'),setup)
    compared=r.compare(r.load_run(root,a['run_id']),r.load_run(root,b['run_id']))
    assert 'changed_suite_hash' in compared['warnings'] and not compared['comparable']
def test_endpoint_uses_existing_bearer_and_curated_body(setup):
    _,p=setup
    def post(url,headers,body,timeout):
        assert url=='http://127.0.0.1:8771/v1/ask'
        assert headers['Authorization']=='Bearer synthetic-token'
        assert json.loads(body)=={'query':'synthetic','k':5}
        return 200,json.dumps(result()).encode()
    assert r.ask_case('synthetic',p,post=post)['query_id']=='synthetic-query'
def test_completed_result_mutation_detected(setup):
    root,p=setup;out=execute(suite(),setup)
    (root/out['run_id']/'decision-1.json').write_text('{}')
    with pytest.raises(r.Refused):r.load_run(root,out['run_id'])
def test_cli_default_offline_and_no_passages_stdout(setup,tmp_path,capsys,monkeypatch):
    import core.memory_ask
    monkeypatch.setattr(core.memory_ask,'runner',r,raising=False)
    path=Path(__file__).parents[2]/'scripts/vault/query_suite.py'
    spec=importlib.util.spec_from_file_location('draft_query_cli',path);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    f=tmp_path/'suite.json';f.write_bytes(suite())
    def never(*a,**kw):raise AssertionError('offline contacted provider')
    assert cli.main(['run','--suite',str(f)],execute=never)==0
    shown=json.loads(capsys.readouterr().out);assert shown['live_calls']==0
    assert 'synthetic retained decision' not in json.dumps(shown) and 'What was decided?' not in json.dumps(shown)
    assert cli.main(['run','--suite',str(f),'--execute'],execute=never)==2
def test_expected_edition_not_available_is_unsupported():
    case=r.validate(suite(expected=[{'file':'record.md','lines':[3,4],'edition':'unavailable-version'}]))['cases'][0]
    out=r.evaluate(case,result());assert out['outcome']=='unsupported'
def test_space_drop_leaves_incomplete_before_lookup(setup):
    root,p=setup;calls=[];checks=[10**12]*4+[r.RESERVE]
    def free():return checks.pop(0) if checks else r.RESERVE
    with pytest.raises(r.Refused):r.run(suite(),profile=p,root=root,verify_confirmation=lambda *a:True,retrieve=lambda *a:calls.append(a),free_bytes=free)
    assert not calls and (next(root.iterdir())/'INCOMPLETE').exists()
def test_case_artifact_collision_rejected():
    with pytest.raises(r.Refused):r.validate(suite(case_id='manifest'))
def test_complete_marker_with_incomplete_still_refused(setup):
    root,p=setup;out=execute(suite(),setup);path=root/out['run_id']
    (path/'INCOMPLETE').write_bytes(b'{"reason":"crash_after_complete"}')
    (path/'INCOMPLETE').chmod(0o600)
    with pytest.raises(r.Refused):r.load_run(root,out['run_id'])
@pytest.mark.parametrize('window',[{'kind':'fixed','axis':'event_time','start':'2026-10-10','end':'2026-10-12'}, {'kind':'fixed','axis':'event_time','start':'bad','end':'also-bad'}, {'kind':'fixed','axis':'event_time','start':'2026-10-12T00:00:00Z','end':'2026-10-10T00:00:00Z'}, {'kind':'rolling_days','axis':'created','days':True}, {'kind':'rolling_days','axis':'created','days':0}, {'kind':'rolling_days','axis':'created','days':3661}])
def test_invalid_window_refused(window):
    with pytest.raises(r.Refused):r.validate(suite(window=window))
@pytest.mark.parametrize('k,timeout',[(True,8),(6,8),(0,8),(5,float('nan')),(5,float('inf')),(5,True),(5,0),(5,9)])
def test_operator_k_timeout_limits_fail_closed(setup,k,timeout):
    root,p=setup
    profile=r.Profile(p.reference,consumer.Config('http://127.0.0.1:8771','synthetic-token',k=k,timeout_s=timeout),True,'unknown')
    with pytest.raises(r.Refused):execute(suite(),(root,profile))
    assert not list(root.iterdir())
def test_operator_attestation_not_gold_confirmation(setup):
    root,p=setup;out=execute(suite(),setup,confirmation_status='operator_attested_not_gold_verified')
    assert out['question_confirmation']=='needs_review'
    receipt=json.loads((root/out['run_id']/'execution-receipt.json').read_text())
    assert receipt['suite_hash']==r.sha(suite()) and receipt['confirmation_ref']=='owner-review-synthetic-1'
    assert receipt['confirmation_status']=='operator_attested_not_gold_verified'
def test_cli_does_not_invent_grant_revision(monkeypatch):
    import core.memory_ask
    monkeypatch.setattr(core.memory_ask,'runner',r,raising=False)
    path=Path(__file__).parents[2]/'scripts/vault/query_suite.py'
    spec=importlib.util.spec_from_file_location('draft_cli_revision',path);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    profile=cli.operator_profile('local-curated',{'MINIMOI_VAULT_ASK_URL':'http://127.0.0.1:8771','MINIMOI_VAULT_ASK_TOKEN':'synthetic-token','MINIMOI_VAULT_ASK_GRANT_REVISION':'invented'})
    assert profile.permissions_fingerprint=='unknown' and 'invented' not in str(profile.metadata)
    assert len(profile.metadata['local_source_sha256']) == 64
    assert profile.metadata['ask_server_source_version'] is None
    before = cli.local_source_version()
    from scripts.vault import runtime_install
    source = Path(__file__)
    monkeypatch.setattr(runtime_install, 'runtime_files', lambda repo: [source])
    assert cli.local_source_version() != before
def test_explicit_cli_attestation_records_pending_gold(setup,tmp_path,capsys,monkeypatch):
    import core.memory_ask
    monkeypatch.setattr(core.memory_ask,'runner',r,raising=False)
    path=Path(__file__).parents[2]/'scripts/vault/query_suite.py'
    spec=importlib.util.spec_from_file_location('draft_cli_explicit',path);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    f=tmp_path/'suite.json';f.write_bytes(suite());f.chmod(0o600);root,p=setup
    def synthetic_execute(raw,**kw):return r.run(raw,**kw,retrieve=lambda *a:result(),free_bytes=lambda:10**12)
    args=['run','--execute','--suite',str(f),'--root',str(root),'--profile','local-curated','--attested-suite-sha256',r.sha(suite()),'--attested-confirmation-reference','owner-review-synthetic-1']
    assert cli.main(args,environ={'MINIMOI_VAULT_ASK_URL':'http://127.0.0.1:8771','MINIMOI_VAULT_ASK_TOKEN':'synthetic-token'},execute=synthetic_execute)==0
    out=json.loads(capsys.readouterr().out)
    assert out['question_confirmation']=='needs_review' and out['counts']['needs_review']==1
    receipt=json.loads((root/out['run_id']/'execution-receipt.json').read_text())
    assert receipt['confirmation_status']=='operator_attested_not_gold_verified'
def test_identity_must_match_hit_containing_expected_range():
    case=r.validate(suite())['cases'][0];raw=result()
    wrong={**raw['keyword'][0],'record_id':'wrong-record'}
    unrelated={**raw['keyword'][0],'lines':[20,25],'record_id':'record1'}
    raw['keyword']=[wrong,unrelated]
    out=r.evaluate(case,raw,verify_source=lambda e:{'verified':True,'source_sha256':'a'*64})
    assert out['outcome']=='fail' and not out['checks'][0]['retained']
def test_identity_cannot_come_from_omitted_same_range_hit():
    case=r.validate(suite())['cases'][0];raw=result();h=raw['keyword'][0]
    raw['keyword']=[{**h,'record_id':'wrong-record','passage':'x'*11950},{**h,'file':'filler.md','record_id':'filler','passage':'y'*11950},{**h,'record_id':'record1','passage':'z'*11950}]
    out=r.evaluate(case,raw)
    assert out['retrieval']['omitted']['keyword']>0 and out['outcome']=='fail'
@pytest.mark.parametrize('omitted',['manifest.json','suite.json','report.json','execution-receipt.json','decision-1.json','decision-1.attempt.json'])
def test_marker_cannot_omit_generated_artifact(setup,omitted):
    root,p=setup;out=execute(suite(),setup);path=root/out['run_id'];marker=json.loads((path/'COMPLETE').read_text())
    del marker['files'][omitted];(path/'COMPLETE').write_bytes(r.encoded(marker))
    with pytest.raises(r.Refused):r.load_run(root,out['run_id'])
def test_marker_cannot_add_arbitrary_hashed_file(setup):
    root,p=setup;out=execute(suite(),setup);path=root/out['run_id'];marker=json.loads((path/'COMPLETE').read_text())
    extra=path/'extra.json';extra.write_bytes(b'{}');extra.chmod(0o600)
    marker['files']['extra.json']=r.sha(b'{}');(path/'COMPLETE').write_bytes(r.encoded(marker))
    with pytest.raises(r.Refused):r.load_run(root,out['run_id'])
@pytest.mark.parametrize('field',['suite_hash','run_id','counts','case_ids'])
def test_rehashed_but_inconsistent_saved_identity_refused(setup,field):
    root,p=setup;out=execute(suite(),setup);path=root/out['run_id'];marker=json.loads((path/'COMPLETE').read_text())
    name='manifest.json' if field in ('suite_hash','run_id') else 'report.json'
    obj=json.loads((path/name).read_text());obj[field]={'pass':1} if field=='counts' else ([] if field=='case_ids' else 'changed')
    data=r.encoded(obj);(path/name).write_bytes(data);marker['files'][name]=r.sha(data)
    if name=='report.json':marker['report_sha256']=r.sha(data)
    (path/'COMPLETE').write_bytes(r.encoded(marker))
    with pytest.raises(r.Refused):r.load_run(root,out['run_id'])
@pytest.mark.parametrize('values',[[],[{}],[None],[{'index_built_at':'a'}],[{'index_built_at':'','index_updated_at':'b','embedder':'m'}],[{'index_built_at':'a','index_updated_at':'b','embedder':' '}]] )
def test_unknown_watermarks_never_comparable(setup,values):
    import copy
    root,p=setup;out=execute(suite(),setup);a=r.load_run(root,out['run_id']);a['report']['watermarks']=values;b=copy.deepcopy(a)
    compared=r.compare(a,b)
    assert 'unknown_corpus_index_model' in compared['warnings'] and not compared['comparable']
def test_empty_case_coverage_never_comparable(setup):
    import copy
    root,p=setup;out=execute(suite(),setup);a=r.load_run(root,out['run_id']);a['manifest']['plan']=[];a['report']['case_ids']=[];a['report']['watermarks']=[]
    compared=r.compare(a,copy.deepcopy(a));assert not compared['comparable']
def test_reserved_execution_receipt_case_id_refused():
    with pytest.raises(r.Refused):r.validate(suite(case_id='execution-receipt'))
