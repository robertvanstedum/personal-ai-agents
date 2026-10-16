import importlib.util
import json
from pathlib import Path
import pytest
from core.memory_ask import runner
from tests.memory_ask.test_runner import suite, execute, setup

from core.memory_ask import assessment as a


def fixture(setup):
    result=execute(suite(),setup)
    root,_=setup
    run=runner.load_run(root,result['run_id'],include_details=True)
    obj={'schema':a.SCHEMA,**a.binding(run,'decision-1'),
         'answer':'The owner decided to abandon every shared capability record.md:3-4.',
         'produced_at':None,'producer':{'backend':None,'model':None,'source_version':None},
         'transport_receipt_sha256':None}
    return root,run,obj


def test_range_pass_cannot_approve_wrong_meaning_or_owner_acceptance(setup):
    _,run,obj=fixture(setup)
    value=a.assess(runner.encoded(obj),run)
    assert value['citation_range_verdict']=='ranges_retained'
    assert value['claim_support']==value['owner_usefulness']==value['decision_chronology']=='needs_review'
    assert value['owner_acceptance']=='not_recorded'
    assert value['delivery_verification']=='not_verified_operator_import'
    assert value['source_provenance'][0]['record_id']=='record1'
    assert value['source_provenance'][0]['edition'] is None
    assert value['source_provenance'][0]['speaker']=='robert'
    assert value['source_provenance_state']=='retained_run_metadata_not_live_revalidated'
    assert 'does not establish semantic support' in a.render(value)


@pytest.mark.parametrize('answer,verdict',[
    ('The owner decided something entirely uncited.','fail'),
    ('The owner decided something record.md:1-7.','fail'),
    ('Heading','needs_review')])
def test_invalid_unreferenced_and_heading_are_not_passes(setup,answer,verdict):
    _,run,obj=fixture(setup);obj['answer']=answer
    assert a.assess(runner.encoded(obj),run)['citation_range_verdict']==verdict


@pytest.mark.parametrize('field',['run_id','case_sha256','run_snapshot_sha256','evidence_sha256'])
def test_import_bound_to_exact_saved_run_case_and_evidence(setup,field):
    _,run,obj=fixture(setup);obj[field]='changed'
    with pytest.raises(runner.Refused,match='binding_mismatch'):
        a.assess(runner.encoded(obj),run)


def test_strict_json_no_owner_assertion_and_bounded_answer(setup):
    _,run,obj=fixture(setup)
    raw=runner.encoded(obj)
    with pytest.raises(runner.Refused):a.assess(raw[:-2]+b',"answer":"duplicate"}',run)
    obj['owner_acceptance']='approved'
    with pytest.raises(runner.Refused):a.assess(runner.encoded(obj),run)
    del obj['owner_acceptance'];obj['answer']='x'*(32*1024+1)
    with pytest.raises(runner.Refused,match='answer_invalid'):a.assess(runner.encoded(obj),run)


def test_dates_are_unknown_not_mtime_or_run_date(setup):
    _,run,obj=fixture(setup)
    obj['produced_at']='2026-10-15T08:00:00-05:00'
    value=a.assess(runner.encoded(obj),run)
    assert value['dates']['answer_produced_at']==obj['produced_at']
    assert value['dates']['source_decision_time'] is None
    obj['produced_at']='2026-10-15T08:00:00'
    with pytest.raises(runner.Refused,match='timestamp'):a.assess(runner.encoded(obj),run)


def test_private_input_refuses_symlink_fifo_public_and_oversize(tmp_path):
    tmp_path.chmod(0o700)
    p=tmp_path/'input.json';p.write_bytes(b'{}');p.chmod(0o600)
    assert a.private_input(p)==b'{}'
    link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(OSError):a.private_input(link)
    p.chmod(0o644)
    with pytest.raises(runner.Refused):a.private_input(p)
    import os
    os.mkfifo(tmp_path/'fifo',0o600)
    with pytest.raises(runner.Refused):a.private_input(tmp_path/'fifo')
    p.chmod(0o600);p.write_bytes(b'x'*(a.MAX_INPUT+1))
    with pytest.raises(runner.Refused):a.private_input(p)


def test_plan_and_exclusive_sidecar_leave_original_run_unchanged(setup,tmp_path,capsys):
    root,run,obj=fixture(setup)
    before={p.name:p.read_bytes() for p in (root/run['manifest']['run_id']).iterdir()}
    args=['--runs',str(root),'--run-id',run['manifest']['run_id'],'--case-id','decision-1']
    assert a.main(args)==0
    assert json.loads(capsys.readouterr().out)['writes']==0
    output=tmp_path/'assessments';output.mkdir(mode=0o700)
    inp=tmp_path/'input.json';inp.write_bytes(runner.encoded(obj));inp.chmod(0o600);tmp_path.chmod(0o700)
    assert a.main(args+['--answer-import',str(inp)])==0
    assert not list(output.iterdir());capsys.readouterr()
    assert a.main(args+['--answer-import',str(inp),'--execute','--output',str(root/run['manifest']['run_id'])])==2
    capsys.readouterr()
    execute_args=args+['--answer-import',str(inp),'--execute','--output',str(output)]
    assert a.main(execute_args)==0
    assert a.main(execute_args)==2 # no overwrite of a completed assessment
    assert len([p for p in output.iterdir() if p.name.startswith('a-')])==1
    assert before=={p.name:p.read_bytes() for p in (root/run['manifest']['run_id']).iterdir()}


def test_cli_detects_corrupt_original_before_import(setup,capsys):
    root,run,_=fixture(setup)
    (root/run['manifest']['run_id']/'decision-1.json').write_text('{}')
    assert a.main(['--runs',str(root),'--run-id',run['manifest']['run_id'],'--case-id','decision-1'])==2
    assert 'assessment_refused' in capsys.readouterr().out


def test_publish_api_also_refuses_inside_completed_run(setup):
    root,run,obj=fixture(setup)
    value=a.assess(runner.encoded(obj),run)
    with pytest.raises(runner.Refused,match='separate_output_required'):
        a.publish(value,root/run['manifest']['run_id'],source_runs_root=root)


def test_atomic_parent_directory_replacement_refused(tmp_path,monkeypatch):
    import os
    tmp_path.chmod(0o700)
    current=tmp_path/'current';current.mkdir(mode=0o700)
    replacement=tmp_path/'next';replacement.mkdir(mode=0o700)
    for parent,body in [(current,b'old selected bytes'),(replacement,b'new selected bytes')]:
        p=parent/'input.json';p.write_bytes(body);p.chmod(0o600)
    original_read=os.read;replaced=[]
    def replacing_read(fd,n):
        data=original_read(fd,n)
        if data and not replaced:
            current.rename(tmp_path/'prior')
            replacement.rename(current)
            replaced.append(True)
        return data
    monkeypatch.setattr(a.os,'read',replacing_read)
    with pytest.raises(runner.Refused,match='input_changed'):
        a.private_input(current/'input.json')
    assert replaced and (current/'input.json').read_bytes()==b'new selected bytes'
    assert (tmp_path/'prior'/'input.json').read_bytes()==b'old selected bytes'


def test_template_is_bound_and_unknown_not_a_fake_answer(setup,capsys):
    root,run,_=fixture(setup)
    before={p.name:p.read_bytes() for p in (root/run['manifest']['run_id']).iterdir()}
    args=['--runs',str(root),'--run-id',run['manifest']['run_id'],'--case-id','decision-1','--template']
    assert a.main(args)==0
    obj=json.loads(capsys.readouterr().out)
    assert obj['schema']==a.SCHEMA and obj['answer']==''
    assert obj['produced_at'] is None and set(obj['producer'].values())=={None}
    assert {key:obj[key] for key in a.binding(run,'decision-1')}==a.binding(run,'decision-1')
    assert a.main(args+['--execute'])==2
    assert before=={p.name:p.read_bytes() for p in (root/run['manifest']['run_id']).iterdir()}


def test_saved_sidecar_reloads_and_reports_without_writes(setup,tmp_path,capsys):
    root,run,obj=fixture(setup)
    output=tmp_path/'assessments';output.mkdir(mode=0o700)
    # Keep exact original-byte hash even if operator supplied noncanonical JSON.
    value=a.assess(json.dumps(obj,indent=2).encode(),run)
    name=a.publish(value,output,source_runs_root=root);path=output/name
    before=path.read_bytes()
    assert a.load_assessment(path,run)==value
    args=['--runs',str(root),'--run-id',run['manifest']['run_id'],'--case-id','decision-1','--assessment',str(path)]
    assert a.main(args)==0
    assert 'Delivery unverified' in capsys.readouterr().out
    assert a.main(args+['--execute'])==2
    assert path.read_bytes()==before and len(list(output.iterdir()))==1


def test_sidecar_changed_bytes_and_rehashed_false_acceptance_refused(setup,tmp_path):
    root,run,obj=fixture(setup);output=tmp_path/'assessments';output.mkdir(mode=0o700)
    value=a.assess(runner.encoded(obj),run)
    path=output/a.publish(value,output,source_runs_root=root)
    raw=path.read_bytes();path.write_bytes(raw+b' ')
    with pytest.raises(runner.Refused,match='assessment_hash_mismatch'):
        a.load_assessment(path,run)
    value['owner_acceptance']='accepted'
    forged=output/('a-'+runner.sha(runner.encoded(value))+'.json')
    forged.write_bytes(runner.encoded(value));forged.chmod(0o600)
    with pytest.raises(runner.Refused,match='assessment_derived_fields_mismatch'):
        a.load_assessment(forged,run)


def test_saved_sidecar_cannot_be_rebound_to_revised_question(setup,tmp_path):
    root,first,obj=fixture(setup);output=tmp_path/'assessments';output.mkdir(mode=0o700)
    path=output/a.publish(a.assess(runner.encoded(obj),first),output,source_runs_root=root)
    later=execute(suite(question='Different question?'),setup)
    second=runner.load_run(root,later['run_id'],include_details=True)
    with pytest.raises(runner.Refused,match='binding_mismatch'):
        a.load_assessment(path,second)


def test_template_empty_answer_is_refused_not_synthetic_gold(setup):
    _,run,_=fixture(setup)
    with pytest.raises(runner.Refused,match='answer_invalid'):
        a.assess(runner.encoded(a.template(run,'decision-1')),run)
